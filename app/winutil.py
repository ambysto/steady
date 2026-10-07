"""Windows helpers: PowerShell, netsh, Admin check, Wi-Fi and uplink discovery.

Everything here is read-only. Callers that write to the system (tweaks, actions)
must go through run_powershell() themselves and be explicitly user-approved.
"""
from __future__ import annotations

import base64
import ctypes
import json
import re
import socket
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

CREATE_NO_WINDOW = 0x08000000
_UTF8_PREAMBLE = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "


class PowerShellError(RuntimeError):
    def __init__(self, message: str, returncode: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def encode_command(script: str) -> str:
    """Base64 of the UTF-16LE script, as expected by powershell -EncodedCommand."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def run_powershell(script: str, timeout: float = 30.0) -> str:
    """Run a script in Windows PowerShell 5.1 and return stdout (UTF-8).

    Raises PowerShellError on non-zero exit, timeout, or when powershell.exe is
    missing. Scripts are passed with -EncodedCommand, never string-spliced.
    """
    cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", encode_command(_UTF8_PREAMBLE + script),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise PowerShellError(f"PowerShell timed out after {timeout}s") from exc
    except OSError as exc:
        raise PowerShellError(f"cannot start powershell.exe: {exc}") from exc
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise PowerShellError(
            f"PowerShell exited with {proc.returncode}: {stderr.strip()[:500]}",
            returncode=proc.returncode, stderr=stderr,
        )
    return stdout


def run_powershell_json(script: str, timeout: float = 30.0, depth: int = 4) -> Any:
    """Run a script whose output objects are serialised to JSON and parsed.

    Output is always wrapped in an array so a single result is still a list;
    an empty pipeline yields [].
    """
    wrapped = f"ConvertTo-Json -InputObject @({script}) -Depth {depth} -Compress"
    out = run_powershell(wrapped, timeout=timeout).strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise PowerShellError(f"PowerShell returned invalid JSON: {out[:200]!r}") from exc
    return data if isinstance(data, list) else [data]


def _oem_codepage() -> str:
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except (AttributeError, OSError):
        return "cp437"


def run_netsh(args: list[str], timeout: float = 15.0) -> str:
    """Run netsh and decode its OEM-codepage output. Returns "" on failure."""
    try:
        proc = subprocess.run(
            ["netsh", *args], capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return proc.stdout.decode(_oem_codepage(), errors="replace")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


# --- netsh wlan show interfaces ------------------------------------------------

_KV_RE = re.compile(r"^\s*([^:]+?)\s*:\s*(.*)$")


def parse_netsh_interfaces(text: str) -> list[dict[str, str]]:
    """Split `netsh wlan show interfaces` into one dict per interface.

    A new interface starts at each "Name" key. Within an interface the first
    occurrence of a key wins. Values keep their inner colons (BSSIDs, times).
    """
    interfaces: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        m = _KV_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        if key == "Name":
            current = {}
            interfaces.append(current)
        if current is None:
            continue
        current.setdefault(key, value)
    return interfaces


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    m = re.match(r"-?\d+", value.strip())
    return int(m.group(0)) if m else None


@dataclass(frozen=True)
class WifiState:
    interface: str
    state: str          # "connected", "disconnected", ...
    ssid: str
    bssid: str
    radio_type: str
    channel: int | None
    signal: int | None  # percent
    rssi: int | None    # dBm
    rx_mbps: int | None
    tx_mbps: int | None
    profile: str = ""   # Wi-Fi profile in use (empty when disconnected)

    @property
    def connected(self) -> bool:
        return self.state.lower() == "connected"


def wifi_state_from(fields: dict[str, str]) -> WifiState:
    return WifiState(
        interface=fields.get("Name", ""),
        state=fields.get("State", ""),
        ssid=fields.get("SSID", ""),
        bssid=fields.get("AP BSSID", ""),
        radio_type=fields.get("Radio type", ""),
        channel=_to_int(fields.get("Channel")),
        signal=_to_int(fields.get("Signal")),
        rssi=_to_int(fields.get("Rssi")),
        rx_mbps=_to_int(fields.get("Receive rate (Mbps)")),
        tx_mbps=_to_int(fields.get("Transmit rate (Mbps)")),
        profile=fields.get("Profile", ""),
    )


def get_wifi_states() -> list[WifiState]:
    return [wifi_state_from(f) for f in parse_netsh_interfaces(run_netsh(["wlan", "show", "interfaces"]))]


def get_wifi_state() -> WifiState | None:
    """The connected Wi-Fi interface if any, else the first one, else None."""
    states = get_wifi_states()
    for st in states:
        if st.connected:
            return st
    return states[0] if states else None


# --- adapters and uplink --------------------------------------------------------

_WIFI_MEDIA = "Native 802.11"

def _adapters_ps(physical_only: bool) -> str:
    # DriverDate is normalised to yyyy-MM-dd here: PowerShell 5.1 would otherwise serialise
    # a DateTime as "\/Date(...)\/".
    return (
        f"Get-NetAdapter {'-Physical ' if physical_only else ''}-ErrorAction SilentlyContinue | "
        "Select-Object Name, InterfaceDescription, Status, LinkSpeed, MediaType, PhysicalMediaType, "
        "InterfaceIndex, MacAddress, Virtual, DriverVersion, DriverProvider, "
        "@{n='DriverDate';e={ try { ([datetime]$_.DriverDate).ToString('yyyy-MM-dd') } catch { $null } }}"
    )

# Best IPv4 default route = lowest (route metric + interface metric), like the logger did.
_UPLINK_PS = (
    "Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
    "Where-Object { $_.NextHop -ne '0.0.0.0' } | "
    "Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1 | "
    "ForEach-Object { [pscustomobject]@{ gateway = $_.NextHop; interface_index = $_.InterfaceIndex; "
    "interface_alias = $_.InterfaceAlias; metric = ($_.RouteMetric + $_.InterfaceMetric) } }"
)


def is_wifi_adapter(adapter: dict[str, Any]) -> bool:
    return _WIFI_MEDIA in str(adapter.get("PhysicalMediaType", ""))


# Adapter names/descriptions of VPN and tunnel software.
VPN_RE = re.compile(r"VPN|WireGuard|Wintun|\bTAP\b|TAP-|OpenVPN|NetBird|Surfshark|Tailscale|ZeroTier|NordLynx|"
                     r"Proton|WARP|Fortinet|Cisco AnyConnect|Cloudflare", re.I)


def get_adapters(physical_only: bool = True) -> list[dict[str, Any]]:
    """Network adapters. Physical (Wi-Fi, Ethernet) by default; False adds virtual/VPN ones."""
    return run_powershell_json(_adapters_ps(physical_only))


def get_uplink() -> dict[str, Any] | None:
    """Current primary route: gateway IP, interface index/alias. None if offline."""
    rows = run_powershell_json(_UPLINK_PS)
    return rows[0] if rows else None


class _MibIpForwardRow(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "dwForwardDest", "dwForwardMask", "dwForwardPolicy", "dwForwardNextHop", "dwForwardIfIndex",
        "dwForwardType", "dwForwardProto", "dwForwardAge", "dwForwardNextHopAS",
        "dwForwardMetric1", "dwForwardMetric2", "dwForwardMetric3", "dwForwardMetric4", "dwForwardMetric5",
    )]


_ROW_DWORDS = len(_MibIpForwardRow._fields_)


def parse_forward_table(buffer: bytes) -> list[dict[str, Any]]:
    """Rows of a MIB_IPFORWARDTABLE (GetIpForwardTable): a DWORD count, then 14-DWORD rows."""
    if len(buffer) < 4:
        return []
    count = int.from_bytes(buffer[:4], "little")
    rows = []
    for i in range(count):
        offset = 4 + i * 4 * _ROW_DWORDS
        if offset + 4 * _ROW_DWORDS > len(buffer):
            break
        d = [int.from_bytes(buffer[offset + 4 * j:offset + 4 * j + 4], "little") for j in range(_ROW_DWORDS)]
        rows.append({"dest": d[0], "mask": d[1], "next_hop": d[3], "interface_index": d[4], "metric": d[9]})
    return rows


def best_default_route(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The 0.0.0.0/0 route with a gateway and the lowest metric (the table's metric already
    includes the interface metric, as `Get-NetRoute` RouteMetric + InterfaceMetric)."""
    candidates = [r for r in rows if r["dest"] == 0 and r["mask"] == 0 and r["next_hop"] != 0]
    if not candidates:
        return None
    best = min(candidates, key=lambda r: r["metric"])
    return {
        "gateway": socket.inet_ntoa(best["next_hop"].to_bytes(4, "little")),
        "interface_index": best["interface_index"],
        "metric": best["metric"],
    }


def _forward_table_default_route(lib: Any) -> dict[str, Any] | None:
    size = ctypes.c_ulong(0)
    lib.GetIpForwardTable(None, ctypes.byref(size), False)      # ERROR_INSUFFICIENT_BUFFER: wanted size
    for _ in range(3):                                           # the table can grow between the calls
        buffer = ctypes.create_string_buffer(size.value + 4096)
        size = ctypes.c_ulong(len(buffer))
        if lib.GetIpForwardTable(buffer, ctypes.byref(size), False) == 0:
            return best_default_route(parse_forward_table(buffer.raw[:size.value]))
    return None


def default_route_native() -> dict[str, Any] | None:
    """Best IPv4 default route via iphlpapi (~0.2 ms, no process).

    Asking GetBestRoute for destination 0.0.0.0 can only match 0.0.0.0/0 routes, and Windows picks
    among them by route metric + interface metric. A full-tunnel VPN that adds 0.0.0.0/1 and
    128.0.0.0/1 instead of replacing 0.0.0.0/0 (WireGuard clients) makes that call match the /1 route,
    so then the route table is read and its best 0.0.0.0/0 route with a gateway is returned - the
    physical one, which is what the router, the DNS servers and the network's identity belong to.
    Returns None when there is no gateway default route or the calls fail, so callers can fall
    back to PowerShell.
    """
    try:
        lib = ctypes.WinDLL("iphlpapi")
        row = _MibIpForwardRow()
        if lib.GetBestRoute(0, 0, ctypes.byref(row)) != 0:
            return None
        if row.dwForwardDest == 0 and row.dwForwardMask == 0 and row.dwForwardNextHop != 0:
            return {
                "gateway": socket.inet_ntoa(row.dwForwardNextHop.to_bytes(4, "little")),
                "interface_index": row.dwForwardIfIndex,
                "metric": row.dwForwardMetric1,
            }
        return _forward_table_default_route(lib)
    except (AttributeError, OSError):
        return None


def internet_route_native(destination: str = "1.1.1.1") -> dict[str, Any] | None:
    """The route Internet traffic actually takes: GetBestRoute for a public address.

    The default route alone misses a full-tunnel VPN that leaves 0.0.0.0/0 untouched and adds
    0.0.0.0/1 + 128.0.0.0/1 (WireGuard clients do): asking for 0.0.0.0 then matches the /1 route
    and is no default route at all. A public address matches whichever route carries it, so its
    interface is the VPN's while the VPN is up. `gateway` is None for an on-link route (a tunnel).
    """
    try:
        lib = ctypes.WinDLL("iphlpapi")
        row = _MibIpForwardRow()
        address = int.from_bytes(socket.inet_aton(destination), "little")
        if lib.GetBestRoute(address, 0, ctypes.byref(row)) != 0:
            return None
    except (AttributeError, OSError):
        return None
    return {
        "gateway": socket.inet_ntoa(row.dwForwardNextHop.to_bytes(4, "little")) if row.dwForwardNextHop else None,
        "interface_index": row.dwForwardIfIndex,
        "metric": row.dwForwardMetric1,
    }


def neighbor_physical_address(address: str) -> str | None:
    """Physical (MAC) address of an IPv4 neighbour (the gateway) via iphlpapi.SendARP; answers from
    the ARP cache when it can, no Admin rights needed. None when it does not answer or the call fails."""
    try:
        lib = ctypes.WinDLL("iphlpapi")
        physical = (ctypes.c_ubyte * 8)()
        length = ctypes.c_ulong(len(physical))
        if lib.SendARP(int.from_bytes(socket.inet_aton(address), "little"), 0, physical, ctypes.byref(length)) != 0:
            return None
    except (AttributeError, OSError):
        return None
    if length.value != 6:
        return None
    return ":".join(f"{b:02x}" for b in physical[:6])


class _SocketAddress(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class _DnsServerAddress(ctypes.Structure):   # IP_ADAPTER_DNS_SERVER_ADDRESS_XP
    pass


_DnsServerAddress._fields_ = [("Length", wintypes.ULONG), ("Reserved", wintypes.DWORD),
                              ("Next", ctypes.POINTER(_DnsServerAddress)), ("Address", _SocketAddress)]


class _AdapterAddresses(ctypes.Structure):   # IP_ADAPTER_ADDRESSES_LH, only the fields read here
    pass


_AdapterAddresses._fields_ = [
    ("Length", wintypes.ULONG), ("IfIndex", wintypes.DWORD), ("Next", ctypes.POINTER(_AdapterAddresses)),
    ("AdapterName", ctypes.c_char_p), ("FirstUnicastAddress", ctypes.c_void_p),
    ("FirstAnycastAddress", ctypes.c_void_p), ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.POINTER(_DnsServerAddress)),
]

_GAA_SKIP = 0x0001 | 0x0002 | 0x0004 | 0x0020   # unicast, anycast, multicast addresses, friendly name
_ERROR_BUFFER_OVERFLOW = 111
_PLACEHOLDER_DNS = {"fec0:0:0:ffff::1", "fec0:0:0:ffff::2", "fec0:0:0:ffff::3"}   # Windows' "none set" IPv6 defaults


def _sockaddr_text(sa: _SocketAddress) -> str | None:
    if not sa.lpSockaddr or sa.iSockaddrLength < 8:
        return None
    raw = ctypes.string_at(sa.lpSockaddr, sa.iSockaddrLength)
    family = int.from_bytes(raw[:2], "little")
    if family == socket.AF_INET:
        return socket.inet_ntop(socket.AF_INET, raw[4:8])
    if family == socket.AF_INET6 and len(raw) >= 24:
        return socket.inet_ntop(socket.AF_INET6, raw[8:24])
    return None


def dns_servers_native() -> dict[int, list[str]] | None:
    """DNS servers per interface index via iphlpapi.GetAdaptersAddresses (no process).
    IPv4 first, Windows' fec0:0:0:ffff:: placeholders left out. None if the call fails."""
    try:
        lib = ctypes.WinDLL("iphlpapi")
    except (AttributeError, OSError):
        return None
    size = wintypes.ULONG(16 * 1024)
    for _ in range(3):   # the list can grow between the size query and the real call
        buf = ctypes.create_string_buffer(size.value)
        rc = lib.GetAdaptersAddresses(0, _GAA_SKIP, None, buf, ctypes.byref(size))
        if rc != _ERROR_BUFFER_OVERFLOW:
            break
    else:
        return None
    if rc != 0:
        return None
    out: dict[int, list[str]] = {}
    node = ctypes.cast(buf, ctypes.POINTER(_AdapterAddresses))
    while node:
        servers: list[str] = []
        dns = node.contents.FirstDnsServerAddress
        while dns:
            text = _sockaddr_text(dns.contents.Address)
            if text and text not in _PLACEHOLDER_DNS and text not in servers:
                servers.append(text)
            dns = dns.contents.Next
        out[int(node.contents.IfIndex)] = sorted(servers, key=lambda s: ":" in s)   # stable: IPv4 first
        node = node.contents.Next
    return out


# Every IPv4 default route with its interface: metric (route + interface), adapter kind, the
# interface's IPv4 address. Interfaces without a NetAdapter (some VPNs) count as virtual.
_PATHS_PS = r"""
$routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
  Where-Object { $_.NextHop -ne '0.0.0.0' }
foreach ($r in $routes) {
  $if = Get-NetIPInterface -InterfaceIndex $r.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
  $ad = Get-NetAdapter -InterfaceIndex $r.ifIndex -ErrorAction SilentlyContinue
  $ip = Get-NetIPAddress -InterfaceIndex $r.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.AddressState -eq 'Preferred' } | Select-Object -First 1
  [pscustomobject]@{
    interface_index = [int]$r.ifIndex; name = [string]$r.InterfaceAlias; gateway = [string]$r.NextHop
    route_metric = [int]$r.RouteMetric; interface_metric = [int]$if.InterfaceMetric
    automatic_metric = ([string]$if.AutomaticMetric -eq 'Enabled'); ipv4 = [string]$ip.IPAddress
    virtual = $(if ($ad) { [bool]$ad.Virtual } else { $true }); media = [string]$ad.PhysicalMediaType
    status = [string]$ad.Status
  }
}
"""


def get_paths() -> list[dict[str, Any]]:
    """Default routes per interface (read-only, one PowerShell call). See app/failover.py."""
    return run_powershell_json(_PATHS_PS, timeout=60)


def get_gateway() -> str | None:
    route = default_route_native()
    if route is not None:
        return route["gateway"]
    # No native answer: either offline (PowerShell will say so too) or the call failed.
    try:
        uplink = get_uplink()
    except PowerShellError:
        return None
    return uplink["gateway"] if uplink else None


# --- visible Wi-Fi networks (netsh wlan show networks mode=bssid) -----------------

@dataclass(frozen=True)
class ScanEntry:
    ssid: str            # "" for hidden networks
    bssid: str
    signal: int | None   # percent
    radio_type: str
    band: str
    channel: int | None


_SSID_RE = re.compile(r"^SSID\s+\d+\s*:\s*(.*)$")
_BSSID_RE = re.compile(r"^\s+BSSID\s+\d+\s*:\s*(.*)$")


def parse_netsh_networks(text: str) -> list[ScanEntry]:
    """One entry per BSSID. Older Windows builds omit "Band"; it is then derived from the channel."""
    entries: list[ScanEntry] = []
    ssid, cur = "", None

    def flush() -> None:
        if cur is None:
            return
        channel = _to_int(cur.get("Channel"))
        band = cur.get("Band") or ("" if channel is None else "2.4 GHz" if channel <= 14 else "5 GHz")
        entries.append(ScanEntry(ssid, cur["bssid"], _to_int(cur.get("Signal")),
                                 cur.get("Radio type", ""), band, channel))

    for line in text.splitlines():
        m = _SSID_RE.match(line)
        if m:
            flush()
            ssid, cur = m.group(1).strip(), None
            continue
        m = _BSSID_RE.match(line)
        if m:
            flush()
            cur = {"bssid": m.group(1).strip().lower()}
            continue
        if cur is not None:
            kv = _KV_RE.match(line)
            if kv:
                cur.setdefault(kv.group(1).strip(), kv.group(2).strip())
    flush()
    return entries


def get_scan() -> list[ScanEntry]:
    """Networks in Windows' last scan results. Does not trigger a new scan."""
    return parse_netsh_networks(run_netsh(["wlan", "show", "networks", "mode=bssid"]))


# --- driver store (pnputil) --------------------------------------------------------

@dataclass(frozen=True)
class StoredDriver:
    published: str   # oem18.inf
    original: str    # mtkwl6ex.inf
    provider: str
    version: str
    date: str        # yyyy-MM-dd


def parse_pnputil_drivers(text: str) -> list[StoredDriver]:
    drivers: list[StoredDriver] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        fields = {}
        for line in block.splitlines():
            kv = _KV_RE.match(line)
            if kv:
                fields.setdefault(kv.group(1).strip(), kv.group(2).strip())
        if "Published Name" not in fields:
            continue
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})\s+(\S+)", fields.get("Driver Version", ""))
        date, version = (f"{m.group(3)}-{m.group(1)}-{m.group(2)}", m.group(4)) if m else ("", "")
        drivers.append(StoredDriver(fields["Published Name"], fields.get("Original Name", ""),
                                    fields.get("Provider Name", ""), version, date))
    return drivers


