"""Connectivity probes that do not depend on ICMP: TCP connect and HTTP 204.

ICMP is the first thing a network may deprioritise or drop, while real traffic (TCP/HTTPS)
works fine; Windows' own "is there internet" check (NCSI) and proxy clients (Clash url-test,
v2ray) use HTTP for that reason. Probes never raise: failures come back as ok=False.
"""
from __future__ import annotations

import http.client
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import USER_AGENT


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    rtt_ms: float | None
    error: str = ""


def tcp_connect(target: str, timeout_s: float = 3.0) -> ProbeResult:
    """target is "host:port". Measures the time to complete the TCP handshake."""
    try:
        host, _, port = target.rpartition(":")
        addr = (host.strip("[]"), int(port))
    except ValueError:
        return ProbeResult(False, None, f"bad target {target!r}")
    started = time.perf_counter()
    try:
        with socket.create_connection(addr, timeout=timeout_s):
            return ProbeResult(True, (time.perf_counter() - started) * 1000.0)
    except socket.timeout:
        return ProbeResult(False, None, "timeout")
    except OSError as exc:
        return ProbeResult(False, None, f"{type(exc).__name__}: {exc}")


def http_204(url: str, timeout_s: float = 3.0) -> ProbeResult:
    """GET url and require exactly 204 No Content. A captive portal usually answers 200 or a
    redirect for such URLs, so anything else is reported as not-connected with the reason."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ProbeResult(False, None, f"bad url {url!r}")
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    started = time.perf_counter()
    conn = None
    try:
        conn = cls(parts.hostname, parts.port, timeout=timeout_s)
        conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Connection": "close"})
        resp = conn.getresponse()
        resp.read(1024)
        rtt = (time.perf_counter() - started) * 1000.0
        if resp.status == 204:
            return ProbeResult(True, rtt)
        kind = "captive portal?" if resp.status in (200, 301, 302, 303, 307, 308) else "unexpected status"
        return ProbeResult(False, None, f"HTTP {resp.status} ({kind})")
    except socket.timeout:
        return ProbeResult(False, None, "timeout")
    except (OSError, http.client.HTTPException) as exc:
        return ProbeResult(False, None, f"{type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            conn.close()


def run_probe(kind: str, target: str, timeout_s: float = 3.0) -> ProbeResult:
    if kind == "tcp":
        return tcp_connect(target, timeout_s)
    if kind == "http":
        return http_204(target, timeout_s)
    return ProbeResult(False, None, f"unknown probe kind {kind!r}")
