"""Whether the speed test's connection costs money (ADR-0024, decision 4): the question before a run
and the 250 MB cap depend on it.

Windows keeps a cost for every network connection: what "Set as metered connection" sets, what it sets
itself for mobile broadband, and what a phone's hotspot can tell it. `verdict` decides from plain
readings (pure, tested without Windows); `read` takes those readings unelevated and read-only through
the Network List Manager's COM interfaces and iphlpapi, with ctypes (no third-party package, ADR-0001).
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Callable

# NLM_CONNECTION_COST (netlistmgr.h)
COST_UNKNOWN = 0x0
COST_UNRESTRICTED = 0x1
COST_FIXED = 0x2
COST_VARIABLE = 0x4
COST_OVERDATALIMIT = 0x10000
COST_CONGESTED = 0x20000
COST_ROAMING = 0x40000
COST_APPROACHINGDATALIMIT = 0x80000
_COSTS_MONEY = COST_FIXED | COST_VARIABLE | COST_OVERDATALIMIT | COST_ROAMING | COST_APPROACHINGDATALIMIT

# A phone's USB tether shows up as one of these adapters, which Windows usually reports as an
# unmetered Ethernet: Android's RNDIS tethering device, the iPhone's USB networking.
TETHER_DESCRIPTIONS = ("remote ndis", "apple mobile device ethernet")
# Interface kinds with no physical medium of their own (app/traffic.py): what is underneath counts.
VIRTUAL_KINDS = ("tunnel", "ppp", "other")

IPV4_TARGET = "1.1.1.1"
IPV6_TARGET = "2606:4700:4700::1111"


def costs_money(cost: int | None) -> bool:
    """A fixed or variable cost, roaming, or over or near the data limit. Unknown and unrestricted: no."""
    return bool(cost) and bool(cost & _COSTS_MONEY)


def is_tether(description: str | None) -> bool:
    text = (description or "").lower()
    return any(name in text for name in TETHER_DESCRIPTIONS)


@dataclass
class Interface:
    index: int
    kind: str                                   # app/traffic.py's kinds: wifi, ethernet, cellular, tunnel, …
    description: str = ""


@dataclass
class Connection:
    """One connected network connection as the Network List Manager reports it."""
    interface_index: int | None                 # None when its adapter could not be mapped to an interface
    internet: bool
    cost: int | None                            # NLM_CONNECTION_COST flags, None when it could not be read
    network_id: str | None = None               # the network's GUID, kept to remember a confirmation


@dataclass
class Verdict:
    metered: bool
    # When the route leaves by a VPN or other tunnel and only a network underneath is metered, the
    # verdict is a guess: these are that network's ids, and a confirmation is remembered for them.
    # Empty when the verdict is certain (or not metered).
    guessed_from: tuple[str, ...] = field(default_factory=tuple)

    @property
    def guessed(self) -> bool:
        return self.metered and bool(self.guessed_from)


NOT_METERED = Verdict(False)


def _metered_interface(index: int | None, interfaces: dict[int, Interface], connections: list[Connection]) -> bool:
    """Mobile broadband or a phone's tether by the adapter, else a cost that costs money on its connection."""
    if index is None:
        return False
    interface = interfaces.get(index)
    if interface is not None and (interface.kind == "cellular" or is_tether(interface.description)):
        return True
    return any(c.interface_index == index and costs_money(c.cost) for c in connections)


