"""Tweak framework: read / capture / apply / restore with backup, verification and rollback.

See docs/adr/0003-tweak-framework.md. In short:
  enable : read -> check support/admin -> capture original and SAVE backup.json -> apply
           -> read back; on error or no effect, roll back to the pre-apply values
  disable: with a backup, restore it and verify it matches; without one, use a known
           default if the tweak has one, otherwise refuse and leave things alone

Tweaks are declarations built from four primitive kinds (adapter advanced property,
HKLM DWORD, powercfg AC/DC index, adapter binding), plus measured tweaks whose value is
derived from a measurement of the network in use (ADR-0015). The catalog lives in CATALOG.

    python -m app.tweaks list
    python -m app.tweaks enable <id>             # dry run: shows the plan
    python -m app.tweaks enable <id> --apply
    python -m app.tweaks disable <id> --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from . import calibration, config, i18n, winutil
from .i18n import msg
from .winsys import System

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

    def validate_original(self, original: dict[str, Any]) -> None:
        """Raise ValueError if `original` is not shaped like this tweak's capture()."""
        self.restore_key(original)


def _wifi_adapter(sys_: System) -> str:
    name = sys_.wifi_adapter_name()
    if not name:
        raise Unsupported(msg("tweak.reason.no_wifi_card"))
    return name


class AdapterPropertyTweak(Tweak):
    """An advanced property of the Wi-Fi driver. Property names differ per chip vendor, so
    `candidates` lists (DisplayName regex, target DisplayValue regex) pairs; the first
    property present on this card wins. Values are written as RegistryValue."""

    def __init__(self, id: str, name: str, risk: str, candidates: list[tuple[str, str]], **kw: Any) -> None:
        kw.setdefault("disrupts_network", True)  # changing a driver property restarts the adapter
        super().__init__(id, name, risk, **kw)
        self.candidates = [(re.compile(dn, re.I), re.compile(tv, re.I)) for dn, tv in candidates]

    def _resolve(self, sys_: System) -> tuple[str, dict[str, Any], str]:
        adapter = _wifi_adapter(sys_)
        props = sys_.adapter_properties(adapter)
        for dn_re, tv_re in self.candidates:
            prop = next((p for p in props if dn_re.fullmatch(p["DisplayName"])), None)
            if prop is None:
                continue
            for disp, reg in zip(prop["ValidDisplayValues"], prop["ValidRegistryValues"]):
                if tv_re.fullmatch(disp):
                    return adapter, prop, reg
            raise Unsupported(msg("tweak.reason.no_matching_value", property=prop["DisplayName"],
                                  values=list(prop["ValidDisplayValues"]) or msg("tweak.common.unknown")))
        raise Unsupported(msg("tweak.reason.no_property"))

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
        except Unsupported as exc:
            return Reading(False, False, None, exc.message)
        return Reading(True, cur == target, prop["DisplayValue"])

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


