"""Bufferbloat measurement: how much does latency grow while the line is busy?

Idle pings first, then pings while downloading, then while uploading. Generates real
traffic, so it only runs when the user asks (docs/DIAGNOSTICS.md, check 14). Everything
that touches the network is injectable so the maths is tested without a network.

The same run is the speed test (ADR-0024): with `events`, `measure` reports each phase, the live
rate read from the loads' `Meter` on a fixed beat, and each ping round, and keeps each direction's
rate after the ramp (`Phase.steady_mbps`); `app/speedtest.py` turns that into the figures.
"""
from __future__ import annotations

import http.client
import socket
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from . import USER_AGENT

# speed.cloudflare.com answers 403 to a single request above ~25 MB (verified 2026-10-04: 25 MB ok,
# 100 MB refused), so each request asks for 25 MB and the download worker simply asks again.
DOWN_URL = "https://speed.cloudflare.com/__down?bytes=25000000"
UP_URL = "https://speed.cloudflare.com/__up"
RAMP_S = 2.0          # samples taken while the line is still picking up speed are dropped
PING_TIMEOUT_MS = 900  # a ping lost under load waited at least this long
# A load phase runs for LOAD_S; MAX_BYTES only stops it on a very fast line, to bound the data used.
# (It was 100 MB until 2026-10-07: every phase then read 80 Mbps on a line that uploads ~230.)
MAX_BYTES = 1_000_000_000
LOAD_S = 10.0
# On mobile data each direction stops at this instead (500 MB in all), so a line above 200 Mbps reads
# as capped: the result is then incomplete, never a false ok (as on Apple).
METERED_MAX_BYTES = 250_000_000
# The most a load phase can report: it stops at MAX_BYTES, so a faster line reads as this.
LOAD_CEILING_MBPS = MAX_BYTES * 8 / LOAD_S / 1e6
CHUNK = 64 * 1024

METER_INTERVAL = 0.25  # how often the live rate is read while a direction loads

Ping = Callable[[str], "float | None"]          # address -> RTT in ms, None if lost
# (seconds, stop) -> bytes moved. With `measure(events=...)` it is also given `meter=`, a Meter it
# counts its bytes into as they move.
Load = Callable[..., int]
# ("stage", name) | ("rate", name, seconds, mbps) | ("ping", name, seconds, ms) | ("ended", name, Phase)
Event = tuple


@dataclass
class Phase:
    name: str                                   # "idle" | "download" | "upload"
    rtts: dict[str, list[float | None]] = field(default_factory=dict)   # target label -> samples
    mbps: float | None = None
    error: str = ""
    capped: bool = False                        # the load stopped at its byte limit: the line may be faster
    refused: int | None = None                  # HTTP status when the test server refused the load
    retry_after_s: float | None = None          # how long the server asked us to wait, when it said
    # Only with `measure(events=...)`: the rate after the ramp (what a speed test reports; `mbps` stays
    # the whole phase's average, which check #14 and its vectors use) and the live rates read.
    steady_mbps: float | None = None
    series: list[tuple[float, float]] = field(default_factory=list)   # (seconds into the phase, Mbps)


@dataclass
class Measurement:
    idle: Phase
    download: Phase
    upload: Phase
    server: str | None = None                   # the test server's location code (e.g. HKG), when a reply named it


