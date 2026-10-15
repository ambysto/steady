"""The speed test (ADR-0024, the Windows side of ADR-0023): check #14's run read as the figures a speed
test app shows. One run sets both this and the Bufferbloat result, so the line is loaded once.

Pure: `result()` turns a `bufferbloat.Measurement` made with `events` into the figures, `Live` turns the
events into what the card shows while it runs, and `to_dict` / `from_dict` keep the latest result in the
data folder with the run's measurement, so check #14 comes back with it after a restart.
"""
from __future__ import annotations

import json
import statistics
import threading
from pathlib import Path
from typing import Any, Iterable

from . import config
from .bufferbloat import LOAD_S, RAMP_S, Measurement, Phase

INTERNET = "internet"       # the target whose pings are the figures (the router's are check #14's alone)
MIN_MBPS = 1.0              # diagnostics.BLOAT_MIN_MBPS: below it the load did not load the line
MAX_POINTS = 48             # a stored series keeps at most this many points
IDLE_S = 4.0
DURATION_S = IDLE_S + 2 * LOAD_S
STAGES = ("idle", "download", "upload")


# --- statistics shared by the live view and the result -------------------------------------------

def median(replies: Iterable[float | None]) -> float | None:
    got = [r for r in replies if r is not None]
    return statistics.median(got) if got else None


def jitter(replies: Iterable[float | None]) -> float | None:
    """The mean absolute difference between consecutive replies, as speed.cloudflare.com reports it."""
    got = [r for r in replies if r is not None]
    if len(got) < 2:
        return None
    return sum(abs(b - a) for a, b in zip(got, got[1:])) / (len(got) - 1)


def loss(replies: list[float | None]) -> float | None:
    return 100.0 * sum(1 for r in replies if r is None) / len(replies) if replies else None


def latency(replies: list[float | None]) -> dict[str, float | None]:
    return {"median_ms": median(replies), "jitter_ms": jitter(replies)}


