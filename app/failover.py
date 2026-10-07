"""Failover to a backup network path (ADR-0008). Off by default.

    Path            one way out to the Internet (a physical adapter with an IPv4 default route)
    probe_path      can this path reach the Internet? (TCP connect bound to the path's address)
    FailoverPolicy  pure decision logic and every safety limit
    MetricSwitch    the only writer: interface metric, backed up in backup.json first
    Failover        runtime inside the monitor process

Which path is "preferred" right now is not kept in memory: it is the backup.json entry
`failover:<ifIndex>`. A restart, a crash or a switch made by the elevated helper (UAC) can
therefore never leave the policy out of step with the machine.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config, i18n, winutil
from .i18n import msg

log = logging.getLogger("stableinternet.failover")

BACKUP_PREFIX = "failover:"
PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 443))
Message = Any
Event = tuple[str, Message, str]   # (kind, message, level)


# --- paths ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Path:
    index: int
    name: str
    gateway: str
    ipv4: str
    route_metric: int
    interface_metric: int
    automatic: bool
    kind: str            # "wifi" | "ethernet" | "other"

    @property
    def effective(self) -> int:
        return self.route_metric + self.interface_metric

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Path":
        media = str(row.get("media") or "")
        kind = "wifi" if "802.11" in media else "ethernet" if "802.3" in media else "other"
        return cls(int(row["interface_index"]), str(row.get("name") or ""), str(row.get("gateway") or ""),
                   str(row.get("ipv4") or ""), int(row.get("route_metric") or 0), int(row.get("interface_metric") or 0),
                   bool(row.get("automatic_metric")), kind)


def usable_paths(rows: list[dict[str, Any]]) -> list[Path]:
    """Physical adapters that are up and have an address: VPNs ride on another path, skip them."""
    out = []
    for row in rows or []:
        if row.get("virtual") or str(row.get("status") or "Up") != "Up" or not row.get("ipv4"):
            continue
        try:
            out.append(Path.from_row(row))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda p: p.effective)


WSAEADDRNOTAVAIL = 10049


def probe_path(ipv4: str, targets: tuple = PROBE_TARGETS, timeout: float = 2.0,
               connect: Callable[..., Any] = socket.create_connection) -> bool | None:
    """True if a TCP connection from this address reaches any target. Windows sends with the
    strong host model, so a socket bound to an interface's address leaves through that interface.
    None: the address is no longer this machine's (DHCP gave a new one) - unknown, not down."""
    for host, port in targets:
        try:
            with connect((host, port), timeout=timeout, source_address=(ipv4, 0)):
                return True
        except OSError as exc:
            if getattr(exc, "winerror", None) == WSAEADDRNOTAVAIL:
                return None
            continue
    return False


# --- policy ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Limits:
    threshold_s: float = 20        # main path down this long before switching
    backup_healthy_s: float = 10   # the backup must have been working this long
    failback_s: float = 120        # main path healthy this long before switching back
    cooldown_s: float = 60         # between two switches
    max_per_hour: int = 6
    flap_limit: int = 3            # switches within flap_window_s -> trip
    flap_window_s: float = 1800

    @classmethod
    def from_settings(cls, fo: dict[str, Any]) -> "Limits":
        return cls(float(fo.get("threshold_s", 20)), 10.0, float(fo.get("failback_s", 120)),
                   float(fo.get("cooldown_s", 60)), int(fo.get("max_per_hour", 6)))


@dataclass(frozen=True)
class Observation:
    now: float
    paths: list[Path]
    healthy: dict[int, bool | None]    # index -> latest probe result (None: unknown)
    primary: int | None                # interface Windows routes through right now
    preferred: int | None = None       # backup we promoted (from backup.json), if any
    home: int | None = None            # the main path we switched away from (stored with the backup)
    manual: bool = False               # the user chose the backup path while failover was off


@dataclass(frozen=True)
class Decision:
    action: str | None = None          # "prefer" | "restore"
    target: int | None = None
    home: int | None = None
    events: list[Event] = field(default_factory=list)
    trip: bool = False
    quiet: bool = False                # no incident: the main path works and nothing is switched


