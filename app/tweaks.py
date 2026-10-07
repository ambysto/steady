"""Tweak framework: read / capture / apply / restore with backup, verification and rollback.

See docs/adr/0003-tweak-framework.md. In short:
  enable : read -> check support/admin -> capture original and SAVE backup.json -> apply
           -> read back; on error or no effect, roll back to the pre-apply values
  disable: with a backup, restore it and verify it matches; without one, use a known
           default if the tweak has one, otherwise refuse and leave things alone

Tweaks are declarations built from a few primitive kinds (adapter advanced property,
HKLM DWORD, powercfg AC/DC index, adapter binding, TCP global parameter, adapter RSC, global
offload setting) plus one measured tweak (DnsFastestTweak, ADR-0015). The catalog itself lives
in CATALOG.

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any, Callable

from . import config, dnsprobe, i18n, winutil
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


@dataclass(frozen=True)
class Reading:
    supported: bool
    enabled: bool
    current: Any = None
    reason: Message = ""
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
    property present on this card wins. Values are written as RegistryValue.

    `precondition(sys_)` may return a message saying why the tweak does not suit the machine
    right now (e.g. the connected network has no 5 GHz access point). It only gates turning the
    tweak on: a tweak that already holds its target value is still read as on, so it can be
    turned off."""

    def __init__(self, id: str, name: str, risk: str, candidates: list[tuple[str, str]], *,
                 precondition: Callable[[System], Message | None] | None = None, **kw: Any) -> None:
        kw.setdefault("disrupts_network", True)  # changing a driver property restarts the adapter
        super().__init__(id, name, risk, **kw)
        self.candidates = [(re.compile(dn, re.I), re.compile(tv, re.I)) for dn, tv in candidates]
        self._precondition = precondition

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
            if cur != target and self._precondition is not None:
                why = self._precondition(sys_)
                if why:
                    raise Unsupported(why)
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


class TcpGlobalTweak(Tweak):
    """A `netsh int tcp set global` parameter. `default` is Windows' own value, used to restore when
    there is no backup (None: refuse instead)."""

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


class OffloadGlobalTweak(Tweak):
    """A Set-NetOffloadGlobalSetting parameter. No known default: restoring needs the backup."""

    has_default_restore = False

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
        snapshot = self._snapshot(self._info(sys_, original["guid"]), self._doh(sys_))
        for address, now in snapshot["doh"].items():   # an entry somebody else changed is not ours to compare
            was = original["doh"][address]
            same = (now["present"], now["auto_upgrade"], now["fallback_to_udp"]) == \
                   (was["present"], was["auto_upgrade"], was["fallback_to_udp"])
            if not same and not (now["present"] and _looks_ours(now)) and not (was["present"] and not now["present"]):
                snapshot["doh"][address] = dict(was)
        return snapshot

    def backup_conflict(self, sys_: System, original: dict[str, Any]) -> Message | None:
        info = sys_.dns_interface()
        if info is not None and original.get("guid") and info["guid"] != original["guid"]:
            return msg("tweak.reason.dns_other_interface")
        return None

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
        info = self._info(sys_, original["guid"])   # the interface that was changed, wherever it is now
        wanted = list(original["servers"]) if original["static"] else None
        if (wanted is None and info["static"]) or (wanted is not None and (not info["static"] or info["servers"] != wanted)):
            sys_.dns_servers_set(info["index"], wanted)
        doh = self._doh(sys_)
        for address, was in original["doh"].items():
            now = doh.get(address)
            if was["present"] and now is None:   # the entry vanished: put it back
                sys_.doh_set(address, was["template"] or DOH_ADDRESSES[address][1], bool(was["auto_upgrade"]),
                             bool(was["fallback_to_udp"]))
            elif was["present"]:
                if (now["auto_upgrade"], now["fallback_to_udp"]) != (was["auto_upgrade"], was["fallback_to_udp"]) \
                        and _looks_ours(now):
                    sys_.doh_set(address, None, bool(was["auto_upgrade"]), bool(was["fallback_to_udp"]))
            elif now is not None and _looks_ours(now):   # not there before: apply() added it
                sys_.doh_remove(address)

    def restore_key(self, original: dict[str, Any]) -> Any:
        # DHCP servers come and go with the lease: only "static" and a static list must come back.
        return (original.get("guid"), bool(original["static"]),
                tuple(original["servers"]) if original["static"] else None,
                tuple(sorted((a, bool(d["present"]), bool(d["auto_upgrade"]) if d["present"] else None,
                              bool(d["fallback_to_udp"]) if d["present"] else None)
                             for a, d in original["doh"].items())))


# --- read cache ------------------------------------------------------------------------

