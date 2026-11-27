"""The line is measured by one thing at a time: a manual speed test, a measured tweak, or a background
throughput sample (ADR-0027). Overlapping measurements see about half the bandwidth each, so the
background sampler skips while another measurement holds the line (the app/slowdown.js equivalent of
`Api._line_lock`, reachable from the monitor's own thread)."""
from __future__ import annotations

import threading

_lock: threading.Lock | None = None


def register(lock: threading.Lock) -> None:
    """Called once by the local server with the lock its speed test and measured tweak take."""
    global _lock
    _lock = lock


def busy() -> bool:
    """Whether another measurement is loading the line right now."""
    lock = _lock
    return lock is not None and lock.locked()
