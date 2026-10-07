"""Path MTU: the largest IPv4 packet that crosses the whole path, found with "do not fragment" pings.

An access link with a smaller MTU than the PC's interface (PPPoE: 1492) is harmless while routers
answer "fragmentation needed" (PMTUD). When that answer is filtered, large packets vanish: pages
hang half-loaded and big downloads stall while small pings look perfect.

The search is pure: `probe()` takes a function that sends one packet of a given total size and says
what happened, so the tests drive it with a fake path. `measure()` wires it to real ICMP
(app/icmp.py, no Admin, no child process). Read-only: nothing on the machine is changed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable

OVERHEAD = 28            # IPv4 header (20) + ICMP echo header (8): packet size = payload + 28
MIN_MTU = 576            # every IPv4 host must accept this; the search never goes below it
TARGETS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
TIMEOUT_MS = 800
TRIES = 2                # a lost packet is retried once before it counts as "too big"

PASSED, TOO_BIG, LOST = "passed", "too_big", "lost"

Send = Callable[[int], str]   # total packet size in bytes -> PASSED / TOO_BIG / LOST


@dataclass(frozen=True)
class PathResult:
    target: str
    mtu: int | None      # largest packet that got through; None when the target never answered
    probes: int = 0      # packets sent


def probe(send: Send, ceiling: int, tries: int = TRIES) -> tuple[int | None, int]:
    """(path MTU, packets sent). Binary search between MIN_MTU and `ceiling` (the interface MTU).

    A TOO_BIG answer is final; a LOST packet is retried, so one random loss does not shrink the
    result. Returns (None, n) when even a MIN_MTU packet gets no reply (target filters ICMP).
    """
    sent = 0

    def fits(size: int) -> bool:
        nonlocal sent
        for _ in range(tries):
            sent += 1
            outcome = send(size)
            if outcome == PASSED:
                return True
            if outcome == TOO_BIG:
                return False
        return False

    if ceiling < MIN_MTU or not fits(MIN_MTU):
        return None, sent
    if fits(ceiling):
        return ceiling, sent
    good, bad = MIN_MTU, ceiling
    while bad - good > 1:
        mid = (good + bad) // 2
        if fits(mid):
            good = mid
        else:
            bad = mid
    return good, sent


def real_send(target: str, timeout_ms: int = TIMEOUT_MS) -> tuple[Send, Callable[[], None]]:
    """A Send for one target over its own ICMP handle (Pinger is not thread-safe), and its closer."""
    from . import icmp
    pinger = icmp.Pinger()

    def send(size: int) -> str:
        r = pinger.ping(target, timeout_ms, size=size - OVERHEAD, dont_fragment=True)
        if r.ok:
            return PASSED
        return TOO_BIG if r.status == icmp.IP_PACKET_TOO_BIG else LOST

    return send, pinger.close


def measure(ceiling: int, targets: Iterable[str] = TARGETS,
            sender: Callable[[str], tuple[Send, Callable[[], None]]] = real_send) -> list[PathResult]:
    """Probe every target in parallel; results in the order of `targets`."""
    def one(target: str) -> PathResult:
        send, close = sender(target)
        try:
            mtu, sent = probe(send, ceiling)
        finally:
            close()
        return PathResult(target, mtu, sent)

    targets = list(targets)
    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
        return list(pool.map(one, targets))
