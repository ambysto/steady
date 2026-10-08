"""One throughput sample: a short download and a short upload, timed from the first byte (ADR-0021).

The monitor takes one every `speed.interval_min` minutes when the user turned it on. It is not a speed
test: it moves ~12 MB, just enough to tell a line that delivers its normal speed from one that has
fallen to a fraction of it. Both directions are timed from the first byte of the body, so the TLS
handshake and the first round trip do not count, and a baseline built from the same method compares
with itself. Everything that touches the network or the interface counters is injectable.
"""
from __future__ import annotations

import ctypes
import http.client
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from . import USER_AGENT
from .bufferbloat import ServerRefused, _retry_after

DOWN_URL = "https://speed.cloudflare.com/__down?bytes={bytes}"
UP_URL = "https://speed.cloudflare.com/__up"
DOWN_BYTES = 10_000_000
UP_BYTES = 2_000_000
TIMEOUT_S = 15.0           # a phase that has not finished by then reports what it moved so far
CHUNK = 64 * 1024
OVERHEAD = 1.08            # headers, TLS records, ACKs: what a transfer costs on the wire beyond its payload

# Traffic of this PC other than the sample itself, over the whole sample, counts as "own traffic" when it is
# both at least FOREIGN_MIN_MBPS and at least FOREIGN_SHARE of what the sample moved: the PC is then
# competing with itself, which says nothing about the line.
FOREIGN_MIN_MBPS = 5.0
FOREIGN_SHARE = 0.25

Download = Callable[[int, float], "tuple[int, float]"]   # (bytes wanted, timeout s) -> (bytes read, seconds)
Upload = Callable[[int, float], "tuple[int, float]"]     # (bytes to send, timeout s) -> (bytes sent, seconds)


def mbps(nbytes: int, seconds: float) -> float:
    return nbytes * 8 / max(seconds, 1e-3) / 1e6


def _connect(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return cls(parts.hostname or "", parts.port, timeout=timeout), path


def timed_download(nbytes: int, timeout_s: float = TIMEOUT_S, url: str = DOWN_URL) -> tuple[int, float]:
    """GET `nbytes` and return (bytes read, seconds from the first byte to the last or the deadline)."""
    conn, path = _connect(url.format(bytes=nbytes), timeout_s)
    try:
        conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Connection": "close"})
        resp = conn.getresponse()
        if resp.status in (403, 429):
            raise ServerRefused(resp.status, _retry_after(resp.getheader("Retry-After")))
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status}")
        started = time.monotonic()
        deadline = started + timeout_s
        total = 0
        while total < nbytes and time.monotonic() < deadline:
            chunk = resp.read(min(CHUNK, nbytes - total))
            if not chunk:
                break
            total += len(chunk)
        return total, time.monotonic() - started
    except http.client.HTTPException as exc:
        raise OSError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()


def timed_upload(nbytes: int, timeout_s: float = TIMEOUT_S, url: str = UP_URL) -> tuple[int, float]:
    """POST `nbytes` of zeros; the clock stops when the server has answered, i.e. has received them all."""
    conn, path = _connect(url, timeout_s)
    try:
        conn.putrequest("POST", path)
        conn.putheader("User-Agent", USER_AGENT)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(nbytes))
        conn.endheaders()
        started = time.monotonic()
        block = bytes(CHUNK)
        sent = 0
        while sent < nbytes:
            part = min(CHUNK, nbytes - sent)
            conn.sock.sendall(block[:part])   # type: ignore[union-attr]
            sent += part
        resp = conn.getresponse()
        resp.read()
        if resp.status in (403, 429):
            raise ServerRefused(resp.status, _retry_after(resp.getheader("Retry-After")))
        if resp.status >= 400:
            raise OSError(f"HTTP {resp.status}")
        return sent, time.monotonic() - started
    except http.client.HTTPException as exc:
        raise OSError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()


# --- this PC's own traffic ---------------------------------------------------------------------

