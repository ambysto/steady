"""Continuous network monitor: pings router + Internet targets, polls Wi-Fi, detects
outages, aggregates per minute into Storage.

Layout:
  OutageTracker     pure logic, classifies outages from per-tick results
  MinuteAggregator  pure logic, folds samples into per-minute rows
  Monitor           threads + I/O; every external call is injectable for tests

Read-only: it only sends ICMP, runs `netsh wlan show interfaces` and reads routes.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config, dnswatch, icmp, winutil
from .dnswatch import KINDS as DNS_EVENT_KINDS, DnsWatch, network_key
from .i18n import msg, render, t
from .notify import Notifier, OutageNotifier
from .probe import run_probe
from .singleton import SingleInstance
from .storage import Storage

log = logging.getLogger("stableinternet.monitor")

OUTAGE_THRESHOLD = 3        # consecutive failed ticks before an outage is declared
ROUTE_POLL_S = 2            # how often the default route is compared (a native call, no process)
MERGE_WINDOW_S = 60         # Internet drops closer together than this are one unstable episode (ADR-0014)
GAP_MIN_S = 30              # a pause between ticks longer than this is a monitoring gap (sleep/hang)
RAW_WINDOW_S = 15 * 60      # raw ping samples kept in RAM
PURGE_EVERY_S = 3600
ROUTER = "router"


@dataclass(frozen=True)
class MonitorEvent:
    ts: int
    kind: str
    message: Any          # plain text, or an i18n.msg() dict for prose (ADR-0006)
    duration: float | None = None


# --- outage classification -----------------------------------------------------

class OutageTracker:
    """Declares an outage after `threshold` consecutive failures and closes it on the
    first success. Events are emitted when an outage *closes*, with ts = recovery time
    and duration measured from the first failed tick.

    router_down    : PC could not reach the router  (local link / Wi-Fi / adapter)
    internet_down  : no Internet target answered; message says whether the router
                     was also failing (then it is the local link) or not (ISP side)

    An Internet outage that closes is held back for `merge_window_s`: if another one starts
    within that time the two are one unstable episode, reported as a single event that spans
    both (ts = last recovery, duration from the first drop) and counts the drops. `active()`
    is not affected - it always reflects what is down right now (toasts, watchdog, tray).
    """

    def __init__(self, threshold: int = OUTAGE_THRESHOLD, merge_window_s: float = MERGE_WINDOW_S) -> None:
        self.threshold = threshold
        self.merge_window_s = merge_window_s
        self._held: dict[str, Any] | None = None   # closed Internet outage(s) waiting to be reported
        self._streak = {"router": 0, "internet": 0}
        self._first_fail: dict[str, float | None] = {"router": None, "internet": None}
        self._outage_start: dict[str, float | None] = {"router": None, "internet": None}
        self._internet_overlap = False
        self._router_ip: str | None = None

    def active(self) -> dict[str, float]:
        """Outages currently open: kind -> start timestamp."""
        return {k: v for k, v in self._outage_start.items() if v is not None}

    def _router_message(self, cut: bool = False) -> Any:
        return msg("event.router_down" + (".cut" if cut else ""), router=self._router_ip or "?")

    def _internet_message(self, cut: bool = False, overlap: bool | None = None, drops: int = 1) -> Any:
        local = self._internet_overlap if overlap is None else overlap
        key = "event.internet_down.local" if local else "event.internet_down.wan"
        if cut:
            return msg(key + ".cut")
        if drops > 1:
            return msg(key + ".unstable", drops=drops)
        return msg(key)

    def _held_event(self, cut: bool = False) -> MonitorEvent:
        held = self._held
        assert held is not None
        self._held = None
        return MonitorEvent(int(held["end"]), "internet_down",
                            self._internet_message(cut=cut, overlap=held["overlap"], drops=held["drops"]),
                            round(held["end"] - held["start"]))

    def flush(self, ts: float) -> list[MonitorEvent]:
        """Report the held Internet outage once nothing more can merge into it (also on shutdown)."""
        if self._held is None:
            return []
        return [self._held_event()]

    def interrupt(self, last_ts: float) -> list[MonitorEvent]:
        """Monitoring stopped seeing the network after `last_ts` (sleep, hang, paused process).

        Open outages are closed at `last_ts` - what happened during the gap is unknown, so
        it must not be counted as downtime - and all streaks restart from zero.
        """
        events = []
        if self._outage_start["router"] is not None:
            events.append(MonitorEvent(int(last_ts), "router_down", self._router_message(cut=True),
                                       round(last_ts - self._outage_start["router"])))
        if self._outage_start["internet"] is not None:
            start = self._outage_start["internet"]
            if self._held is not None:   # the open drop continues an earlier one: one event for both
                start = self._held["start"]
                self._held = None
            events.append(MonitorEvent(int(last_ts), "internet_down", self._internet_message(cut=True),
                                       round(last_ts - start)))
        elif self._held is not None:
            events.append(self._held_event())
        self._streak = {"router": 0, "internet": 0}
        self._first_fail = {"router": None, "internet": None}
        self._outage_start = {"router": None, "internet": None}
        self._internet_overlap = False
        return events

    def update(self, ts: float, router_ok: bool, internet_ok: bool, router_ip: str | None = None) -> list[MonitorEvent]:
        events: list[MonitorEvent] = []
        if router_ip:
            self._router_ip = router_ip

        # Router first: the Internet outage classification below reads its streak.
        if router_ok:
            start = self._outage_start["router"]
            if start is not None:
                events.append(MonitorEvent(int(ts), "router_down", self._router_message(), round(ts - start)))
                self._outage_start["router"] = None
            self._streak["router"] = 0
            self._first_fail["router"] = None
        else:
            if self._streak["router"] == 0:
                self._first_fail["router"] = ts
            self._streak["router"] += 1
            if self._streak["router"] == self.threshold:
                self._outage_start["router"] = self._first_fail["router"]

        if internet_ok:
            start = self._outage_start["internet"]
            if start is not None:
                held = self._held
                self._held = {
                    "start": held["start"] if held else start,
                    "end": ts,
                    "drops": (held["drops"] if held else 0) + 1,
                    "overlap": self._internet_overlap or bool(held and held["overlap"]),
                }
                self._outage_start["internet"] = None
            self._streak["internet"] = 0
            self._first_fail["internet"] = None
        else:
            if self._streak["internet"] == 0:
                self._first_fail["internet"] = ts
            self._streak["internet"] += 1
            if self._streak["internet"] == self.threshold:
                first = self._first_fail["internet"]
                if self._held is not None and first - self._held["end"] > self.merge_window_s:
                    events.append(self._held_event())   # too far apart: a separate episode
                self._outage_start["internet"] = first
                self._internet_overlap = False
            if self._outage_start["internet"] is not None and self._streak["router"] > 0:
                self._internet_overlap = True
        if (self._held is not None and self._outage_start["internet"] is None
                and ts - self._held["end"] > self.merge_window_s):
            events.append(self._held_event())
        return events


# --- per-minute aggregation ----------------------------------------------------

@dataclass
class _Bucket:
    ip: str | None = None
    sent: int = 0
    lost: int = 0
    rtts: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class MinuteBatch:
    minute_ts: int
    rows: list[dict[str, Any]]      # one per target: target, ip, sent, lost, avg, max, jitter
    signal_avg: float | None


def _round1(x: float) -> float:
    return round(x, 1)


class MinuteAggregator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._minute: int | None = None
        self._buckets: dict[str, _Bucket] = {}
        self._signals: list[float] = []

    @staticmethod
    def _minute_of(ts: float) -> int:
        return int(ts // 60) * 60

    def roll(self, ts: float) -> MinuteBatch | None:
        """If ts is past the current minute, close it and return its batch."""
        with self._lock:
            minute = self._minute_of(ts)
            if self._minute is None:
                self._minute = minute
                return None
            if minute <= self._minute:
                return None
            batch = self._close_locked()
            self._minute = minute
            return batch

    def add_ping(self, target: str, ip: str | None, rtt_ms: float | None) -> None:
        with self._lock:
            b = self._buckets.setdefault(target, _Bucket())
            b.ip = ip
            b.sent += 1
            if rtt_ms is None:
                b.lost += 1
            else:
                b.rtts.append(rtt_ms)

    def add_signal(self, percent: float) -> None:
        with self._lock:
            self._signals.append(percent)

    def flush(self) -> MinuteBatch | None:
        """Close the current (possibly partial) minute, e.g. on shutdown."""
        with self._lock:
            if self._minute is None:
                return None
            batch = self._close_locked()
            self._minute = None
            return batch

    def _close_locked(self) -> MinuteBatch | None:
        rows = []
        for target, b in self._buckets.items():
            r = b.rtts
            avg = _round1(sum(r) / len(r)) if r else None
            mx = max(r) if r else None
            jitter = (_round1(sum(abs(r[i] - r[i - 1]) for i in range(1, len(r))) / (len(r) - 1))
                      if len(r) > 1 else None)
            rows.append({"target": target, "ip": b.ip, "sent": b.sent, "lost": b.lost,
                         "avg": avg, "max": mx, "jitter": jitter})
        sig = _round1(sum(self._signals) / len(self._signals)) if self._signals else None
        minute = self._minute
        self._buckets, self._signals = {}, []
        return MinuteBatch(minute, rows, sig) if (rows or sig is not None) else None


# --- the monitor ---------------------------------------------------------------

_tls = threading.local()


def _default_ping(address: str, timeout_ms: int) -> icmp.PingResult:
    pinger = getattr(_tls, "pinger", None)
    if pinger is None:
        pinger = _tls.pinger = icmp.Pinger()  # one handle per worker thread: Pinger isn't thread-safe
    return pinger.ping(address, timeout_ms)


class Monitor:
    def __init__(self, storage: Storage, settings: dict[str, Any] | None = None, *,
                 clock: Callable[[], float] = time.time,
                 ping_fn: Callable[[str, int], Any] = _default_ping,
                 wifi_fn: Callable[[], winutil.WifiState | None] = winutil.get_wifi_state,
                 gateway_fn: Callable[[], str | None] = winutil.get_gateway,
                 probe_fn: Callable[[str, str, float], Any] = run_probe,
                 notify: Callable[[str, str], Any] | None = None,
                 route_fn: Callable[[], dict | None] = winutil.default_route_native,
                 path_fn: Callable[[], dict | None] = winutil.internet_route_native,
                 dns_fn: Callable[[], dict[int, list[str]] | None] = winutil.dns_servers_native) -> None:
        self.storage = storage
        self._notify = notify
        self._route_fn, self._dns_fn, self._path_fn = route_fn, dns_fn, path_fn
        self._last_route: tuple[int, str] | None = None   # (interface index, gateway) of the last default route seen
        self._dns_watch = DnsWatch()
        self.settings = settings if settings is not None else config.load_settings()
        after_s = float(self.settings.get("notify", {}).get("outage_after_s", 30))
        self._outage_toasts = OutageNotifier(notify, after_s) if notify else None
        self._clock = clock
        self._ping_fn = ping_fn
        self._wifi_fn = wifi_fn
        self._gateway_fn = gateway_fn
        self._probe_fn = probe_fn
        # Probes: name -> (kind, target). They confirm "there is Internet" when ICMP says otherwise.
        pcfg = self.settings.get("probes") or {}
        self._probes: dict[str, tuple[str, str]] = {}
        if pcfg.get("enabled"):
            self._probes = {**{n: ("tcp", t) for n, t in (pcfg.get("tcp") or {}).items()},
                            **{n: ("http", u) for n, u in (pcfg.get("http") or {}).items()}}
        self._probe_interval = float(pcfg.get("interval_s", 10))
        self._probe_round: tuple[float, bool] | None = None   # (when, did any probe succeed)
        self._probe_pool = (ThreadPoolExecutor(max_workers=len(self._probes), thread_name_prefix="probe")
                            if self._probes else None)

        self._lock = threading.RLock()
        self._targets: dict[str, str | None] = {ROUTER: None, **self.settings["targets"]}
        self._wifi: winutil.WifiState | None = None
        self._wifi_since: float | None = None      # when the current Wi-Fi state began
        self._last_profile: str = ""               # last profile seen while connected (for reconnect)
        self._last_gap_end: float | None = None    # end of the latest monitoring gap (sleep/hang)
        self._samples: deque[tuple[float, str, float | None]] = deque()
        self._tracker = OutageTracker()
        self._agg = MinuteAggregator()
        self._pool = ThreadPoolExecutor(max_workers=len(self._targets), thread_name_prefix="ping")
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_purge = 0.0
        self._last_tick: float | None = None
        self._gap_s = max(GAP_MIN_S, 10 * float(self.settings["ping_interval_s"]))
        self._logged_errors: set[str] = set()
        self.write_errors = 0

    # -- public API --------------------------------------------------------------

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("monitor already started")
        self._stop.clear()
        self.refresh_gateway()
        self.poll_wifi(baseline=True)
        self.poll_route(baseline=True)
        self._seed_dns()
        self._record(MonitorEvent(int(self._clock()), "monitor_start",
                                  msg("event.monitor_start", gateway=self._targets[ROUTER] or "?", pid=os.getpid())))
        loops = [("ping-loop", self._ping_loop), ("slow-loop", self._slow_loop)]
        if self._probes:
            loops.append(("probe-loop", self._probe_loop))
        for name, fn in loops:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._probe_pool is not None:
            self._probe_pool.shutdown(wait=False, cancel_futures=True)
        self._write_batch(self._agg.flush())
        with self._lock:
            held = self._tracker.flush(self._clock())
        for ev in held:
            self._record(ev)
        self._record(MonitorEvent(int(self._clock()), "monitor_stop", msg("event.monitor_stop", pid=os.getpid())))

    def snapshot(self, window_s: float = 300) -> dict[str, Any]:
        """State for the UI: targets, Wi-Fi, open outages, recent raw samples."""
        now = self._clock()
        with self._lock:
            samples = [[round(ts, 3), tgt, rtt] for ts, tgt, rtt in self._samples if ts >= now - window_s]
            return {
                "ts": now,
                "targets": dict(self._targets),
                "wifi": asdict(self._wifi) if self._wifi else None,
                "wifi_since": self._wifi_since,
                "last_profile": self._last_profile,
                "last_gap_end": self._last_gap_end,
                "outages": self._tracker.active(),
                "probe": ({"ts": self._probe_round[0], "ok": self._probe_round[1]} if self._probe_round else None),
                "samples": samples,
                "write_errors": self.write_errors,
            }

    # -- one tick of work (called by the loop; tests call it directly) -------------

    def run_tick(self) -> None:
        with self._lock:
            targets = dict(self._targets)
        timeout_ms = int(self.settings["ping_timeout_ms"])
        futures = {name: self._pool.submit(self._ping_one, ip, timeout_ms) for name, ip in targets.items()}
        results = {name: f.result() for name, f in futures.items()}
        now = self._clock()

        self._write_batch(self._agg.roll(now))
        events: list[MonitorEvent] = []
        with self._lock:
            last, self._last_tick = self._last_tick, now
            if last is not None and now - last > self._gap_s:
                self._last_gap_end = now
                events += self._tracker.interrupt(last)
                if self._outage_toasts:
                    self._outage_toasts.reset()   # what happened during the gap is unknown: no "recovered" toast
                events.append(MonitorEvent(int(now), "monitor_gap",
                                           msg("event.monitor_gap", seconds=round(now - last)),
                                           round(now - last)))
            for name, rtt in results.items():
                self._agg.add_ping(name, targets[name], rtt)
                self._samples.append((now, name, rtt))
            while self._samples and self._samples[0][0] < now - RAW_WINDOW_S:
                self._samples.popleft()

            internet = [rtt for name, rtt in results.items() if name != ROUTER]
            # "Internet is up" if ANY ICMP target answers or the latest probe round (TCP/HTTP)
            # succeeded: ICMP alone is the first thing a network may drop or rate-limit.
            internet_ok = any(r is not None for r in internet) or self._probes_confirm(now)
            events += self._tracker.update(now, results.get(ROUTER) is not None, internet_ok, targets.get(ROUTER))
            active = self._tracker.active()
        for ev in events:
            self._record(ev)
        if self._outage_toasts:
            try:
                self._outage_toasts.update(now, active)
            except Exception:   # a toast problem must never stop the tick
                log.exception("outage toast failed")

        if now - self._last_purge >= PURGE_EVERY_S:
            self._last_purge = now
            self._guarded(lambda: self.storage.purge(self.settings["retention_days"], now=now), "purge")

    # -- probes (TCP / HTTP), a slower cadence than ping --------------------------------

    def _probes_confirm(self, now: float) -> bool:
        """True when the latest probe round is recent and at least one probe succeeded. A stale
        round (just started, or the loop was paused by sleep) never confirms anything."""
        rnd = self._probe_round
        if rnd is None:
            return False
        fresh = now - rnd[0] <= 2.5 * self._probe_interval + float(self.settings["probes"].get("timeout_s", 3))
        return fresh and rnd[1]

    def _probe_one(self, kind: str, target: str, timeout_s: float) -> float | None:
        try:
            res = self._probe_fn(kind, target, timeout_s)
        except Exception as exc:  # a broken probe is a failed probe, never a dead monitor
            self._log_once(f"probe:{type(exc).__name__}:{exc}", "probe failed: %r", exc)
            return None
        return res.rtt_ms if res.ok else None

    def run_probe_round(self) -> None:
        """One round of every probe in parallel (called by the probe loop; tests call it directly)."""
        if not self._probes or self._probe_pool is None:
            return
        timeout_s = float(self.settings["probes"].get("timeout_s", 3))
        futures = {name: self._probe_pool.submit(self._probe_one, kind, target, timeout_s)
                   for name, (kind, target) in self._probes.items()}
        results = {name: f.result() for name, f in futures.items()}
        now = self._clock()
        with self._lock:
            for name, rtt in results.items():
                self._agg.add_ping(name, self._probes[name][1], rtt)
                self._samples.append((now, name, rtt))
            self._probe_round = (now, any(r is not None for r in results.values()))

    def _ping_one(self, ip: str | None, timeout_ms: int) -> float | None:
        if not ip:
            return None
        try:
            res = self._ping_fn(ip, timeout_ms)
        except Exception as exc:  # a broken ping must never kill the monitor
            self._log_once(f"ping:{type(exc).__name__}:{exc}", "ping failed: %r", exc)
            return None
        return res.rtt_ms if res.ok else None

    # -- slow work: Wi-Fi + gateway ------------------------------------------------

    def refresh_gateway(self) -> None:
        try:
            gw = self._gateway_fn()
        except Exception as exc:
            self._log_once(f"gateway:{exc}", "gateway lookup failed: %r", exc)
            return
        with self._lock:
            old = self._targets[ROUTER]
            if gw and gw != old:
                self._targets[ROUTER] = gw
                changed = old is not None
            else:
                changed = False
        if changed:
            self._record(MonitorEvent(int(self._clock()), "gateway_change", f"{old} -> {gw}"))

    def poll_route(self, baseline: bool = False) -> None:
        """Notice the route of Internet traffic moving to another interface (VPN on/off, wired <->
        Wi-Fi) within ROUTE_POLL_S: `route_change` carries that moment, which a gateway that stays
        the same (or is only re-read every `gateway_refresh_s`) cannot. A route that disappears is a
        lost connection, not a switch; the next route is compared with the last one that existed."""
        try:
            route = self._path_fn()
        except Exception as exc:
            self._log_once(f"route:{exc}", "route lookup failed: %r", exc)
            return
        if not route:
            return
        current = (route["interface_index"], route.get("gateway") or "")   # a tunnel's route is on-link: no gateway
        with self._lock:
            before, self._last_route = self._last_route, current
        if baseline or before is None or before == current:
            return
        if before[0] != current[0]:
            self._record(MonitorEvent(int(self._clock()), "route_change", f"if{before[0]} -> if{current[0]}"))
        self.refresh_gateway()   # a new gateway is picked up now, not up to gateway_refresh_s later

    def _seed_dns(self) -> None:
        try:
            stored = self.storage.query_events(kinds=list(DNS_EVENT_KINDS), limit=500)
        except Exception as exc:
            self._log_once(f"dns-seed:{exc}", "cannot read DNS history: %r", exc)
            return
        self._dns_watch.seed(reversed(stored))   # newest first -> oldest first

    def check_dns(self) -> None:
        """Compare the DNS servers of the network in use with what it used before (app/dnswatch.py)."""
        try:
            route, table = self._route_fn(), self._dns_fn()
        except Exception as exc:
            self._log_once(f"dns:{exc}", "DNS lookup failed: %r", exc)
            return
        if not route or table is None:
            return
        with self._lock:
            wifi = self._wifi
        ssid = wifi.ssid if wifi is not None and wifi.connected else None
        network = network_key(route["interface_index"], route.get("gateway"), ssid)
        now = int(self._clock())
        for kind, message, level in self._dns_watch.observe(network, table.get(route["interface_index"], []),
                                                            by_app=lambda: self._dns_changed_by_app(now)):
            self._guarded(lambda: self.storage.add_event(now, kind, message, level=level), f"event {kind}")
            if kind == "dns_changed" and self._notify is not None:
                self._notify(t("notify.dns_changed.title"), render(message))

    def _dns_changed_by_app(self, now: float) -> bool:
        """Did a tweak of this app change the DNS servers just now? (storage is shared with the elevated writer)"""
        try:
            since = int(now) - dnswatch.APP_CHANGE_WINDOW_S
            return dnswatch.app_changed(self.storage.query_events(since=since, kinds=list(dnswatch.TWEAK_EVENT_KINDS),
                                                                  limit=50), now)
        except Exception as exc:
            self._log_once(f"dns-app:{exc}", "cannot read tweak history: %r", exc)
            return False

    def poll_wifi(self, baseline: bool = False) -> None:
        try:
            st = self._wifi_fn()
        except Exception as exc:
            self._log_once(f"wifi:{exc}", "wifi poll failed: %r", exc)
            return
        if st is None:
            return
        events: list[MonitorEvent] = []
        now = int(self._clock())
        with self._lock:
            prev = self._wifi
            if prev is None or st.state != prev.state:
                self._wifi_since = self._clock()
            if st.connected and st.profile:
                self._last_profile = st.profile
            if prev is not None and not baseline:
                if st.state != prev.state:
                    events.append(MonitorEvent(now, "wifi_state", f"{prev.state} -> {st.state} ({st.ssid})"))
                elif st.connected and prev.bssid and st.bssid != prev.bssid:
                    events.append(MonitorEvent(now, "roam", f"{prev.bssid} -> {st.bssid} ch{st.channel}"))
            self._wifi = st
        if st.signal is not None:
            self._agg.add_signal(st.signal)
        for ev in events:
            self._record(ev)
        if any(e.kind == "wifi_state" for e in events):
            self.refresh_gateway()  # a reconnect often brings a new gateway

    # -- loops ---------------------------------------------------------------------

    def _ping_loop(self) -> None:
        interval = float(self.settings["ping_interval_s"])
        next_tick = time.monotonic()
        while not self._stop.is_set():
            try:
                self.run_tick()
            except Exception:
                log.exception("tick failed")  # keep monitoring
            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay < -interval * 5:       # fell far behind (e.g. system sleep): don't burst
                next_tick = time.monotonic()
                delay = 0
            self._stop.wait(max(delay, 0))

    def _probe_loop(self) -> None:
        next_round = time.monotonic()
        while not self._stop.is_set():
            try:
                self.run_probe_round()
            except Exception:
                log.exception("probe round failed")  # keep monitoring
            next_round += self._probe_interval
            delay = next_round - time.monotonic()
            if delay < -self._probe_interval * 5:    # after sleep: don't burst to catch up
                next_round = time.monotonic()
                delay = 0
            self._stop.wait(max(delay, 0))

    def _slow_loop(self) -> None:
        next_wifi = next_gw = next_dns = next_route = time.monotonic()
        dns_cfg = self.settings.get("dns_watch") or {}
        while not self._stop.is_set():
            now = time.monotonic()
            if dns_cfg.get("enabled") and now >= next_dns:
                self.check_dns()
                next_dns = time.monotonic() + float(dns_cfg.get("interval_s", 60))
            if now >= next_route:
                self.poll_route()
                next_route = time.monotonic() + ROUTE_POLL_S
            if now >= next_wifi:
                self.poll_wifi()
                next_wifi = time.monotonic() + float(self.settings["wifi_poll_s"])
            if now >= next_gw:
                self.refresh_gateway()
                next_gw = time.monotonic() + float(self.settings["gateway_refresh_s"])
            self._stop.wait(0.25)

    # -- persistence helpers ---------------------------------------------------------

    def _write_batch(self, batch: MinuteBatch | None) -> None:
        if batch is None:
            return

        def write() -> None:
            for r in batch.rows:
                self.storage.add_minute_stat(batch.minute_ts, r["target"], r["sent"], r["lost"], ip=r["ip"],
                                             avg=r["avg"], max=r["max"], jitter=r["jitter"])
            with self._lock:
                w = self._wifi
            if w is not None:
                self.storage.add_wifi_stat(batch.minute_ts, state=w.state, ssid=w.ssid, bssid=w.bssid,
                                           channel=w.channel, signal=batch.signal_avg, rssi=w.rssi,
                                           rx_mbps=w.rx_mbps, tx_mbps=w.tx_mbps)

        self._guarded(write, "minute batch")

    def _record(self, ev: MonitorEvent) -> None:
        self._guarded(lambda: self.storage.add_event(ev.ts, ev.kind, ev.message, duration=ev.duration),
                      f"event {ev.kind}")

    def _guarded(self, fn: Callable[[], Any], what: str) -> None:
        try:
            fn()
        except (sqlite3.Error, OSError) as exc:  # disk full / locked: keep monitoring, count it
            self.write_errors += 1
            self._log_once(f"write:{what}:{exc}", "storage write failed (%s): %r", what, exc)

    def _log_once(self, key: str, msg: str, *args: Any) -> None:
        if key not in self._logged_errors:
            self._logged_errors.add(key)
            log.warning(msg, *args)


MUTEX_NAME = "StableInternet.Monitor"
# A monitor restarted back-to-back (Stop then Start of the task, an upgrade) starts while the
# previous one still holds the mutex; waiting this long lets it exit instead of both quitting.
MUTEX_WAIT_SECONDS = 10.0


def mutex_name() -> str:
    """One monitor per data directory: two monitors only conflict when they share metrics.db."""
    import hashlib
    digest = hashlib.sha1(str(config.data_dir().resolve()).lower().encode("utf-8")).hexdigest()[:12]
    return f"{MUTEX_NAME}.{digest}"


def _setup_logging(log_file: str | None) -> None:
    import logging.handlers
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:           # pythonw.exe has no console streams
        handlers.append(logging.StreamHandler())
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                                             encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def _make_notifier() -> Notifier:
    """Toasts follow settings.json -> notify.enabled, re-read every 10 s so the UI can switch them."""
    cache = {"at": -1e9, "on": True}

    def enabled() -> bool:
        now = time.monotonic()
        if now - cache["at"] > 10:
            cache.update(at=now, on=bool(config.load_settings().get("notify", {}).get("enabled", True)))
        return cache["on"]
    return Notifier(enabled=enabled)


def _start_server(mon: "Monitor", storage: Storage, failover: Any = None) -> Any:
    """The API/UI server is optional: if it cannot start (port taken...), keep monitoring."""
    srv_settings = config.load_settings().get("server", {})
    if not srv_settings.get("enabled", True):
        return None
    from .server import Api, Server
    try:
        server = Server(Api(monitor=mon, storage=storage, failover=failover), int(srv_settings.get("port", 47613)))
    except OSError as exc:
        log.error("server not started (port %s): %r", srv_settings.get("port"), exc)
        return None
    server.start()
    log.info("server -> %s", server.url)
    return server


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Run the StableInternet monitor in the foreground.")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (default: run until Ctrl+C)")
    ap.add_argument("--log-file", help="also log to this file (rotating); needed when started without a console")
    ap.add_argument("--instance-wait", type=float, default=MUTEX_WAIT_SECONDS, metavar="SECONDS",
                    help="how long to wait for a previous monitor that is still exiting (default: %(default)s)")
    args = ap.parse_args(argv)
    _setup_logging(args.log_file)

    guard = SingleInstance(mutex_name())
    started = time.monotonic()
    if not guard.acquire(wait=args.instance_wait):
        log.info("another monitor instance is already running; exiting")
        return 0
    if time.monotonic() - started >= 0.5:
        log.info("previous monitor instance exited after %.1f s; starting", time.monotonic() - started)
    from .failover import Failover  # idle unless enabled (ADR-0008, off by default)
    from .watchdog import Watchdog  # stays idle unless enabled in settings.json (off by default)

    storage = Storage(config.db_path())
    notifier = _make_notifier()
    mon = Monitor(storage, notify=notifier.notify)
    wd = Watchdog(mon, storage, notify=notifier.notify)
    fo = Failover(storage, notify=notifier.notify)
    server = None
    try:
        mon.start()
        wd.start()
        fo.start()
        log.info("monitoring -> %s", storage.path)
        server = _start_server(mon, storage, fo)
        deadline = time.monotonic() + args.seconds if args.seconds else None
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.stop()
        fo.stop()
        wd.stop()
        mon.stop()
        storage.close()
        guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
