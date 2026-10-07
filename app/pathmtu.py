"""Path MTU probe: the largest IPv4 packet that reaches the internet without being split.

Pings with the Don't Fragment flag and a binary search on the payload size. A PPPoE line
carries 1492-byte packets; an interface left at 1500 then loses large packets wherever the
"fragmentation needed" reply is filtered (pages that hang half-loaded, stalled uploads).

The local interface's MTU is a ceiling: Windows refuses a DF packet larger than it, so the
probe can never report more than the current MTU (ADR-0015, rule 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

IP_ICMP_HEADER = 28                  # IPv4 (20) + ICMP (8)
LOW, HIGH = 1280, 1500               # packet sizes searched
TARGETS = ("1.1.1.1", "8.8.8.8")
TRIES = 2                            # a size passes if any of TRIES echoes comes back
PPPOE = 1492                         # the usual answer: tried before searching

Ping = Callable[[str, int], bool]    # (address, payload bytes) -> got a reply


@dataclass(frozen=True)
class PathMtu:
    mtu: int | None                  # largest packet that got through; None: nothing did
    per_target: dict[str, int | None] = field(default_factory=dict)
    probes: int = 0


def _passes(ping: Ping, target: str, packet: int) -> bool:
    return any(ping(target, packet - IP_ICMP_HEADER) for _ in range(TRIES))


def probe_target(ping: Ping, target: str, low: int = LOW, high: int = HIGH) -> tuple[int | None, int]:
    """Largest packet in [low, high] that reaches `target`, and the number of sizes tried."""
    tried = 1
    if not _passes(ping, target, low):
        return None, tried
    tried += 1
    if _passes(ping, target, high):
        return high, tried
    good, bad = low, high            # invariant: good passes, bad fails
    if low < PPPOE < high:           # most lines that fail 1500 are PPPoE: settle it in two more tries
        tried += 1
        if _passes(ping, target, PPPOE):
            good = PPPOE
            tried += 1
            if not _passes(ping, target, PPPOE + 1):
                return PPPOE, tried
            good = PPPOE + 1
        else:
            bad = PPPOE
    while bad - good > 1:
        mid = (good + bad) // 2
        tried += 1
        if _passes(ping, target, mid):
            good = mid
        else:
            bad = mid
    return good, tried


def measure(ping: Ping, targets: tuple[str, ...] = TARGETS) -> PathMtu:
    """The path MTU is the largest size any target proved: one target that filters big echoes
    must not drag the result down, and a size that reached one target crossed the shared first
    hops (the PPPoE link) that set the limit."""
    per_target: dict[str, int | None] = {}
    probes = 0
    for target in targets:
        per_target[target], n = probe_target(ping, target)
        probes += n
    proved = [v for v in per_target.values() if v is not None]
    return PathMtu(max(proved) if proved else None, per_target, probes)


def icmp_ping(timeout_ms: int = 1000) -> Ping:
    from .icmp import Pinger
    pinger = Pinger()

    def ping(address: str, payload: int) -> bool:
        return pinger.ping(address, timeout_ms, size=payload, dont_fragment=True).ok
    return ping
