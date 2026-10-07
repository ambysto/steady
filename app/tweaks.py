"""Tweak framework: read / capture / apply / restore with backup, verification and rollback.

See docs/adr/0003-tweak-framework.md. In short:
  enable : read -> check support/admin -> capture original and SAVE backup.json -> apply
           -> read back; on error or no effect, roll back to the pre-apply values
  disable: with a backup, restore it and verify it matches; without one, use a known
           default if the tweak has one, otherwise refuse and leave things alone

Tweaks are declarations built from a few primitive kinds (adapter advanced property,
HKLM DWORD, powercfg AC/DC index, adapter binding, TCP global parameter, adapter RSC, global
offload setting), plus tweaks whose value comes from a measurement: DnsFastestTweak measures
in apply() (ADR-0015), a MeasuredTweak takes a measurement made before enabling it
(ADR-0016). The catalog itself lives in CATALOG.

    python -m app.tweaks list
    python -m app.tweaks enable <id>             # dry run: shows the plan
    python -m app.tweaks enable <id> --apply
    python -m app.tweaks disable <id> --apply
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any, Callable, NamedTuple

from . import bufferbloat, calibration, config, dnsprobe, i18n, winutil
from .i18n import msg
from .winsys import _ADAPTER_GUID_RE, OFFLOAD_VALUES, TCP_GLOBAL_VALUES, System

RISKS = ("low", "medium", "experimental")
Message = Any   # an i18n.msg() dict, or plain text (ADR-0006)


class _MessageError(RuntimeError):
    def __init__(self, message: Message) -> None:
        super().__init__(i18n.render(message, i18n.DEFAULT))
        self.message = message


class NoDefaultRestore(_MessageError):
    """Restoring without a backup is not safe for this tweak."""


class Unsupported(_MessageError):
    """The tweak does not apply to this machine (missing property, key, adapter...)."""


class Refused(_MessageError):
    """A measured tweak will not derive a value from this measurement (nothing to fix, too slow...)."""


@dataclass(frozen=True)
class Reading:
    supported: bool
    enabled: bool
    current: Any = None
    reason: Message = ""
    driver_default: bool = False   # enabled only because the driver ships with the target value
    warning: Message = ""   # on, but something about the machine now deserves a look (shown beside the switch)


# --- the primitive kinds -------------------------------------------------------------

class Tweak:
    """Base class. Subclasses implement the four operations; `read` and `capture` must
    have no side effects. `original` dicts are JSON-serialisable and stored in backup.json."""

    has_default_restore = True

    def __init__(self, id: str, name: Message, risk: str, *, group: Message = "", needs_admin: bool = True,
                 disrupts_network: bool = False, needs_reboot: bool = False, note: Message = "") -> None:
        if risk not in RISKS:
            raise ValueError(f"risk must be one of {RISKS}")
        if not re.fullmatch(r"[a-z0-9_]+", id):
            raise ValueError(f"bad tweak id {id!r}")
        self.id, self.name, self.risk, self.group, self.note = id, name, risk, group, note
        self.needs_admin, self.disrupts_network, self.needs_reboot = needs_admin, disrupts_network, needs_reboot

    def read(self, sys_: System) -> Reading:
        raise NotImplementedError

    def capture(self, sys_: System) -> dict[str, Any]:
        raise NotImplementedError

    def apply(self, sys_: System) -> None:
        raise NotImplementedError

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        raise NotImplementedError

    def restore_key(self, original: dict[str, Any]) -> Any:
        """The part of an `original` that a successful restore must reproduce."""
        return original

    def recapture(self, sys_: System, original: dict[str, Any]) -> dict[str, Any]:
        """capture() after restoring `original`, to compare with it. A tweak whose subject can move (the uplink
        changes) reads the subject `original` names, not whatever is current."""
        return self.capture(sys_)

    def backup_conflict(self, sys_: System, original: dict[str, Any]) -> Message | None:
        """Why turning the tweak on must wait while `original` is the saved backup (None: nothing in the way)."""
        return None

    def backup_reading(self, sys_: System, original: dict[str, Any]) -> Reading | None:
        """What to show while the saved backup `original` belongs to a subject read() does not look at (it
        reports the tweak off or unsupported there): a reading with enabled=True makes the switch turn the
        tweak off, which restores that backup. None: nothing to add."""
        return None

    def backup_in_effect(self, sys_: System, original: dict[str, Any]) -> bool:
        """Is the change made after `original` was captured still on the machine? Asked before an outside backup
        (the old backup.json, ADR-0018) is trusted: a backup of a tweak that is off is stale, and one whose "on"
        only the backup itself creates is forged. Call check_original first."""
        if self.read(sys_).enabled:
            return True
        elsewhere = self.backup_reading(sys_, original)
        return bool(elsewhere and elsewhere.enabled)

    def check_original(self, sys_: System, original: Any) -> None:
        """Raise ValueError unless `original` is shaped like this tweak's capture() AND every field lies in
        this tweak's own domain, judged from its declaration and the machine now, never from the file
        (ADR-0017: backup.json is writable without Admin, the helper that restores it is not). Fails closed."""
        raise ValueError(f"{type(self).__name__} cannot check a backup")


# --- backup checks (ADR-0017) ------------------------------------------------------------

DWORD_MAX = 0xFFFFFFFF


def _require(condition: bool, problem: str) -> None:
    if not condition:
        raise ValueError(problem)


def _fields(original: Any, required: set[str], optional: frozenset[str] = frozenset()) -> dict[str, Any]:
    _require(isinstance(original, dict), "the backup entry is not an object")
    keys = set(original)
    _require(required <= keys <= required | optional,
             f"fields {sorted(keys)}, expected {sorted(required)}" + (f" (+ {sorted(optional)})" if optional else ""))
    return original


def _is_int(value: Any, low: int = 0, high: int = DWORD_MAX) -> bool:
    return type(value) is int and low <= value <= high


def _same(stored: Any, expected: Any, what: str) -> None:
    _require(stored == expected, f"{what} {stored!r} is not this tweak's ({expected!r})")


def _check_guid(guid: Any) -> None:
    _require(isinstance(guid, str) and _ADAPTER_GUID_RE.match(guid) is not None, f"{guid!r} is not an interface GUID")


def _check_wifi_adapter(sys_: System, stored: Any) -> str:
    try:
        adapter = _wifi_adapter(sys_)
    except Unsupported:
        raise ValueError("there is no Wi-Fi card to restore to") from None
    _same(stored, adapter, "adapter")
    return adapter


def _wifi_adapter(sys_: System) -> str:
    name = sys_.wifi_adapter_name()
    if not name:
        raise Unsupported(msg("tweak.reason.no_wifi_card"))
    return name


class Match(NamedTuple):
    """How one driver family spells a property. `name` and `value` are the English DisplayName and
    target DisplayValue regexes (the fallback on a driver whose keyword is not listed); `keyword`
    is the RegistryKeyword, which a localized driver does not translate; `registry` is the registry
    value that means "target" for that keyword, recorded only where it was read from a card or is
    fixed by Microsoft (docs/TWEAKS.md, "How a property and its value are matched")."""
    name: str
    value: str
    keyword: str | None = None
    registry: str | None = None


def _keyword_key(keyword: str) -> str:
    return keyword.lstrip("*").lower()


class AdapterPropertyTweak(Tweak):
    """An advanced property of the Wi-Fi driver. Property names differ per chip vendor and are
    translated on a localized driver, so `candidates` lists a `Match` per driver family (a plain
    (DisplayName regex, DisplayValue regex) pair is a Match with no keyword). The property is found
    by RegistryKeyword first, in the order listed, then by DisplayName. The target is the known
    registry value when the property was found by its keyword, otherwise the value whose
    DisplayValue matches. Values are written as RegistryValue.

    `precondition(sys_)` may return a message saying why the tweak does not suit the machine
    right now (e.g. the connected network has no 5 GHz access point). It only gates turning the
    tweak on: a tweak that already holds its target value is still read as on, so it can be
    turned off."""

    def __init__(self, id: str, name: str, risk: str, candidates: list[tuple[str, str]], *,
                 precondition: Callable[[System], Message | None] | None = None, **kw: Any) -> None:
        kw.setdefault("disrupts_network", True)  # changing a driver property restarts the adapter
        super().__init__(id, name, risk, **kw)
        self.candidates = [Match(*c) for c in candidates]
        self._name_res = [(re.compile(m.name, re.I), re.compile(m.value, re.I)) for m in self.candidates]
        self._precondition = precondition

    def _resolve(self, sys_: System) -> tuple[str, dict[str, Any], str]:
        adapter = _wifi_adapter(sys_)
        props = sys_.adapter_properties(adapter)
        for m, (_, tv_re) in zip(self.candidates, self._name_res):
            if m.keyword is None:
                continue
            prop = next((p for p in props if _keyword_key(p["RegistryKeyword"]) == _keyword_key(m.keyword)), None)
            if prop is not None:
                return adapter, prop, self._target(prop, m, tv_re)
        for m, (dn_re, tv_re) in zip(self.candidates, self._name_res):
            prop = next((p for p in props if dn_re.fullmatch(p["DisplayName"])), None)
            if prop is not None:
                return adapter, prop, self._target(prop, m, tv_re)
        raise Unsupported(msg("tweak.reason.no_property"))

    @staticmethod
    def _target(prop: dict[str, Any], m: Match, tv_re: re.Pattern) -> str:
        """The registry value to write for `prop`, found through `m`."""
        pairs = list(zip(prop["ValidDisplayValues"], prop["ValidRegistryValues"]))
        by_text = next((reg for disp, reg in pairs if tv_re.fullmatch(disp)), None)
        known = (m.registry if m.registry is not None and m.keyword is not None
                 and _keyword_key(prop["RegistryKeyword"]) == _keyword_key(m.keyword)
                 and m.registry in prop["ValidRegistryValues"] else None)
        # A driver that spells the same value in English but numbers it differently is not the card
        # this registry value was recorded on: write nothing rather than the wrong value.
        if known is not None and (by_text is None or by_text == known):
            return known
        if known is None and by_text is not None:
            return by_text
        raise Unsupported(msg("tweak.reason.no_matching_value", property=prop["DisplayName"],
                              values=list(prop["ValidDisplayValues"]) or msg("tweak.common.unknown")))

    @staticmethod
    def _current(prop: dict[str, Any]) -> str:
        values = prop["RegistryValue"]
        if len(values) != 1:
            raise Unsupported(msg("tweak.reason.multi_value", property=prop["DisplayName"]))
        return values[0]

    def read(self, sys_: System) -> Reading:
        try:
            _, prop, target = self._resolve(sys_)
            cur = self._current(prop)
            default = prop.get("DefaultRegistryValue")
            if cur != target and self._precondition is not None:
                why = self._precondition(sys_)
                if why:
                    raise Unsupported(why)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        return Reading(True, cur == target, prop["DisplayValue"],
                       driver_default=cur == target and default is not None and str(default) == str(cur))

    def capture(self, sys_: System) -> dict[str, Any]:
        adapter, prop, _ = self._resolve(sys_)
        return {"adapter": adapter, "keyword": prop["RegistryKeyword"], "value": self._current(prop),
                "display": prop["DisplayValue"]}

    def apply(self, sys_: System) -> None:
        adapter, prop, target = self._resolve(sys_)
        sys_.adapter_property_set(adapter, prop["RegistryKeyword"], target)

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is not None:
            sys_.adapter_property_set(original["adapter"], original["keyword"], original["value"])
        else:
            adapter, prop, _ = self._resolve(sys_)
            sys_.adapter_property_reset(adapter, prop["RegistryKeyword"])

    def restore_key(self, original: dict[str, Any]) -> Any:
        return (original["adapter"], original["keyword"], str(original["value"]))

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"adapter", "keyword", "value"}, frozenset({"display"}))
        _check_wifi_adapter(sys_, original["adapter"])
        try:
            _, prop, _ = self._resolve(sys_)   # the property this tweak changes on this card, however it is spelled
        except Unsupported:
            raise ValueError("this card has no property this tweak changes") from None
        # Exactly as Windows reports it, as capture() saved it: "WakeOnMagicPacket" and "*WakeOnMagicPacket" are
        # two registry values, so the looser _keyword_key() match used to find the property is not enough here.
        _same(original["keyword"], prop["RegistryKeyword"], "property")
        _require(isinstance(original["value"], str) and original["value"] in prop["ValidRegistryValues"],
                 f"{original['value']!r} is not a valid value of {prop['DisplayName']!r}")

    def original_from_display(self, sys_: System, display_value: str) -> dict[str, Any]:
        """Build an `original` from a DisplayValue (e.g. "Auto" in the EXP-001 manual backup)."""
        adapter, prop, _ = self._resolve(sys_)
        for disp, reg in zip(prop["ValidDisplayValues"], prop["ValidRegistryValues"]):
            if disp == display_value:
                return {"adapter": adapter, "keyword": prop["RegistryKeyword"], "value": reg, "display": disp}
        raise ValueError(f"{display_value!r} is not a valid value of '{prop['DisplayName']}'")


class RegistryDwordTweak(Tweak):
    """An HKLM DWORD. `target(current)` gives the value to write (so bit flags can be OR-ed in);
    the tweak is enabled when the current value equals that target. `path` may be a function of
    the System (e.g. the Wi-Fi card's class key). Without a backup, restore deletes the value
    when Windows' default is "not set" (`default_absent`), otherwise it refuses."""

    def __init__(self, id: str, name: str, risk: str, *, path: str | Callable[[System], str | None],
                 value_name: str, target: Callable[[int | None], int], default_absent: bool = True,
                 **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self._path, self.value_name, self.target, self.default_absent = path, value_name, target, default_absent
        self.has_default_restore = default_absent

    def _path_of(self, sys_: System) -> str:
        path = self._path(sys_) if callable(self._path) else self._path
        if not path:
            raise Unsupported(msg("tweak.reason.no_registry_key"))
        return path

    def read(self, sys_: System) -> Reading:
        try:
            cur = sys_.registry_get(self._path_of(sys_), self.value_name)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        return Reading(True, cur is not None and cur == self.target(cur), cur)

    def capture(self, sys_: System) -> dict[str, Any]:
        path = self._path_of(sys_)
        return {"path": path, "name": self.value_name, "value": sys_.registry_get(path, self.value_name)}

    def apply(self, sys_: System) -> None:
        path = self._path_of(sys_)
        sys_.registry_set_dword(path, self.value_name, self.target(sys_.registry_get(path, self.value_name)))

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            if not self.default_absent:
                raise NoDefaultRestore(msg("tweak.reason.no_default_registry"))
            sys_.registry_delete(self._path_of(sys_), self.value_name)
        elif original["value"] is None:
            sys_.registry_delete(original["path"], original["name"])
        else:
            sys_.registry_set_dword(original["path"], original["name"], int(original["value"]))

    def restore_key(self, original: dict[str, Any]) -> Any:
        return (original["path"], original["name"], original["value"])

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"path", "name", "value"})
        try:
            path = self._path_of(sys_)
        except Unsupported:
            raise ValueError("the registry key of this tweak is not on this PC") from None
        _same(original["path"], path, "registry path")
        _same(original["name"], self.value_name, "registry value")
        _require(original["value"] is None or _is_int(original["value"]), f"{original['value']!r} is not a DWORD")


