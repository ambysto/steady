"""The measurement behind a measured tweak's value (ADR-0016).

A measured tweak derives its value from a measurement taken on the network in use, just
before it is turned on. This module checks such a measurement (the elevated helper trusts
nothing from its caller), names the network it was taken on without keeping the gateway's
address, tells when a stored measurement no longer fits, and judges the measurement taken
again right after applying. Everything is pure except measure_upload / measure_path_mtu / current_network_id.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
from typing import Any, Callable

from . import bufferbloat

MAX_AGE_S = 600                 # the elevated helper refuses an older measurement
STALE_AFTER_S = 30 * 86400      # the UI asks to measure again after this
MIN_UPLOAD_MBPS = 1.0           # below this the load did not really load the line
AT_CEILING = 0.95               # this close to the load's own limit, the line may be faster
MIN_SAMPLES = 8                 # loaded latency samples needed
NOT_NEEDED_MS = 30              # rise under load below this: nothing to fix (diagnostics "ok")
HELPED_MIN_DROP_MS, HELPED_MIN_DROP = 20.0, 0.30
_NETWORK_RE = re.compile(r"^[0-9a-f]{16}$")


def network_id(gateway: str | None, physical: str | None) -> str | None:
    """16 hex digits naming the router (gateway IPv4 + its physical address) without storing either."""
    if not gateway:
        return None
    raw = f"{gateway}|{(physical or '').lower()}".encode("ascii", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def current_network_id(route: Callable[[], dict | None] | None = None,
                       physical: Callable[[str], str | None] | None = None) -> str | None:
    from . import winutil
    found = (route or winutil.default_route_native)()
    gateway = found.get("gateway") if found else None
    return network_id(gateway, (physical or winutil.neighbor_physical_address)(gateway) if gateway else None)


def upload_record(summary: dict[str, Any], network: str | None, now: float) -> dict[str, Any]:
    """A measurement as a measured tweak receives and stores it."""
    return {"kind": "upload", **summary, "measured_at": int(now), "network": network}


def _number(m: dict, key: str, lo: float, hi: float) -> float:
    value = m.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ValueError(f"measurement {key} missing or out of range: {value!r}")
    return float(value)


def check_upload(m: Any, now: float) -> dict[str, Any]:
    """The upload measurement `m`, validated and reduced to its known fields; ValueError otherwise."""
    if not isinstance(m, dict) or m.get("kind") != "upload":
        raise ValueError("not an upload measurement")
    out: dict[str, Any] = {"kind": "upload",
                           "upload_mbps": _number(m, "upload_mbps", 0, 100_000),
                           "idle_ms": _number(m, "idle_ms", 0, 60_000),
                           "loaded_ms": _number(m, "loaded_ms", 0, 60_000),
                           "loss_pct": _number(m, "loss_pct", 0, 100)}
    samples, measured_at = m.get("samples"), m.get("measured_at")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 0 <= samples <= 100_000:
        raise ValueError(f"measurement samples out of range: {samples!r}")
    if isinstance(measured_at, bool) or not isinstance(measured_at, int):
        raise ValueError("measurement has no time")
    if not now - MAX_AGE_S <= measured_at <= now + 60:
        raise ValueError("measurement is too old (or from the future)")
    network = m.get("network")
    if network is not None and not (isinstance(network, str) and _NETWORK_RE.match(network)):
        raise ValueError("bad network id")
    out.update(samples=samples, measured_at=measured_at, network=network)
    return out


def encode(m: dict[str, Any]) -> str:
    """For the elevated helper's command line: no quotes or spaces to escape."""
    return base64.urlsafe_b64encode(json.dumps(m, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decode(text: str) -> Any:
    if len(text) > 4096:
        raise ValueError("measurement too long")
    try:
        return json.loads(base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise ValueError(f"unreadable measurement: {exc}") from None


def rise_ms(m: dict[str, Any]) -> float:
    """How much latency grows under load over idle (the bufferbloat)."""
    return float(m["loaded_ms"]) - float(m["idle_ms"])


def staleness(record: dict[str, Any] | None, network: str | None, now: float) -> list[str]:
    """Why a stored measurement may no longer fit: "other_network", "old"."""
    if not record:
        return []
    out = []
    if network and record.get("network") and record["network"] != network:
        out.append("other_network")
    if now - int(record.get("measured_at") or 0) > STALE_AFTER_S:
        out.append("old")
    return out


def verdict(before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    """Did the tweak help? Compares the latency rise under load before and right after applying."""
    was = rise_ms(before)
    if after is None:
        return {"before_ms": round(was, 1), "after_ms": None, "helped": None}
    now_ms = rise_ms(after)
    drop = was - now_ms
    helped = drop >= HELPED_MIN_DROP_MS and drop >= HELPED_MIN_DROP * was
    return {"before_ms": round(was, 1), "after_ms": round(now_ms, 1), "helped": helped}


def measure_upload(now: Callable[[], float] = time.time) -> dict[str, Any] | None:
    """Real traffic (~15 s, up to 100 MB): idle pings, then pings while uploading. None if it failed."""
    from . import bufferbloat, winutil
    targets = {"internet": "1.1.1.1"}
    gateway = winutil.get_gateway()
    if gateway:
        targets = {"router": gateway, **targets}
    _, upload = bufferbloat.real_loads()
    summary = bufferbloat.upload_summary(bufferbloat.measure(bufferbloat.real_ping(), targets, None, upload))
    return None if summary is None else upload_record(summary, current_network_id(), now())


# --- path MTU (the mtu_pmtu tweak) -------------------------------------------------------------

MIN_PATH_TARGETS = 2            # one answering target that drops some pings anyway could fake a small path


def path_mtu_record(interface_mtu: int, results: list[Any], tunnel: bool, network: str | None,
                    now: float) -> dict[str, Any] | None:
    """A path MTU measurement (pmtu.PathResult per target) as the mtu_pmtu tweak receives and stores it.
    None when no target answered."""
    answered = [r for r in results if r.mtu]
    if not answered:
        return None
    return {"kind": "path_mtu", "interface_mtu": int(interface_mtu), "path_mtu": max(r.mtu for r in answered),
            "answered": len(answered), "too_big": any(r.too_big for r in answered), "tunnel": bool(tunnel),
            "measured_at": int(now), "network": network}


def _integer(m: dict, key: str, lo: int, hi: int) -> int:
    value = m.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"measurement {key} missing or out of range: {value!r}")
    return value


def _flag(m: dict, key: str) -> bool:
    value = m.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"measurement {key} is not true/false: {value!r}")
    return value


def _when_and_where(m: dict, now: float) -> dict[str, Any]:
    measured_at, network = m.get("measured_at"), m.get("network")
    if isinstance(measured_at, bool) or not isinstance(measured_at, int):
        raise ValueError("measurement has no time")
    if not now - MAX_AGE_S <= measured_at <= now + 60:
        raise ValueError("measurement is too old (or from the future)")
    if network is not None and not (isinstance(network, str) and _NETWORK_RE.match(network)):
        raise ValueError("bad network id")
    return {"measured_at": measured_at, "network": network}


def check_path_mtu(m: Any, now: float) -> dict[str, Any]:
    """The path MTU measurement `m`, validated and reduced to its known fields; ValueError otherwise."""
    if not isinstance(m, dict) or m.get("kind") != "path_mtu":
        raise ValueError("not a path MTU measurement")
    return {"kind": "path_mtu", "interface_mtu": _integer(m, "interface_mtu", 576, 65535),
            "path_mtu": _integer(m, "path_mtu", 576, 1500), "answered": _integer(m, "answered", 1, 16),
            "too_big": _flag(m, "too_big"), "tunnel": _flag(m, "tunnel"), **_when_and_where(m, now)}


def path_mtu_verdict(before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    """Did lowering the MTU help? Yes when the path now carries the full (new) interface MTU."""
    value = int(before["path_mtu"])
    if after is None:
        return {"mtu": value, "path_after": None, "helped": None}
    return {"mtu": value, "path_after": int(after["path_mtu"]),
            "helped": int(after["path_mtu"]) >= int(after["interface_mtu"])}


def measure_path_mtu(now: Callable[[], float] = time.time) -> dict[str, Any] | None:
    """Do-not-fragment pings (~1 s, a few dozen small packets) on the interface Internet traffic leaves by."""
    from . import pmtu, winutil
    internet = winutil.internet_route_native(pmtu.TARGETS[0])
    if not internet:
        return None
    default = winutil.default_route_native()
    tunnel = default is None or default["interface_index"] != internet["interface_index"]
    interface = winutil.get_interface_mtu(internet["interface_index"])
    if not interface:
        return None
    return path_mtu_record(interface["mtu"], pmtu.measure(interface["mtu"]), tunnel, current_network_id(), now())