class FailoverPolicy:
    """Pure: no clock, no I/O. One Observation per tick."""

    def __init__(self, limits: Limits = Limits()) -> None:
        self.limits = limits
        self._state: dict[int, tuple[bool, float]] = {}   # index -> (healthy, since)
        self.switches: deque[float] = deque()
        self.tripped = False
        self._noted: set[str] = set()                      # incident-scoped "said it once" flags

    def _track(self, obs: Observation) -> None:
        for p in obs.paths:
            if obs.healthy.get(p.index, False) is None:
                continue                                    # unknown: no new information
            ok = bool(obs.healthy.get(p.index))
            prev = self._state.get(p.index)
            if prev is None or prev[0] != ok:
                self._state[p.index] = (ok, obs.now)
        for gone in set(self._state) - {p.index for p in obs.paths}:
            del self._state[gone]

    def forget_health(self) -> None:
        """After sleep or a stall: durations measured across the gap mean nothing."""
        self._state.clear()

    def healthy_for(self, index: int | None, now: float) -> float:
        st = self._state.get(index) if index is not None else None
        return now - st[1] if st and st[0] else 0.0

    def down_for(self, index: int | None, now: float) -> float:
        st = self._state.get(index) if index is not None else None
        return now - st[1] if st and not st[0] else 0.0

    def _note(self, key: str, event: Event, events: list[Event]) -> None:
        if key not in self._noted:
            self._noted.add(key)
            events.append(event)

    def _name(self, obs: Observation, index: int | None) -> str:
        return next((p.name for p in obs.paths if p.index == index), str(index))

    def decide(self, obs: Observation) -> Decision:
        self._track(obs)
        now, lim, events = obs.now, self.limits, []
        while self.switches and self.switches[0] <= now - 3600:
            self.switches.popleft()

        if obs.preferred is not None:                       # running on a backup path
            home = obs.home if obs.home is not None else obs.primary
            present = {p.index for p in obs.paths}
            home_back = home in present and self.healthy_for(home, now) >= lim.failback_s
            backup_dead = obs.preferred not in present or self.down_for(obs.preferred, now) >= lim.threshold_s
            if obs.manual:
                home_back = False                           # the user's choice: only undone if it breaks
            if home_back or (backup_dead and home in present and self.healthy_for(home, now) >= lim.backup_healthy_s):
                self._noted.clear()
                return Decision("restore", obs.preferred, home, events)
            return Decision(None, events=events)

        self._noted.discard("restored")
        primary = obs.primary
        if primary is None or self.down_for(primary, now) < lim.threshold_s:
            self._noted = {k for k in self._noted if not k.startswith("incident")}
            return Decision(None, events=events, quiet=self.down_for(primary, now) == 0)

        backups = sorted((p for p in obs.paths if p.index != primary and self.healthy_for(p.index, now) >= lim.backup_healthy_s),
                         key=lambda p: p.effective)
        if not backups:
            self._note("incident:no_backup", ("failover_no_backup", msg("failover.event.no_backup",
                                                                         path=self._name(obs, primary)), "info"), events)
            return Decision(None, events=events)
        if self.tripped:
            return Decision(None, events=events)
        if self.switches and now - self.switches[-1] < lim.cooldown_s:
            return Decision(None, events=events)
        if len(self.switches) >= lim.max_per_hour:
            self._note("incident:cap", ("failover_skip", msg("failover.event.hourly_cap", count=lim.max_per_hour), "info"),
                       events)
            return Decision(None, events=events)
        recent = [s for s in self.switches if s > now - lim.flap_window_s]
        if len(recent) >= lim.flap_limit:
            self.tripped = True
            events.append(("failover_tripped", msg("failover.event.tripped", count=len(recent)), "bad"))
            return Decision(None, events=events, trip=True)
        backup = backups[0]                                  # lowest metric among the healthy ones
        return Decision("prefer", backup.index, primary, events)

    def performed(self, now: float) -> None:
        """A switch really happened (or was simulated in dry run): it counts toward every limit.
        Only offering it to the user (no Admin rights) does not."""
        self.switches.append(now)