class _MibIfRow(ctypes.Structure):
    _fields_ = [("wszName", ctypes.c_wchar * 256), ("dwIndex", ctypes.c_uint32), ("dwType", ctypes.c_uint32),
                ("dwMtu", ctypes.c_uint32), ("dwSpeed", ctypes.c_uint32), ("dwPhysAddrLen", ctypes.c_uint32),
                ("bPhysAddr", ctypes.c_ubyte * 8), ("dwAdminStatus", ctypes.c_uint32),
                ("dwOperStatus", ctypes.c_uint32), ("dwLastChange", ctypes.c_uint32),
                ("dwInOctets", ctypes.c_uint32), ("dwInUcastPkts", ctypes.c_uint32),
                ("dwInNUcastPkts", ctypes.c_uint32), ("dwInDiscards", ctypes.c_uint32),
                ("dwInErrors", ctypes.c_uint32), ("dwInUnknownProtos", ctypes.c_uint32),
                ("dwOutOctets", ctypes.c_uint32), ("dwOutUcastPkts", ctypes.c_uint32),
                ("dwOutNUcastPkts", ctypes.c_uint32), ("dwOutDiscards", ctypes.c_uint32),
                ("dwOutErrors", ctypes.c_uint32), ("dwOutQLen", ctypes.c_uint32),
                ("dwDescrLen", ctypes.c_uint32), ("bDescr", ctypes.c_ubyte * 256)]


def interface_octets(if_index: int) -> int | None:
    """Bytes received plus sent by an interface so far (32-bit counters: take differences modulo 2**32,
    see `traffic_delta`). None when the call fails."""
    try:
        row = _MibIfRow()
        row.dwIndex = if_index
        if ctypes.WinDLL("iphlpapi").GetIfEntry(ctypes.byref(row)) != 0:
            return None
        return row.dwInOctets + row.dwOutOctets
    except (AttributeError, OSError):
        return None


def traffic_delta(before: int | None, after: int | None) -> int | None:
    """Bytes between two readings of the summed 32-bit in+out counters. Each counter wraps on its own, so
    one wrap in a 10 s window is possible; a negative difference is read as one wrap of the sum."""
    if before is None or after is None:
        return None
    diff = after - before
    return diff if diff >= 0 else diff + 2 ** 32


@dataclass
class Sample:
    down_mbps: float | None = None
    up_mbps: float | None = None
    foreign_mbps: float | None = None   # this PC's other traffic during the sample; None: not measurable
    refused: int | None = None          # HTTP status when the test server refused
    retry_after_s: float | None = None
    error: str = ""

    @property
    def own_traffic(self) -> bool:
        if self.foreign_mbps is None:
            return False
        moved = [v for v in (self.down_mbps, self.up_mbps) if v is not None]
        return bool(moved) and self.foreign_mbps >= max(FOREIGN_MIN_MBPS, FOREIGN_SHARE * max(moved))


def take_sample(*, download: Download = timed_download, upload: Upload = timed_upload,
                octets: Callable[[], int | None] = lambda: None,
                clock: Callable[[], float] = time.monotonic) -> Sample:
    """Download then upload. A phase the server refused or that failed leaves its number None; a refusal
    stops the sample (the other phase would be refused too)."""
    out = Sample()
    started, before = clock(), octets()
    moved = 0
    try:
        got, secs = download(DOWN_BYTES, TIMEOUT_S)
        moved += got
        if got > 0:
            out.down_mbps = mbps(got, secs)
        got, secs = upload(UP_BYTES, TIMEOUT_S)
        moved += got
        if got > 0:
            out.up_mbps = mbps(got, secs)
    except ServerRefused as exc:
        out.refused, out.retry_after_s = exc.status, exc.retry_after_s
    except OSError as exc:
        out.error = f"{type(exc).__name__}: {exc}"
    spent = max(clock() - started, 1e-3)
    delta = traffic_delta(before, octets())
    if delta is not None and moved:
        out.foreign_mbps = max(0.0, delta - moved * OVERHEAD) * 8 / spent / 1e6
    return out
