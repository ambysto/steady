"""System access for tweaks - the only module that writes to the machine.

Writes covered: adapter advanced properties, HKLM DWORD values, powercfg indices of the
active plan, adapter protocol bindings, TCP global parameters (netsh), receive segment
coalescing, global offload settings, the DNS servers of the uplink and the DoH entries
Windows keeps for them, the tool's own QoS throttle policy. Every write raises SystemWriteError on failure;
callers (app.tweaks) handle backup, verification and rollback.

String values reach PowerShell only as base64 (see ps_literal): PowerShell also treats
the typographic quotes U+2018..U+201B as single quotes, so doubling ' is not enough.
"""
from __future__ import annotations

import base64
import ipaddress
import re
import subprocess
import winreg
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .winutil import (CREATE_NO_WINDOW, VPN_RE, PowerShellError, _oem_codepage, default_route_native,
                      get_adapters, get_scan, get_uplink, get_wifi_states, is_wifi_adapter, run_powershell,
                      run_powershell_json)

NET_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_POWER_RE = re.compile(r"Current (AC|DC) Power Setting Index:\s*0x([0-9a-fA-F]+)")
_NETSH_LINE_RE = re.compile(r"^\s*(.+?)\s*:\s*(.*?)\s*$")
_DOH_TEMPLATE_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?$")

# `netsh int tcp set global <key>=<value>` parameters a tweak may change -> the label of `show global`.
TCP_GLOBAL_SETTINGS = {"ecncapability": "ECN Capability"}
TCP_GLOBAL_VALUES = ("enabled", "disabled", "default")
# Set-NetOffloadGlobalSetting parameters a tweak may change.
OFFLOAD_SETTINGS = ("PacketCoalescingFilter",)
OFFLOAD_VALUES = ("Default", "Enabled", "Disabled")
_QOS_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
QOS_MIN_BPS, QOS_MAX_BPS = 1_000_000, 1_000_000_000


class SystemReadError(RuntimeError):
    pass


class SystemWriteError(RuntimeError):
    pass


def ps_literal(value: str) -> str:
    """A PowerShell expression that evaluates to `value`, with no quoting pitfalls."""
    b64 = base64.b64encode(str(value).encode("utf-8")).decode("ascii")
    return f"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')))"


def _check_guid(value: str) -> str:
    if not _GUID_RE.match(value):
        raise ValueError(f"not a GUID: {value!r}")
    return value


def _check_qos_name(name: str) -> str:
    if not _QOS_NAME_RE.match(name):
        raise ValueError(f"bad QoS policy name: {name!r}")
    return name


def _check_hklm_path(path: str) -> str:
    if not path or path.startswith("\\") or ".." in path.split("\\"):
        raise ValueError(f"bad HKLM sub-path: {path!r}")
    return path


@dataclass(frozen=True)
class WifiBands:
    """What an adapter's Wi-Fi connection looks like, for tweaks that depend on the band."""
    ssid: str
    current_band: str          # band of the access point the card is on now; "" when it cannot be told
    bands: frozenset[str]      # bands of this network's access points in Windows' last scan


