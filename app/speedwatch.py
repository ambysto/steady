"""Takes throughput samples on a schedule and keeps the slowdown history (ADR-0021).

Off until the user turns `speed.enabled` on; the setting is re-read every cycle, so it takes effect
without restarting the monitor. One cycle = skip check, one sample (app/speedsample.py), the evidence of
that moment, then the rules of app/slowdown.py decide whether an episode opens, goes on or ends. What the
monitor knows (router loss, Wi-Fi link, tunnels, outages) comes in through injected functions.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any, Callable

from . import config, slowdown, speedsample
from .i18n import msg
from .slowdown import DIRECTIONS, SlowdownTracker
from .speedsample import Sample
from .storage import Storage

log = logging.getLogger("stableinternet.speedwatch")

FIRST_SAMPLE_DELAY_S = 60.0   # after the monitor starts: let the route and Wi-Fi state settle
TICK_S = 5.0
SKIP_OUTAGE, SKIP_NETWORK_CHANGE = "outage", "network_change"
SKIP_OWN_TRAFFIC, SKIP_REFUSED, SKIP_ERROR = "own_traffic", "refused", "error"


class SpeedWatch:
    def __init__(self, storage: Storage, *,
                 load_settings: Callable[[], dict[str, Any]] = config.load_settings,
                 skip_reason: Callable[[], str | None] = lambda: None,
                 evidence: Callable[[], dict[str, Any]] = lambda: {},
                 network: Callable[[], str] = lambda: slowdown.network_id(None, None),
                 octets: Callable[[], int | None] = lambda: None,
                 sample_fn: Callable[..., Sample] = speedsample.take_sample,
                 clock: Callable[[], float] = time.time) -> None:
        self.storage = storage
        self._load, self._skip_reason, self._evidence = load_settings, skip_reason, evidence
        self._network, self._octets, self._sample_fn, self._clock = network, octets, sample_fn, clock
        self._trackers = {d: SlowdownTracker(d) for d in DIRECTIONS}
        self._current_network: str | None = None
        self._next_at = 0.0
        self._last_ts: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            raise RuntimeError("speed watch already started")
        now = self._clock()
        self._next_at = now + FIRST_SAMPLE_DELAY_S
        self._guarded(lambda: self._close_left_open(now), "close open slowdowns")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="speed-loop", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None
        self._guarded(lambda: [self._apply(t) for tr in self._trackers.values() for t in tr.interrupt()],
                      "close slowdowns")

    def _close_left_open(self, now: float) -> None:
        """Episodes a previous run left open end at the last sample that run took."""
        rows = self.storage.query_speed_samples(int(now) - 7 * 86400, int(now) + 1)
        self.storage.close_open_slowdowns(rows[-1]["ts"] if rows else int(now))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._clock() >= self._next_at and self._settings().get("enabled"):
                    self.run_once()
                elif not self._settings().get("enabled"):
                    self._next_at = max(self._next_at, self._clock())
            except Exception:
                log.exception("speed sample failed")   # keep monitoring
            self._stop.wait(TICK_S)

    def _settings(self) -> dict[str, Any]:
        try:
            return self._load().get("speed") or {}
        except Exception:
            return {}

    # -- one cycle ---------------------------------------------------------------------

    def run_once(self) -> str | None:
        """Takes one sample and files it. Returns why nothing was measured, else None (tests read it)."""
        cfg = self._settings()
        interval_s = float(cfg.get("interval_min", 30)) * 60
        now = int(self._clock())
        network = self._network()
        self._next_at = now + interval_s
        if network != self._current_network:
            for tracker in self._trackers.values():
                for transition in tracker.interrupt():
                    self._apply(transition)
            self._current_network = network

        reason = self._skip_reason()
        sample = Sample()
        if reason is None:
            sample = self._sample_fn(octets=self._octets)
            if sample.refused is not None:
                reason = SKIP_REFUSED
                self._next_at = now + max(sample.retry_after_s or 0.0, 2 * interval_s)
            elif sample.down_mbps is None and sample.up_mbps is None:
                reason = SKIP_ERROR
            elif sample.own_traffic:
                reason = SKIP_OWN_TRAFFIC
        evidence = {**self._evidence(), "down_mbps": _round(sample.down_mbps), "up_mbps": _round(sample.up_mbps),
                    "foreign_mbps": _round(sample.foreign_mbps)}
        if sample.error:
            evidence["error"] = sample.error
        if sample.refused is not None:
            evidence["refused"] = sample.refused
        self._guarded(lambda: self.storage.add_speed_sample(
            now, network=network, down_mbps=_round(sample.down_mbps), up_mbps=_round(sample.up_mbps),
            skipped=reason, evidence=evidence), "speed sample")
        self._last_ts = now
        if reason is None:
            self._guarded(lambda: self._track(now, network, sample, evidence, cfg, interval_s), "slowdowns")
        return reason

    def _track(self, now: int, network: str, sample: Sample, evidence: dict[str, Any],
               cfg: dict[str, Any], interval_s: float) -> None:
        for direction, value, plan_key in ((slowdown.DOWNLOAD, sample.down_mbps, "plan_down_mbps"),
                                           (slowdown.UPLOAD, sample.up_mbps, "plan_up_mbps")):
            if value is None:
                continue
            base = self._baseline(direction, network, now, cfg.get(plan_key))
            if base is None:
                continue
            tracker = self._trackers[direction]
            tracker.max_gap_s = 3 * interval_s
            for transition in tracker.update(now, value, base, evidence):
                self._apply(transition, network)

    def _baseline(self, direction: str, network: str, now: int, plan: Any) -> float | None:
        column = "down_mbps" if direction == slowdown.DOWNLOAD else "up_mbps"
        rows = self.storage.query_speed_samples(now - slowdown.BASELINE_DAYS * 86400, now, network=network)
        values = [r[column] for r in rows if r["skipped"] is None and r[column] is not None]
        return slowdown.baseline(values, float(plan) if plan else None)

    # -- persistence -------------------------------------------------------------------

    def _apply(self, transition: slowdown.Transition, network: str | None = None) -> None:
        ep = transition.episode
        network = network or self._current_network
        evidence = {"open": ep.evidence_open, "close": ep.evidence_close}
        if transition.kind == "open":
            ep.id = self.storage.add_slowdown(ep.start_ts, ep.direction, ep.baseline_mbps, ep.min_mbps,
                                              network=network, verdict=ep.verdict, evidence=evidence)
            return
        if ep.id is None:   # opened while storage failed: nothing to update
            return
        self.storage.update_slowdown(ep.id, end_ts=ep.end_ts, min_mbps=ep.min_mbps, avg_mbps=ep.avg_mbps,
                                     samples=ep.samples, verdict=ep.verdict, evidence=evidence)
        if transition.kind == "close" and ep.end_ts is not None:
            self.storage.add_event(
                ep.end_ts, "slowdown",
                msg(f"event.slowdown.{ep.verdict}", direction=msg(f"event.slowdown.{ep.direction}"),
                    avg=round(ep.avg_mbps, 1), baseline=round(ep.baseline_mbps)),
                duration=ep.end_ts - ep.start_ts)

    def _guarded(self, fn: Callable[[], Any], what: str) -> None:
        try:
            fn()
        except (sqlite3.Error, OSError) as exc:   # disk full / locked: keep monitoring
            log.warning("storage write failed (%s): %r", what, exc)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)