def verdict(route_indexes: list[int], interfaces: dict[int, Interface], connections: list[Connection]) -> Verdict:
    """Metered when an interface the Internet routes leave by (IPv4, IPv6) is mobile broadband, a phone's
    USB tether, or a connection whose cost costs money. When none is but a route leaves by a tunnel (a
    VPN), every other connection that reaches the Internet counts the same way, and the verdict is a
    guess named by those networks' ids. A tunnel counts only when Windows lists it as a connected network:
    with IPv6 on but no IPv6 route, the IPv6 route can be the loopback or a Teredo tunnel, which is no
    VPN. An underlying network whose id could not be read cannot be remembered, so the verdict is then
    certain (it asks every time). No route: not metered."""
    routes = list(dict.fromkeys(route_indexes))
    if any(_metered_interface(i, interfaces, connections) for i in routes):
        return Verdict(True)
    listed = {c.interface_index for c in connections}
    if not any(i in interfaces and interfaces[i].kind in VIRTUAL_KINDS and i in listed for i in routes):
        return NOT_METERED
    under = [c for c in connections if c.internet and c.interface_index not in routes
             and (costs_money(c.cost) or _metered_interface(c.interface_index, interfaces, []))]
    if not under:
        return NOT_METERED
    if any(not c.network_id for c in under):
        return Verdict(True)
    return Verdict(True, tuple(sorted({c.network_id for c in under if c.network_id})))


# --- reading it from Windows ---------------------------------------------------------------------------

_CLSID_NETWORK_LIST_MANAGER = "DCB00C01-570F-4A9B-8D69-199FDBA5723B"
_IID_INETWORK_LIST_MANAGER = "DCB00000-570F-4A9B-8D69-199FDBA5723B"
_IID_INETWORK_CONNECTION_COST = "DCB0000A-570F-4A9B-8D69-199FDBA5723B"
_CLSCTX_SERVER = 0x1 | 0x4 | 0x10        # inproc server, local server, remote server
_RPC_E_CHANGED_MODE = -2147417850        # 0x80010106: this thread already has another apartment
_COINIT_MULTITHREADED = 0x0

# vtable slots (netlistmgr.h). IUnknown is 0-2; IDispatch adds 3-6.
_RELEASE = 2
_QUERY_INTERFACE = 0
_LIST_GET_NETWORK_CONNECTIONS = 9         # INetworkListManager::GetNetworkConnections(IEnumNetworkConnections**)
_ENUM_NEXT = 8                            # IEnumNetworkConnections::Next(ULONG, INetworkConnection**, ULONG*)
_CONNECTION_GET_NETWORK = 7               # INetworkConnection::GetNetwork(INetwork**)
_CONNECTION_IS_CONNECTED_TO_INTERNET = 8  # INetworkConnection::get_IsConnectedToInternet(VARIANT_BOOL*)
_CONNECTION_GET_ADAPTER_ID = 12           # INetworkConnection::GetAdapterId(GUID*)
_NETWORK_GET_NETWORK_ID = 11              # INetwork::GetNetworkId(GUID*)
_COST_GET_COST = 3                        # INetworkConnectionCost::GetCost(DWORD*)


class _Guid(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


def _guid(text: str) -> _Guid:
    g = _Guid()
    ctypes.memmove(g.data, uuid.UUID(text).bytes_le, 16)
    return g


def _call(obj: ctypes.c_void_p, slot: int, *args: tuple[Any, Any]) -> None:
    """Calls method `slot` of the COM object `obj` with (ctypes type, value) arguments; raises OSError on a
    failing HRESULT."""
    vtable = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *[a[0] for a in args])(vtable[slot])
    hr = method(obj, *[a[1] for a in args])
    if hr < 0:
        raise OSError(f"HRESULT {hr & 0xFFFFFFFF:#010x}")


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        vtable = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[_RELEASE])(obj)