class System(Protocol):
    """What tweaks may do. Reads have no side effects."""

    def wifi_adapter_name(self) -> str | None: ...
    def adapter_properties(self, adapter: str) -> list[dict[str, Any]]: ...
    def adapter_class_key(self, adapter: str) -> str | None: ...
    def registry_get(self, path: str, name: str) -> int | None: ...
    def power_get(self, subgroup: str, setting: str) -> tuple[int, int]: ...
    def binding_get(self, adapter: str, component: str) -> bool | None: ...
    def wifi_ssid_bands(self, adapter: str) -> WifiBands | None: ...
    def tcp_global_get(self, setting: str) -> str | None: ...
    def rsc_get(self, adapter: str) -> dict[str, Any] | None: ...
    def offload_global_get(self, setting: str) -> str | None: ...
    def dns_interface(self) -> dict[str, Any] | None: ...
    def doh_get(self) -> dict[str, dict[str, Any]]: ...

    def qos_policy_get(self, name: str) -> int | None: ...

    def adapter_property_set(self, adapter: str, keyword: str, value: str) -> None: ...
    def adapter_property_reset(self, adapter: str, keyword: str) -> None: ...
    def registry_set_dword(self, path: str, name: str, value: int) -> None: ...
    def registry_delete(self, path: str, name: str) -> None: ...
    def power_set(self, subgroup: str, setting: str, ac: int, dc: int) -> None: ...
    def interface_metric_set(self, interface_index: int, metric: int | None) -> None: ...
    def binding_set(self, adapter: str, component: str, enabled: bool) -> None: ...
    def tcp_global_set(self, setting: str, value: str) -> None: ...
    def rsc_set(self, adapter: str, ipv4: bool | None, ipv6: bool | None) -> None: ...
    def offload_global_set(self, setting: str, value: str) -> None: ...
    def dns_servers_set(self, interface_index: int, servers: list[str] | None) -> None: ...
    def doh_set(self, address: str, template: str, auto_upgrade: bool, fallback_to_udp: bool) -> None: ...
    def doh_remove(self, address: str) -> None: ...

    def qos_policy_set(self, name: str, bits_per_second: int) -> None: ...
    def qos_policy_remove(self, name: str) -> None: ...


def _as_list(value: Any) -> list[str]:
    # PowerShell 5.1 serialises an array produced by a calculated property as
    # {"value": [...], "Count": n} instead of a plain JSON array.
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _check_address(address: str) -> str:
    try:
        ipaddress.ip_address(address)
    except ValueError:
        raise ValueError(f"not an IP address: {address!r}") from None
    return address


def _ps_bool(value: bool) -> str:
    return "$true" if value else "$false"


def _uplink_route() -> dict[str, Any] | None:
    return default_route_native() or get_uplink()


