"""Network activity for the floating monitor: how fast the Internet path moves data, which apps
hold connections, and the basic facts of the connection. Read-only, no Admin, no child process.

    Throughput   GetIfEntry2 byte counters of the interface Internet traffic takes, sampled once
                 a second by a thread that runs only while someone is watching (stops when idle).
    Apps         GetExtendedTcpTable / GetExtendedUdpTable give the owning process of every socket;
                 apps are ranked by established TCP connections to other machines. Windows has no
                 per-app byte counters without Admin (ETW), so this is a count, not a speed.
    Connection   interface name and kind, link speed, local IPv4, DNS servers.
"""
from __future__ import annotations

import collections
import ctypes
import ipaddress
import socket
import struct
import threading
import time
from ctypes import wintypes
from typing import Any, Callable

from . import winutil

SAMPLE_S = 1.0
HISTORY_S = 120          # seconds of throughput the window can show right after it opens
IDLE_STOP_S = 30         # the sampler stops this long after the last request
APPS_CACHE_S = 3.0
CONNECTION_CACHE_S = 10.0
APPS_LIMIT = 15

_AF_INET, _AF_INET6 = 2, 23
_TCP_TABLE_OWNER_PID_ALL = 5
_UDP_TABLE_OWNER_PID = 1
_TCP_ESTABLISHED = 5
_ERROR_INSUFFICIENT_BUFFER = 122

# IFTYPE values (ipifcons.h) -> what the UI calls the connection
_KIND_BY_IFTYPE = {6: "ethernet", 71: "wifi", 243: "cellular", 244: "cellular", 23: "ppp", 131: "tunnel", 53: "tunnel"}


# --- interface counters ----------------------------------------------------------------------

class _MibIfRow2(ctypes.Structure):   # MIB_IF_ROW2 (netioapi.h), 1352 bytes
    _fields_ = [
        ("InterfaceLuid", ctypes.c_uint64), ("InterfaceIndex", wintypes.ULONG),
        ("InterfaceGuid", ctypes.c_ubyte * 16),
        ("Alias", ctypes.c_wchar * 257), ("Description", ctypes.c_wchar * 257),
        ("PhysicalAddressLength", wintypes.ULONG), ("PhysicalAddress", ctypes.c_ubyte * 32),
        ("PermanentPhysicalAddress", ctypes.c_ubyte * 32),
        ("Mtu", wintypes.ULONG), ("Type", wintypes.ULONG), ("TunnelType", ctypes.c_int),
        ("MediaType", ctypes.c_int), ("PhysicalMediumType", ctypes.c_int), ("AccessType", ctypes.c_int),
        ("DirectionType", ctypes.c_int), ("InterfaceAndOperStatusFlags", ctypes.c_ubyte),
        ("OperStatus", ctypes.c_int), ("AdminStatus", ctypes.c_int), ("MediaConnectState", ctypes.c_int),
        ("NetworkGuid", ctypes.c_ubyte * 16), ("ConnectionType", ctypes.c_int),
        ("TransmitLinkSpeed", ctypes.c_uint64), ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("InOctets", ctypes.c_uint64), ("InUcastPkts", ctypes.c_uint64), ("InNUcastPkts", ctypes.c_uint64),
        ("InDiscards", ctypes.c_uint64), ("InErrors", ctypes.c_uint64), ("InUnknownProtos", ctypes.c_uint64),
        ("InUcastOctets", ctypes.c_uint64), ("InMulticastOctets", ctypes.c_uint64),
        ("InBroadcastOctets", ctypes.c_uint64),
        ("OutOctets", ctypes.c_uint64), ("OutUcastPkts", ctypes.c_uint64), ("OutNUcastPkts", ctypes.c_uint64),
        ("OutDiscards", ctypes.c_uint64), ("OutErrors", ctypes.c_uint64), ("OutUcastOctets", ctypes.c_uint64),
        ("OutMulticastOctets", ctypes.c_uint64), ("OutBroadcastOctets", ctypes.c_uint64),
        ("OutQLen", ctypes.c_uint64),
    ]


