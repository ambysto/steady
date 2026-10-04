"""Watchdog: recover from local network failures, within hard safety limits.

    WatchdogPolicy  pure decision logic - every safety limit lives here (ADR-0004)
    Watchdog        runtime inside the monitor process: builds observations, runs actions

See docs/WATCHDOG.md for the behaviour and docs/adr/0004-watchdog-safety.md for why.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config, winutil
from .actions import ActionResult, Actions
from . import i18n
from .i18n import msg, t
from .notify import WATCHDOG_TOASTS

log = logging.getLogger("stableinternet.watchdog")

WIFI_DOWN, ROUTER_UNREACHABLE = "wifi_down", "router_unreachable"
RECONNECT, RECONNECT_FORCE, RESTART = "reconnect", "reconnect_force", "restart_adapter"
LADDER = {WIFI_DOWN: [RECONNECT, RESTART], ROUTER_UNREACHABLE: [RECONNECT_FORCE, RESTART]}
NEEDS_ADMIN = {RESTART}
ACTION_TEXT = {a: msg(f"watchdog.action.{a}") for a in (RECONNECT, RECONNECT_FORCE, RESTART)}
PROBLEM_TEXT = {p: msg(f"watchdog.problem.{p}") for p in (WIFI_DOWN, ROUTER_UNREACHABLE)}
GAP_GRACE_S = 60
TWEAK_GRACE_S = 120
TWEAK_EVENT_KINDS = ("tweak_enabled", "tweak_disabled", "tweak_failed")

Message = Any                     # an i18n.msg() dict (ADR-0006)
Event = tuple[str, Message, str]  # (kind, message, level)


@dataclass(frozen=True)
class Limits:
    threshold_s: float = 15
    cooldown_s: float = 120
    max_per_hour: int = 6
    verify_s: float = 60
    trip_after: int = 3

    @classmethod
    def from_settings(cls, wd: dict[str, Any]) -> "Limits":
        return cls(float(wd.get("threshold_s", 15)), float(wd.get("cooldown_s", 120)),
                   int(wd.get("max_per_hour", 6)), float(wd.get("verify_s", 60)), int(wd.get("trip_after", 3)))


@dataclass(frozen=True)
class Observation:
    now: float
    wifi_state: str | None            # "connected", "disconnected", ...; None = no Wi-Fi adapter
    wifi_since: float | None          # when that state began
    router_down_since: float | None   # open router outage (monitor), None if the router answers
    uplink: str = "wifi"              # "wifi" | "other" (e.g. Ethernet carries the traffic) | "none"
    is_admin: bool = False
    grace_until: float = 0.0          # no action before this (just woke up, a tweak just changed things)
    user_disconnected: bool = False   # the Wi-Fi was disconnected by the user


@dataclass(frozen=True)
class Decision:
    action: str | None = None
    problem: str | None = None
    reason: Message = ""
    events: list[Event] = field(default_factory=list)
    trip: bool = False


@dataclass
class _Incident:
    kind: str
    since: float
    step: int = 0
    acted: bool = False
    last_action: str | None = None
    last_action_ts: float | None = None
    verify_pending: bool = False
    last_skip: Message | None = None


def _secs(seconds: float) -> int:
    return int(seconds)


class WatchdogPolicy:
    """Pure: no clock, no I/O. Feed it one Observation per tick."""

    def __init__(self, limits: Limits = Limits(), action_history: list[float] | None = None,
                 dry_run: bool = False) -> None:
        self.limits, self.dry_run = limits, dry_run
        self.actions: deque[float] = deque(sorted(action_history or []))
        self.consecutive_ineffective = 0
        self.tripped = False
        self.incident: _Incident | None = None

    @staticmethod
    def problem(obs: Observation) -> tuple[str, float] | None:
        if obs.wifi_state is None:
            return None  # no Wi-Fi adapter at all: nothing to recover
        if obs.wifi_state != "connected":
            return WIFI_DOWN, obs.wifi_since if obs.wifi_since is not None else obs.now
        if obs.router_down_since is not None:
            return ROUTER_UNREACHABLE, obs.router_down_since
        return None

    def _actions_last_hour(self, now: float) -> int:
        while self.actions and self.actions[0] <= now - 3600:
            self.actions.popleft()
        return len(self.actions)

    def _skip(self, inc: _Incident, reason: Message, events: list[Event]) -> Decision:
        if inc.last_skip != reason:  # log a reason once per incident, not every second
            inc.last_skip = reason
            events.append(("watchdog_skip", msg("watchdog.event.skip", problem=PROBLEM_TEXT[inc.kind], reason=reason),
                           "info"))
        return Decision(None, inc.kind, reason, events)

    def decide(self, obs: Observation) -> Decision:
        events: list[Event] = []
        if self.tripped:
            return Decision(None, None, msg("watchdog.reason.tripped"), events)
        now, lim = obs.now, self.limits
        prob = self.problem(obs)
        inc = self.incident

        # 1. Judge the previous action.
        if inc is not None and inc.verify_pending:
            if prob is None:
                events.append(("watchdog_recovered",
                               msg("watchdog.event.recovered_after", after=_secs(now - inc.last_action_ts),
                                   action=ACTION_TEXT[inc.last_action], duration=_secs(now - inc.since)), "info"))
                if not self.dry_run:
                    self.consecutive_ineffective = 0
                self.incident = None
                return Decision(None, None, msg("watchdog.reason.recovered"), events)
            if now - inc.last_action_ts >= lim.verify_s:
                inc.verify_pending = False
                if not self.dry_run:
                    self.consecutive_ineffective += 1
                    events.append(("watchdog_ineffective",
                                   msg("watchdog.event.ineffective", action=ACTION_TEXT[inc.last_action],
                                       seconds=_secs(lim.verify_s), count=self.consecutive_ineffective,
                                       limit=lim.trip_after), "warn"))
                    if self.consecutive_ineffective >= lim.trip_after:
                        self.tripped = True
                        events.append(("watchdog_tripped", msg("watchdog.event.tripped", count=lim.trip_after), "bad"))
                        return Decision(None, inc.kind, msg("watchdog.reason.trip"), events, trip=True)

        # 2. No problem: close the incident.
        if prob is None:
            if inc is not None and inc.acted:
                events.append(("watchdog_recovered", msg("watchdog.event.recovered", duration=_secs(now - inc.since)),
                               "info"))
            self.incident = None
            return Decision(None, None, "", events)

        kind, since = prob
        if inc is None:
            inc = self.incident = _Incident(kind, since)
        inc.kind = kind
        duration = now - inc.since
        if duration < lim.threshold_s:
            return Decision(None, kind, msg("watchdog.reason.below_threshold"), events)
        if inc.verify_pending:
            return Decision(None, kind, msg("watchdog.reason.verifying"), events)

        # 3. Conditions under which acting would be wrong.
        if obs.uplink == "other":
            return self._skip(inc, msg("watchdog.reason.other_uplink"), events)
        if now < obs.grace_until:
            return self._skip(inc, msg("watchdog.reason.grace"), events)
        if kind == WIFI_DOWN and obs.user_disconnected:
            return self._skip(inc, msg("watchdog.reason.user_disconnected"), events)

        ladder = LADDER[kind]
        while inc.step < len(ladder) and ladder[inc.step] in NEEDS_ADMIN and not obs.is_admin:
            inc.step += 1
            events.append(("watchdog_skip", msg("watchdog.event.skip_admin", action=ACTION_TEXT[RESTART]), "info"))
        if inc.step >= len(ladder):
            return self._skip(inc, msg("watchdog.reason.ladder_done"), events)

        # 4. Rate limits - checked last so they bound everything above.
        last = self.actions[-1] if self.actions else None
        if last is not None and now - last < lim.cooldown_s:
            return self._skip(inc, msg("watchdog.reason.cooldown", seconds=_secs(lim.cooldown_s)), events)
        if self._actions_last_hour(now) >= lim.max_per_hour:
            return self._skip(inc, msg("watchdog.reason.hourly_cap", count=lim.max_per_hour), events)

        action = ladder[inc.step]
        inc.step += 1
        inc.acted = True
        inc.last_action, inc.last_action_ts, inc.verify_pending, inc.last_skip = action, now, True, None
        self.actions.append(now)
        return Decision(action, kind, msg("watchdog.reason.acting", problem=PROBLEM_TEXT[kind], seconds=_secs(duration)),
                        events)


# --- runtime ---------------------------------------------------------------------------

def uplink_kind(route_ifindex: int | None, adapters: list[dict[str, Any]]) -> str:
    """"other" when the default route goes through another physical adapter that is Up - a cable,
    a phone tethered over USB ("Unspecified" media), a USB 4G modem: the PC is online without the
    Wi-Fi, so resetting it would only cut a link nobody uses. VPN/virtual routes keep counting as
    Wi-Fi (they ride on it); an index not in the list too (the caller re-reads the list)."""
    if route_ifindex is None:
        return "none"
    for a in adapters:
        if a.get("InterfaceIndex") == route_ifindex:
            other = not a.get("Virtual") and not winutil.is_wifi_adapter(a) and a.get("Status") == "Up"
            return "other" if other else "wifi"
    return "wifi"


_REASON_PS = (
    "Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'; Id=8003} "
    "-MaxEvents 1 -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ "
    "ts = [DateTimeOffset]::new($_.TimeCreated).ToUnixTimeSeconds(); "
    "reason = $(if ($_.Message -match 'Reason:\\s*(.+)') { $matches[1].Trim() } else { '' }) } }"
)


def last_disconnect() -> dict[str, Any] | None:
    """Most recent WLAN disconnect event: {ts, reason}, or None."""
    rows = winutil.run_powershell_json(_REASON_PS, timeout=30)
    return rows[0] if rows else None


class Watchdog:
    """Runs inside the monitor process. Re-reads settings every few seconds, so enabling it
    in settings.json (or the UI) takes effect without a restart."""

    SETTINGS_EVERY_S = 10
    UPLINK_EVERY_S = 10
    ADAPTERS_EVERY_S = 300

    def __init__(self, monitor: Any, storage: Any, *, actions: Actions | None = None,
                 clock: Callable[[], float] = time.time,
                 load_settings: Callable[[], dict] = config.load_settings,
                 save_settings: Callable[[dict], None] = config.save_settings,
                 is_admin: Callable[[], bool] = winutil.is_admin,
                 route_fn: Callable[[], dict | None] = winutil.default_route_native,
                 adapters_fn: Callable[[], list[dict]] = winutil.get_adapters,
                 disconnect_fn: Callable[[], dict | None] = last_disconnect,
                 run_async: bool = True, notify: Callable[[str, str], Any] | None = None) -> None:
        self.monitor, self.storage = monitor, storage
        self._notify = notify
        self.actions = actions or Actions()
        self._clock, self._load, self._save = clock, load_settings, save_settings
        self._is_admin, self._route_fn, self._adapters_fn, self._disconnect_fn = (
            is_admin, route_fn, adapters_fn, disconnect_fn)
        self._run_async = run_async
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()          # one action at a time
        self._policy: WatchdogPolicy | None = None
        self._policy_key: tuple | None = None
        self._settings: dict | None = None
        self._next_settings = self._next_uplink = self._next_adapters = 0.0
        self._uplink = "wifi"
        self._unknown_checked: set[int] = set()
        self._adapters: list[dict] = []
        self._admin: bool | None = None
        self._user_disc_for: float | None = None   # incident (wifi_since) the check was made for
        self._user_disc = False

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="watchdog", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("watchdog tick failed")
            self._stop.wait(1.0)

    # -- inputs ----------------------------------------------------------------------

    def _wd_settings(self, now: float) -> dict:
        if self._settings is None or now >= self._next_settings:
            self._settings = self._load()
            self._next_settings = now + self.SETTINGS_EVERY_S
        return self._settings.get("watchdog", {})

    def _policy_for(self, wd: dict, now: float) -> WatchdogPolicy:
        limits, dry = Limits.from_settings(wd), bool(wd.get("dry_run"))
        key = (limits, dry)
        if self._policy is None or key != self._policy_key:
            # Seed the hourly cap from the log so restarts (or crash loops) cannot exceed it.
            history = [e["ts"] for e in self.storage.query_events(since=int(now) - 3600, kinds=["watchdog_action"],
                                                                   limit=1000)]
            self._policy, self._policy_key = WatchdogPolicy(limits, history, dry), key
        return self._policy

    def _read_adapters(self, now: float) -> None:
        try:
            self._adapters = self._adapters_fn()
        except Exception as exc:
            log.warning("adapter list failed: %r", exc)
        self._next_adapters = now + self.ADAPTERS_EVERY_S

    def _refresh_uplink(self, now: float) -> str:
        if now >= self._next_adapters:
            self._read_adapters(now)
        if now >= self._next_uplink:
            try:
                route = self._route_fn()
                index = route["interface_index"] if route else None
                known = {a.get("InterfaceIndex") for a in self._adapters}
                if index is not None and index not in known and index not in self._unknown_checked:
                    # Plugged in since the last read (phone, USB modem)? Once per index: a VPN's
                    # virtual adapter is never in the physical list and must not cost a read every tick.
                    self._unknown_checked.add(index)
                    self._read_adapters(now)
                self._uplink = uplink_kind(index, self._adapters)
            except Exception as exc:
                log.warning("route lookup failed: %r", exc)
            self._next_uplink = now + self.UPLINK_EVERY_S
        return self._uplink

    def _grace_until(self, snap: dict) -> float:
        grace = (snap.get("last_gap_end") or 0) + GAP_GRACE_S
        tweak = self.storage.query_events(kinds=list(TWEAK_EVENT_KINDS), limit=1)
        if tweak:
            grace = max(grace, tweak[0]["ts"] + TWEAK_GRACE_S)
        return grace

    def _user_disconnected(self, wifi_state: str | None, wifi_since: float | None, now: float,
                           threshold: float) -> bool:
        """Asks Windows once per Wi-Fi outage, just before the watchdog could act."""
        if wifi_state in (None, "connected") or wifi_since is None or now - wifi_since < threshold - 2:
            return False
        if self._user_disc_for != wifi_since:
            self._user_disc_for = wifi_since
            try:
                ev = self._disconnect_fn()
                reason = str(ev.get("reason", "")).lower() if isinstance(ev, dict) else ""
                recent = isinstance(ev, dict) and float(ev.get("ts", 0)) >= wifi_since - 30
            except Exception as exc:  # an unreadable log must not wedge the watchdog
                log.warning("disconnect reason lookup failed: %r", exc)
                reason, recent = "", False
            self._user_disc = recent and ("by the user" in reason or "user wants" in reason)
        return self._user_disc

    def observe(self, now: float, threshold: float) -> tuple[Observation, dict]:
        snap = self.monitor.snapshot(window_s=0)
        wifi = snap.get("wifi") or None
        state = wifi.get("state") if wifi else None
        if self._admin is None:
            self._admin = bool(self._is_admin())
        obs = Observation(
            now=now, wifi_state=state, wifi_since=snap.get("wifi_since"),
            router_down_since=snap.get("outages", {}).get("router"),
            uplink=self._refresh_uplink(now), is_admin=self._admin, grace_until=self._grace_until(snap),
            user_disconnected=self._user_disconnected(state, snap.get("wifi_since"), now, threshold))
        return obs, snap

    # -- one tick ------------------------------------------------------------------------

    def _record(self, kind: str, message: Message, level: str) -> None:
        try:
            self.storage.add_event(int(self._clock()), kind, message, level=level)
        except Exception as exc:
            log.warning("cannot record %s: %r", kind, exc)
        if self._notify is not None and kind in WATCHDOG_TOASTS:   # dry_run, skip, recovered stay quiet
            try:
                self._notify(t(WATCHDOG_TOASTS[kind]), i18n.render(message))
            except Exception as exc:
                log.warning("cannot notify %s: %r", kind, exc)

    def tick(self) -> Decision | None:
        now = self._clock()
        wd = self._wd_settings(now)
        if not wd.get("enabled"):
            self._policy = None  # start clean (and re-seeded) when enabled again
            return None
        policy = self._policy_for(wd, now)
        obs, snap = self.observe(now, policy.limits.threshold_s)
        decision = policy.decide(obs)
        for kind, message, level in decision.events:
            self._record(kind, message, level)
        if decision.trip:
            self._trip(now)
        if decision.action:
            self._perform(decision, snap, bool(wd.get("dry_run")))
        return decision

    def _trip(self, now: float) -> None:
        settings = self._load()
        settings.setdefault("watchdog", {}).update(enabled=False, tripped_at=int(now))
        try:
            self._save(settings)
        except Exception as exc:
            self._record("watchdog_error", msg("watchdog.event.trip_save_failed", error=str(exc)), "bad")
        self._settings = None  # re-read on the next tick

    def _perform(self, decision: Decision, snap: dict, dry_run: bool) -> None:
        wifi = snap.get("wifi") or {}
        interface = wifi.get("interface") or ""
        profile = wifi.get("profile") or snap.get("last_profile") or ""
        what = dict(action=ACTION_TEXT[decision.action], reason=decision.reason)
        if dry_run:
            self._record("watchdog_dry_run", msg("watchdog.event.dry_run", **what), "info")
            return
        self._record("watchdog_action", msg("watchdog.event.action", **what), "warn")

        def run() -> None:
            with self._busy:
                result = self._execute(decision.action, interface, profile)
            if not result.ok:
                self._record("watchdog_error", msg("watchdog.event.failed", action=ACTION_TEXT[decision.action],
                                                  error=result.message), "bad")

        if self._run_async:
            threading.Thread(target=run, name="watchdog-action", daemon=True).start()
        else:
            run()

    def _execute(self, action: str, interface: str, profile: str) -> ActionResult:
        try:
            if action in (RECONNECT, RECONNECT_FORCE):
                if not profile:
                    return ActionResult(False, msg("watchdog.error.no_profile"))
                return self.actions.reconnect(interface, profile, force=action == RECONNECT_FORCE)
            if action == RESTART:
                return self.actions.restart_adapter(interface)
        except Exception as exc:  # bad names, unexpected errors: report, never crash the monitor
            return ActionResult(False, f"{type(exc).__name__}: {exc}")
        return ActionResult(False, f"unknown action {action}")