def _read_connection(conn: ctypes.c_void_p, index_of: Callable[[bytes], int | None]) -> Connection:
    internet = ctypes.c_short()
    try:
        _call(conn, _CONNECTION_IS_CONNECTED_TO_INTERNET, (ctypes.POINTER(ctypes.c_short), ctypes.byref(internet)))
    except OSError:
        pass
    index = None
    adapter = _Guid()
    try:
        _call(conn, _CONNECTION_GET_ADAPTER_ID, (ctypes.POINTER(_Guid), ctypes.byref(adapter)))
        index = index_of(bytes(adapter.data))
    except OSError:
        pass
    cost: int | None = None
    cost_obj = ctypes.c_void_p()
    try:
        iid = _guid(_IID_INETWORK_CONNECTION_COST)
        _call(conn, _QUERY_INTERFACE, (ctypes.POINTER(_Guid), ctypes.byref(iid)),
              (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(cost_obj)))
        value = wintypes.DWORD()
        _call(cost_obj, _COST_GET_COST, (ctypes.POINTER(wintypes.DWORD), ctypes.byref(value)))
        cost = int(value.value)
    except OSError:
        pass
    finally:
        _release(cost_obj)
    network_id = None
    network = ctypes.c_void_p()
    try:
        _call(conn, _CONNECTION_GET_NETWORK, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(network)))
        nid = _Guid()
        _call(network, _NETWORK_GET_NETWORK_ID, (ctypes.POINTER(_Guid), ctypes.byref(nid)))
        network_id = str(uuid.UUID(bytes_le=bytes(nid.data)))
    except OSError:
        pass
    finally:
        _release(network)
    return Connection(index, internet.value != 0, cost, network_id)


def read_connections(index_of: Callable[[bytes], int | None] | None = None) -> list[Connection]:
    """Every connected network connection, with its interface, Internet access, cost and network id. What
    cannot be read is left out or None; a failure of COM itself gives an empty list, never an exception."""
    if index_of is None:
        from . import winutil
        index_of = winutil.interface_index_of_guid
    out: list[Connection] = []
    try:
        ole32 = ctypes.WinDLL("ole32")
    except (AttributeError, OSError):
        return out
    ole32.CoInitializeEx.restype = ctypes.c_long
    hr = ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    initialised = hr >= 0          # S_OK or S_FALSE: ours to undo; RPC_E_CHANGED_MODE: COM works, not ours
    if hr < 0 and hr != _RPC_E_CHANGED_MODE:
        return out
    manager, enum = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid, iid = _guid(_CLSID_NETWORK_LIST_MANAGER), _guid(_IID_INETWORK_LIST_MANAGER)
        ole32.CoCreateInstance.restype = ctypes.c_long
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_SERVER, ctypes.byref(iid),
                                  ctypes.byref(manager)) < 0:
            return out
        _call(manager, _LIST_GET_NETWORK_CONNECTIONS, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(enum)))
        for _ in range(64):   # a machine has a handful; the bound only guards against a broken enumerator
            conn, fetched = ctypes.c_void_p(), ctypes.c_ulong()
            _call(enum, _ENUM_NEXT, (ctypes.c_ulong, 1), (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(conn)),
                  (ctypes.POINTER(ctypes.c_ulong), ctypes.byref(fetched)))
            if fetched.value == 0 or not conn:
                break
            try:
                out.append(_read_connection(conn, index_of))
            finally:
                _release(conn)
    except OSError:
        pass
    finally:
        _release(enum)
        _release(manager)
        if initialised:
            ole32.CoUninitialize()
    return out


def read() -> Verdict:
    """The verdict for the connection the speed test would use now. Anything that cannot be read counts as
    not metered (as an unknown route did before), so a broken reading never blocks the test."""
    try:
        from . import traffic, winutil
        routes = [r["interface_index"] for r in (winutil.internet_route_native(IPV4_TARGET),
                                                 winutil.internet_route6_native(IPV6_TARGET)) if r]
        if not routes:
            return NOT_METERED
        connections = read_connections()
        interfaces: dict[int, Interface] = {}
        for index in {*routes, *(c.interface_index for c in connections if c.interface_index is not None)}:
            row = traffic.interface_row(index)
            if row:
                interfaces[index] = Interface(index, row["kind"], row.get("description") or "")
        return verdict(routes, interfaces, connections)
    except Exception:
        return NOT_METERED