def interface_row(index: int) -> dict[str, Any] | None:
    """Counters and identity of one interface (GetIfEntry2). None if it is gone or the call fails."""
    try:
        lib = ctypes.WinDLL("iphlpapi")
        row = _MibIfRow2()
        row.InterfaceIndex = index
        if lib.GetIfEntry2(ctypes.byref(row)) != 0:
            return None
    except (AttributeError, OSError):
        return None
    return {"index": index, "alias": row.Alias, "description": row.Description,
            "kind": _KIND_BY_IFTYPE.get(int(row.Type), "other"),
            "rx_link_bps": int(row.ReceiveLinkSpeed), "tx_link_bps": int(row.TransmitLinkSpeed),
            "in_octets": int(row.InOctets), "out_octets": int(row.OutOctets)}


def rates(prev: tuple[float, int, int] | None, cur: tuple[float, int, int]) -> tuple[float, float] | None:
    """(ts, in_octets, out_octets) twice -> (down, up) in bits per second. None when there is no
    earlier sample, no time passed, or a counter went backwards (the interface was reset)."""
    if prev is None:
        return None
    dt = cur[0] - prev[0]
    d_in, d_out = cur[1] - prev[1], cur[2] - prev[2]
    if dt <= 0 or d_in < 0 or d_out < 0:
        return None
    return d_in * 8 / dt, d_out * 8 / dt


class Meter:
    """Samples the Internet interface's counters once a second while someone is watching.

    The first snapshot() starts the thread; it stops by itself IDLE_STOP_S after the last one, so
    the monitor does no extra work while the floating window is closed. A change of interface
    (Wi-Fi to cable, a VPN coming up) starts a new series instead of producing a bogus spike."""

    def __init__(self, route: Callable[[], dict | None] = winutil.internet_route_native,
                 read_row: Callable[[int], dict | None] = interface_row,
                 clock: Callable[[], float] = time.time, start_thread: bool = True) -> None:
        self._route, self._read_row, self._clock = route, read_row, clock
        self._start_thread = start_thread
        self._lock = threading.Lock()
        self._series: collections.deque[tuple[float, float, float]] = collections.deque()
        self._prev: tuple[float, int, int] | None = None
        self._index: int | None = None
        self.interface: dict[str, Any] | None = None
        self._last_request = 0.0
        self._thread: threading.Thread | None = None

    def sample(self) -> None:
        """One reading (the thread calls it every second; tests call it directly)."""
        route = self._route()
        index = route["interface_index"] if route else None
        row = self._read_row(index) if index is not None else None
        now = self._clock()
        with self._lock:
            if row is None or index != self._index:
                self._prev = None
            self._index = index
            if row is not None:
                self.interface = {k: row[k] for k in ("index", "alias", "description", "kind", "rx_link_bps", "tx_link_bps")}
                cur = (now, row["in_octets"], row["out_octets"])
                bps = rates(self._prev, cur)
                self._prev = cur
                if bps is not None:
                    self._series.append((round(now, 3), round(bps[0]), round(bps[1])))
            else:
                self.interface = None
            while self._series and self._series[0][0] < now - HISTORY_S:
                self._series.popleft()

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._clock() - self._last_request > IDLE_STOP_S:
                    self._thread = None
                    return
            try:
                self.sample()
            except Exception:   # a bad reading must not end the thread while the window is open
                pass
            time.sleep(SAMPLE_S)

    def snapshot(self) -> dict[str, Any]:
        thread = None
        with self._lock:
            self._last_request = self._clock()
            if self._start_thread and self._thread is None:
                thread = self._thread = threading.Thread(target=self._run, name="traffic-meter", daemon=True)
        if thread is not None:
            thread.start()
        with self._lock:
            return {"interface": dict(self.interface) if self.interface else None,
                    "series": [list(s) for s in self._series]}


# --- sockets per process -------------------------------------------------------------------------