_READ_METHODS = ("wifi_adapter_name", "adapter_properties", "adapter_class_key", "registry_get", "power_get",
                 "binding_get", "wifi_ssid_bands", "tcp_global_get", "rsc_get", "offload_global_get", "dns_interface",
                 "doh_get")


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
    warning: Message = ""


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

    def _backup_ids(self) -> set[str] | None:
        try:
            return set(self._load())
        except Exception:
            return None

    def _state(self, t: Tweak, sys_: Any, backups: set[str] | None) -> TweakState:
        try:
            r, error = t.read(sys_), None
        except Exception as exc:
            r, error = Reading(False, False, None, msg("tweak.reason.unreadable")), f"{type(exc).__name__}: {exc}"
        return TweakState(t.id, t.name, t.risk, t.group, r.supported, r.enabled, r.current, r.reason,
                          None if backups is None else t.id in backups, t.needs_admin, t.disrupts_network,
                          t.needs_reboot, t.has_default_restore, error, t.note, r.warning)

    def states(self) -> list[TweakState]:
        """Read-only snapshot of every tweak; reads are shared across tweaks."""
        cached, backups = _CachedReads(self.system), self._backup_ids()
        return [self._state(t, cached, backups) for t in self._tweaks.values()]

    def state(self, tweak_id: str) -> TweakState:
        return self._state(self.get(tweak_id), _CachedReads(self.system), self._backup_ids())

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

    def enable(self, tweak_id: str) -> Outcome:
        t = self.get(tweak_id)
        with self._lock:
            try:
                r = t.read(self.system)
            except Exception as exc:
                return self._fail(t, msg("tweak.result.read_failed", error=str(exc)))
            if not r.supported:
                return Outcome(False, False, msg("tweak.result.unsupported", reason=r.reason), self.state(t.id))
            if r.enabled:
                return Outcome(True, False, msg("tweak.result.already_on"), self.state(t.id))
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
                t.apply(self.system)
                applied = t.read(self.system).enabled
                problem = None if applied else msg("tweak.result.no_effect")
            except Exception as exc:
                problem = msg("tweak.result.apply_failed", error=exc.message if isinstance(exc, _MessageError) else str(exc))
            if problem is None:
                self.last_change_ts = self._clock()
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
                self._event("tweak_disabled", msg("tweak.event.disabled_default", name=t.name, tweak_id=t.id), "info")
                return Outcome(True, True, msg("tweak.result.restored_default"), self.state(t.id))

            original = entry["original"]
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


def _has_5ghz_access_point(sys_: System) -> Message | None:
    """Why "prefer 5 GHz" would do nothing here, or None when the connected network offers 5 GHz."""
    seen = sys_.wifi_ssid_bands()
    if seen is None:
        return msg("tweak.reason.not_connected")
    ssid, bands = seen
    if not bands:
        return msg("tweak.reason.bands_unknown", ssid=ssid)
    if not any(re.sub(r"\s", "", band).lower() == "5ghz" for band in bands):
        return msg("tweak.reason.no_5ghz", ssid=ssid)
    return None


def _name(tweak_id: str) -> Message:
    return msg(f"tweak.{tweak_id}.name")


def _note(tweak_id: str) -> Message:
    return msg(f"tweak.{tweak_id}.note")


GROUP_WIFI, GROUP_POWER, GROUP_STACK = (msg("tweak.group.wifi_card"), msg("tweak.group.power"),
                                        msg("tweak.group.stack"))


def build_catalog(*, dns_benchmark: Benchmark = benchmark_servers, captive: CaptiveCheck = captive_portal) -> list[Tweak]:
    """`dns_benchmark` and `captive` are what dns_fastest asks the network; tests pass fakes."""
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
        AdapterPropertyTweak("wifi_prefer_5g", _name("wifi_prefer_5g"), "low",
                             [(r"Preferred Band|Band Preference", _N + r"Prefer 5\s?GHz(?: band)?")],
                             precondition=_has_5ghz_access_point, group=GROUP_WIFI, note=_note("wifi_prefer_5g")),
        AdapterPropertyTweak("wifi_tx_power_max", _name("wifi_tx_power_max"), "low",
                             [(r"Transmit Power(?: Level)?|Tx Power(?: Level)?", _N + r"Highest")],
                             group=GROUP_WIFI, note=_note("wifi_tx_power_max")),
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
        TcpGlobalTweak("tcp_ecn", _name("tcp_ecn"), "experimental", setting="ecncapability", target="enabled",
                       default="default", group=GROUP_STACK, note=_note("tcp_ecn")),
        RscTweak("rsc_off", _name("rsc_off"), "experimental", group=GROUP_STACK, note=_note("rsc_off")),
        OffloadGlobalTweak("packet_coalescing_off", _name("packet_coalescing_off"), "experimental",
                           setting="PacketCoalescingFilter", target="Disabled", group=GROUP_STACK,
                           note=_note("packet_coalescing_off")),
        DnsFastestTweak("dns_fastest", _name("dns_fastest"), "medium", benchmark=dns_benchmark, captive=captive,
                        group=GROUP_STACK, note=_note("dns_fastest")),
    ]


CATALOG: list[Tweak] = build_catalog()


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
        out = mgr.enable(args.tweak_id) if enable else mgr.disable(args.tweak_id)
        print(i18n.render(out.message, args.lang))
        return 0 if out.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