class PowerCfgTweak(Tweak):
    """An AC/DC index of the active power plan. No known per-setting default exists, so
    disabling without a backup is refused (see ADR-0003)."""

    has_default_restore = False

    def __init__(self, id: str, name: str, risk: str, *, subgroup: str, setting: str, ac: int, dc: int,
                 valid: range = range(DWORD_MAX + 1), **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self.subgroup, self.setting, self.ac, self.dc, self.valid = subgroup, setting, ac, dc, valid

    def read(self, sys_: System) -> Reading:
        ac, dc = sys_.power_get(self.subgroup, self.setting)
        return Reading(True, (ac, dc) == (self.ac, self.dc), {"ac": ac, "dc": dc})

    def capture(self, sys_: System) -> dict[str, Any]:
        ac, dc = sys_.power_get(self.subgroup, self.setting)
        return {"subgroup": self.subgroup, "setting": self.setting, "ac": ac, "dc": dc}

    def apply(self, sys_: System) -> None:
        sys_.power_set(self.subgroup, self.setting, self.ac, self.dc)

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            raise NoDefaultRestore(msg("tweak.reason.no_default_powercfg"))
        sys_.power_set(original["subgroup"], original["setting"], int(original["ac"]), int(original["dc"]))

    def restore_key(self, original: dict[str, Any]) -> Any:
        return (original["subgroup"], original["setting"], int(original["ac"]), int(original["dc"]))

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"subgroup", "setting", "ac", "dc"})
        _same(original["subgroup"], self.subgroup, "power subgroup")
        _same(original["setting"], self.setting, "power setting")
        for side in ("ac", "dc"):
            _require(type(original[side]) is int and original[side] in self.valid,
                     f"{side} index {original[side]!r} is outside {self.valid.start}..{self.valid.stop - 1}")


class BindingTweak(Tweak):
    """A protocol binding of the Wi-Fi card (e.g. ms_tcpip6). Windows' default is enabled."""

    def __init__(self, id: str, name: str, risk: str, *, component: str, enabled: bool, **kw: Any) -> None:
        kw.setdefault("disrupts_network", True)
        super().__init__(id, name, risk, **kw)
        self.component, self.target_enabled = component, enabled

    def read(self, sys_: System) -> Reading:
        try:
            state = sys_.binding_get(_wifi_adapter(sys_), self.component)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        if state is None:
            return Reading(False, False, None, msg("tweak.reason.no_binding", component=self.component))
        return Reading(True, state == self.target_enabled, state)

    def capture(self, sys_: System) -> dict[str, Any]:
        adapter = _wifi_adapter(sys_)
        return {"adapter": adapter, "component": self.component, "enabled": sys_.binding_get(adapter, self.component)}

    def apply(self, sys_: System) -> None:
        sys_.binding_set(_wifi_adapter(sys_), self.component, self.target_enabled)

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            sys_.binding_set(_wifi_adapter(sys_), self.component, True)
        else:
            sys_.binding_set(original["adapter"], original["component"], bool(original["enabled"]))

    def restore_key(self, original: dict[str, Any]) -> Any:
        return (original["adapter"], original["component"], bool(original["enabled"]))

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"adapter", "component", "enabled"})
        _check_wifi_adapter(sys_, original["adapter"])
        _same(original["component"], self.component, "binding")
        _require(isinstance(original["enabled"], bool), f"{original['enabled']!r} is not true or false")


class TcpGlobalTweak(Tweak):
    """A `netsh int tcp set global` parameter. `default` is Windows' own value, used to restore when
    there is no backup (None: refuse instead)."""

    VALUES = TCP_GLOBAL_VALUES

    def __init__(self, id: str, name: str, risk: str, *, setting: str, target: str, default: str | None,
                 **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self.setting, self.target, self.default = setting, target, default
        self.has_default_restore = default is not None

    def _value(self, sys_: System) -> str:
        value = sys_.tcp_global_get(self.setting)
        if value is None:
            raise Unsupported(msg("tweak.reason.no_tcp_setting", setting=self.setting))
        return value

    def read(self, sys_: System) -> Reading:
        try:
            value = self._value(sys_)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        return Reading(True, value == self.target, value)

    def capture(self, sys_: System) -> dict[str, Any]:
        return {"setting": self.setting, "value": self._value(sys_)}

    def apply(self, sys_: System) -> None:
        sys_.tcp_global_set(self.setting, self.target)

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            if self.default is None:
                raise NoDefaultRestore(msg("tweak.reason.no_default_tcp"))
            sys_.tcp_global_set(self.setting, self.default)
        else:
            sys_.tcp_global_set(original["setting"], original["value"])

    def restore_key(self, original: dict[str, Any]) -> Any:
        return (original["setting"], str(original["value"]))

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"setting", "value"})
        _same(original["setting"], self.setting, "setting")
        _require(original["value"] in self.VALUES, f"{original['value']!r} is not one of {self.VALUES}")