class Meter:
    """What a load has moved so far, read while it runs, and the test server's location."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bytes = 0
        self.server: str | None = None

    def add(self, count: int) -> None:
        with self._lock:
            self._bytes += count

    @property
    def bytes(self) -> int:
        with self._lock:
            return self._bytes

    def note_server(self, code: str | None) -> None:
        if code and self.server is None:
            self.server = code


class LiveRate:
    """The live figure: the rate over about the last `window` seconds, from readings of a running total."""

    def __init__(self, window: float = 1.0) -> None:
        self.window = window
        self._points: list[tuple[float, int]] = []

    def add(self, seconds: float, total: int) -> float | None:
        """Mbit/s since the oldest reading still in the window; None until two readings are apart in time."""
        self._points.append((seconds, total))
        # The base is the newest reading at least `window` old, so the span covers the whole window.
        while len(self._points) > 2 and self._points[1][0] <= seconds - self.window:
            self._points.pop(0)
        base_s, base_bytes = self._points[0]
        if seconds <= base_s:
            return None
        return max(0, total - base_bytes) * 8 / (seconds - base_s) / 1e6


def steady_mbps(at_ramp: tuple[float, int] | None, total: int, seconds: float) -> float | None:
    """The rate after the ramp: what moved between the ramp's reading and the end of the load, over that
    time. None when the load ended within half a second of the ramp (too short to trust)."""
    if at_ramp is None or seconds - at_ramp[0] < 0.5 or total <= at_ramp[1]:
        return None
    return (total - at_ramp[1]) * 8 / (seconds - at_ramp[0]) / 1e6


def server_code(colo: str | None, cf_ray: str | None) -> str | None:
    """speed.cloudflare.com names its data centre in `colo`; any Cloudflare reply ends `CF-RAY` with it."""
    if colo and colo.strip():
        return colo.strip()
    if cf_ray and "-" in cf_ray:
        return cf_ray.rsplit("-", 1)[1].strip() or None
    return None


# --- the load generators ---------------------------------------------------------------------

class ServerRefused(OSError):
    """The test server refused the load (HTTP 429/403): a limit on its side, not a property of the line.
    speed.cloudflare.com does this per address after a few runs and says when to come back."""

    def __init__(self, status: int, retry_after_s: float | None) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.retry_after_s = status, retry_after_s


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:   # an HTTP date: rare, and a rough "later" is enough
        return None


def _connect(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return cls(parts.hostname, parts.port, timeout=timeout), path


def http_download(url: str, seconds: float, stop: threading.Event, connections: int = 4,
                  max_bytes: int = MAX_BYTES, meter: Meter | None = None) -> int:
    """Pull `url` over several connections until the deadline, `stop` or `max_bytes`. Returns bytes read.
    `meter`, if given, counts the bytes as they arrive and keeps the server's location."""
    total, lock = [0], threading.Lock()
    deadline = time.monotonic() + seconds
    errors: list[str] = []
    refused: list[ServerRefused] = []

    def worker() -> None:
        while not stop.is_set() and time.monotonic() < deadline and total[0] < max_bytes and not refused:
            conn = None
            try:
                conn, path = _connect(url, 5)
                conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Connection": "close"})
                resp = conn.getresponse()
                if meter is not None:
                    meter.note_server(server_code(resp.getheader("colo"), resp.getheader("CF-RAY")))
                if resp.status in (403, 429):
                    refused.append(ServerRefused(resp.status, _retry_after(resp.getheader("Retry-After"))))
                    return
                if resp.status != 200:
                    errors.append(f"HTTP {resp.status}")
                    return
                while not stop.is_set() and time.monotonic() < deadline and total[0] < max_bytes:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    with lock:
                        total[0] += len(chunk)
                    if meter is not None:
                        meter.add(len(chunk))
            except (OSError, http.client.HTTPException) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                return
            finally:
                if conn is not None:
                    conn.close()   # closing mid-stream ends the transfer at the deadline

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(connections)]
    [t.start() for t in threads]
    [t.join(seconds + 8) for t in threads]
    if refused:   # even after some data: the load was not kept up, so the phase measured a part-time load
        raise refused[0]
    if total[0] == 0 and errors:
        raise OSError(errors[0])
    return total[0]


def _zeros(count: int, sent: list[int], lock: threading.Lock, stop: threading.Event,
           deadline: float, meter: Meter | None = None) -> Iterator[bytes]:
    block = bytes(CHUNK)
    left = count
    while left > 0 and not stop.is_set() and time.monotonic() < deadline:
        n = min(CHUNK, left)
        left -= n
        with lock:
            sent[0] += n
        if meter is not None:
            meter.add(n)
        yield block[:n]


def http_upload(url: str, seconds: float, stop: threading.Event, connections: int = 4,
                max_bytes: int = MAX_BYTES, meter: Meter | None = None) -> int:
    """POST zeros over several connections until the deadline. Returns bytes sent (counted into `meter`
    as they are handed to the socket, if given)."""
    sent, lock = [0], threading.Lock()
    deadline = time.monotonic() + seconds
    errors: list[str] = []
    per_request = 25_000_000

    def worker() -> None:
        while not stop.is_set() and time.monotonic() < deadline and sent[0] < max_bytes:
            conn = None
            try:
                conn, path = _connect(url, 5)
                conn.putrequest("POST", path)
                conn.putheader("User-Agent", USER_AGENT)
                conn.putheader("Content-Type", "application/octet-stream")
                conn.putheader("Content-Length", str(per_request))
                conn.endheaders()
                for chunk in _zeros(per_request, sent, lock, stop, deadline, meter):
                    conn.sock.sendall(chunk)
                    if sent[0] >= max_bytes:
                        break
            except (OSError, http.client.HTTPException) as exc:
                if not (stop.is_set() or time.monotonic() >= deadline):
                    errors.append(f"{type(exc).__name__}: {exc}")
                return
            finally:
                if conn is not None:
                    conn.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(connections)]
    [t.start() for t in threads]
    [t.join(seconds + 8) for t in threads]
    if sent[0] == 0 and errors:
        raise OSError(errors[0])
    return sent[0]