def _is_local(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def parse_tcp_table(buf: bytes, family: int) -> list[tuple[int, int, str]]:
    """MIB_TCPTABLE_OWNER_PID / MIB_TCP6TABLE_OWNER_PID -> [(pid, state, remote address)]."""
    count = struct.unpack_from("<I", buf, 0)[0]
    rows = []
    if family == _AF_INET:     # state, local addr, local port, remote addr, remote port, pid
        for i in range(count):
            state, _, _, remote, _, pid = struct.unpack_from("<6I", buf, 4 + 24 * i)
            rows.append((pid, state, socket.inet_ntoa(remote.to_bytes(4, "little"))))
    else:                      # local addr[16], scope, port, remote addr[16], scope, port, state, pid
        for i in range(count):
            off = 4 + 56 * i
            remote = socket.inet_ntop(socket.AF_INET6, buf[off + 24:off + 40])
            state, pid = struct.unpack_from("<2I", buf, off + 48)
            rows.append((pid, state, remote))
    return rows


def parse_udp_table(buf: bytes, family: int) -> list[tuple[int, str]]:
    """MIB_UDPTABLE_OWNER_PID / MIB_UDP6TABLE_OWNER_PID -> [(pid, local address)]."""
    count = struct.unpack_from("<I", buf, 0)[0]
    rows = []
    if family == _AF_INET:     # local addr, local port, pid
        for i in range(count):
            local, _, pid = struct.unpack_from("<3I", buf, 4 + 12 * i)
            rows.append((pid, socket.inet_ntoa(local.to_bytes(4, "little"))))
    else:                      # local addr[16], scope, port, pid
        for i in range(count):
            off = 4 + 28 * i
            rows.append((struct.unpack_from("<I", buf, off + 24)[0], socket.inet_ntop(socket.AF_INET6, buf[off:off + 16])))
    return rows


def count_by_process(tcp: list[tuple[int, int, str]], udp: list[tuple[int, str]]) -> dict[int, dict[str, int]]:
    """Per process: established TCP connections to other machines, UDP sockets not on loopback.
    Only processes with at least one such TCP connection are kept (UDP alone is mostly listeners)."""
    out: dict[int, dict[str, int]] = {}
    for pid, state, remote in tcp:
        if pid and state == _TCP_ESTABLISHED and not _is_local(remote):
            out.setdefault(pid, {"tcp": 0, "udp": 0})["tcp"] += 1
    for pid, local in udp:
        if pid in out and not (_is_local(local) and local not in ("0.0.0.0", "::")):
            out[pid]["udp"] += 1
    return out


def _extended_table(fn: Any, family: int, table_class: int) -> bytes | None:
    size = wintypes.DWORD(0)
    for _ in range(3):   # the table can grow between the size query and the real call
        buf = ctypes.create_string_buffer(max(size.value, 4))
        rc = fn(buf, ctypes.byref(size), False, family, table_class, 0)
        if rc == 0:
            return buf.raw
        if rc != _ERROR_INSUFFICIENT_BUFFER:
            return None
    return None


def socket_owners() -> tuple[list[tuple[int, int, str]], list[tuple[int, str]]] | None:
    try:
        lib = ctypes.WinDLL("iphlpapi")
    except (AttributeError, OSError):
        return None
    tcp: list[tuple[int, int, str]] = []
    udp: list[tuple[int, str]] = []
    for family in (_AF_INET, _AF_INET6):
        raw = _extended_table(lib.GetExtendedTcpTable, family, _TCP_TABLE_OWNER_PID_ALL)
        if raw is not None:
            tcp += parse_tcp_table(raw, family)
        raw = _extended_table(lib.GetExtendedUdpTable, family, _UDP_TABLE_OWNER_PID)
        if raw is not None:
            udp += parse_udp_table(raw, family)
    return tcp, udp


# --- process names -----------------------------------------------------------------------------

class _ProcessEntry32(ctypes.Structure):   # PROCESSENTRY32W
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def process_exes() -> dict[int, str]:
    """pid -> executable file name for every process (Toolhelp; works for other users' processes)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    out: dict[int, str] = {}
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[int(entry.th32ProcessID)] = entry.szExeFile
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def process_path(pid: int) -> str | None:
    """Full image path; None for processes we may not query (protected, other sessions)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_wchar_p,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
    finally:
        k32.CloseHandle(handle)


def file_description(path: str) -> str | None:
    """The FileDescription of an executable's version resource ("Windows Explorer"), if any."""
    ver = ctypes.WinDLL("version")
    ver.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    ver.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, data):
        return None
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    codes = []
    if ver.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) and length.value >= 4:
        lang, page = struct.unpack("<HH", ctypes.string_at(ptr, 4))
        codes.append(f"{lang:04x}{page:04x}")
    codes += ["040904b0", "040904e4", "000004b0"]
    for code in codes:
        if ver.VerQueryValueW(data, f"\\StringFileInfo\\{code}\\FileDescription", ctypes.byref(ptr),
                              ctypes.byref(length)) and length.value > 1:
            text = ctypes.wstring_at(ptr, length.value - 1).strip()
            if text:
                return text
    return None