def thinned(series: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Every n-th point, so at most MAX_POINTS are kept; the last one always is."""
    if len(series) <= MAX_POINTS:
        return list(series)
    step = (len(series) - 1) / (MAX_POINTS - 1)
    return [series[int(round(i * step))] for i in range(MAX_POINTS)]


def replies(phase: Phase) -> list[float | None]:
    return list(phase.rtts.get(INTERNET, []))


def figure(phase: Phase) -> float | None:
    """The direction's speed: the rate after the ramp, or the whole phase's when the load ended before
    the ramp did (a very fast line hitting the byte limit). None when refused, or when the load stayed
    under MIN_MBPS (it did not load the line, as check #14 says too)."""
    if phase.refused is not None or phase.mbps is None or phase.mbps < MIN_MBPS:
        return None
    return phase.steady_mbps if phase.steady_mbps is not None else phase.mbps


def needs_confirmation(cellular: bool) -> bool:
    """Ask only on mobile data, every time (intent decision 3): on Wi-Fi or Ethernet the test runs at once."""
    return cellular


# --- the result -----------------------------------------------------------------------------------

def result(m: Measurement, at: float) -> dict[str, Any]:
    """The figures of one run, as the API returns and the data folder keeps them (times in Unix seconds)."""
    down, up = figure(m.download), figure(m.upload)
    refusals = [p for p in (m.download, m.upload) if p.refused is not None]
    waits = [p.retry_after_s for p in refusals if p.retry_after_s is not None]
    # The loaded phases' replies are after the ramp already; a phase that did not load says nothing.
    loaded = [r for p, f in ((m.download, down), (m.upload, up)) if f is not None for r in replies(p)]
    return {
        "measured_at": at,
        "download_mbps": down, "upload_mbps": up,
        "download_capped": down is not None and m.download.capped,
        "upload_capped": up is not None and m.upload.capped,
        "idle": latency(replies(m.idle)),
        "during_download": latency(replies(m.download)) if down is not None else None,
        "during_upload": latency(replies(m.upload)) if up is not None else None,
        "loss_pct": loss(replies(m.idle) + loaded),
        "download_series": [list(p) for p in thinned(m.download.series)] if down is not None else None,
        "upload_series": [list(p) for p in thinned(m.upload.series)] if up is not None else None,
        "server": m.server,
        "refused_status": refusals[0].refused if refusals else None,
        "retry_until": at + max(waits) if waits else None,
    }


def has_figures(r: dict[str, Any] | None) -> bool:
    return bool(r) and (r.get("download_mbps") is not None or r.get("upload_mbps") is not None)


# --- the live view --------------------------------------------------------------------------------

class Live:
    """A run as it goes, built from `bufferbloat.measure`'s events: what the card shows before the result."""

    def __init__(self) -> None:
        self.stage = "idle"
        self.seconds = 0.0                  # into the current phase, from the latest event
        self.series: dict[str, list[tuple[float, float]]] = {"download": [], "upload": []}
        self._pings: dict[str, list[tuple[float, float | None]]] = {s: [] for s in STAGES}
        self._figures: dict[str, float | None] = {}   # each ended direction's figure, as the result shows it

    def apply(self, event: tuple) -> None:
        kind, name = event[0], event[1]
        if kind == "stage":
            self.stage, self.seconds = name, 0.0
        elif kind == "rate":
            if name == self.stage:
                self.seconds = max(self.seconds, event[2])
            self.series.setdefault(name, []).append((event[2], event[3]))
        elif kind == "ping":
            if name == self.stage:
                self.seconds = max(self.seconds, event[2])
            self._pings.setdefault(name, []).append((event[2], event[3]))
        elif kind == "ended":
            self._figures[name] = figure(event[2])   # kept when None: the direction ended without a figure

    def mbps(self, direction: str) -> float | None:
        """The latest rate while the direction loads, the result's figure once it has ended (so it does
        not change when the result arrives), None before. Without that figure, the mean after the ramp."""
        if direction in self._figures:
            return self._figures[direction]
        series = self.series.get(direction, [])
        if direction == self.stage:
            return series[-1][1] if series else None
        steady = [v for s, v in series if s >= RAMP_S] or [v for _, v in series]
        return sum(steady) / len(steady) if steady else None

    def _replies(self, phase: str) -> list[float | None]:
        rows = self._pings.get(phase, [])
        if phase == "idle":
            return [ms for _, ms in rows]
        steady = [ms for s, ms in rows if s >= RAMP_S]
        return steady if steady else [ms for _, ms in rows]

    def latency(self, phase: str) -> dict[str, float | None] | None:
        got = self._replies(phase)
        return latency(got) if got else None

    @property
    def loss_pct(self) -> float | None:
        return loss([r for s in STAGES for r in self._replies(s)])

    @property
    def fraction(self) -> float:
        """How far the run is, 0..1, by the phases' planned lengths."""
        before = {"idle": 0.0, "download": IDLE_S, "upload": IDLE_S + LOAD_S}.get(self.stage, 0.0)
        length = IDLE_S if self.stage == "idle" else LOAD_S
        return min(1.0, (before + min(self.seconds, length)) / DURATION_S)

    def snapshot(self) -> dict[str, Any]:
        """The job's progress (GET /api/jobs/<id>): everything the card draws while the test runs."""
        return {"stage": self.stage, "fraction": round(self.fraction, 4),
                "download_mbps": self.mbps("download"), "upload_mbps": self.mbps("upload"),
                "idle": self.latency("idle"), "during_download": self.latency("download"),
                "during_upload": self.latency("upload"), "loss_pct": self.loss_pct,
                "download_series": [list(p) for p in thinned(self.series.get("download", []))],
                "upload_series": [list(p) for p in thinned(self.series.get("upload", []))]}


# --- keeping the latest result --------------------------------------------------------------------

def _phase_to_dict(p: Phase) -> dict[str, Any]:
    return {"name": p.name, "rtts": p.rtts, "mbps": p.mbps, "error": p.error, "capped": p.capped,
            "refused": p.refused, "retry_after_s": p.retry_after_s}


def _phase_from_dict(d: dict[str, Any]) -> Phase:
    rtts = d["rtts"]
    if not isinstance(rtts, dict) or not all(isinstance(v, list) for v in rtts.values()):
        raise ValueError("bad rtts")
    return Phase(str(d["name"]), {str(k): [None if x is None else float(x) for x in v] for k, v in rtts.items()},
                 None if d.get("mbps") is None else float(d["mbps"]), str(d.get("error") or ""),
                 bool(d.get("capped")), None if d.get("refused") is None else int(d["refused"]),
                 None if d.get("retry_after_s") is None else float(d["retry_after_s"]))


def measurement_to_dict(m: Measurement) -> dict[str, Any]:
    return {"idle": _phase_to_dict(m.idle), "download": _phase_to_dict(m.download),
            "upload": _phase_to_dict(m.upload), "server": m.server}


def measurement_from_dict(d: dict[str, Any]) -> Measurement:
    return Measurement(_phase_from_dict(d["idle"]), _phase_from_dict(d["download"]),
                       _phase_from_dict(d["upload"]), d.get("server"))


class Store:
    """`speedtest.json` in the data folder: the latest result with the run's check #14 measurement. A file
    that does not decode reads as empty."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()   # request threads read while a job writes

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.path)

    def last(self) -> tuple[dict[str, Any] | None, Measurement | None]:
        """The latest result and its measurement; either is None when missing or no longer decodes."""
        data = self.load()
        last = data.get("last")
        if not isinstance(last, dict) or not isinstance(last.get("measured_at"), (int, float)):
            return None, None
        try:
            m = measurement_from_dict(data["measurement"]) if isinstance(data.get("measurement"), dict) else None
        except (KeyError, TypeError, ValueError):
            m = None
        return last, m

    def save(self, r: dict[str, Any], m: Measurement, check: list[dict[str, Any]], check_ts: float | None = None) -> None:
        """`check` is check #14's result of the same run, as stored messages (ADR-0006)."""
        with self._lock:
            self._save({**self._read(), "last": r, "measurement": measurement_to_dict(m), "check": check,
                        "check_ts": check_ts if check_ts is not None else r.get("measured_at")})


def default_store() -> Store:
    return Store(config.data_dir() / "speedtest.json")
