"""System access for tweaks - the only module that writes to the machine.

Writes covered: adapter advanced properties, HKLM DWORD values, powercfg indices of the
active plan, adapter protocol bindings, the app's own QoS upload policy, an interface's IPv4
MTU (ADR-0015), the global TCP ECN capability, an adapter's RSC, the global packet coalescing filter, an
interface's IPv4 DNS servers and the DNS-over-HTTPS flags of a resolver. Every write raises SystemWriteError on failure;
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
from typing import Any, Callable, Protocol

from .winutil import (CREATE_NO_WINDOW, PowerShellError, _oem_codepage, is_wifi_adapter,
                      run_powershell, run_powershell_json)

NET_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_POWER_RE = re.compile(r"Current (AC|DC) Power Setting Index:\s*0x([0-9a-fA-F]+)")
_QOS_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")
MTU_MIN, MTU_MAX = 576, 9216      # sanity only (restoring may need a jumbo-frame original); tweaks set 1280-1500
UPLOAD_LIMIT_MIN, UPLOAD_LIMIT_MAX = 2_000_000, 1_000_000_000   # bits per second
ECN_VALUES = ("enabled", "disabled", "default")                 # netsh int tcp ... ecncapability=
PACKET_COALESCING_VALUES = ("Enabled", "Disabled", "Default")   # Set-NetOffloadGlobalSetting -PacketCoalescingFilter
_ECN_RE = re.compile(r"^\s*ECN Capability\s*:\s*(\S+)", re.I | re.M)
TCPIP_INTERFACES_KEY = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"


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


def _check_hklm_path(path: str) -> str:
    if not path or path.startswith("\\") or ".." in path.split("\\"):
        raise ValueError(f"bad HKLM sub-path: {path!r}")
    return path


class System(Protocol):
    """What tweaks may do. Reads have no side effects."""

    def wifi_adapter_name(self) -> str | None: ...
    def adapter_properties(self, adapter: str) -> list[dict[str, Any]]: ...
    def adapter_class_key(self, adapter: str) -> str | None: ...
    def registry_get(self, path: str, name: str) -> int | None: ...
    def power_get(self, subgroup: str, setting: str) -> tuple[int, int]: ...
    def binding_get(self, adapter: str, component: str) -> bool | None: ...
    def current_network(self) -> dict[str, Any] | None: ...
    def qos_throttle_get(self, name: str) -> int | None: ...
    def interface_mtu_get(self, interface_index: int) -> int: ...
    def tcp_ecn_get(self) -> str: ...
    def rsc_get(self, adapter: str) -> dict[str, bool] | None: ...
    def packet_coalescing_get(self) -> str: ...
    def dns_get(self, interface_index: int) -> dict[str, Any]: ...
    def dns_suffix_get(self, interface_index: int) -> str: ...
    def doh_get(self, server: str) -> dict[str, bool] | None: ...

    def adapter_property_set(self, adapter: str, keyword: str, value: str) -> None: ...
    def adapter_property_reset(self, adapter: str, keyword: str) -> None: ...
    def registry_set_dword(self, path: str, name: str, value: int) -> None: ...
    def registry_delete(self, path: str, name: str) -> None: ...
    def power_set(self, subgroup: str, setting: str, ac: int, dc: int) -> None: ...
    def interface_metric_set(self, interface_index: int, metric: int | None) -> None: ...
    def binding_set(self, adapter: str, component: str, enabled: bool) -> None: ...
    def qos_throttle_set(self, name: str, bits_per_second: int) -> None: ...
    def qos_policy_remove(self, name: str) -> None: ...
    def interface_mtu_set(self, interface_index: int, mtu: int) -> None: ...
    def tcp_ecn_set(self, value: str) -> None: ...
    def rsc_set(self, adapter: str, ipv4: bool | None, ipv6: bool | None) -> None: ...
    def packet_coalescing_set(self, value: str) -> None: ...
    def dns_servers_set(self, interface_index: int, servers: list[str]) -> None: ...
    def dns_servers_reset(self, interface_index: int) -> None: ...
    def doh_set(self, server: str, auto_upgrade: bool, fallback: bool) -> None: ...


def _check_qos_name(name: str) -> str:
    if not _QOS_NAME_RE.match(name):
        raise ValueError(f"bad QoS policy name: {name!r}")
    return name


def _check_mtu(mtu: int) -> int:
    if not MTU_MIN <= int(mtu) <= MTU_MAX:
        raise ValueError(f"MTU out of range: {mtu}")
    return int(mtu)


def _check_choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise ValueError(f"bad {what}: {value!r}")
    return value


def _check_ipv4(value: str) -> str:
    """A canonical IPv4 address, or ValueError: the only form of DNS server that reaches PowerShell."""
    return str(ipaddress.IPv4Address(str(value).strip()))


def _ipv4_list(values: Any) -> list[str]:
    out = []
    for v in values:
        try:
            out.append(_check_ipv4(v))
        except ValueError:
            continue    # IPv6 and anything else: only IPv4 DNS is handled here
    return out


def _ps_bool(value: bool) -> str:
    if not isinstance(value, bool):
        raise ValueError(f"not a bool: {value!r}")
    return "$true" if value else "$false"


def _as_list(value: Any) -> list[str]:
    # PowerShell 5.1 serialises an array produced by a calculated property as
    # {"value": [...], "Count": n} instead of a plain JSON array.
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


class WindowsSystem:
    def __init__(self, *, ps: Callable[..., str] = run_powershell, ps_json: Callable[..., Any] = run_powershell_json,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self._ps, self._ps_json, self._run = ps, ps_json, run

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

    def current_network(self) -> dict[str, Any] | None:
        """The network the default route uses: {"key", "interface_index", "gateway"} (key as in dnswatch),
        or None when there is no default route."""
        from .dnswatch import network_key
        from .winutil import default_route_native, get_wifi_state
        route = default_route_native()
        if not route:
            return None
        wifi = get_wifi_state()
        ssid = wifi.ssid if wifi is not None and wifi.connected else None
        return {"key": network_key(route["interface_index"], route.get("gateway"), ssid),
                "interface_index": int(route["interface_index"]), "gateway": route.get("gateway") or ""}

    def qos_throttle_get(self, name: str) -> int | None:
        """Throttle rate (bits/s) of the named QoS policy in the local store; None if absent."""
        rows = self._ps_json(f"Get-NetQosPolicy -Name {ps_literal(_check_qos_name(name))} "
                             "-ErrorAction SilentlyContinue | Select-Object ThrottleRateActionBitsPerSecond")
        if not rows:
            return None
        rate = rows[0].get("ThrottleRateActionBitsPerSecond")
        return int(rate) if rate else None

    def interface_mtu_get(self, interface_index: int) -> int:
        idx = int(interface_index)
        rows = self._ps_json(f"Get-NetIPInterface -InterfaceIndex {idx} -AddressFamily IPv4 -ErrorAction Stop | "
                             "Select-Object NlMtu")
        if not rows:
            raise SystemReadError(f"no IPv4 interface {idx}")
        return int(rows[0]["NlMtu"])

    def tcp_ecn_get(self) -> str:
        """The "ECN Capability" of `netsh int tcp show global`: enabled, disabled or default."""
        proc = self._run(["netsh", "int", "tcp", "show", "global"], capture_output=True, timeout=30,
                         creationflags=CREATE_NO_WINDOW)
        text = proc.stdout.decode(_oem_codepage(), errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
        m = _ECN_RE.search(text or "")
        if proc.returncode != 0 or not m:
            raise SystemReadError("cannot read the TCP ECN capability")
        return m.group(1).lower()

    def rsc_get(self, adapter: str) -> dict[str, bool] | None:
        """Receive Segment Coalescing of an adapter: enabled flags and what the hardware supports
        ({"ipv4", "ipv6", "ipv4_supported", "ipv6_supported"}); None when Windows has no RSC
        object for it (many Wi-Fi drivers have none)."""
        rows = self._ps_json(
            f"Get-NetAdapterRsc -Name {ps_literal(adapter)} -ErrorAction SilentlyContinue | Select-Object "
            "@{n='IPv4';e={[bool]$_.IPv4Enabled}}, @{n='IPv6';e={[bool]$_.IPv6Enabled}}, "
            "@{n='IPv4Supported';e={[bool]$_.RscHardwareCapabilities.IPv4Supported}}, "
            "@{n='IPv6Supported';e={[bool]$_.RscHardwareCapabilities.IPv6Supported}}")
        if not rows:
            return None
        r = rows[0]
        return {"ipv4": bool(r["IPv4"]), "ipv6": bool(r["IPv6"]),
                "ipv4_supported": bool(r["IPv4Supported"]), "ipv6_supported": bool(r["IPv6Supported"])}

    def packet_coalescing_get(self) -> str:
        """PacketCoalescingFilter of Get-NetOffloadGlobalSetting: Enabled, Disabled or Default."""
        rows = self._ps_json("Get-NetOffloadGlobalSetting -ErrorAction Stop | "
                             "Select-Object @{n='Value';e={[string]$_.PacketCoalescingFilter}}")
        if not rows or not rows[0].get("Value"):
            raise SystemReadError("cannot read the packet coalescing filter")
        return str(rows[0]["Value"])

    def _static_nameservers(self, interface_guid: str) -> str:
        """`NameServer` (REG_SZ, comma-separated) of the interface's TCP/IP key; "" when absent or empty
        (then the servers come from DHCP)."""
        path = TCPIP_INTERFACES_KEY + "\\{" + _check_guid(interface_guid) + "}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                value, kind = winreg.QueryValueEx(k, "NameServer")
        except FileNotFoundError:
            return ""
        except OSError as exc:
            raise SystemReadError(f"cannot read HKLM\\{path}\\NameServer: {exc}") from exc
        if kind != winreg.REG_SZ:
            raise SystemReadError(f"HKLM\\{path}\\NameServer is not a string (type {kind})")
        return str(value)

    def dns_get(self, interface_index: int) -> dict[str, Any]:
        """IPv4 DNS of an interface: {"effective": servers in use (static or from DHCP), "static": the
        servers set by hand, [] when they come from DHCP}."""
        idx = int(interface_index)
        try:
            rows = self._ps_json(
                f"Get-DnsClientServerAddress -InterfaceIndex {idx} -AddressFamily IPv4 -ErrorAction Stop | "
                "Select-Object @{n='Servers';e={@($_.ServerAddresses)}}")
            guid = self._ps(f"(Get-NetAdapter -InterfaceIndex {idx} -ErrorAction Stop).InterfaceGuid").strip()
        except PowerShellError as exc:
            raise SystemReadError(f"cannot read the DNS servers of interface {idx}: {exc}") from exc
        effective = _ipv4_list(_as_list(rows[0].get("Servers"))) if rows else []
        raw = self._static_nameservers(guid.strip("{}")) if guid else ""
        return {"effective": effective, "static": _ipv4_list(re.split(r"[,;\s]+", raw.strip()))}

    def dns_suffix_get(self, interface_index: int) -> str:
        """The connection-specific DNS suffix of an interface ("" when it has none)."""
        idx = int(interface_index)
        try:
            return self._ps(f"(Get-DnsClient -InterfaceIndex {idx} -ErrorAction Stop).ConnectionSpecificSuffix").strip()
        except PowerShellError as exc:
            raise SystemReadError(f"cannot read the DNS suffix of interface {idx}: {exc}") from exc

    def doh_get(self, server: str) -> dict[str, bool] | None:
        """DNS-over-HTTPS flags of a registered resolver ({"auto_upgrade", "fallback"}); None when Windows
        has no DoH template for it. Windows 10 has no such cmdlet: that is a SystemReadError."""
        address = _check_ipv4(server)
        try:
            rows = self._ps_json(
                f"Get-DnsClientDohServerAddress -ServerAddress {address} -ErrorAction SilentlyContinue | Select-Object "
                "@{n='AutoUpgrade';e={[bool]$_.AutoUpgrade}}, @{n='Fallback';e={[bool]$_.AllowFallbackToUdp}}")
        except PowerShellError as exc:
            raise SystemReadError(f"cannot read the DoH settings of {address}: {exc}") from exc
        if not rows:
            return None
        return {"auto_upgrade": bool(rows[0]["AutoUpgrade"]), "fallback": bool(rows[0]["Fallback"])}

    def interface_metric_get(self, interface_index: int) -> dict[str, Any]:
        """{"automatic": bool, "metric": int} of an interface's IPv4 settings."""
        idx = int(interface_index)
        rows = self._ps_json(f"Get-NetIPInterface -InterfaceIndex {idx} -AddressFamily IPv4 -ErrorAction Stop | "
                             "Select-Object InterfaceMetric, @{n='Automatic';e={[string]$_.AutomaticMetric -eq 'Enabled'}}")
        if not rows:
            raise SystemReadError(f"no IPv4 interface {idx}")
        return {"automatic": bool(rows[0]["Automatic"]), "metric": int(rows[0]["InterfaceMetric"])}

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

    def binding_set(self, adapter: str, component: str, enabled: bool) -> None:
        if not _COMPONENT_RE.match(component):
            raise ValueError(f"bad component id: {component!r}")
        verb = "Enable" if enabled else "Disable"
        self._ps_write(f"{verb}-NetAdapterBinding -Name {ps_literal(adapter)} -ComponentID {component} "
                       "-ErrorAction Stop", f"{verb.lower()} {component} on {adapter}")

    def qos_throttle_set(self, name: str, bits_per_second: int) -> None:
        """Create or update the app's own policy: every outgoing TCP and UDP flow, throttled."""
        rate = int(bits_per_second)
        if not UPLOAD_LIMIT_MIN <= rate <= UPLOAD_LIMIT_MAX:
            raise ValueError(f"upload limit out of range: {rate}")
        n = ps_literal(_check_qos_name(name))
        self._ps_write(f"if (Get-NetQosPolicy -Name {n} -ErrorAction SilentlyContinue) "
                       f"{{ Set-NetQosPolicy -Name {n} -ThrottleRateActionBitsPerSecond {rate} -ErrorAction Stop }} "
                       f"else {{ New-NetQosPolicy -Name {n} -IPProtocolMatchCondition Both "
                       f"-ThrottleRateActionBitsPerSecond {rate} -ErrorAction Stop | Out-Null }}",
                       f"setting QoS policy {name} to {rate} bit/s")

    def qos_policy_remove(self, name: str) -> None:
        n = ps_literal(_check_qos_name(name))
        self._ps_write(f"Get-NetQosPolicy -Name {n} -ErrorAction SilentlyContinue | "
                       "Remove-NetQosPolicy -Confirm:$false -ErrorAction Stop", f"removing QoS policy {name}")

    def interface_mtu_set(self, interface_index: int, mtu: int) -> None:
        idx = int(interface_index)
        self._ps_write(f"Set-NetIPInterface -InterfaceIndex {idx} -AddressFamily IPv4 -NlMtuBytes {_check_mtu(mtu)} "
                       "-ErrorAction Stop", f"setting the MTU of interface {idx}")

    def tcp_ecn_set(self, value: str) -> None:
        arg = f"ecncapability={_check_choice(value, ECN_VALUES, 'ECN value')}"
        try:
            proc = self._run(["netsh", "int", "tcp", "set", "global", arg], capture_output=True, timeout=30,
                             creationflags=CREATE_NO_WINDOW)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise SystemWriteError(f"setting TCP ECN to {value} failed: {exc}") from exc
        if proc.returncode != 0:
            raise SystemWriteError(f"netsh int tcp set global {arg} exited {proc.returncode}")

    def rsc_set(self, adapter: str, ipv4: bool | None, ipv6: bool | None) -> None:
        """Turn RSC on or off per address family; None leaves that family alone."""
        steps = [f"{'Enable' if on else 'Disable'}-NetAdapterRsc -Name {ps_literal(adapter)} {flag} -ErrorAction Stop"
                 for flag, on in (("-IPv4", ipv4), ("-IPv6", ipv6)) if on is not None]
        if steps:
            self._ps_write("; ".join(steps), f"setting RSC on {adapter}")

    def packet_coalescing_set(self, value: str) -> None:
        self._ps_write("Set-NetOffloadGlobalSetting -PacketCoalescingFilter "
                       f"{_check_choice(value, PACKET_COALESCING_VALUES, 'packet coalescing value')} -ErrorAction Stop",
                       f"setting the packet coalescing filter to {value}")

    def dns_servers_set(self, interface_index: int, servers: list[str]) -> None:
        """Static IPv4 DNS servers for an interface (at least one)."""
        idx = int(interface_index)
        addresses = [_check_ipv4(x) for x in servers]
        if not addresses or len(addresses) > 8:
            raise ValueError(f"need 1-8 DNS servers, got {len(addresses)}")
        listing = ", ".join(f"'{a}'" for a in addresses)
        self._ps_write(f"Set-DnsClientServerAddress -InterfaceIndex {idx} -ServerAddresses ({listing}) "
                       "-ErrorAction Stop", f"setting the DNS servers of interface {idx}")

    def dns_servers_reset(self, interface_index: int) -> None:
        """Back to the servers the network hands out (DHCP)."""
        idx = int(interface_index)
        self._ps_write(f"Set-DnsClientServerAddress -InterfaceIndex {idx} -ResetServerAddresses -ErrorAction Stop",
                       f"resetting the DNS servers of interface {idx}")

    def doh_set(self, server: str, auto_upgrade: bool, fallback: bool) -> None:
        address = _check_ipv4(server)
        self._ps_write(f"Set-DnsClientDohServerAddress -ServerAddress {address} -AutoUpgrade {_ps_bool(auto_upgrade)} "
                       f"-AllowFallbackToUdp {_ps_bool(fallback)} -ErrorAction Stop",
                       f"setting DNS over HTTPS for {address}")