# --- measuring ---------------------------------------------------------------------------------

def _sample(ping: Ping, targets: dict[str, str], seconds: float, interval: float,
            sleep: Callable[[float], None], clock: Callable[[], float],
            starts: list[float] | None = None,
            on_round: Callable[[float, "float | None"], None] | None = None,
            origin: float | None = None) -> dict[str, list[float | None]]:
    """Ping every target once per `interval` for `seconds` (targets pinged in parallel). `starts`, if
    given, receives the clock time each round began: under load a round can take most of a second.
    `on_round`, if given, gets each round's start (seconds after `origin`, default now) and the
    "internet" target's reply."""
    out: dict[str, list[float | None]] = {label: [] for label in targets}
    origin = clock() if origin is None else origin
    end = clock() + seconds
    while clock() < end:
        started = clock()
        if starts is not None:
            starts.append(started)
        threads = []
        results: dict[str, float | None] = {}

        def one(label: str, addr: str) -> None:
            try:
                results[label] = ping(addr)
            except Exception:
                results[label] = None

        for label, addr in targets.items():
            t = threading.Thread(target=one, args=(label, addr), daemon=True)
            t.start()
            threads.append(t)
        [t.join(2) for t in threads]
        for label in targets:
            out[label].append(results.get(label))
        if on_round is not None:
            on_round(started - origin, results.get("internet"))
        sleep(max(0.0, interval - (clock() - started)))
    return out


def run_meter(meter: Meter, started: float, done: threading.Event, report: Callable[[float, float], None], *,
              clock: Callable[[], float] = time.monotonic, ramp_s: float = RAMP_S,
              interval: float = METER_INTERVAL,
              wait: Callable[[float], bool] | None = None) -> tuple[tuple[float, int] | None, list[tuple[float, float]]]:
    """Read `meter` every `interval` until `done` (the load ended: a frozen total would read as a rate
    falling to zero), report the rate over the last second, and return the first reading taken once
    the ramp was over with the rates read. On a fixed beat from `started`, so a slow report does not
    stretch the interval."""
    wait = wait or done.wait
    rate, at_ramp, series, tick = LiveRate(), None, [], 0
    while not done.is_set():
        seconds, total = clock() - started, meter.bytes
        if at_ramp is None and seconds >= ramp_s:
            at_ramp = (seconds, total)
        mbps = rate.add(seconds, total)
        if mbps is not None:
            series.append((seconds, mbps))
            try:
                report(seconds, mbps)
            except Exception:   # a broken listener must not stop the reading
                pass
        tick += 1
        if wait(max(0.0, started + tick * interval - clock())):
            break
    return at_ramp, series


