"""Bufferbloat measurement: how much does latency grow while the line is busy?

Idle pings first, then pings while downloading, then while uploading. Generates real
traffic, so it only runs when the user asks (docs/DIAGNOSTICS.md, check 14). Everything
that touches the network is injectable so the maths is tested without a network.
"""
from __future__ import annotations

import http.client
import socket
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator
from urllib.parse import urlsplit

from . import USER_AGENT

# speed.cloudflare.com answers 403 to a single request above ~25 MB (verified 2026-10-04: 25 MB ok,
# 100 MB refused), so each request asks for 25 MB and the download worker simply asks again.
DOWN_URL = "https://speed.cloudflare.com/__down?bytes=25000000"
UP_URL = "https://speed.cloudflare.com/__up"
RAMP_S = 2.0          # samples taken while the line is still picking up speed are dropped
# A load phase runs for LOAD_S; MAX_BYTES only stops it on a very fast line, to bound the data used.
# (It was 100 MB until 2026-10-07: every phase then read 80 Mbps on a line that uploads ~230.)
MAX_BYTES = 1_000_000_000
LOAD_S = 10.0
# The most a load phase can report: it stops at MAX_BYTES, so a faster line reads as this.
LOAD_CEILING_MBPS = MAX_BYTES * 8 / LOAD_S / 1e6
CHUNK = 64 * 1024

Ping = Callable[[str], "float | None"]          # address -> RTT in ms, None if lost
Load = Callable[[float, threading.Event], int]  # (seconds, stop) -> bytes moved


@dataclass
class Phase:
    name: str                                   # "idle" | "download" | "upload"
    rtts: dict[str, list[float | None]] = field(default_factory=dict)   # target label -> samples
    mbps: float | None = None
    error: str = ""
    capped: bool = False                        # the load stopped at its byte limit: the line may be faster
    refused: int | None = None                  # HTTP status when the test server refused the load
    retry_after_s: float | None = None          # how long the server asked us to wait, when it said


@dataclass
class Measurement:
    idle: Phase
    download: Phase
    upload: Phase


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
                  max_bytes: int = MAX_BYTES) -> int:
    """Pull `url` over several connections until the deadline, `stop` or `max_bytes`. Returns bytes read."""
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
           deadline: float) -> Iterator[bytes]:
    block = bytes(CHUNK)
    left = count
    while left > 0 and not stop.is_set() and time.monotonic() < deadline:
        n = min(CHUNK, left)
        left -= n
        with lock:
            sent[0] += n
        yield block[:n]


def http_upload(url: str, seconds: float, stop: threading.Event, connections: int = 4,
                max_bytes: int = MAX_BYTES) -> int:
    """POST zeros over several connections until the deadline. Returns bytes sent."""
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
                for chunk in _zeros(per_request, sent, lock, stop, deadline):
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
            sleep: Callable[[float], None], clock: Callable[[], float]) -> dict[str, list[float | None]]:
    """Ping every target once per `interval` for `seconds` (targets pinged in parallel)."""
    out: dict[str, list[float | None]] = {label: [] for label in targets}
    end = clock() + seconds
    while clock() < end:
        started = clock()
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
        sleep(max(0.0, interval - (clock() - started)))
    return out


def measure(ping: Ping, targets: dict[str, str], download: Load | None, upload: Load, *, idle_s: float = 4,
            load_s: float = LOAD_S, interval: float = 0.2, ramp_s: float = RAMP_S, max_bytes: int = MAX_BYTES,
            sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Measurement:
    """download=None skips the download phase (a measured tweak only needs the upload, ADR-0016).
    `max_bytes` is the byte limit the loads stop at; a phase that reaches it is marked `capped`."""
    idle = Phase("idle", _sample(ping, targets, idle_s, interval, sleep, clock))

    def loaded(name: str, load: Load) -> Phase:
        phase = Phase(name)
        stop, moved = threading.Event(), [0]
        failure: list[str] = []

        def run() -> None:
            try:
                moved[0] = load(load_s, stop)
            except ServerRefused as exc:
                failure.append(f"{type(exc).__name__}: {exc}")
                phase.refused, phase.retry_after_s = exc.status, exc.retry_after_s
            except Exception as exc:
                failure.append(f"{type(exc).__name__}: {exc}")

        thread = threading.Thread(target=run, daemon=True)
        started = clock()
        thread.start()
        samples = _sample(ping, targets, load_s, interval, sleep, clock)
        stop.set()
        thread.join(10)
        elapsed = max(clock() - started, 1e-6)
        drop = int(ramp_s / interval)
        phase.rtts = {label: s[drop:] if len(s) > drop else [] for label, s in samples.items()}
        phase.mbps = moved[0] * 8 / elapsed / 1e6
        phase.error = failure[0] if failure else ""
        phase.capped = moved[0] >= max_bytes
        return phase

    skipped = Phase("download", error="skipped")
    return Measurement(idle, loaded("download", download) if download else skipped, loaded("upload", upload))


def upload_summary(m: Measurement, label: str = "internet") -> dict[str, float | int] | None:
    """The upload phase of `m` reduced to what a measured tweak keeps (ADR-0016): upload Mbps, median
    idle and loaded latency to `label`, number of loaded samples. None when the load did not run."""
    if m.upload.mbps is None or (m.upload.error and not m.upload.mbps):
        return None
    idle = [s for s in m.idle.rtts.get(label, []) if s is not None]
    loaded = m.upload.rtts.get(label, [])
    got = [s for s in loaded if s is not None]
    if not idle or not got:
        return None
    return {"upload_mbps": round(float(m.upload.mbps), 2), "idle_ms": round(statistics.median(idle), 1),
            "loaded_ms": round(statistics.median(got), 1), "samples": len(got),
            "loss_pct": round(100.0 * (len(loaded) - len(got)) / len(loaded), 1)}


def real_loads() -> tuple[Load, Load]:
    return (lambda s, stop: http_download(DOWN_URL, s, stop), lambda s, stop: http_upload(UP_URL, s, stop))


def real_ping() -> Ping:
    """A ping function using the ICMP API; thread-safe because each calling thread gets its own handle."""
    from . import icmp
    local = threading.local()

    def ping(address: str) -> float | None:
        p = getattr(local, "p", None)
        if p is None:
            p = local.p = icmp.Pinger()
        r = p.ping(address, 900)
        return r.rtt_ms if r.ok else None
    return ping
