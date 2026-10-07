"""System access for tweaks - the only module that writes to the machine.

Writes covered: adapter advanced properties, HKLM DWORD values, powercfg indices of the
active plan, adapter protocol bindings, the tool's own QoS throttle policy. Every write raises SystemWriteError on failure;
callers (app.tweaks) handle backup, verification and rollback.

String values reach PowerShell only as base64 (see ps_literal): PowerShell also treats
the typographic quotes U+2018..U+201B as single quotes, so doubling ' is not enough.
"""
from __future__ import annotations

import base64
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


class System(Protocol):
    """What tweaks may do. Reads have no side effects."""

    def wifi_adapter_name(self) -> str | None: ...
    def adapter_properties(self, adapter: str) -> list[dict[str, Any]]: ...
    def adapter_class_key(self, adapter: str) -> str | None: ...
    def registry_get(self, path: str, name: str) -> int | None: ...
    def power_get(self, subgroup: str, setting: str) -> tuple[int, int]: ...
    def binding_get(self, adapter: str, component: str) -> bool | None: ...
    def qos_policy_get(self, name: str) -> int | None: ...

    def adapter_property_set(self, adapter: str, keyword: str, value: str) -> None: ...
    def adapter_property_reset(self, adapter: str, keyword: str) -> None: ...
    def registry_set_dword(self, path: str, name: str, value: int) -> None: ...
    def registry_delete(self, path: str, name: str) -> None: ...
    def power_set(self, subgroup: str, setting: str, ac: int, dc: int) -> None: ...
    def interface_metric_set(self, interface_index: int, metric: int | None) -> None: ...
    def binding_set(self, adapter: str, component: str, enabled: bool) -> None: ...
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