def measure(ping: Ping, targets: dict[str, str], download: Load | None, upload: Load, *, idle_s: float = 4,
            load_s: float = LOAD_S, interval: float = 0.2, ramp_s: float = RAMP_S, max_bytes: int = MAX_BYTES,
            sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
            events: Callable[[Event], None] | None = None) -> Measurement:
    """download=None skips the download phase (a measured tweak only needs the upload, ADR-0016).
    `max_bytes` is the byte limit the loads stop at; a phase that reaches it is marked `capped`.
    With `events` (the speed test, ADR-0024) each load is also given `meter=`, read every
    METER_INTERVAL, and `events` gets each phase's start, the live rate, each ping round's Internet
    reply and each loaded phase once it has ended."""
    def emit(*event: Any) -> None:
        if events is not None:
            try:
                events(event)
            except Exception:   # a broken listener must not stop the measurement
                pass

    emit("stage", "idle")
    idle = Phase("idle", _sample(ping, targets, idle_s, interval, sleep, clock,
                                 on_round=(lambda s, ms: emit("ping", "idle", s, ms)) if events else None))
    server: list[str] = []

    def loaded(name: str, load: Load) -> Phase:
        emit("stage", name)
        phase = Phase(name)
        stop, moved, ended = threading.Event(), [0], [0.0]
        meter, load_done, metered = Meter(), threading.Event(), events is not None
        failure: list[str] = []

        def run() -> None:
            try:
                moved[0] = load(load_s, stop, meter=meter) if metered else load(load_s, stop)
            except ServerRefused as exc:
                failure.append(f"{type(exc).__name__}: {exc}")
                phase.refused, phase.retry_after_s = exc.status, exc.retry_after_s
            except Exception as exc:
                failure.append(f"{type(exc).__name__}: {exc}")
            finally:
                ended[0] = clock() - started
                load_done.set()

        thread = threading.Thread(target=run, daemon=True)
        started = clock()
        reading: list[Any] = []
        meter_thread = None
        if metered:
            meter_thread = threading.Thread(target=lambda: reading.extend(run_meter(
                meter, started, load_done, lambda s, mbps: emit("rate", name, s, mbps), clock=clock, ramp_s=ramp_s)),
                daemon=True)
        thread.start()
        if meter_thread is not None:
            meter_thread.start()
        starts: list[float] = []
        samples = _sample(ping, targets, load_s, interval, sleep, clock, starts,
                          on_round=(lambda s, ms: emit("ping", name, s, ms)) if events else None, origin=started)
        stop.set()
        thread.join(10)
        elapsed = max(clock() - started, 1e-6)
        # Drop the ramp by time, not by count: on a bloated line each round waits for slow or lost pings,
        # so a fixed count of rounds can cover most of the phase.
        keep = next((i for i, t in enumerate(starts) if t - started >= ramp_s), len(starts))
        phase.rtts = {label: s[keep:] for label, s in samples.items()}
        phase.mbps = moved[0] * 8 / elapsed / 1e6
        phase.error = failure[0] if failure else ""
        phase.capped = moved[0] >= max_bytes
        if meter_thread is not None:
            load_done.set()       # the load thread may still hang past its join
            meter_thread.join(2)
            at_ramp, phase.series = reading if reading else (None, [])
            # A worker blocked past the deadline (a socket timeout) delays the load's return, not its
            # bytes: the load never runs longer than load_s.
            phase.steady_mbps = steady_mbps(at_ramp, moved[0], min(ended[0] or elapsed, load_s))
            if meter.server and not server:
                server.append(meter.server)
        emit("ended", name, phase)
        return phase

    skipped = Phase("download", error="skipped")
    down = loaded("download", download) if download else skipped
    up = loaded("upload", upload)
    return Measurement(idle, down, up, server[0] if server else None)


def upload_summary(m: Measurement, label: str = "internet") -> dict[str, float | int] | None:
    """The upload phase of `m` reduced to what a measured tweak keeps (ADR-0016): upload Mbps, median
    idle and loaded latency to `label`, samples answered and attempted. A ping lost under load counts
    as PING_TIMEOUT_MS, a lower bound: on a badly bloated line the slowest pings are the lost ones.
    None when the load did not run or there is no idle baseline."""
    if m.upload.mbps is None or (m.upload.error and not m.upload.mbps):
        return None
    idle = [s for s in m.idle.rtts.get(label, []) if s is not None]
    loaded = m.upload.rtts.get(label, [])
    got = [s for s in loaded if s is not None]
    if not idle or not loaded:
        return None
    floored = [PING_TIMEOUT_MS if s is None else max(s, 0.0) for s in loaded]
    return {"upload_mbps": round(float(m.upload.mbps), 2), "idle_ms": round(statistics.median(idle), 1),
            "loaded_ms": round(statistics.median(floored), 1), "samples": len(got), "attempted": len(loaded),
            "loss_pct": round(100.0 * (len(loaded) - len(got)) / len(loaded), 1)}


def real_loads(max_bytes: int = MAX_BYTES) -> tuple[Load, Load]:
    return (lambda s, stop, meter=None: http_download(DOWN_URL, s, stop, max_bytes=max_bytes, meter=meter),
            lambda s, stop, meter=None: http_upload(UP_URL, s, stop, max_bytes=max_bytes, meter=meter))


def real_ping() -> Ping:
    """A ping function using the ICMP API; thread-safe because each calling thread gets its own handle."""
    from . import icmp
    local = threading.local()

    def ping(address: str) -> float | None:
        p = getattr(local, "p", None)
        if p is None:
            p = local.p = icmp.Pinger()
        r = p.ping(address, PING_TIMEOUT_MS)
        return r.rtt_ms if r.ok else None
    return ping