class PowerCfgTweak(Tweak):
    """An AC/DC index of the active power plan. No known per-setting default exists, so
    disabling without a backup is refused (see ADR-0003)."""

    has_default_restore = False

    def __init__(self, id: str, name: str, risk: str, *, subgroup: str, setting: str, ac: int, dc: int,
                 **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self.subgroup, self.setting, self.ac, self.dc = subgroup, setting, ac, dc

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


class MeasuredTweak(Tweak):
    """A tweak whose value comes from a measurement taken just before enabling it (ADR-0015).
    `derive` is pure and carries the safety margins; `apply_value` writes the derived value and
    `applied_value` reads back what is in effect. Plain `apply` has no value to write."""

    measurement_kind = ""

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


UPLOAD_POLICY = "StableInternet-Upload"
UPLOAD_MARGIN = 0.85
UPLOAD_MIN_BPS, UPLOAD_MAX_BPS = 1_000_000, 1_000_000_000
UPLOAD_STEP_BPS = 100_000


class UploadShapingTweak(MeasuredTweak):
    """A QoS policy of the tool's own name throttling all outbound traffic to 85% of the measured
    upload, so the queue builds on the PC (short) instead of in the modem (long). Restore removes
    exactly that policy, which is also the safe default without a backup (the name is ours)."""

    measurement_kind = "upload"

    def __init__(self, id: str, name: Message, risk: str, *, policy: str = UPLOAD_POLICY, **kw: Any) -> None:
        super().__init__(id, name, risk, **kw)
        self.policy = policy

    def check(self, measurement: Any, now: float) -> dict[str, Any]:
        return calibration.check_upload(measurement, now)

    def derive(self, measurement: dict[str, Any]) -> int:
        mbps, samples = float(measurement["upload_mbps"]), int(measurement["samples"])
        if mbps < calibration.MIN_UPLOAD_MBPS or samples < calibration.MIN_SAMPLES:
            raise Refused(msg("tweak.upload_shaping.refused.weak", mbps=mbps, samples=samples))
        rise = calibration.rise_ms(measurement)
        if rise < calibration.NOT_NEEDED_MS:
            raise Refused(msg("tweak.upload_shaping.refused.not_needed", rise=max(rise, 0.0)))
        cap = int(mbps * 1e6 * UPLOAD_MARGIN) // UPLOAD_STEP_BPS * UPLOAD_STEP_BPS
        if not UPLOAD_MIN_BPS <= cap <= UPLOAD_MAX_BPS:
            raise Refused(msg("tweak.upload_shaping.refused.bounds", cap=cap / 1e6,
                              low=UPLOAD_MIN_BPS / 1e6, high=UPLOAD_MAX_BPS / 1e6))
        return cap

    def applied_value(self, sys_: System) -> int | None:
        return sys_.qos_policy_get(self.policy)

    def value_matches(self, applied: int | None, value: int) -> bool:
        # Windows may round the rate it stores; 1% is far below the margin taken.
        return applied is not None and abs(applied - value) <= max(8_000, value // 100)

    def describe_value(self, value: int, measurement: dict[str, Any]) -> Message:
        return msg("tweak.upload_shaping.value", limit=value / 1e6, upload=float(measurement["upload_mbps"]))

    def read(self, sys_: System) -> Reading:
        rate = self.applied_value(sys_)
        return Reading(True, rate is not None, None if rate is None else {"limit_mbps": round(rate / 1e6, 1)})

    def capture(self, sys_: System) -> dict[str, Any]:
        return {"policy": self.policy, "rate_bps": self.applied_value(sys_)}

    def apply_value(self, sys_: System, value: int) -> None:
        sys_.qos_policy_set(self.policy, value)

    def restore(self, sys_: System, original: dict[str, Any] | None) -> None:
        if original is None or original["rate_bps"] is None:
            sys_.qos_policy_remove(self.policy)
        else:
            sys_.qos_policy_set(original["policy"], int(original["rate_bps"]))

    def restore_key(self, original: dict[str, Any]) -> Any:
        rate = original["rate_bps"]
        return (original["policy"], None if rate is None else int(rate))


# --- read cache ------------------------------------------------------------------------

_READ_METHODS = ("wifi_adapter_name", "adapter_properties", "adapter_class_key", "registry_get", "power_get",
                 "binding_get", "qos_policy_get")


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
    measured: bool = False       # the value comes from a measurement (ADR-0015)
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
        measurement = entry.get("measurement") if isinstance(entry, dict) and r.enabled else None
        return TweakState(t.id, t.name, t.risk, t.group, r.supported, r.enabled, r.current, r.reason,
                          None if backups is None else t.id in backups, t.needs_admin, t.disrupts_network,
                          t.needs_reboot, t.has_default_restore, error, t.note,
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
            return t.restore_key(t.capture(self.system)) == t.restore_key(to)
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
        """measurement: required by a measured tweak (ADR-0015), ignored by the others."""
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
                problem = msg("tweak.result.apply_failed", error=str(exc))
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
            try:
                r = t.read(self.system)
            except Exception as exc:
                return self._fail(t, msg("tweak.result.read_failed", error=str(exc)))
            try:
                backup = self._load()
            except Exception as exc:
                return self._fail(t, msg("tweak.result.backup_unreadable", error=str(exc)))
            entry = backup.get(t.id)
            if entry is None and not r.enabled:
                return Outcome(True, False, msg("tweak.result.already_off"), self.state(t.id))
            if not r.supported and entry is None:
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
                self._event("tweak_disabled", msg("tweak.event.disabled_default", name=t.name, tweak_id=t.id), "info")
                return Outcome(True, True, msg("tweak.result.restored_default"), self.state(t.id))

            original = entry["original"]
            try:
                t.restore(self.system, original)
                matches = t.restore_key(t.capture(self.system)) == t.restore_key(original)
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
                t.validate_original(original)
            except (KeyError, TypeError, ValueError) as exc:
                return Outcome(False, False, msg("tweak.result.bad_backup", error=str(exc)), None)
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
            elif st.enabled:
                lines.append(msg("tweak.plan.will_default") if st.can_restore_without_backup else
                             msg("tweak.plan.will_refuse"))
            else:
                lines.append(msg("tweak.plan.nothing_off"))
        if st.needs_admin and not self._is_admin():
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


def _name(tweak_id: str) -> Message:
    return msg(f"tweak.{tweak_id}.name")


def _note(tweak_id: str) -> Message:
    return msg(f"tweak.{tweak_id}.note")


GROUP_WIFI, GROUP_POWER, GROUP_STACK, GROUP_MEASURED = (
    msg("tweak.group.wifi_card"), msg("tweak.group.power"), msg("tweak.group.stack"), msg("tweak.group.measured"))


def build_catalog() -> list[Tweak]:
    return [
        # 1. Wi-Fi card (driver advanced properties). Property names vary by chip vendor.
        AdapterPropertyTweak("wifi_power_saving", _name("wifi_power_saving"), "low",
                             [(r"Power Saving", r"Disabled"), (r"MIMO Power Save Mode", r"No SMPS")],
                             group=GROUP_WIFI, note=_note("wifi_power_saving")),
        AdapterPropertyTweak("wifi_wake_magic", _name("wifi_wake_magic"), "low",
                             [(r"Wake on Magic Packet", r"Disabled")], group=GROUP_WIFI,
                             note=_note("wifi_wake_magic")),
        AdapterPropertyTweak("wifi_wake_pattern", _name("wifi_wake_pattern"), "low",
                             [(r"Wake on Pattern Match", r"Disabled")], group=GROUP_WIFI),
        AdapterPropertyTweak("wifi_roaming", _name("wifi_roaming"), "low",
                             [(r"Roaming Aggressiveness", _N + r"Lowest")], group=GROUP_WIFI,
                             note=_note("wifi_roaming")),
        AdapterPropertyTweak("wifi_bw20_5g", _name("wifi_bw20_5g"), "experimental",
                             [(r"5GHz channel bandwidth", _N + r"20MHz only")], group=GROUP_WIFI,
                             note=_note("wifi_bw20_5g")),
        AdapterPropertyTweak("wifi_mode_ac", _name("wifi_mode_ac"), "experimental",
                             [(re.escape("802.11ax/ac/n/abg"), _N + re.escape("802.11ac"))], group=GROUP_WIFI,
                             note=_note("wifi_mode_ac")),
        # 2. Windows power management
        RegistryDwordTweak("device_power_off", _name("device_power_off"), "low", path=_wifi_class_key,
                           value_name="PnPCapabilities", target=lambda cur: (cur or 0) | PNP_NO_POWER_OFF,
                           default_absent=False,  # the driver INF sets a value (16 here); deleting it is a guess
                           group=GROUP_POWER, disrupts_network=True, note=_note("device_power_off")),
        PowerCfgTweak("power_wireless_max", _name("power_wireless_max"), "low",
                      subgroup=SUB_WIRELESS, setting=SET_WIRELESS, ac=0, dc=0, group=GROUP_POWER,
                      note=_note("power_wireless_max")),
        PowerCfgTweak("power_pcie_aspm_off", _name("power_pcie_aspm_off"), "low", subgroup=SUB_PCIE,
                      setting=SET_ASPM, ac=0, dc=0, group=GROUP_POWER, note=_note("power_pcie_aspm_off")),
        # 3. Network stack
        RegistryDwordTweak("tcp_timedwait", _name("tcp_timedwait"), "medium", path=TCPIP_PARAMS,
                           value_name="TcpTimedWaitDelay", target=lambda cur: 30, default_absent=True,
                           group=GROUP_STACK, needs_reboot=True, note=_note("tcp_timedwait")),
        BindingTweak("ipv6_off", _name("ipv6_off"), "experimental", component="ms_tcpip6", enabled=False,
                     group=GROUP_STACK, note=_note("ipv6_off")),
        # 4. Measured tweaks (ADR-0015)
        UploadShapingTweak("upload_shaping", _name("upload_shaping"), "medium", group=GROUP_MEASURED,
                           note=_note("upload_shaping")),
    ]


CATALOG: list[Tweak] = build_catalog()


def enable_measured(mgr: TweakManager, tweak_id: str, measure: Callable[[], dict | None],
                    enable: Callable[[dict], Any], record_event: EventSink) -> dict[str, Any]:
    """Turn a measured tweak on (ADR-0015): measure with it off, refuse before any write (and so
    before any UAC prompt) when there is nothing to fix, enable through `enable(measurement)` (the
    manager directly, or the elevated helper), then measure again and record whether it helped.

    `enable` returns an object with ok / message and optionally changed / cancelled."""
    t = mgr.get(tweak_id)
    if not isinstance(t, MeasuredTweak):
        raise ValueError(f"{tweak_id} is not a measured tweak")
    if mgr.state(tweak_id).enabled:
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
    v = calibration.verdict(before, after)
    key = {True: "helped", False: "no_help", None: "unknown"}[v["helped"]]
    params = dict(name=t.name, before=v["before_ms"], after=v["after_ms"], tweak_id=t.id)
    record_event("tweak_verified", msg(f"tweak.event.verified_{key}", **params), "warn" if key == "no_help" else "info")
    result.update(verdict=v, message=msg("tweak.result.measured_enabled", value=t.describe_value(value, before),
                                         verdict=msg(f"tweak.verdict.{key}", **params)))
    return result


def measure_for(t: MeasuredTweak) -> Callable[[], dict | None]:
    """The real measurement a measured tweak derives its value from (generates traffic)."""
    return {"upload": calibration.measure_upload}[t.measurement_kind]


def default_manager(storage: Any = None) -> TweakManager:
    from .winsys import WindowsSystem

    def record(kind: str, message: Message, level: str) -> None:
        if storage is not None:
            storage.add_event(int(time.time()), kind, message, level=level)

    return TweakManager(WindowsSystem(), CATALOG, record_event=record)


def list_states() -> dict[str, dict[str, Any]] | None:
    """For diagnostics check #10. None while no tweak is declared. No side effects."""
    if not CATALOG:
        return None
    return {s.id: {"risk": s.risk, "enabled": s.enabled, "supported": s.supported}
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
                why = i18n.render(s.reason, args.lang) or s.error
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
