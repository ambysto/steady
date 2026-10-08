"""Slowdown episodes from throughput samples (ADR-0021): the rules, with no I/O.

A line can be throttled or congested while ping, loss and probes look perfect (2026-10-07: download
300 -> 5 Mb/s, upload and latency untouched). The monitor therefore samples throughput now and then
(app/speedsample.py); this module turns those samples into episodes and says, from what the monitor
knew at that moment, whether the PC <-> router hop can be blamed.

Everything here is a pure function or a small state machine over numbers, so the rules are tested with
plain values (tests/test_slowdown.py).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

DOWNLOAD, UPLOAD = "download", "upload"
DIRECTIONS = (DOWNLOAD, UPLOAD)

OPEN_RATIO = 0.5          # a sample below this share of the baseline is "slow"
CLOSE_RATIO = 0.7         # an open episode ends at the first sample at or above this share
OPEN_AFTER = 2            # consecutive slow samples before an episode exists (one low sample is noise)
MIN_BASELINE_SAMPLES = 6  # fewer samples than this and there is no baseline yet
BASELINE_DAYS = 14

# The PC <-> router hop is blamed (`local`) when any of these holds at the time of the sample. They
# follow the link thresholds of diagnostics #11/#13 (docs/DIAGNOSTICS.md).
LOCAL_ROUTER_LOSS_PCT = 5.0
LOCAL_RSSI_DBM = -76
LOCAL_LINK_MBPS = 30.0

OUTSIDE, LOCAL, UNKNOWN = "outside", "local", "unknown"
VERDICTS = (OUTSIDE, LOCAL, UNKNOWN)


def network_id(gateway: str | None, ssid: str | None) -> str:
    """What a baseline belongs to: the same gateway on the same Wi-Fi name (wired: no name)."""
    return f"{gateway or '?'}|{ssid or ''}"


def baseline(values: list[float], plan_mbps: float | None = None) -> float | None:
    """What this line normally delivers: the plan speed when the user entered one, else the median of
    the best quarter of the samples. The best quarter, not all of them, so a long slow stretch cannot
    pull the baseline down to itself. None until there are MIN_BASELINE_SAMPLES samples."""
    if plan_mbps is not None and plan_mbps > 0:
        return float(plan_mbps)
    good = sorted(v for v in values if v is not None and v > 0)
    if len(good) < MIN_BASELINE_SAMPLES:
        return None
    top = good[len(good) - max(1, len(good) // 4):]
    return float(statistics.median(top))


def verdict(evidence: dict[str, Any] | None, direction: str = DOWNLOAD) -> str:
    """`local` when the PC <-> router hop can explain the slowness, `outside` when it is clean,
    `unknown` when it cannot be told (no evidence, a tunnel carrying the traffic, no router data).

    `outside` means outside the PC <-> router hop: the router itself, the line or the provider. The app
    cannot separate those, and a report says so."""
    if not evidence or evidence.get("vpn"):
        return UNKNOWN
    loss = evidence.get("router_loss_pct")
    if loss is None:
        return UNKNOWN
    link = evidence.get("rx_mbps" if direction == DOWNLOAD else "tx_mbps")
    rssi = evidence.get("rssi")
    if (loss >= LOCAL_ROUTER_LOSS_PCT or (rssi is not None and rssi < LOCAL_RSSI_DBM)
            or (link is not None and 0 < link <= LOCAL_LINK_MBPS)):
        return LOCAL
    return OUTSIDE


def combine(opening: str, closing: str | None) -> str:
    """One verdict for an episode from the evidence at its start and at its end. Two that disagree
    give `unknown`: the cause may have changed while it lasted."""
    if closing is None or closing == opening:
        return opening
    return UNKNOWN


@dataclass
class Episode:
    direction: str
    start_ts: int
    baseline_mbps: float
    min_mbps: float
    total_mbps: float
    samples: int
    end_ts: int | None = None
    evidence_open: dict[str, Any] | None = None
    evidence_close: dict[str, Any] | None = None
    id: int | None = None        # the storage row, set by whoever persists it

    @property
    def avg_mbps(self) -> float:
        return self.total_mbps / self.samples

    @property
    def verdict(self) -> str:
        return combine(verdict(self.evidence_open, self.direction),
                       verdict(self.evidence_close, self.direction) if self.evidence_close else None)


@dataclass(frozen=True)
class Transition:
    kind: str          # "open" | "update" | "close"
    episode: Episode


@dataclass
class _Pending:
    ts: int
    mbps: float
    evidence: dict[str, Any] | None


@dataclass
class SlowdownTracker:
    """Samples of one direction in, episode transitions out. `max_gap_s` is the longest pause between
    samples that still counts as one episode: past it the open episode ends at its last slow sample
    (the monitor was off or asleep, and what happened meanwhile is not known)."""
    direction: str
    max_gap_s: float = 3 * 3600.0
    active: Episode | None = None
    _pending: list[_Pending] = field(default_factory=list)
    _last_ts: int | None = None

    def update(self, ts: int, mbps: float, base: float, evidence: dict[str, Any] | None = None) -> list[Transition]:
        out: list[Transition] = []
        if self._last_ts is not None and ts - self._last_ts > self.max_gap_s:
            out += self.interrupt()
        self._last_ts = ts
        if self.active is not None:
            ep = self.active
            if mbps >= CLOSE_RATIO * ep.baseline_mbps:
                ep.end_ts, ep.evidence_close = ts, evidence
                self.active = None
                return out + [Transition("close", ep)]
            ep.min_mbps = min(ep.min_mbps, mbps)
            ep.total_mbps += mbps
            ep.samples += 1
            return out + [Transition("update", ep)]
        if mbps >= OPEN_RATIO * base:
            self._pending.clear()
            return out
        self._pending.append(_Pending(ts, mbps, evidence))
        if len(self._pending) < OPEN_AFTER:
            return out
        first = self._pending[0]
        ep = Episode(self.direction, first.ts, base, min(p.mbps for p in self._pending),
                     sum(p.mbps for p in self._pending), len(self._pending), evidence_open=first.evidence)
        self._pending.clear()
        self.active = ep
        return out + [Transition("open", ep)]

    def interrupt(self) -> list[Transition]:
        """The samples stopped (sleep, monitor off): an open episode ends at its last slow sample."""
        self._pending.clear()
        ep, self.active = self.active, None
        if ep is None:
            return []
        ep.end_ts = self._last_ts
        return [Transition("close", ep)]