def parse_netsh_table(text: str) -> dict[str, str]:
    """`label : value` lines of a netsh `show` command (labels as printed, values lower-cased)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _NETSH_LINE_RE.match(line)
        if m and m.group(2):
            out[m.group(1)] = m.group(2).lower()
    return out


class WindowsSystem:
    def __init__(self, *, ps: Callable[..., str] = run_powershell, ps_json: Callable[..., Any] = run_powershell_json,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 route: Callable[[], dict[str, Any] | None] = _uplink_route,
                 wifi_states: Callable[[], Any] = get_wifi_states, scan: Callable[[], Any] = get_scan) -> None:
        self._ps, self._ps_json, self._run, self._route = ps, ps_json, run, route
        self._wifi_states, self._scan = wifi_states, scan

    # -- reads --------------------------------------------------------------------------

    def wifi_adapter_name(self) -> str | None:
        rows = self._ps_json("Get-NetAdapter -Physical -ErrorAction SilentlyContinue | "
                             "Select-Object Name, Status, PhysicalMediaType, Virtual")
        wifi = [r for r in rows if is_wifi_adapter(r) and not r.get("Virtual")]
        wifi.sort(key=lambda r: r.get("Status") != "Up")
        return wifi[0]["Name"] if wifi else None

    def adapter_properties(self, adapter: str) -> list[dict[str, Any]]:
        try:
            rows = self._ps_json(
                f"Get-NetAdapterAdvancedProperty -Name {ps_literal(adapter)} -ErrorAction Stop | Select-Object "
                "DisplayName, DisplayValue, RegistryKeyword, "
                "@{n='RegistryValue';e={@($_.RegistryValue)}}, "
                "@{n='ValidDisplayValues';e={@($_.ValidDisplayValues)}}, "
                "@{n='ValidRegistryValues';e={@($_.ValidRegistryValues)}}, "
                "@{n='DefaultRegistryValue';e={$_.DefaultRegistryValue}}")
        except PowerShellError as exc:
            raise SystemReadError(f"cannot read advanced properties of {adapter!r}: {exc}") from exc
        out = []
        for r in rows:
            out.append({"DisplayName": r.get("DisplayName") or "", "DisplayValue": r.get("DisplayValue") or "",
                        "RegistryKeyword": r.get("RegistryKeyword") or "",
                        "RegistryValue": _as_list(r.get("RegistryValue")),
                        "ValidDisplayValues": _as_list(r.get("ValidDisplayValues")),
                        "ValidRegistryValues": _as_list(r.get("ValidRegistryValues")),
                        "DefaultRegistryValue": r.get("DefaultRegistryValue")})
        return out

    def adapter_class_key(self, adapter: str) -> str | None:
        """HKLM sub-path of the adapter's device class key (where PnPCapabilities lives)."""
        try:
            guid = self._ps(f"(Get-NetAdapter -Name {ps_literal(adapter)} -ErrorAction Stop).InterfaceGuid").strip()
        except PowerShellError as exc:
            raise SystemReadError(f"cannot find adapter {adapter!r}: {exc}") from exc
        if not guid:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NET_CLASS_KEY, 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as cls:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(cls, i)
                    except OSError:
                        return None
                    i += 1
                    if not re.fullmatch(r"\d{4}", sub):
                        continue
                    try:
                        with winreg.OpenKey(cls, sub) as k:
                            inst, _ = winreg.QueryValueEx(k, "NetCfgInstanceId")
                    except OSError:
                        continue
                    if str(inst).lower() == guid.lower():
                        return f"{NET_CLASS_KEY}\\{sub}"
        except OSError as exc:
            raise SystemReadError(f"cannot read the network class key: {exc}") from exc

    def registry_get(self, path: str, name: str) -> int | None:
        """HKLM DWORD value; None when the value (or key) does not exist."""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _check_hklm_path(path), 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                value, kind = winreg.QueryValueEx(k, name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SystemReadError(f"cannot read HKLM\\{path}\\{name}: {exc}") from exc
        if kind != winreg.REG_DWORD:
            raise SystemReadError(f"HKLM\\{path}\\{name} is not a DWORD (type {kind})")
        return int(value)

    def _powercfg(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["powercfg", *args], capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)

    def power_get(self, subgroup: str, setting: str) -> tuple[int, int]:
        proc = self._powercfg("/q", "SCHEME_CURRENT", _check_guid(subgroup), _check_guid(setting))
        text = proc.stdout.decode(_oem_codepage(), errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
        found = {m.group(1): int(m.group(2), 16) for m in _POWER_RE.finditer(text or "")}
        if proc.returncode != 0 or set(found) != {"AC", "DC"}:
            raise SystemReadError(f"cannot read powercfg {subgroup}/{setting}")
        return found["AC"], found["DC"]

    def binding_get(self, adapter: str, component: str) -> bool | None:
        if not _COMPONENT_RE.match(component):
            raise ValueError(f"bad component id: {component!r}")
        rows = self._ps_json(f"Get-NetAdapterBinding -Name {ps_literal(adapter)} -ComponentID {component} "
                             "-ErrorAction SilentlyContinue | Select-Object Enabled")
        return bool(rows[0]["Enabled"]) if rows else None

    def wifi_ssid_bands(self, adapter: str) -> WifiBands | None:
        """The connection of `adapter` (the netsh interface with that name): SSID, the band it is on
        now and the bands of the same network in Windows' last scan. None when that interface is
        not connected. When netsh does not list the interface, or does not say which band, the
        fields come back empty (unknown), never as "not connected". Never scans."""
        state = next((st for st in self._wifi_states() if st.interface.lower() == adapter.lower()), None)
        if state is None:
            return WifiBands("", "", frozenset())
        if not state.connected:
            return None
        bssid = state.bssid.lower()
        # A hidden network is not listed under its name, so the connected BSSID counts too.
        same = [e for e in self._scan()
                if e.band and ((state.ssid and e.ssid == state.ssid) or (bssid and e.bssid.lower() == bssid))]
        current = state.band or next((e.band for e in same if bssid and e.bssid.lower() == bssid), "")
        return WifiBands(state.ssid, current, frozenset(e.band for e in same))

    def qos_policy_get(self, name: str) -> int | None:
        """Throttle rate (bit/s) of the QoS policy `name` in the default (persistent) store; None if
        there is none. Lists the store rather than asking by name, so "absent" never hides an error."""
        rows = self._ps_json("@(Get-NetQosPolicy -ErrorAction Stop | Where-Object { $_.Name -eq "
                             f"{ps_literal(_check_qos_name(name))} }}) | Select-Object Name, ThrottleRateAction")
        if not rows:
            return None
        return int(rows[0].get("ThrottleRateAction") or 0)

    def interface_metric_get(self, interface_index: int) -> dict[str, Any]:
        """{"automatic": bool, "metric": int} of an interface's IPv4 settings."""
        idx = int(interface_index)
        rows = self._ps_json(f"Get-NetIPInterface -InterfaceIndex {idx} -AddressFamily IPv4 -ErrorAction Stop | "
                             "Select-Object InterfaceMetric, @{n='Automatic';e={[string]$_.AutomaticMetric -eq 'Enabled'}}")
        if not rows:
            raise SystemReadError(f"no IPv4 interface {idx}")
        return {"automatic": bool(rows[0]["Automatic"]), "metric": int(rows[0]["InterfaceMetric"])}

    def _netsh(self, *args: str) -> tuple[int, str]:
        proc = self._run(["netsh", *args], capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        out = proc.stdout.decode(_oem_codepage(), errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
        return proc.returncode, out or ""

    def tcp_global_get(self, setting: str) -> str | None:
        """Lower-case value of a `netsh int tcp show global` parameter ("enabled"...); None when not listed."""
        if setting not in TCP_GLOBAL_SETTINGS:
            raise ValueError(f"unknown TCP global setting: {setting!r}")
        code, out = self._netsh("int", "tcp", "show", "global")
        if code != 0:
            raise SystemReadError(f"netsh int tcp show global exited {code}")
        return parse_netsh_table(out).get(TCP_GLOBAL_SETTINGS[setting])

    def rsc_get(self, adapter: str) -> dict[str, Any] | None:
        """Receive Segment Coalescing of an adapter: {"ipv4", "ipv6"} enabled flags and {"ipv4_supported",
        "ipv6_supported"} from the hardware capabilities. None when Windows has no RSC data for it."""
        try:
            rows = self._ps_json(
                f"Get-NetAdapterRsc -Name {ps_literal(adapter)} -ErrorAction Stop | Select-Object "
                "@{n='IPv4';e={[bool]$_.IPv4Enabled}}, @{n='IPv6';e={[bool]$_.IPv6Enabled}}, "
                "@{n='IPv4Supported';e={[bool]$_.RscHardwareCapabilities.IPv4Supported}}, "
                "@{n='IPv6Supported';e={[bool]$_.RscHardwareCapabilities.IPv6Supported}}")
        except PowerShellError as exc:
            if re.search(r"No MSFT_NetAdapterRscSettingData|not supported", str(exc), re.I):
                return None
            raise SystemReadError(f"cannot read RSC of {adapter!r}: {exc}") from exc
        if not rows:
            return None
        r = rows[0]
        return {"ipv4": bool(r["IPv4"]), "ipv6": bool(r["IPv6"]),
                "ipv4_supported": bool(r["IPv4Supported"]), "ipv6_supported": bool(r["IPv6Supported"])}

    def offload_global_get(self, setting: str) -> str | None:
        if setting not in OFFLOAD_SETTINGS:
            raise ValueError(f"unknown offload setting: {setting!r}")
        try:
            value = self._ps(f"(Get-NetOffloadGlobalSetting -ErrorAction Stop).{setting}").strip()
        except PowerShellError as exc:
            raise SystemReadError(f"cannot read {setting}: {exc}") from exc
        return value or None

    def dns_interface(self) -> dict[str, Any] | None:
        """The uplink's IPv4 DNS configuration: {"index", "alias", "servers", "static", "static_v6", "suffix",
        "domain_joined", "vpn_up"}. `static` is a DNS server typed in by hand (the interface's NameServer
        value), as opposed to one from DHCP. None when there is no uplink."""
        route = self._route()
        if not route:
            return None
        idx = int(route["interface_index"])
        try:
            rows = self._ps_json(
                f"$i = {idx}; $a = Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop; $g = $a.InterfaceGuid; "
                "$v4 = Get-ItemProperty \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\$g\" "
                "-ErrorAction SilentlyContinue; "
                "$v6 = Get-ItemProperty \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters\\Interfaces\\$g\" "
                "-ErrorAction SilentlyContinue; "
                "[pscustomobject]@{ Alias = $a.Name; StaticV4 = [bool]$v4.NameServer; StaticV6 = [bool]$v6.NameServer; "
                "Servers = @(Get-DnsClientServerAddress -InterfaceIndex $i -AddressFamily IPv4 "
                "-ErrorAction SilentlyContinue | ForEach-Object { $_.ServerAddresses }); "
                "Suffix = [string](Get-DnsClient -InterfaceIndex $i -ErrorAction SilentlyContinue).ConnectionSpecificSuffix; "
                "Domain = [bool](Get-CimInstance Win32_ComputerSystem).PartOfDomain; "
                "Up = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' } | "
                "ForEach-Object { \"$($_.Name) $($_.InterfaceDescription)\" }) }")
        except PowerShellError as exc:
            raise SystemReadError(f"cannot read the DNS configuration of interface {idx}: {exc}") from exc
        if not rows:
            return None
        r = rows[0]
        return {"index": idx, "alias": r.get("Alias") or "", "servers": _as_list(r.get("Servers")),
                "static": bool(r.get("StaticV4")), "static_v6": bool(r.get("StaticV6")),
                "suffix": (r.get("Suffix") or "").strip(), "domain_joined": bool(r.get("Domain")),
                "vpn_up": any(VPN_RE.search(name) for name in _as_list(r.get("Up")))}

    def doh_get(self) -> dict[str, dict[str, Any]]:
        """DoH entries Windows knows: address -> {"template", "auto_upgrade", "fallback_to_udp"}."""
        try:
            rows = self._ps_json(
                "Get-DnsClientDohServerAddress -ErrorAction Stop | Select-Object ServerAddress, DohTemplate, "
                "@{n='AutoUpgrade';e={[bool]$_.AutoUpgrade}}, @{n='Fallback';e={[bool]$_.AllowFallbackToUdp}}")
        except PowerShellError as exc:
            raise SystemReadError(f"cannot read the DoH server list: {exc}") from exc
        return {str(r["ServerAddress"]): {"template": r.get("DohTemplate") or "",
                                          "auto_upgrade": bool(r.get("AutoUpgrade")),
                                          "fallback_to_udp": bool(r.get("Fallback"))} for r in rows}

    # -- writes -------------------------------------------------------------------------

    def _ps_write(self, script: str, what: str) -> None:
        try:
            self._ps(script)
        except PowerShellError as exc:
            raise SystemWriteError(f"{what} failed: {exc}") from exc

    def interface_metric_set(self, interface_index: int, metric: int | None) -> None:
        """metric=None: back to Windows' automatic metric (ADR-0008)."""
        idx = int(interface_index)
        if metric is None:
            how = "-AutomaticMetric Enabled"
        else:
            if not 1 <= int(metric) <= 9999:
                raise ValueError(f"interface metric out of range: {metric}")
            how = f"-InterfaceMetric {int(metric)}"
        self._ps_write(f"Set-NetIPInterface -InterfaceIndex {idx} -AddressFamily IPv4 {how} -ErrorAction Stop",
                       f"setting the metric of interface {idx}")

    def adapter_property_set(self, adapter: str, keyword: str, value: str) -> None:
        self._ps_write(f"Set-NetAdapterAdvancedProperty -Name {ps_literal(adapter)} "
                       f"-RegistryKeyword {ps_literal(keyword)} -RegistryValue {ps_literal(value)} -ErrorAction Stop",
                       f"setting {keyword}={value} on {adapter}")

    def adapter_property_reset(self, adapter: str, keyword: str) -> None:
        self._ps_write(f"Reset-NetAdapterAdvancedProperty -Name {ps_literal(adapter)} "
                       f"-RegistryKeyword {ps_literal(keyword)} -ErrorAction Stop",
                       f"resetting {keyword} on {adapter}")

    def registry_set_dword(self, path: str, name: str, value: int) -> None:
        if not 0 <= int(value) <= 0xFFFFFFFF:
            raise ValueError(f"DWORD out of range: {value}")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _check_hklm_path(path), 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))
        except OSError as exc:
            raise SystemWriteError(f"writing HKLM\\{path}\\{name}={value} failed: {exc}") from exc

    def registry_delete(self, path: str, name: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _check_hklm_path(path), 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            return  # already absent: the goal state
        except OSError as exc:
            raise SystemWriteError(f"deleting HKLM\\{path}\\{name} failed: {exc}") from exc

    def power_set(self, subgroup: str, setting: str, ac: int, dc: int) -> None:
        sub, sett = _check_guid(subgroup), _check_guid(setting)
        for args in (("/setacvalueindex", "SCHEME_CURRENT", sub, sett, str(int(ac))),
                     ("/setdcvalueindex", "SCHEME_CURRENT", sub, sett, str(int(dc))),
                     ("/setactive", "SCHEME_CURRENT")):
            proc = self._powercfg(*args)
            if proc.returncode != 0:
                raise SystemWriteError(f"powercfg {' '.join(args)} exited {proc.returncode}")

    def qos_policy_set(self, name: str, bits_per_second: int) -> None:
        """Create (or re-rate) a policy throttling all outbound traffic, in the persistent store."""
        rate = int(bits_per_second)
        if not QOS_MIN_BPS <= rate <= QOS_MAX_BPS:
            raise ValueError(f"throttle rate out of range: {rate}")
        lit = ps_literal(_check_qos_name(name))
        self._ps_write(f"$name = {lit}; "
                       "if (@(Get-NetQosPolicy -ErrorAction Stop | Where-Object { $_.Name -eq $name }).Count) { "
                       f"Set-NetQosPolicy -Name $name -ThrottleRateActionBitsPerSecond {rate} -Confirm:$false -ErrorAction Stop }} "
                       f"else {{ New-NetQosPolicy -Name $name -Default -ThrottleRateActionBitsPerSecond {rate} "
                       "-ErrorAction Stop | Out-Null }", f"setting QoS policy {name} to {rate} bit/s")

    def qos_policy_remove(self, name: str) -> None:
        """Remove exactly the policy `name`: persistent store first, then the active store if it lingers."""
        lit = ps_literal(_check_qos_name(name))
        self._ps_write(f"$name = {lit}; "
                       "if (@(Get-NetQosPolicy -ErrorAction Stop | Where-Object { $_.Name -eq $name }).Count) { "
                       "Remove-NetQosPolicy -Name $name -Confirm:$false -ErrorAction Stop }; "
                       "if (@(Get-NetQosPolicy -PolicyStore ActiveStore -ErrorAction Stop | "
                       "Where-Object { $_.Name -eq $name }).Count) { "
                       "Remove-NetQosPolicy -Name $name -PolicyStore ActiveStore -Confirm:$false -ErrorAction Stop }",
                       f"removing QoS policy {name}")

    def binding_set(self, adapter: str, component: str, enabled: bool) -> None:
        if not _COMPONENT_RE.match(component):
            raise ValueError(f"bad component id: {component!r}")
        verb = "Enable" if enabled else "Disable"
        self._ps_write(f"{verb}-NetAdapterBinding -Name {ps_literal(adapter)} -ComponentID {component} "
                       "-ErrorAction Stop", f"{verb.lower()} {component} on {adapter}")

    def tcp_global_set(self, setting: str, value: str) -> None:
        if setting not in TCP_GLOBAL_SETTINGS:
            raise ValueError(f"unknown TCP global setting: {setting!r}")
        if value not in TCP_GLOBAL_VALUES:
            raise ValueError(f"bad TCP global value: {value!r}")
        code, out = self._netsh("int", "tcp", "set", "global", f"{setting}={value}")
        if code != 0:
            raise SystemWriteError(f"netsh int tcp set global {setting}={value} exited {code}: {out.strip()[:200]}")

    def rsc_set(self, adapter: str, ipv4: bool | None, ipv6: bool | None) -> None:
        """Turn RSC on/off per address family; None leaves a family alone."""
        for flag, switch in ((ipv4, "-IPv4"), (ipv6, "-IPv6")):
            if flag is None:
                continue
            verb = "Enable" if flag else "Disable"
            self._ps_write(f"{verb}-NetAdapterRsc -Name {ps_literal(adapter)} {switch} -ErrorAction Stop",
                           f"{verb.lower()} RSC {switch} on {adapter}")

    def offload_global_set(self, setting: str, value: str) -> None:
        if setting not in OFFLOAD_SETTINGS:
            raise ValueError(f"unknown offload setting: {setting!r}")
        if value not in OFFLOAD_VALUES:
            raise ValueError(f"bad offload value: {value!r}")
        self._ps_write(f"Set-NetOffloadGlobalSetting -{setting} {value} -ErrorAction Stop", f"setting {setting}={value}")

    def dns_servers_set(self, interface_index: int, servers: list[str] | None) -> None:
        """IPv4 DNS servers of an interface. None: back to DHCP's (-ResetServerAddresses, both families)."""
        idx = int(interface_index)
        if servers is None:
            how = "-ResetServerAddresses"
        else:
            if not servers:
                raise ValueError("no DNS servers given")
            how = "-ServerAddresses " + ",".join(f"'{_check_address(s)}'" for s in servers)
        self._ps_write(f"Set-DnsClientServerAddress -InterfaceIndex {idx} {how} -ErrorAction Stop",
                       f"setting the DNS servers of interface {idx}")

    def doh_set(self, address: str, template: str, auto_upgrade: bool, fallback_to_udp: bool) -> None:
        """Make Windows use DoH for `address` (adds the entry with `template` if it is missing)."""
        addr = _check_address(address)
        if not _DOH_TEMPLATE_RE.match(template):
            raise ValueError(f"bad DoH template: {template!r}")
        flags = f"-AutoUpgrade {_ps_bool(auto_upgrade)} -AllowFallbackToUdp {_ps_bool(fallback_to_udp)}"
        self._ps_write(
            f"$e = Get-DnsClientDohServerAddress -ServerAddress '{addr}' -ErrorAction SilentlyContinue; "
            f"if ($e) {{ Set-DnsClientDohServerAddress -ServerAddress '{addr}' {flags} -ErrorAction Stop }} "
            f"else {{ Add-DnsClientDohServerAddress -ServerAddress '{addr}' -DohTemplate '{template}' {flags} "
            "-ErrorAction Stop }", f"setting DoH for {addr}")

    def doh_remove(self, address: str) -> None:
        addr = _check_address(address)
        self._ps_write(f"Get-DnsClientDohServerAddress -ServerAddress '{addr}' -ErrorAction SilentlyContinue | "
                       "Remove-DnsClientDohServerAddress -ErrorAction Stop", f"removing the DoH entry of {addr}")