class RscTweak(Tweak):
    """Receive Segment Coalescing of the Wi-Fi card, per address family. Only the families the card's
    hardware supports are touched; a card with none is unsupported. No known default: backup only."""

    has_default_restore = False

    def __init__(self, id: str, name: str, risk: str, **kw: Any) -> None:
        kw.setdefault("disrupts_network", True)   # the driver restarts the adapter
        super().__init__(id, name, risk, **kw)

    def _state(self, sys_: System) -> tuple[str, dict[str, Any]]:
        adapter = _wifi_adapter(sys_)
        rsc = sys_.rsc_get(adapter)
        if rsc is None or not (rsc["ipv4_supported"] or rsc["ipv6_supported"]):
            raise Unsupported(msg("tweak.reason.no_rsc"))
        return adapter, rsc

    def read(self, sys_: System) -> Reading:
        try:
            _, rsc = self._state(sys_)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        on = [rsc[f] for f in ("ipv4", "ipv6") if rsc[f + "_supported"]]
        return Reading(True, not any(on), {"ipv4": rsc["ipv4"], "ipv6": rsc["ipv6"]})

    def capture(self, sys_: System) -> dict[str, Any]:
        adapter, rsc = self._state(sys_)
        return {"adapter": adapter, **{f: rsc[f] if rsc[f + "_supported"] else None for f in ("ipv4", "ipv6")}}

    def apply(self, sys_: System) -> None:
        adapter, rsc = self._state(sys_)
        sys_.rsc_set(adapter, *(False if rsc[f + "_supported"] else None for f in ("ipv4", "ipv6")))

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            raise NoDefaultRestore(msg("tweak.reason.no_default_rsc"))
        sys_.rsc_set(original["adapter"], original["ipv4"], original["ipv6"])

    def restore_key(self, original: dict[str, Any]) -> Any:
        return (original["adapter"], original["ipv4"], original["ipv6"])

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"adapter", "ipv4", "ipv6"})
        _check_wifi_adapter(sys_, original["adapter"])
        for family in ("ipv4", "ipv6"):
            _require(original[family] is None or isinstance(original[family], bool),
                     f"{family} {original[family]!r} is not true, false or null")


class OffloadGlobalTweak(Tweak):
    """A Set-NetOffloadGlobalSetting parameter. No known default: restoring needs the backup."""

    has_default_restore = False
    VALUES = OFFLOAD_VALUES

    def __init__(self, id: str, name: str, risk: str, *, setting: str, target: str, **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self.setting, self.target = setting, target

    def _value(self, sys_: System) -> str:
        value = sys_.offload_global_get(self.setting)
        if value is None:
            raise Unsupported(msg("tweak.reason.no_offload_setting", setting=self.setting))
        return value

    def read(self, sys_: System) -> Reading:
        try:
            value = self._value(sys_)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        return Reading(True, value == self.target, value)

    def capture(self, sys_: System) -> dict[str, Any]:
        return {"setting": self.setting, "value": self._value(sys_)}

    def apply(self, sys_: System) -> None:
        sys_.offload_global_set(self.setting, self.target)

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            raise NoDefaultRestore(msg("tweak.reason.no_default_offload"))
        sys_.offload_global_set(original["setting"], original["value"])

    def restore_key(self, original: dict[str, Any]) -> Any:
        return (original["setting"], str(original["value"]))

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"setting", "value"})
        _same(original["setting"], self.setting, "setting")
        _require(original["value"] in self.VALUES, f"{original['value']!r} is not one of {self.VALUES}")


# --- DNS: the fastest public servers, over HTTPS (ADR-0015) ---------------------------------------

# Providers Windows ships a DoH template for: provider -> (template, IPv4 addresses).
DOH_PROVIDERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "cloudflare": ("https://cloudflare-dns.com/dns-query", ("1.1.1.1", "1.0.0.1")),
    "google": ("https://dns.google/dns-query", ("8.8.8.8", "8.8.4.4")),
    "quad9": ("https://dns.quad9.net/dns-query", ("9.9.9.9", "149.112.112.112")),
}
DOH_ADDRESSES: dict[str, tuple[str, str]] = {address: (provider, template)
                                             for provider, (template, addresses) in DOH_PROVIDERS.items()
                                             for address in addresses}   # address -> (provider, template)
CAPTIVE_PROBE_URL = "http://cp.cloudflare.com/generate_204"

Benchmark = Callable[[list[str]], "list[dnsprobe.ServerBenchmark]"]
CaptiveCheck = Callable[[], bool]


class NoFastServers(_MessageError):
    """The benchmark did not find two working providers to switch to."""


def benchmark_servers(servers: list[str]) -> list[dnsprobe.ServerBenchmark]:
    """The DNS benchmark of diagnostics check #6, one thread per server."""
    with ThreadPoolExecutor(max_workers=max(1, len(servers))) as pool:
        return list(pool.map(lambda s: dnsprobe.benchmark([s], timeout=1.5)[0], servers))


def captive_portal() -> bool:
    """True when the connectivity probe is answered the way a captive portal answers it."""
    from . import probe
    result = probe.http_204(CAPTIVE_PROBE_URL, 3.0)
    return not result.ok and "captive portal" in result.error


def choose_dns_servers(bench: list[dnsprobe.ServerBenchmark]) -> list[str]:
    """The servers to use, from a benchmark of DOH_ADDRESSES (pure). A server that failed a query is
    out; providers are ranked by their fastest server. Result: the winner's fastest, the runner-up's
    fastest (so the first two never share a provider), then the winner's other server if it works."""
    by_provider: dict[str, list[tuple[float, str]]] = {}
    for b in bench:
        if b.server in DOH_ADDRESSES and b.median_ms is not None and not b.failures:
            by_provider.setdefault(DOH_ADDRESSES[b.server][0], []).append((b.median_ms, b.server))
    ranked = sorted((sorted(rows) for rows in by_provider.values()), key=lambda rows: rows[0])
    if len(ranked) < 2:
        raise NoFastServers(msg("tweak.reason.dns_no_fast_servers"))
    winner, runner_up = ranked[0], ranked[1]
    return [winner[0][1], runner_up[0][1]] + [row[1] for row in winner[1:]]


def _looks_ours(entry: dict[str, Any]) -> bool:
    """A DoH entry in the state apply() leaves it in (auto-upgrade on, fallback to UDP on)."""
    return bool(entry["auto_upgrade"] and entry["fallback_to_udp"])