# --- the writer --------------------------------------------------------------------------------

def preferred_entry(backup: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    """(preferred backup path, its backup.json entry), or (None, {})."""
    for key, entry in (backup or {}).items():
        if key.startswith(BACKUP_PREFIX) and isinstance(entry, dict):
            try:
                return int(key[len(BACKUP_PREFIX):]), entry
            except ValueError:
                continue
    return None, {}


def preferred_from_backup(backup: dict[str, Any]) -> tuple[int | None, int | None]:
    """(preferred backup path, home path) recorded in backup.json, or (None, None)."""
    index, entry = preferred_entry(backup)
    return index, entry.get("home")


def in_use_refusal(index: int, route: dict | None, name: str | None = None) -> Message | None:
    """A switch the user asked for, to the path Windows routes through right now, would only
    lower that path's metric further (seen in a real test: the button was drawn from a stale state).
    The route is read at click time, never taken from what the page showed."""
    if route and int(route.get("interface_index", -1)) == int(index):
        return msg("failover.result.in_use", path=name or str(index))
    return None


def record_user_switch(storage: Any, action: str, res: "SwitchResult", clock: Callable[[], float] = time.time) -> None:
    """Switches the user made (button, UAC) belong in the log like the automatic ones."""
    if res.ok and i18n.is_message(res.message) and res.message.get("key") == "failover.result.nothing":
        return
    kind = ("failover_switched" if action == "prefer" else "failover_restored") if res.ok else "failover_error"
    level = ("warn" if action == "prefer" else "info") if res.ok else "bad"
    try:
        storage.add_event(int(clock()), kind, res.message, level=level)
    except Exception as exc:
        log.warning("cannot record %s: %r", kind, exc)


def user_source(load_settings: Callable[[], dict] = config.load_settings) -> str:
    """Who owns a switch the user asked for: failover (it switches back by itself) when it is
    on, else the user (kept until they switch back, or the path breaks)."""
    try:
        return "failover" if load_settings().get("failover", {}).get("enabled") else "manual"
    except Exception:
        return "manual"


def check_metric_original(index: Any, entry: Any) -> dict[str, Any]:
    """The original metric of a backup.json entry, refused (ValueError) unless it is one prefer() could have
    recorded: backup.json is writable without Admin, the helper that restores it is not (ADR-0017)."""
    if type(index) is not int or index < 1:
        raise ValueError(f"interface {index!r} is not an index")
    if not isinstance(entry, dict):
        raise ValueError("the backup entry is not an object")
    original = entry.get("original") or {"automatic": True}
    if not isinstance(original, dict) or not isinstance(original.get("automatic"), bool):
        raise ValueError(f"{original!r} is not an interface metric")
    metric = original.get("metric")
    if not original["automatic"] and not (type(metric) is int and 1 <= metric <= 9999):
        raise ValueError(f"metric {metric!r} is outside 1..9999")
    return original


@dataclass(frozen=True)
class SwitchResult:
    ok: bool
    message: Message


class MetricSwitch:
    """Promote a backup path by lowering its interface metric; restore the original later.
    Backup first, verify by reading back, roll back on failure (ADR-0003, ADR-0008)."""

    def __init__(self, system: Any, *, load_backup: Callable[[], dict] = config.load_backup,
                 save_backup: Callable[[dict], None] = config.save_backup, clock: Callable[[], float] = time.time) -> None:
        self.system, self._load, self._save, self._clock = system, load_backup, save_backup, clock

    @staticmethod
    def target_metric(backup: Path, home: Path | None) -> int:
        """Low enough that the backup's route + interface metric beats the home path's."""
        if home is None:
            return 1
        return max(1, min(9999, home.effective - backup.route_metric - 1))

    def prefer(self, backup: Path, home: Path | None, source: str = "failover") -> SwitchResult:
        key = f"{BACKUP_PREFIX}{backup.index}"
        try:
            original = self.system.interface_metric_get(backup.index)
            with config.backup_lock():        # check and claim in one step: one switch at a time
                store = self._load()
                if any(k.startswith(BACKUP_PREFIX) for k in store):
                    return SwitchResult(False, msg("failover.result.already"))
                store[key] = {"original": original, "home": home.index if home else None, "name": backup.name,
                              "captured_at": int(self._clock()), "source": source}
                self._save(store)          # backup before touching anything
        except Exception as exc:
            return SwitchResult(False, msg("failover.result.backup_failed", error=str(exc)))
        metric = self.target_metric(backup, home)
        try:
            self.system.interface_metric_set(backup.index, metric)
            now = self.system.interface_metric_get(backup.index)
            if now["automatic"] or now["metric"] != metric:
                raise RuntimeError(f"read back {now}")
        except Exception as exc:
            rolled = self.restore(backup.index)
            return SwitchResult(False, msg("failover.result.apply_failed", error=str(exc),
                                           rollback=msg("failover.result.rolled_back" if rolled.ok
                                                        else "failover.result.rollback_failed")))
        return SwitchResult(True, msg("failover.result.switched", path=backup.name))

    def restore(self, index: int) -> SwitchResult:
        key = f"{BACKUP_PREFIX}{index}"
        try:
            store = self._load()
        except Exception as exc:
            return SwitchResult(False, msg("failover.result.backup_failed", error=str(exc)))
        entry = store.get(key)
        if not entry:
            return SwitchResult(True, msg("failover.result.nothing"))
        try:
            original = check_metric_original(index, entry)
        except ValueError as exc:
            return SwitchResult(False, msg("failover.result.backup_rejected", error=str(exc)))
        try:
            self.system.interface_metric_set(index, None if original.get("automatic") else int(original["metric"]))
            now = self.system.interface_metric_get(index)
            if bool(now["automatic"]) != bool(original.get("automatic")) or (
                    not original.get("automatic") and now["metric"] != int(original["metric"])):
                raise RuntimeError(f"read back {now}, expected {original}")
        except Exception as exc:
            return SwitchResult(False, msg("failover.result.restore_failed", error=str(exc)))
        try:
            config.put_backup_entry(key, None, load=self._load, save=self._save)
        except Exception as exc:
            return SwitchResult(False, msg("failover.result.backup_failed", error=str(exc)))
        return SwitchResult(True, msg("failover.result.restored", path=entry.get("name") or index))

    def restore_all(self) -> list[SwitchResult]:
        try:
            keys = [k for k in self._load() if k.startswith(BACKUP_PREFIX)]
        except Exception as exc:
            return [SwitchResult(False, msg("failover.result.backup_failed", error=str(exc)))]
        return [self.restore(int(k[len(BACKUP_PREFIX):])) for k in keys if k[len(BACKUP_PREFIX):].isdigit()]


# --- runtime ---------------------------------------------------------------------------------

TOASTS = {"failover_switched": "notify.failover.switched.title", "failover_available": "notify.failover.available.title",
          "failover_failback_available": "notify.failover.failback.title", "failover_tripped": "notify.failover.tripped.title",
          "failover_error": "notify.failover.error.title"}


class Failover:
    """Runs inside the monitor process; re-reads settings every few seconds (like the watchdog)."""

    PATHS_EVERY_S = 60
    PROBE_EVERY_S = 5
    SETTINGS_EVERY_S = 10
    RETRY_S = (60, 300, 900)       # after a failed switch: wait, then try again (said once per incident)
    GAP_S = 30                     # ticks further apart than this: the machine slept

    def __init__(self, storage: Any, *, clock: Callable[[], float] = time.time,
                 load_settings: Callable[[], dict] = config.load_settings,
                 save_settings: Callable[[dict], None] = config.save_settings,
                 load_backup: Callable[[], dict] = config.load_backup,
                 paths_fn: Callable[[], list[dict]] = winutil.get_paths,
                 route_fn: Callable[[], dict | None] = winutil.default_route_native,
                 probe_fn: Callable[[str], bool] = probe_path,
                 is_admin: Callable[[], bool] = winutil.is_admin,
                 switch: MetricSwitch | None = None,
                 notify: Callable[[str, str], Any] | None = None) -> None:
        self.storage = storage
        self._clock, self._load, self._save, self._load_backup = clock, load_settings, save_settings, load_backup
        self._paths_fn, self._route_fn, self._probe_fn, self._is_admin = paths_fn, route_fn, probe_fn, is_admin
        self._switch = switch
        self._notify = notify
        self.policy: FailoverPolicy | None = None
        self._policy_key: Limits | None = None
        self.paths: list[Path] = []
        self.healthy: dict[int, bool] = {}
        self.primary: int | None = None
        self._next_paths = self._next_settings = 0.0
        self._settings: dict | None = None
        self._admin: bool | None = None
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="path-probe")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._offered: tuple[str, int | None] | None = None   # what the user was last told about
        self._failures = 0
        self._retry_at: float | None = None
        self._last_tick: float | None = None

    @property
    def switch(self) -> MetricSwitch:
        if self._switch is None:
            from .winsys import WindowsSystem
            self._switch = MetricSwitch(WindowsSystem(), load_backup=self._load_backup)
        return self._switch

    # -- lifecycle ------------------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="failover", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("failover tick failed")
            self._stop.wait(self.PROBE_EVERY_S)

    # -- state for the API ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """For the API. Paths are discovered even while failover is off (at most once a minute),
        so the UI can say whether a backup exists; health is only known while it runs."""
        self.refresh_paths(self._clock())
        if self.policy is None:            # not running: ask Windows which path it uses (native, cheap)
            try:
                route = self._route_fn()
                self.primary = route["interface_index"] if route else None
            except Exception:
                pass
        with self._lock:
            preferred, entry = self._preferred_entry()
            home = entry.get("home")
            policy = self.policy
            return {"paths": [{"index": p.index, "name": p.name, "kind": p.kind, "gateway": p.gateway,
                               "metric": p.effective, "healthy": self.healthy.get(p.index),
                               "primary": p.index == self.primary, "preferred": p.index == preferred} for p in self.paths],
                    "primary": self.primary, "preferred": preferred, "home": home,
                    "preferred_name": entry.get("name"),     # shown even when that adapter is gone
                    "tripped": bool(policy and policy.tripped)}

    def _preferred(self) -> tuple[int | None, int | None]:
        index, entry = self._preferred_entry()
        return index, entry.get("home")

    def _preferred_entry(self) -> tuple[int | None, dict[str, Any]]:
        try:
            return preferred_entry(self._load_backup())
        except Exception:
            return None, {}

    # -- one tick ---------------------------------------------------------------------------
    def _settings_now(self, now: float) -> dict:
        if self._settings is None or now >= self._next_settings:
            self._settings = self._load()
            self._next_settings = now + self.SETTINGS_EVERY_S
        return self._settings.get("failover", {})

    def refresh_paths(self, now: float) -> None:
        if now < self._next_paths:
            return
        self._next_paths = now + self.PATHS_EVERY_S
        try:
            paths = usable_paths(self._paths_fn())
        except Exception as exc:
            log.warning("path discovery failed: %r", exc)
            return
        with self._lock:
            self.paths = paths

    def probe_all(self) -> None:
        paths = list(self.paths)
        results = dict(zip([p.index for p in paths], self._pool.map(lambda p: self._probe_fn(p.ipv4), paths)))
        if any(r is None for r in results.values()):
            self._next_paths = 0.0             # an address changed: rediscover on the next tick
        route = None
        try:
            route = self._route_fn()
        except Exception as exc:
            log.warning("route lookup failed: %r", exc)
        with self._lock:
            self.healthy = results
            self.primary = route["interface_index"] if route else None

    def _record(self, kind: str, message: Message, level: str) -> None:
        try:
            self.storage.add_event(int(self._clock()), kind, message, level=level)
        except Exception as exc:
            log.warning("cannot record %s: %r", kind, exc)
        if self._notify is not None and kind in TOASTS:
            try:
                self._notify(i18n.t(TOASTS[kind]), i18n.render(message))
            except Exception as exc:
                log.warning("cannot notify %s: %r", kind, exc)

    def tick(self) -> Decision | None:
        now = self._clock()
        slept = self._last_tick is not None and now - self._last_tick > self.GAP_S
        self._last_tick = now
        fo = self._settings_now(now)
        preferred, entry = self._preferred_entry()
        home, manual = entry.get("home"), entry.get("source") == "manual"
        if not fo.get("enabled"):
            self.policy = None
            if preferred is not None and not manual:   # turned off while on a backup path: put things back
                self._apply("restore", preferred, home, dry_run=False)
            return None
        limits = Limits.from_settings(fo)
        if self.policy is None or limits != self._policy_key:
            self.policy, self._policy_key = FailoverPolicy(limits), limits
        elif slept:
            self.policy.forget_health()
        self.refresh_paths(now)
        self.probe_all()
        decision = self.policy.decide(Observation(now, list(self.paths), dict(self.healthy), self.primary, preferred,
                                                  home, manual))
        for kind, message, level in decision.events:
            self._record(kind, message, level)
        if decision.trip:
            settings = self._load()
            settings.setdefault("failover", {}).update(enabled=False, tripped_at=int(now))
            try:
                self._save(settings)
            except Exception as exc:
                log.warning("cannot save the trip: %r", exc)
            self._settings = None
        if decision.action:
            self._apply(decision.action, decision.target, decision.home, dry_run=bool(fo.get("dry_run")))
        elif decision.quiet:
            self._offered = None       # the incident is over: a new one may be offered again
            self._failures, self._retry_at = 0, None
        return decision

    def _path(self, index: int | None) -> Path | None:
        return next((p for p in self.paths if p.index == index), None)

    def _apply(self, action: str, target: int | None, home: int | None, dry_run: bool) -> None:
        backup, home_path = self._path(target), self._path(home)
        name = backup.name if backup else str(target)
        now = self._clock()
        retrying = self._offered == (action, target)
        if retrying and (self._retry_at is None or now < self._retry_at):
            return              # already said / already simulated / waiting to retry a failed switch
        self._offered, self._retry_at = (action, target), None
        if dry_run:
            self._record("failover_dry_run", msg(f"failover.event.dry_run_{action}", path=name), "info")
            if action == "prefer" and self.policy is not None:
                self.policy.performed(self._clock())
            return
        if self._admin is None:
            self._admin = bool(self._is_admin())
        if not self._admin:     # never raise a UAC prompt nobody asked for: tell the user instead
            kind = "failover_available" if action == "prefer" else "failover_failback_available"
            self._record(kind, msg(f"failover.event.{'available' if action == 'prefer' else 'failback_available'}",
                                   path=name, home=home_path.name if home_path else ""), "warn")
            return
        if action == "prefer" and backup is not None:
            res = self.switch.prefer(backup, home_path, source="failover")
            kind = "failover_switched" if res.ok else "failover_error"
            if res.ok and self.policy is not None:
                self.policy.performed(self._clock())
        else:
            res = self.switch.restore(target)
            kind = "failover_restored" if res.ok else "failover_error"
        if not res.ok:
            # Try again later, not every tick (a missing adapter, a policy that blocks the change...).
            self._retry_at = now + self.RETRY_S[min(self._failures, len(self.RETRY_S) - 1)]
            self._failures += 1
            if retrying:
                log.warning("failover %s %s failed again: %s", action, target, i18n.render(res.message))
                return          # the user was told the first time
        else:
            self._failures = 0
        self._record(kind, res.message, "warn" if res.ok and action == "prefer" else "info" if res.ok else "bad")
