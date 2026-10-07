"""Calibrations: measurements that measured tweaks take their value from (ADR-0015).

One entry per kind in data/calibration.json:

    {"upload_mbps": {"value": 41.8, "measured_at": 1791331200, "network": "12|192.0.2.1|HomeNet",
                     "detail": {...}}}

A measurement taken while the tweak it feeds is on does not replace the calibration: with an
upload limit active the bufferbloat test measures the limit, and with a lowered MTU the probe
cannot see above it. Recording it would ratchet the value down on every run.
"""
from __future__ import annotations

from typing import Any, Callable

from . import config

KINDS = ("upload_mbps", "path_mtu", "dns_ranking")
MAX_AGE_S = 30 * 86400


def record(kind: str, value: float, network: str | None, now: float, *, detail: dict[str, Any] | None = None,
           tweak_active: bool = False, load: Callable[[], dict] = config.load_calibration,
           save: Callable[[dict], None] = config.save_calibration) -> bool:
    """Store a measurement. False (nothing stored) without a network to tie it to, or while the
    tweak it feeds is on."""
    if kind not in KINDS:
        raise ValueError(f"unknown calibration kind {kind!r}")
    if not network or tweak_active:
        return False
    with config.calibration_lock():
        store = load()
        store[kind] = {"value": value, "measured_at": int(now), "network": network, "detail": detail or {}}
        save(store)
    return True


def get(kind: str, load: Callable[[], dict] = config.load_calibration) -> dict[str, Any] | None:
    entry = load().get(kind)
    if not isinstance(entry, dict) or not isinstance(entry.get("value"), (int, float)):
        return None
    return entry


def usable(entry: dict[str, Any] | None, network: str | None, now: float) -> bool:
    """Measured on this network, recently enough to apply."""
    return (entry is not None and bool(network) and entry.get("network") == network
            and now - float(entry.get("measured_at") or 0) <= MAX_AGE_S)