def get_driver_store(driver_class: str = "Net") -> list[StoredDriver]:
    """Driver packages of one device class in the driver store (read-only)."""
    try:
        proc = subprocess.run(["pnputil", "/enum-drivers", "/class", driver_class], capture_output=True,
                              timeout=30, creationflags=CREATE_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return []
    return parse_pnputil_drivers(proc.stdout.decode(_oem_codepage(), errors="replace"))


# --- DNS servers and event log ----------------------------------------------------

def get_dns_servers(interface_index: int) -> list[str]:
    """DNS servers configured on one interface (IPv4 first). The index is cast to int:
    it is the only value interpolated into the script."""
    idx = int(interface_index)
    rows = run_powershell_json(
        f"Get-DnsClientServerAddress -InterfaceIndex {idx} -ErrorAction SilentlyContinue | "
        "Sort-Object AddressFamily | ForEach-Object { $_.ServerAddresses }"
    )
    return [str(r) for r in rows]


# One PowerShell process for every event-log query: process start-up dominates the cost.
# "No events found" is a normal empty result; any other failure is reported in `errors`
# so an unreadable log is never mistaken for a clean one.
_DIAG_EVENTS_PS = r"""
$since = (Get-Date).AddDays(-{days})
$errors = New-Object System.Collections.ArrayList
function Get-Ev($label, $filter) {
    try { return @(Get-WinEvent -FilterHashtable $filter -ErrorAction Stop) }
    catch {
        if ($_.FullyQualifiedErrorId -notmatch 'NoMatchingEventsFound') { [void]$errors.Add("${label}: $($_.Exception.Message)") }
        return @()
    }
}
function To-Ts($e) { [DateTimeOffset]::new($e.TimeCreated).ToUnixTimeSeconds() }
$disc = Get-Ev 'disconnects' @{ LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'; Id=8003; StartTime=$since }
$lim  = Get-Ev 'limited_connectivity' @{ LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'; Id=4003; StartTime=$since }
$ihv  = Get-Ev 'ihv_stops' @{ LogName='System'; ProviderName='Microsoft-Windows-WLAN-AutoConfig'; Id=10002; StartTime=$since }
$port = Get-Ev 'port_exhaustion' @{ LogName='System'; ProviderName='Tcpip'; Id=4227; StartTime=$since }
$tw = $null
try { $tw = @(Get-NetTCPConnection -State TimeWait -ErrorAction Stop).Count } catch { [void]$errors.Add("time_wait: $($_.Exception.Message)") }
[pscustomobject]@{
    disconnects = @($disc | ForEach-Object { [pscustomobject]@{ ts = (To-Ts $_); reason = $(if ($_.Message -match 'Reason:\s*(.+)') { $matches[1].Trim() } else { '' }) } })
    limited_connectivity = @($lim | ForEach-Object { To-Ts $_ })
    ihv_stops = @($ihv | ForEach-Object { To-Ts $_ })
    port_exhaustion = @($port | ForEach-Object { To-Ts $_ })
    time_wait = $tw
    errors = @($errors)
}
"""


def get_diag_events(days: int = 7) -> dict[str, Any]:
    """Event-log facts used by the diagnostics. Keys: disconnects [{ts, reason}],
    limited_connectivity / ihv_stops / port_exhaustion [ts], time_wait (int|None), errors [str]."""
    rows = run_powershell_json(_DIAG_EVENTS_PS.replace("{days}", str(int(days))), timeout=90, depth=4)
    return rows[0] if rows else {}