class DnsFastestTweak(Tweak):
    """The uplink's IPv4 DNS set to the fastest public servers, each with DoH auto-upgrade on.

    read() never measures: "on" is a property of the configuration (static servers, all from the
    candidate list, two providers or more, DoH auto-upgrade on). The benchmark runs in apply().
    capture() therefore records the DoH flags of every candidate, not only the chosen ones.

    The backup belongs to one interface, found again by its GUID (an ifIndex can be reused). Turning
    the tweak off, and checking that it worked, look at that interface and not at whichever one is the
    uplink now; turning it on while the backup is another interface's is refused. restore() touches only
    what apply() could have changed: a DoH entry somebody else has changed since is left alone."""

    has_default_restore = False
    CAPTIVE_TTL_S = 60.0

    def __init__(self, id: str, name: str, risk: str, *, benchmark: Benchmark = benchmark_servers,
                 captive: CaptiveCheck = captive_portal, clock: Callable[[], float] = time.monotonic, **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self._benchmark, self._captive, self._clock = benchmark, captive, clock
        self._captive_seen: tuple[float, bool] | None = None
        self._applied: set[str] = set()   # the servers apply() had set, as restore() last saw them

    @staticmethod
    def _info(sys_: System, guid: str | None = None) -> dict[str, Any]:
        info = sys_.dns_interface(guid) if guid else sys_.dns_interface()
        if info is None:
            raise Unsupported(msg("tweak.reason.dns_interface_gone" if guid else "tweak.reason.no_uplink"))
        return info

    @staticmethod
    def _doh(sys_: System) -> dict[str, dict[str, Any]]:
        doh = sys_.doh_get()
        if doh is None:
            raise Unsupported(msg("tweak.reason.no_doh"))
        return doh

    @staticmethod
    def _is_on(info: dict[str, Any], doh: dict[str, dict[str, Any]]) -> bool:
        servers = info["servers"]
        return bool(info["static"] and servers and all(s in DOH_ADDRESSES for s in servers)
                    and len({DOH_ADDRESSES[s][0] for s in servers}) >= 2
                    and all(doh.get(s, {}).get("auto_upgrade") for s in servers))

    def _captive_portal(self, fresh: bool) -> bool:
        """The probe is a web request: listings reuse an answer for a minute, apply() always asks."""
        now = self._clock()
        if fresh or self._captive_seen is None or now - self._captive_seen[0] > self.CAPTIVE_TTL_S:
            self._captive_seen = (now, self._captive())
        return self._captive_seen[1]

    def _blocked(self, info: dict[str, Any], fresh: bool = False) -> Message | None:
        """Why this network should be left alone (None: fine). The cheap checks come first."""
        if info["vpn_up"]:
            return msg("tweak.reason.dns_vpn")
        if info["domain_joined"] or info["suffix"]:
            return msg("tweak.reason.dns_domain")
        if info["static_v6"]:
            return msg("tweak.reason.dns_static_v6")
        if self._captive_portal(fresh):
            return msg("tweak.reason.dns_captive")
        return None

    def read(self, sys_: System) -> Reading:
        try:
            info = self._info(sys_)
            doh = self._doh(sys_)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        current = {"servers": info["servers"], "static": info["static"]}
        blocked = self._blocked(info)
        if self._is_on(info, doh):
            # Static DNS stays with the interface, not with the network: another network may need its own.
            return Reading(True, True, current, warning=blocked or "")
        return Reading(blocked is None, False, current, blocked or "")

    @staticmethod
    def _snapshot(info: dict[str, Any], doh: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {"guid": info["guid"], "interface_index": info["index"], "static": info["static"],
                "servers": list(info["servers"]),
                "doh": {address: {"present": address in doh, "template": doh.get(address, {}).get("template", ""),
                                  "auto_upgrade": doh.get(address, {}).get("auto_upgrade", False),
                                  "fallback_to_udp": doh.get(address, {}).get("fallback_to_udp", False)}
                        for address in DOH_ADDRESSES}}

    def capture(self, sys_: System) -> dict[str, Any]:
        return self._snapshot(self._info(sys_), self._doh(sys_))

    def recapture(self, sys_: System, original: dict[str, Any]) -> dict[str, Any]:
        info = self._info(sys_, original.get("guid"))
        snapshot = self._snapshot(info, self._doh(sys_))
        for address, now in snapshot["doh"].items():   # an entry somebody else changed is not ours to compare
            was = original["doh"][address]
            same = (now["present"], now["auto_upgrade"], now["fallback_to_udp"]) == \
                   (was["present"], was["auto_upgrade"], was["fallback_to_udp"])
            # "apply was using it" is what restore() decided before it wrote the servers back
            ours = (now["present"] and _looks_ours(now)) or (was["present"] and not now["present"]
                                                             and address in self._applied)
            if not same and not ours:
                snapshot["doh"][address] = dict(was)
        return snapshot

    def backup_conflict(self, sys_: System, original: dict[str, Any]) -> Message | None:
        info = sys_.dns_interface()
        if info is not None and original.get("guid") and info["guid"] != original["guid"]:
            return msg("tweak.reason.dns_other_interface")
        return None

    def backup_in_effect(self, sys_: System, original: dict[str, Any]) -> bool:
        """The interface the backup names (its GUID, or the uplink for a backup without one) has this tweak's
        DNS now. backup_reading() only asks whether that interface exists, which a forged GUID satisfies."""
        try:
            return self._is_on(self._info(sys_, original.get("guid")), self._doh(sys_))
        except Unsupported:
            return False

    def backup_reading(self, sys_: System, original: dict[str, Any]) -> Reading | None:
        """The backup is another interface's (or the uplink cannot be read at all): the interface that was changed
        still has the tweak's DNS, so the switch must offer to turn it off. Gone adapter: nothing to restore."""
        guid = original.get("guid")
        if not guid:
            return None
        try:
            here = sys_.dns_interface()
        except Exception:
            here = None
        if here is not None and here["guid"] == guid:
            return None          # read() already looked at it
        try:
            info = self._info(sys_, guid)
        except Unsupported:
            return None
        return Reading(True, True, {"servers": info["servers"], "static": info["static"]},
                       warning=msg("tweak.reason.dns_backup_elsewhere", name=info["alias"]))

    def apply(self, sys_: System) -> None:
        info = self._info(sys_)
        self._doh(sys_)
        blocked = self._blocked(info, fresh=True)
        if blocked is not None:   # the state may have changed since it was read (a VPN came up...)
            raise Unsupported(blocked)
        servers = choose_dns_servers(self._benchmark(list(DOH_ADDRESSES)))
        for address in servers:   # DoH first: the moment the server is used, Windows already knows to upgrade
            sys_.doh_set(address, DOH_ADDRESSES[address][1], True, True)
        sys_.dns_servers_set(info["index"], servers)

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            raise NoDefaultRestore(msg("tweak.reason.no_default_dns"))
        info = self._info(sys_, original.get("guid"))   # the interface that was changed, wherever it is now
        self._applied = {s for s in info["servers"] if s in DOH_ADDRESSES}   # read before the servers are written back
        wanted = list(original["servers"]) if original["static"] else None
        if (wanted is None and info["static"]) or (wanted is not None and (not info["static"] or info["servers"] != wanted)):
            sys_.dns_servers_set(info["index"], wanted)
        doh = self._doh(sys_)
        for address, was in original["doh"].items():
            now = doh.get(address)
            if was["present"] and now is None:   # the entry vanished: put it back, if apply was using it
                if address in self._applied:
                    # Always the provider's own template: a URL from the file could send DNS anywhere (ADR-0017).
                    sys_.doh_set(address, DOH_ADDRESSES[address][1], bool(was["auto_upgrade"]),
                                 bool(was["fallback_to_udp"]))
            elif was["present"]:
                if (now["auto_upgrade"], now["fallback_to_udp"]) != (was["auto_upgrade"], was["fallback_to_udp"]) \
                        and _looks_ours(now):
                    sys_.doh_set(address, None, bool(was["auto_upgrade"]), bool(was["fallback_to_udp"]))
            elif now is not None and _looks_ours(now):   # not there before: apply() added it
                sys_.doh_remove(address)

    def restore_key(self, original: dict[str, Any]) -> Any:
        # DHCP servers come and go with the lease: only "static" and a static list must come back.
        # (the interface is not part of it: recapture() looks it up by the GUID of the backup)
        return (bool(original["static"]),
                tuple(original["servers"]) if original["static"] else None,
                tuple(sorted((a, bool(d["present"]), bool(d["auto_upgrade"]) if d["present"] else None,
                              bool(d["fallback_to_udp"]) if d["present"] else None)
                             for a, d in original["doh"].items())))

    MAX_SERVERS = 8

    def check_original(self, sys_: System, original: Any) -> None:
        _fields(original, {"interface_index", "static", "servers", "doh"}, frozenset({"guid"}))   # no guid: older backup
        _check_guid(original.get("guid", "{00000000-0000-0000-0000-000000000000}"))
        _require(_is_int(original["interface_index"], 1), f"interface {original['interface_index']!r} is not an index")
        _require(isinstance(original["static"], bool), "static is not true or false")
        servers = original["servers"]
        _require(isinstance(servers, list) and all(isinstance(s, str) for s in servers), "servers is not a list")
        if original["static"]:
            _require(1 <= len(servers) <= self.MAX_SERVERS, f"{len(servers)} static servers")
            for server in servers:
                _require(_unicast_ipv4(server), f"{server!r} is not a unicast IPv4 address")
        doh = original["doh"]
        _require(isinstance(doh, dict) and set(doh) <= set(DOH_ADDRESSES), "DoH entries for other addresses")
        for address, was in doh.items():
            _fields(was, {"present", "template", "auto_upgrade", "fallback_to_udp"})
            _require(all(isinstance(was[f], bool) for f in ("present", "auto_upgrade", "fallback_to_udp")),
                     f"DoH flags of {address} are not true or false")
            _require(was["template"] in ("", DOH_ADDRESSES[address][1]),
                     f"DoH template {was['template']!r} is not {DOH_ADDRESSES[address][0]}'s")


def _unicast_ipv4(text: str) -> bool:
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return not (address.is_unspecified or address.is_multicast or address.is_reserved)


# --- calibrated by a measurement made before enabling (ADR-0016) -----------------------------------

class MeasuredTweak(Tweak):
    """A tweak whose value comes from a measurement taken just before enabling it (ADR-0016).
    `derive` is pure and carries the safety margins; `apply_value` writes the derived value and
    `applied_value` reads back what is in effect. Plain `apply` has no value to write."""

    measurement_kind = ""
    verdict_prefix = "tweak"   # messages <prefix>.event.verified_<key> and <prefix>.verdict.<key>

    def check(self, measurement: Any, now: float) -> dict[str, Any]:
        """The measurement validated (ValueError when it is malformed or too old)."""
        raise NotImplementedError

    def derive(self, measurement: dict[str, Any]) -> int:
        """The value to apply, or Refused."""
        raise NotImplementedError

    def apply(self, sys_: System) -> None:
        raise Refused(msg("tweak.reason.needs_measurement"))

    def apply_value(self, sys_: System, value: int) -> None:
        raise NotImplementedError

    def applied_value(self, sys_: System) -> int | None:
        raise NotImplementedError

    def value_matches(self, applied: int | None, value: int) -> bool:
        return applied == value

    def describe_value(self, value: int, measurement: dict[str, Any]) -> Message:
        """The value and the measurement it came from, for the user."""
        raise NotImplementedError

    def verdict(self, before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
        """Did it help? The measurement before enabling against the one right after; "helped" is True,
        False or None (could not measure again). The other fields fill the verdict messages."""
        return calibration.verdict(before, after)

    def verdict_params(self, v: dict[str, Any]) -> dict[str, Any]:
        return dict(before=v["before_ms"], after=v["after_ms"])


UPLOAD_POLICY = "StableInternet-Upload"
UPLOAD_MARGIN = 0.85
UPLOAD_MIN_BPS, UPLOAD_MAX_BPS = 1_000_000, 1_000_000_000
UPLOAD_STEP_BPS = 100_000
# Destinations the upload limit must not reach: private, link-local and unique-local networks (a NAS
# copy or casting runs at LAN speed, far above any Internet uplink).
LOCAL_DESTINATIONS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10")


class UploadShapingTweak(MeasuredTweak):
    """A QoS policy of the tool's own name throttling outbound traffic to 85% of the measured upload,
    so the queue builds on the PC (short) instead of in the modem (long). The throttle matches all
    traffic (-Default), so one more specific policy per local network exempts the LAN; they are
    written before the throttle and removed after it. Restore removes exactly the tool's policies,
    which is also the safe default without a backup (the names are ours)."""

    measurement_kind = "upload"

    def __init__(self, id: str, name: Message, risk: str, *, policy: str = UPLOAD_POLICY, **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self.policy = policy
        self.exemptions = {f"{policy}-Local{i}": prefix for i, prefix in enumerate(LOCAL_DESTINATIONS, 1)}

    def check(self, measurement: Any, now: float) -> dict[str, Any]:
        return calibration.check_upload(measurement, now)

    def derive(self, measurement: dict[str, Any]) -> int:
        mbps, attempted = float(measurement["upload_mbps"]), int(measurement["attempted"])
        if mbps < calibration.MIN_UPLOAD_MBPS or attempted < calibration.MIN_SAMPLES:
            raise Refused(msg("tweak.upload_shaping.refused.weak", mbps=mbps, samples=attempted))
        # The measurement stops at a fixed volume, so near its ceiling it shows its own limit, not the line's.
        ceiling = bufferbloat.LOAD_CEILING_MBPS
        if mbps >= calibration.AT_CEILING * ceiling:
            raise Refused(msg("tweak.upload_shaping.refused.ceiling", mbps=mbps, ceiling=ceiling))
        rise = calibration.rise_ms(measurement)
        if rise < calibration.NOT_NEEDED_MS:
            raise Refused(msg("tweak.upload_shaping.refused.not_needed", rise=max(rise, 0.0)))
        cap = int(mbps * 1e6 * UPLOAD_MARGIN) // UPLOAD_STEP_BPS * UPLOAD_STEP_BPS
        if not UPLOAD_MIN_BPS <= cap <= UPLOAD_MAX_BPS:
            raise Refused(msg("tweak.upload_shaping.refused.bounds", cap=cap / 1e6,
                              low=UPLOAD_MIN_BPS / 1e6, high=UPLOAD_MAX_BPS / 1e6))
        return cap

    def _policies(self, sys_: System) -> dict[str, dict[str, Any]]:
        """The tool's policies by lower-case name (the ActiveStore lower-cases them)."""
        return {name.lower(): p for name, p in sys_.qos_policies_get(self.policy).items()}

    def _exempted(self, policies: dict[str, dict[str, Any]]) -> list[str]:
        """Exemption policies present with their intended destination."""
        return sorted(n for n, prefix in self.exemptions.items()
                      if (policies.get(n.lower()) or {}).get("destination") == prefix)

    def applied_value(self, sys_: System) -> int | None:
        """The throttle in effect, counted only while every local network is exempted."""
        policies = self._policies(sys_)
        rate = (policies.get(self.policy.lower()) or {}).get("rate_bps")
        return rate if rate is not None and len(self._exempted(policies)) == len(self.exemptions) else None

    def value_matches(self, applied: int | None, value: int) -> bool:
        # Windows may round the rate it stores; 1% is far below the margin taken.
        return applied is not None and abs(applied - value) <= max(8_000, value // 100)

    def describe_value(self, value: int, measurement: dict[str, Any]) -> Message:
        return msg("tweak.upload_shaping.value", limit=value / 1e6, upload=float(measurement["upload_mbps"]))

    def read(self, sys_: System) -> Reading:
        policies = self._policies(sys_)
        rate = (policies.get(self.policy.lower()) or {}).get("rate_bps")
        if rate is None:
            return Reading(True, False, None)
        return Reading(True, True, {"limit_mbps": round(rate / 1e6, 1),
                                    "local_exempt": len(self._exempted(policies)) == len(self.exemptions)})

    def capture(self, sys_: System) -> dict[str, Any]:
        policies = self._policies(sys_)
        return {"policy": self.policy, "rate_bps": (policies.get(self.policy.lower()) or {}).get("rate_bps"),
                "exempt": self._exempted(policies)}

    def apply_value(self, sys_: System, value: int) -> None:
        for name, prefix in self.exemptions.items():   # the LAN is exempted before anything is throttled
            sys_.qos_exempt_set(name, prefix)
        sys_.qos_policy_set(self.policy, value)

    def check_original(self, sys_: System, original: Any) -> None:
        # Only the tool's own policies, at a rate this tweak could have set (ADR-0017). `exempt` is missing
        # from backups made before the LAN exemptions.
        _fields(original, {"policy", "rate_bps"}, frozenset({"exempt"}))
        _same(original["policy"], self.policy, "QoS policy")
        rate = original["rate_bps"]
        _require(rate is None or _is_int(rate, UPLOAD_MIN_BPS, UPLOAD_MAX_BPS),
                 f"rate {rate!r} is outside {UPLOAD_MIN_BPS}..{UPLOAD_MAX_BPS} bit/s")
        exempt = original.get("exempt", [])
        _require(isinstance(exempt, list) and all(isinstance(n, str) and n in self.exemptions for n in exempt)
                 and len(set(exempt)) == len(exempt), f"exemption policies {exempt!r} are not this tweak's")

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        # Only ever the tool's own names: the policy is self.policy, exemptions are looked up in self.exemptions.
        rate = None if original is None else original["rate_bps"]
        keep = set() if original is None else set(original.get("exempt", []))
        if rate is None:
            sys_.qos_policy_remove(self.policy)
        else:
            sys_.qos_policy_set(self.policy, int(rate))
        present = {n for n in self.exemptions if n.lower() in self._policies(sys_)}
        for name in sorted(present - keep):        # the throttle is gone or back: exemptions can follow
            sys_.qos_policy_remove(name)
        for name in sorted(keep):
            sys_.qos_exempt_set(name, self.exemptions[name])

    def restore_key(self, original: dict[str, Any]) -> Any:
        rate = original["rate_bps"]
        return (original["policy"], None if rate is None else int(rate), tuple(sorted(original.get("exempt", []))))


MTU_LOWEST = 1280            # no plain PPPoE or tunnel is that small; it is also IPv6's minimum
MTU_HIGHEST_PROBED = 1500    # above this the interface uses jumbo frames, a LAN choice to leave alone


class MtuTweak(MeasuredTweak):
    """The uplink's IPv4 MTU lowered to the path MTU measured by check #16, only when large packets
    vanish silently (PMTUD blackhole). Windows keeps no mark of who set an MTU, so read() never
    reports it on: it is on while this tweak's backup exists and that interface's MTU differs from
    the saved one (backup_reading). Like dns_fastest the backup names its interface by GUID."""

    measurement_kind = "path_mtu"
    verdict_prefix = "tweak.mtu_pmtu"
    has_default_restore = False

    @staticmethod
    def _info(sys_: System, guid: str | None = None) -> dict[str, Any]:
        info = sys_.ipv4_interface(guid) if guid else sys_.ipv4_interface()
        if info is None:
            raise Unsupported(msg("tweak.mtu_pmtu.reason.interface_gone" if guid else "tweak.mtu_pmtu.reason.no_uplink"))
        return info

    def check(self, measurement: Any, now: float) -> dict[str, Any]:
        return calibration.check_path_mtu(measurement, now)

    def derive(self, measurement: dict[str, Any]) -> int:
        interface, path = int(measurement["interface_mtu"]), int(measurement["path_mtu"])
        if measurement["tunnel"]:
            raise Refused(msg("tweak.mtu_pmtu.refused.tunnel"))
        if interface > MTU_HIGHEST_PROBED:
            raise Refused(msg("tweak.mtu_pmtu.refused.jumbo", mtu=interface))
        if path >= interface:
            raise Refused(msg("tweak.mtu_pmtu.refused.not_needed", mtu=interface))
        if measurement["too_big"]:
            raise Refused(msg("tweak.mtu_pmtu.refused.pmtud_works", mtu=interface, path=path))
        if int(measurement["answered"]) < calibration.MIN_PATH_TARGETS:
            raise Refused(msg("tweak.mtu_pmtu.refused.one_target", path=path))
        if path < MTU_LOWEST:
            raise Refused(msg("tweak.mtu_pmtu.refused.bounds", path=path, low=MTU_LOWEST))
        return path

    def describe_value(self, value: int, measurement: dict[str, Any]) -> Message:
        return msg("tweak.mtu_pmtu.value", mtu=value, before=int(measurement["interface_mtu"]))

    def verdict(self, before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
        return calibration.path_mtu_verdict(before, after)

    def verdict_params(self, v: dict[str, Any]) -> dict[str, Any]:
        return dict(mtu=v["mtu"], path=v["path_after"])

    def read(self, sys_: System) -> Reading:
        try:
            info = self._info(sys_)
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        return Reading(True, False, {"interface": info["alias"], "mtu": info["mtu"]})

    def backup_in_effect(self, sys_: System, original: dict[str, Any]) -> bool:
        """apply_value() only ever lowers the MTU: the tweak is in effect only where the MTU is below the saved
        one. "Differs" (backup_reading) would let a forged MTU create the "on" it needs."""
        try:
            return int(self._info(sys_, original.get("guid"))["mtu"]) < int(original["mtu"])
        except Unsupported:
            return False

    def backup_reading(self, sys_: System, original: dict[str, Any]) -> Reading | None:
        try:
            info = self._info(sys_, original.get("guid"))
        except Unsupported:
            return None   # the adapter is gone: nothing left to turn off
        if info["mtu"] == int(original["mtu"]):
            return None
        try:
            here = sys_.ipv4_interface()
        except Exception:
            here = None
        elsewhere = here is None or here["guid"] != info["guid"]
        return Reading(True, True, {"interface": info["alias"], "mtu": info["mtu"]},
                       warning=msg("tweak.mtu_pmtu.reason.backup_elsewhere", name=info["alias"]) if elsewhere else "")

    def backup_conflict(self, sys_: System, original: dict[str, Any]) -> Message | None:
        info = sys_.ipv4_interface()
        if info is not None and original.get("guid") and info["guid"] != original["guid"]:
            return msg("tweak.mtu_pmtu.reason.other_interface")
        return None

    @staticmethod
    def _snapshot(info: dict[str, Any]) -> dict[str, Any]:
        return {"guid": info["guid"], "interface_index": info["index"], "alias": info["alias"], "mtu": info["mtu"]}

    def capture(self, sys_: System) -> dict[str, Any]:
        return self._snapshot(self._info(sys_))

    def recapture(self, sys_: System, original: dict[str, Any]) -> dict[str, Any]:
        return self._snapshot(self._info(sys_, original.get("guid")))

    def applied_value(self, sys_: System) -> int | None:
        info = sys_.ipv4_interface()
        return None if info is None else int(info["mtu"])

    def apply_value(self, sys_: System, value: int) -> None:
        info = self._info(sys_)
        if int(value) >= int(info["mtu"]):   # only ever lowers it; the interface may have changed since measuring
            raise Refused(msg("tweak.mtu_pmtu.refused.not_needed", mtu=info["mtu"]))
        sys_.ipv4_mtu_set(info["index"], int(value))

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None:
            raise NoDefaultRestore(msg("tweak.mtu_pmtu.reason.no_default"))
        info = self._info(sys_, original.get("guid"))   # the interface that was changed, wherever it is now
        if int(info["mtu"]) != int(original["mtu"]):
            sys_.ipv4_mtu_set(info["index"], int(original["mtu"]))

    def restore_key(self, original: dict[str, Any]) -> Any:
        return int(original["mtu"])

    def check_original(self, sys_: System, original: Any) -> None:
        # The interface is found by its GUID, and the MTU is one derive() could have lowered from (ADR-0017).
        _fields(original, {"guid", "interface_index", "alias", "mtu"})
        _check_guid(original["guid"])
        _require(_is_int(original["interface_index"], 1), f"interface {original['interface_index']!r} is not an index")
        _require(isinstance(original["alias"], str), "alias is not text")
        _require(_is_int(original["mtu"], MTU_LOWEST, MTU_HIGHEST_PROBED),
                 f"MTU {original['mtu']!r} is outside {MTU_LOWEST}..{MTU_HIGHEST_PROBED}")


# --- read cache ------------------------------------------------------------------------

_READ_METHODS = ("wifi_adapter_name", "adapter_properties", "adapter_class_key", "registry_get", "power_get",
                 "binding_get", "wifi_ssid_bands", "tcp_global_get", "rsc_get", "offload_global_get", "dns_interface",
                 "doh_get", "qos_policies_get", "ipv4_interface")


class _CachedReads:
    """Wraps a System so repeated reads within one listing hit PowerShell once. Writes are
    not exposed: a listing must never change the machine."""

    def __init__(self, inner: System) -> None:
        self._inner, self._cache = inner, {}

    def __getattr__(self, name: str) -> Any:
        if name not in _READ_METHODS:
            raise AttributeError(f"{name} is not available while listing (read-only)")
        fn = getattr(self._inner, name)

        def cached(*args: Any) -> Any:
            key = (name, args)
            if key not in self._cache:
                try:
                    self._cache[key] = (True, fn(*args))
                except Exception as exc:
                    self._cache[key] = (False, exc)
            ok, value = self._cache[key]
            if not ok:
                raise value
            return value
        return cached


# --- the manager -------------------------------------------------------------------------

@dataclass(frozen=True)
class TweakState:
    id: str
    name: Message
    risk: str
    group: Message
    supported: bool
    enabled: bool
    current: Any
    reason: Message
    has_backup: bool | None      # None: backup.json unreadable
    needs_admin: bool
    disrupts_network: bool
    needs_reboot: bool
    can_restore_without_backup: bool
    error: str | None = None     # reading failed (not the same as "unsupported")
    note: Message = ""
    on_by_default: bool = False  # on only because the driver ships that way: the tool wrote nothing, nothing to turn off
    warning: Message = ""
    measured: bool = False       # the value comes from a measurement (ADR-0016)
    measurement: dict[str, Any] | None = None   # the one stored with the backup, while on


@dataclass(frozen=True)
class Outcome:
    ok: bool
    changed: bool
    message: Message
    state: TweakState | None = None


EventSink = Callable[[str, Message, str], None]   # (kind, message, level)


class TweakManager:
    def __init__(self, system: System, tweaks: list[Tweak], *,
                 load_backup: Callable[[], dict] = config.load_backup,
                 save_backup: Callable[[dict], None] = config.save_backup,
                 record_event: EventSink | None = None,
                 is_admin: Callable[[], bool] = winutil.is_admin,
                 clock: Callable[[], float] = time.time) -> None:
        ids = [t.id for t in tweaks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tweak ids")
        self.system, self._tweaks = system, {t.id: t for t in tweaks}
        self._load, self._save = load_backup, save_backup
        self._event = record_event or (lambda kind, message, level: None)
        self._is_admin, self._clock = is_admin, clock
        self._lock = threading.Lock()
        self.last_change_ts: float | None = None

    def _put(self, key: str, entry: dict | None) -> None:
        """One backup entry, written against the current file (other processes write it too)."""
        config.put_backup_entry(key, entry, load=self._load, save=self._save)

    # -- reading ---------------------------------------------------------------------

    def get(self, tweak_id: str) -> Tweak:
        try:
            return self._tweaks[tweak_id]
        except KeyError:
            raise KeyError(f"unknown tweak {tweak_id!r}") from None

    def _backups(self) -> dict[str, Any] | None:
        try:
            return self._load()
        except Exception:
            return None

    def _state(self, t: Tweak, sys_: Any, backups: dict[str, Any] | None) -> TweakState:
        try:
            r, error = t.read(sys_), None
        except Exception as exc:
            r, error = Reading(False, False, None, msg("tweak.reason.unreadable")), f"{type(exc).__name__}: {exc}"
        entry = (backups or {}).get(t.id)
        if isinstance(entry, dict) and not r.enabled:
            try:   # a saved backup that read() cannot see (another interface): let the switch restore it
                r = t.backup_reading(sys_, entry["original"]) or r
            except Exception:
                pass
        measurement = entry.get("measurement") if isinstance(entry, dict) and r.enabled else None
        has_backup = None if backups is None else t.id in backups
        on_by_default = r.enabled and r.driver_default and has_backup is False
        return TweakState(t.id, t.name, t.risk, t.group, r.supported, r.enabled, r.current,
                          msg("tweak.reason.driver_default") if on_by_default else r.reason,
                          has_backup, t.needs_admin, t.disrupts_network, t.needs_reboot,
                          t.has_default_restore, error, t.note, on_by_default, r.warning,
                          isinstance(t, MeasuredTweak), measurement)

    def states(self) -> list[TweakState]:
        """Read-only snapshot of every tweak; reads are shared across tweaks."""
        cached, backups = _CachedReads(self.system), self._backups()
        return [self._state(t, cached, backups) for t in self._tweaks.values()]

    def state(self, tweak_id: str) -> TweakState:
        return self._state(self.get(tweak_id), _CachedReads(self.system), self._backups())

    def recently_changed(self, within_s: float) -> bool:
        """True if a tweak changed the machine in the last `within_s` seconds (for the watchdog)."""
        return self.last_change_ts is not None and self._clock() - self.last_change_ts < within_s

    # -- writing ---------------------------------------------------------------------

    def _fail(self, t: Tweak, message: Message, *, changed: bool = False, level: str = "warn") -> Outcome:
        self._event("tweak_failed", msg("tweak.event.failed", name=t.name, message=message, tweak_id=t.id), level)
        return Outcome(False, changed, message, self.state(t.id))

    def _rollback(self, t: Tweak, to: dict[str, Any]) -> bool:
        try:
            t.restore(self.system, to)
            return t.restore_key(t.recapture(self.system, to)) == t.restore_key(to)
        except Exception:
            return False

    def derive(self, tweak_id: str, measurement: Any) -> tuple[dict[str, Any], int]:
        """For a measured tweak: (checked measurement, value), or Refused. Writes nothing, so callers
        can refuse before a UAC prompt."""
        t = self.get(tweak_id)
        if not isinstance(t, MeasuredTweak):
            raise ValueError(f"{tweak_id} is not a measured tweak")
        try:
            checked = t.check(measurement, self._clock())
        except ValueError as exc:
            raise Refused(msg("tweak.result.bad_measurement", error=str(exc))) from None
        return checked, t.derive(checked)

    def enable(self, tweak_id: str, measurement: Any = None) -> Outcome:
        """measurement: required by a measured tweak (ADR-0016), ignored by the others."""
        t = self.get(tweak_id)
        measured = isinstance(t, MeasuredTweak)
        with self._lock:
            try:
                r = t.read(self.system)
            except Exception as exc:
                return self._fail(t, msg("tweak.result.read_failed", error=str(exc)))
            if not r.supported:
                return Outcome(False, False, msg("tweak.result.unsupported", reason=r.reason), self.state(t.id))
            if r.enabled:
                return Outcome(True, False, msg("tweak.result.already_on"), self.state(t.id))
            value = None
            if measured:
                if measurement is None:
                    return Outcome(False, False, msg("tweak.reason.needs_measurement"), self.state(t.id))
                try:
                    measurement, value = self.derive(t.id, measurement)
                except Refused as exc:
                    return Outcome(False, False, exc.message, self.state(t.id))
            if t.needs_admin and not self._is_admin():
                return Outcome(False, False, msg("tweak.result.needs_admin"), self.state(t.id))
            try:
                backup = self._load()
            except Exception as exc:
                return self._fail(t, msg("tweak.result.backup_unreadable_apply", error=str(exc)))

            created = t.id not in backup
            if not created:
                try:
                    conflict = t.backup_conflict(self.system, backup[t.id]["original"])
                except Exception as exc:
                    return self._fail(t, msg("tweak.result.read_failed", error=str(exc)))
                if conflict is not None:
                    return Outcome(False, False, msg("tweak.result.backup_other", reason=conflict), self.state(t.id))
            try:
                before = t.capture(self.system)
            except Exception as exc:
                return self._fail(t, msg("tweak.result.capture_failed", error=str(exc)))
            if created:
                try:
                    self._put(t.id, {"original": before, "captured_at": int(self._clock()), "source": "capture"})
                except Exception as exc:
                    return self._fail(t, msg("tweak.result.backup_write_failed", error=str(exc)))

            try:
                if measured:
                    t.apply_value(self.system, value)
                    applied = t.value_matches(t.applied_value(self.system), value)
                else:
                    t.apply(self.system)
                    applied = t.read(self.system).enabled
                problem = None if applied else msg("tweak.result.no_effect")
            except Exception as exc:
                problem = msg("tweak.result.apply_failed", error=exc.message if isinstance(exc, _MessageError) else str(exc))
            if problem is None:
                self.last_change_ts = self._clock()
                if measured:
                    self._keep_measurement(t, {**measurement, "value": value})
                self._event("tweak_enabled", msg("tweak.event.enabled", name=t.name, tweak_id=t.id), "info")
                return Outcome(True, True, msg("tweak.result.enabled"), self.state(t.id))

            self.last_change_ts = self._clock()
            if self._rollback(t, before):
                if created:
                    try:
                        self._put(t.id, None)
                    except Exception:
                        pass  # a leftover backup equal to the current value is harmless
                return self._fail(t, msg("tweak.result.rolled_back", problem=problem))
            return self._fail(t, msg("tweak.result.rollback_failed", problem=problem),
                              changed=True, level="bad")

    def _keep_measurement(self, t: Tweak, record: dict[str, Any]) -> None:
        """Store the measurement behind the applied value in the tweak's backup entry. Only
        bookkeeping: the machine is already right, so a failure is logged, not rolled back."""
        try:
            entry = self._load().get(t.id)
            if isinstance(entry, dict):
                self._put(t.id, {**entry, "measurement": record})
        except Exception as exc:
            self._event("tweak_failed", msg("tweak.event.measurement_not_kept", name=t.name, error=str(exc),
                                            tweak_id=t.id), "warn")

    def disable(self, tweak_id: str) -> Outcome:
        t = self.get(tweak_id)
        with self._lock:
            read_error = None
            try:
                r = t.read(self.system)
            except Exception as exc:
                r, read_error = None, exc   # with a backup, restoring does not need the current state
            try:
                backup = self._load()
            except Exception as exc:
                return self._fail(t, msg("tweak.result.backup_unreadable", error=str(exc)))
            entry = backup.get(t.id)
            if entry is None and read_error is not None:
                return self._fail(t, msg("tweak.result.read_failed", error=str(read_error)))
            if entry is None and not r.enabled:
                return Outcome(True, False, msg("tweak.result.already_off"), self.state(t.id))
            if entry is None and r.enabled and r.driver_default:
                # Resetting to the driver default would restart the adapter and leave the same value.
                return Outcome(True, False, msg("tweak.result.driver_default"), self.state(t.id))
            if entry is None and not r.supported:
                return Outcome(False, False, msg("tweak.result.unsupported", reason=r.reason), self.state(t.id))
            if t.needs_admin and not self._is_admin():
                return Outcome(False, False, msg("tweak.result.needs_admin"), self.state(t.id))

            if entry is None:
                if not t.has_default_restore:
                    return Outcome(False, False, msg("tweak.result.no_safe_default"), self.state(t.id))
                try:
                    t.restore(self.system, None)
                except NoDefaultRestore as exc:
                    return Outcome(False, False, msg("tweak.result.kept", reason=exc.message), self.state(t.id))
                except Exception as exc:
                    self.last_change_ts = self._clock()
                    return self._fail(t, msg("tweak.result.default_restore_failed", error=str(exc)), changed=True, level="bad")
                self.last_change_ts = self._clock()
                try:
                    still_on = t.read(self.system).enabled
                except Exception:
                    still_on = False   # cannot read it back: report the reset, as before
                if still_on:
                    return self._fail(t, msg("tweak.result.default_restore_no_effect"), changed=True, level="bad")
                self._event("tweak_disabled", msg("tweak.event.disabled_default", name=t.name, tweak_id=t.id), "info")
                return Outcome(True, True, msg("tweak.result.restored_default"), self.state(t.id))

            try:
                original = entry["original"]
                t.check_original(self.system, original)
            except (KeyError, TypeError, ValueError) as exc:
                return self._fail(t, msg("tweak.result.backup_rejected", error=str(exc)), level="bad")
            except Exception as exc:   # the machine could not be read to judge it: still nothing restored
                return self._fail(t, msg("tweak.result.read_failed", error=str(exc)))
            try:
                t.restore(self.system, original)
                matches = t.restore_key(t.recapture(self.system, original)) == t.restore_key(original)
            except Exception as exc:
                self.last_change_ts = self._clock()
                return self._fail(t, msg("tweak.result.restore_failed", error=str(exc)), changed=True, level="bad")
            self.last_change_ts = self._clock()
            if not matches:
                return self._fail(t, msg("tweak.result.restore_mismatch"),
                                  changed=True, level="bad")
            try:
                self._put(t.id, None)
            except Exception as exc:
                # Restored correctly; only the bookkeeping failed. Re-disabling would restore the same value.
                self._event("tweak_failed", msg("tweak.event.backup_not_removed", name=t.name, error=str(exc), tweak_id=t.id), "warn")
            self._event("tweak_disabled", msg("tweak.event.disabled", name=t.name, tweak_id=t.id), "info")
            return Outcome(True, True, msg("tweak.result.restored"), self.state(t.id))

    def adopt_backup(self, tweak_id: str, original: dict[str, Any], source: str) -> Outcome:
        """Record an original value captured outside the tool (e.g. the EXP-001 manual backup).
        Changes nothing on the machine and never overwrites an existing backup."""
        t = self.get(tweak_id)
        with self._lock:
            try:
                t.check_original(self.system, original)
            except (KeyError, TypeError, ValueError) as exc:
                return Outcome(False, False, msg("tweak.result.bad_backup", error=str(exc)), None)
            except Exception as exc:
                return Outcome(False, False, msg("tweak.result.read_failed", error=str(exc)), None)
            # An outside backup only makes sense for a tweak that is on right now. If it is
            # off, the current value IS the original; adopting a stale one could later
            # "restore" something wrong (seen on the dev PC: most of EXP-001 had been undone).
            try:
                if not t.read(self.system).enabled:
                    return Outcome(False, False, msg("tweak.result.adopt_off"), None)
            except Exception as exc:
                return Outcome(False, False, msg("tweak.result.read_failed", error=str(exc)), None)
            with config.backup_lock():
                try:
                    backup = self._load()
                except Exception as exc:
                    return Outcome(False, False, msg("tweak.result.backup_unreadable", error=str(exc)), None)
                if t.id in backup:
                    return Outcome(False, False, msg("tweak.result.backup_exists"), None)
                backup[t.id] = {"original": original, "captured_at": int(self._clock()), "source": source}
                self._save(backup)
            return Outcome(True, False, msg("tweak.result.adopted", source=source), None)

    def plan(self, tweak_id: str, enable: bool, lang: str | None = None) -> str:
        """What enable/disable would do, without doing it."""
        st = self.state(tweak_id)
        lines = [msg("tweak.plan.header", name=st.name, id=st.id, risk=st.risk),
                 msg("tweak.plan.current", value=repr(st.current),
                     state=msg("tweak.plan.on" if st.enabled else "tweak.plan.off"))]
        if st.error:
            lines.append(msg("tweak.plan.unreadable", error=st.error))
        elif not st.supported:
            lines.append(msg("tweak.plan.unsupported", reason=st.reason))
        elif enable:
            if st.measured and not st.enabled:
                lines.append(msg("tweak.plan.will_measure"))
            lines.append(msg("tweak.plan.nothing_on") if st.enabled else
                         msg("tweak.plan.will_apply",
                             disrupts=msg("tweak.plan.disrupts") if st.disrupts_network else "",
                             reboot=msg("tweak.plan.reboot") if st.needs_reboot else ""))
        else:
            if st.has_backup:
                lines.append(msg("tweak.plan.will_restore"))
            elif st.on_by_default:
                lines.append(msg("tweak.plan.is_default"))
            elif st.enabled:
                lines.append(msg("tweak.plan.will_default") if st.can_restore_without_backup else
                             msg("tweak.plan.will_refuse"))
            else:
                lines.append(msg("tweak.plan.nothing_off"))
        if st.needs_admin and not self._is_admin() and (enable or not st.on_by_default):
            lines.append(msg("tweak.plan.needs_admin"))
        return "\n".join(i18n.render(line, lang) for line in lines)


# --- catalog ------------------------------------------------------------------------------

# Concrete tweaks, exactly the ones described in docs/TWEAKS.md (a test keeps the two in sync).
# Values: DisplayValue regexes tolerate the "1. "-style numeric prefixes some drivers add.
SUB_WIRELESS, SET_WIRELESS = "19cbb8fa-5279-450e-9fac-8a3d5fedd0c1", "12bbebe6-58d6-4636-95bb-3217ef867c1a"
SUB_PCIE, SET_ASPM = "501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5"
TCPIP_PARAMS = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
PNP_NO_POWER_OFF = 0x18        # PnPCapabilities bits that stop Windows powering the card down
_N = r"(?:\d+\.\s*)?"          # optional "1. " prefix


def _wifi_class_key(sys_: System) -> str | None:
    name = sys_.wifi_adapter_name()
    if not name:
        raise Unsupported(msg("tweak.reason.no_wifi_card"))
    return sys_.adapter_class_key(name)


def _band_key(band: str) -> str:
    return re.sub(r"\s", "", band).lower()   # "5 GHz" and "5GHz" are the same band


def _stuck_on_24ghz(sys_: System) -> Message | None:
    """Why "prefer 5 GHz" would do nothing (or harm) here, or None when it fits: the card is on a
    2.4 GHz access point and the same network has a 5 GHz one. A card already on 5 GHz gains
    nothing; one on 6 GHz could be pulled down. The band always comes from a Band field: a channel
    number alone cannot tell 2.4 GHz from 6 GHz."""
    seen = sys_.wifi_ssid_bands(_wifi_adapter(sys_))
    if seen is None:
        return msg("tweak.reason.not_connected")
    if not _band_key(seen.current_band):
        return msg("tweak.reason.band_unknown")
    if _band_key(seen.current_band) != "2.4ghz":
        return msg("tweak.reason.not_on_24ghz", band=seen.current_band)
    if not seen.bands:
        return msg("tweak.reason.bands_unknown", ssid=seen.ssid)
    if not any(_band_key(band) == "5ghz" for band in seen.bands):
        return msg("tweak.reason.no_5ghz", ssid=seen.ssid)
    return None


def _name(tweak_id: str) -> Message:
    return msg(f"tweak.{tweak_id}.name")


def _note(tweak_id: str) -> Message:
    return msg(f"tweak.{tweak_id}.note")


GROUP_WIFI, GROUP_POWER, GROUP_STACK, GROUP_MEASURED = (
    msg("tweak.group.wifi_card"), msg("tweak.group.power"), msg("tweak.group.stack"), msg("tweak.group.measured"))


def build_catalog(*, dns_benchmark: Benchmark = benchmark_servers, captive: CaptiveCheck = captive_portal) -> list[Tweak]:
    """`dns_benchmark` and `captive` are what dns_fastest asks the network; tests pass fakes."""
    return [
        # 1. Wi-Fi card (driver advanced properties). Property names vary by chip vendor.
        AdapterPropertyTweak("wifi_power_saving", _name("wifi_power_saving"), "low",
                             [Match(r"Power Saving", r"Disabled", "LowPowerEnable", "0"),
                              Match(r"MIMO Power Save Mode", r"No SMPS", "MIMOPowerSaveMode")],
                             group=GROUP_WIFI, note=_note("wifi_power_saving")),
        AdapterPropertyTweak("wifi_wake_magic", _name("wifi_wake_magic"), "low",
                             [Match(r"Wake on Magic Packet", r"Disabled", "DisableWakeOnMagic", "1"),
                              Match(r"Wake on Magic Packet", r"Disabled", "*WakeOnMagicPacket", "0")], group=GROUP_WIFI,
                             note=_note("wifi_wake_magic")),
        AdapterPropertyTweak("wifi_wake_pattern", _name("wifi_wake_pattern"), "low",
                             [Match(r"Wake on Pattern Match", r"Disabled", "DisableWakeOnPattern", "1"),
                              Match(r"Wake on Pattern Match", r"Disabled", "*WakeOnPattern", "0")], group=GROUP_WIFI),
        AdapterPropertyTweak("wifi_roaming", _name("wifi_roaming"), "low",
                             [Match(r"Roaming Aggressiveness", _N + r"Lowest", "RoamingAggressiveness")],
                             group=GROUP_WIFI,
                             note=_note("wifi_roaming")),
        AdapterPropertyTweak("wifi_bw20_5g", _name("wifi_bw20_5g"), "experimental",
                             [Match(r"5GHz channel bandwidth", _N + r"20MHz only", "BWSelection5G", "1")],
                             group=GROUP_WIFI,
                             note=_note("wifi_bw20_5g")),
        AdapterPropertyTweak("wifi_mode_ac", _name("wifi_mode_ac"), "experimental",
                             [Match(re.escape("802.11ax/ac/n/abg"), _N + re.escape("802.11ac"), "CurrPhyMode", "1")],
                             group=GROUP_WIFI,
                             note=_note("wifi_mode_ac")),
        AdapterPropertyTweak("wifi_prefer_5g", _name("wifi_prefer_5g"), "low",
                             [Match(r"Preferred Band|Band Preference", _N + r"Prefer 5\s?GHz(?: band)?",
                                    "PreferredBand", "2"),
                              Match(r"Preferred Band|Band Preference", _N + r"Prefer 5\s?GHz(?: band)?",
                                    "RoamingPreferredBandType")],
                             precondition=_stuck_on_24ghz, group=GROUP_WIFI, note=_note("wifi_prefer_5g")),
        AdapterPropertyTweak("wifi_tx_power_max", _name("wifi_tx_power_max"), "low",
                             [Match(r"Transmit Power(?: Level)?|Tx Power(?: Level)?", _N + r"Highest",
                                    "TxPowerLevel", "0"),
                              Match(r"Transmit Power(?: Level)?|Tx Power(?: Level)?", _N + r"Highest",
                                    "TransmitPower")],
                             group=GROUP_WIFI, note=_note("wifi_tx_power_max")),
        # 2. Windows power management
        RegistryDwordTweak("device_power_off", _name("device_power_off"), "low", path=_wifi_class_key,
                           value_name="PnPCapabilities", target=lambda cur: (cur or 0) | PNP_NO_POWER_OFF,
                           default_absent=False,  # the driver INF sets a value (16 here); deleting it is a guess
                           group=GROUP_POWER, disrupts_network=True, note=_note("device_power_off")),
        PowerCfgTweak("power_wireless_max", _name("power_wireless_max"), "low",
                      subgroup=SUB_WIRELESS, setting=SET_WIRELESS, ac=0, dc=0, valid=range(4), group=GROUP_POWER,
                      note=_note("power_wireless_max")),
        PowerCfgTweak("power_pcie_aspm_off", _name("power_pcie_aspm_off"), "low", subgroup=SUB_PCIE,
                      setting=SET_ASPM, ac=0, dc=0, valid=range(3), group=GROUP_POWER,
                      note=_note("power_pcie_aspm_off")),
        # 3. Network stack
        RegistryDwordTweak("tcp_timedwait", _name("tcp_timedwait"), "medium", path=TCPIP_PARAMS,
                           value_name="TcpTimedWaitDelay", target=lambda cur: 30, default_absent=True,
                           group=GROUP_STACK, needs_reboot=True, note=_note("tcp_timedwait")),
        BindingTweak("ipv6_off", _name("ipv6_off"), "experimental", component="ms_tcpip6", enabled=False,
                     group=GROUP_STACK, note=_note("ipv6_off")),
        TcpGlobalTweak("tcp_ecn", _name("tcp_ecn"), "experimental", setting="ecncapability", target="enabled",
                       default="default", group=GROUP_STACK, note=_note("tcp_ecn")),
        RscTweak("rsc_off", _name("rsc_off"), "experimental", group=GROUP_STACK, note=_note("rsc_off")),
        OffloadGlobalTweak("packet_coalescing_off", _name("packet_coalescing_off"), "experimental",
                           setting="PacketCoalescingFilter", target="Disabled", group=GROUP_STACK,
                           note=_note("packet_coalescing_off")),
        DnsFastestTweak("dns_fastest", _name("dns_fastest"), "experimental", benchmark=dns_benchmark, captive=captive,
                        group=GROUP_STACK, note=_note("dns_fastest")),
        # 4. Measured tweaks (ADR-0016)
        UploadShapingTweak("upload_shaping", _name("upload_shaping"), "medium", group=GROUP_MEASURED,
                           note=_note("upload_shaping")),
        MtuTweak("mtu_pmtu", _name("mtu_pmtu"), "medium", group=GROUP_MEASURED, note=_note("mtu_pmtu")),
    ]


CATALOG: list[Tweak] = build_catalog()


def enable_measured(mgr: TweakManager, tweak_id: str, measure: Callable[[], dict | None],
                    enable: Callable[[dict], Any], record_event: EventSink) -> dict[str, Any]:
    """Turn a measured tweak on (ADR-0016): measure with it off, refuse before any write (and so
    before any UAC prompt) when there is nothing to fix, enable through `enable(measurement)` (the
    manager directly, or the elevated helper), then measure again and record whether it helped.

    `enable` returns an object with ok / message and optionally changed / cancelled."""
    t = mgr.get(tweak_id)
    if not isinstance(t, MeasuredTweak):
        raise ValueError(f"{tweak_id} is not a measured tweak")
    state = mgr.state(tweak_id)
    if state.error:          # do not load the line, nor ask for UAC, for a state that cannot be read
        return {"ok": False, "changed": False, "message": msg("tweak.result.read_failed", error=state.error)}
    if not state.supported:
        return {"ok": False, "changed": False, "message": msg("tweak.result.unsupported", reason=state.reason)}
    if state.enabled:
        return {"ok": True, "changed": False, "message": msg("tweak.result.already_on")}
    before = measure()
    if before is None:
        return {"ok": False, "changed": False, "message": msg("tweak.result.measure_failed")}
    try:
        _, value = mgr.derive(tweak_id, before)
    except Refused as exc:
        return {"ok": False, "changed": False, "message": exc.message, "measurement": before}
    out = enable(before)
    result = {"ok": bool(out.ok), "changed": bool(getattr(out, "changed", out.ok)), "message": out.message,
              "measurement": before}
    if getattr(out, "cancelled", False):
        result["cancelled"] = True
    if not (out.ok and result["changed"]):
        return result
    after = measure()
    v = t.verdict(before, after)
    key = {True: "helped", False: "no_help", None: "unknown"}[v["helped"]]
    params = dict(name=t.name, tweak_id=t.id, **t.verdict_params(v))
    prefix = t.verdict_prefix
    record_event("tweak_verified", msg(f"{prefix}.event.verified_{key}", **params),
                 "warn" if key == "no_help" else "info")
    result.update(verdict=v, message=msg("tweak.result.measured_enabled", value=t.describe_value(value, before),
                                         verdict=msg(f"{prefix}.verdict.{key}", **params)))
    return result


def measure_for(t: MeasuredTweak) -> Callable[[], dict | None]:
    """The real measurement a measured tweak derives its value from (generates traffic)."""
    return {"upload": calibration.measure_upload, "path_mtu": calibration.measure_path_mtu}[t.measurement_kind]


def default_manager(storage: Any = None) -> TweakManager:
    from .winsys import WindowsSystem

    def record(kind: str, message: Message, level: str) -> None:
        if storage is not None:
            storage.add_event(int(time.time()), kind, message, level=level)

    return TweakManager(WindowsSystem(), CATALOG, record_event=record)


def list_states() -> dict[str, dict[str, Any]] | None:
    """For diagnostics (check #10, and #14 for the upload limit). None while no tweak is declared.
    No side effects."""
    if not CATALOG:
        return None
    return {s.id: {"risk": s.risk, "enabled": s.enabled, "supported": s.supported, "current": s.current}
            for s in default_manager().states()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tweaks", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "enable", "disable"])
    ap.add_argument("tweak_id", nargs="?")
    ap.add_argument("--apply", action="store_true", help="actually change the machine (default: show the plan)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lang", help="language for the output (default: settings.json ui.language)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from .storage import Storage
    with Storage(config.db_path()) as storage:
        mgr = default_manager(storage)
        if args.action == "list":
            states = mgr.states()
            if args.json:
                print(json.dumps(i18n.localize([asdict(s) for s in states], args.lang), ensure_ascii=False,
                                 indent=2, default=str))
            elif not states:
                print(i18n.t("tweak.cli.none", args.lang))
            for s in ([] if args.json else states):
                flag = (i18n.t("tweak.cli.on", args.lang) if s.enabled
                        else "—" if s.supported else i18n.t("tweak.cli.unsupported", args.lang))
                why = i18n.render(s.reason, args.lang) or i18n.render(s.warning, args.lang) or s.error
                print(f"{s.id:24} {flag:14} {s.risk:12} {i18n.render(s.name, args.lang)}  [{s.current!r}]"
                      + (f"  ({why})" if why else ""))
            return 0
        if not args.tweak_id:
            ap.error("tweak_id is required")
        enable = args.action == "enable"
        if not args.apply:
            print(mgr.plan(args.tweak_id, enable, args.lang) + "\n\n" + i18n.t("tweak.cli.dry_run", args.lang))
            return 0
        if enable and isinstance(mgr.get(args.tweak_id), MeasuredTweak):
            print(i18n.t("tweak.cli.measuring", args.lang), flush=True)
            res = enable_measured(mgr, args.tweak_id, measure_for(mgr.get(args.tweak_id)),
                                  lambda m: mgr.enable(args.tweak_id, m),
                                  lambda kind, message, level: storage.add_event(int(time.time()), kind, message,
                                                                                 level=level))
            print(i18n.render(res["message"], args.lang))
            return 0 if res["ok"] else 1
        out = mgr.enable(args.tweak_id) if enable else mgr.disable(args.tweak_id)
        print(i18n.render(out.message, args.lang))
        return 0 if out.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
