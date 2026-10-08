"""Whether the supporter (Pro) features are unlocked (ADR-0021 point 7).

A stub until the key check exists (Polar.sh, its own ADR): every feature behind `is_supporter()` is open
to everyone for now. Callers must go through this function and nothing else, so the real check is one
change here. Do not turn this into a real check in the change that adds a feature behind it.
"""
from __future__ import annotations


def is_supporter() -> bool:
    return True