def display_name(exe: str, description: str | None) -> str:
    """What the list shows: the version resource's description, else the file name without .exe."""
    if description:
        return description
    return exe[:-4] if exe.lower().endswith(".exe") else exe


def group_apps(counts: dict[int, dict[str, int]], exes: dict[int, str], paths: dict[int, str | None],
               describe: Callable[[str], str | None], limit: int = APPS_LIMIT) -> list[dict[str, Any]]:
    """Processes of the same program (a browser's many processes) become one row, busiest first."""
    groups: dict[str, dict[str, Any]] = {}
    for pid, n in counts.items():
        exe = exes.get(pid) or f"PID {pid}"
        path = paths.get(pid)
        name = display_name(exe, describe(path) if path else None)
        key = f"{name}|{exe}".lower()     # two installed versions of one app are still one app
        row = groups.get(key)
        if row is None:
            row = groups[key] = {"name": name, "exe": exe, "processes": 0, "tcp": 0, "udp": 0}
        row["processes"] += 1
        row["tcp"] += n["tcp"]
        row["udp"] += n["udp"]
    return sorted(groups.values(), key=lambda r: (-r["tcp"], -r["udp"], r["name"].lower()))[:limit]


class AppList:
    """Apps with open connections, cached a few seconds; file descriptions cached per path."""

    def __init__(self, owners: Callable[[], Any] = socket_owners, exes: Callable[[], dict[int, str]] = process_exes,
                 path_of: Callable[[int], str | None] = process_path,
                 describe: Callable[[str], str | None] = file_description,
                 clock: Callable[[], float] = time.time) -> None:
        self._owners, self._exes, self._path_of, self._describe_raw = owners, exes, path_of, describe
        self._clock = clock
        self._descriptions: dict[str, str | None] = {}
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._lock = threading.Lock()

    def _describe(self, path: str) -> str | None:
        if path not in self._descriptions:
            try:
                self._descriptions[path] = self._describe_raw(path)
            except OSError:
                self._descriptions[path] = None
        return self._descriptions[path]

    def apps(self) -> list[dict[str, Any]] | None:
        with self._lock:
            now = self._clock()
            if self._cache and now - self._cache[0] < APPS_CACHE_S:
                return self._cache[1]
            owners = self._owners()
            if owners is None:
                return None
            counts = count_by_process(*owners)
            counts.pop(0, None)
            exes = self._exes()
            rows = group_apps(counts, exes, {pid: self._path_of(pid) for pid in counts}, self._describe)
            self._cache = (now, rows)
            return rows


# --- the connection's basic facts ------------------------------------------------------------------

def local_ipv4(destination: str = "1.1.1.1") -> str | None:
    """The address this PC uses to reach the Internet. connect() on UDP only picks a route: nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((destination, 53))
            return s.getsockname()[0]
    except OSError:
        return None


class Traffic:
    """What GET /api/traffic returns: throughput series, apps, connection facts."""

    def __init__(self, meter: Meter | None = None, app_list: AppList | None = None,
                 local_address: Callable[[], str | None] = local_ipv4,
                 dns: Callable[[], dict[int, list[str]] | None] = winutil.dns_servers_native,
                 clock: Callable[[], float] = time.time) -> None:
        self.meter = meter or Meter(clock=clock)
        self.app_list = app_list or AppList(clock=clock)
        self._local_address, self._dns, self._clock = local_address, dns, clock
        self._connection: tuple[float, int | None, dict[str, Any]] | None = None

    def connection(self, interface: dict[str, Any] | None) -> dict[str, Any]:
        index = interface["index"] if interface else None
        cached = self._connection
        if cached and cached[1] == index and self._clock() - cached[0] < CONNECTION_CACHE_S:
            return cached[2]
        servers = (self._dns() or {}).get(index, []) if index is not None else []
        info = {"local_ipv4": self._local_address(), "dns": servers}
        self._connection = (self._clock(), index, info)
        return info

    def snapshot(self) -> dict[str, Any]:
        meter = self.meter.snapshot()
        return {"ts": self._clock(), "interface": meter["interface"], "series": meter["series"],
                "apps": self.app_list.apps(), "connection": self.connection(meter["interface"])}
