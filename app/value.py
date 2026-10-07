"""What the app did for the user lately (ADR-0020 point 8): counted from the log, never estimated.

Only things the app itself did and can show count: connections the watchdog brought back after a real
action, changes measured to help (a measured tweak's own check, or the before/after comparison of
ADR-0007 for tweaks and manual steps), switches to the backup connection. No "time saved", no speed
gained: the app does not measure those. Pure apart from the storage it reads.
"""
from __future__ import annotations

from typing import Any

WINDOW_DAYS = 7
WATCHDOG_KINDS = ("watchdog_action", "watchdog_dry_run", "watchdog_recovered")
RECOVERED_AFTER = "watchdog.event.recovered_after"


def _key(event: dict) -> str | None:
    message = event.get("message")
    return message.get("key") if isinstance(message, dict) else None


def _param(event: dict, name: str) -> Any:
    message = event.get("message")
    return message.get("params", {}).get(name) if isinstance(message, dict) else None


def recoveries(events: list[dict]) -> list[int]:
    """Incident lengths (s) of the recoveries the watchdog brought about, oldest first.

    A `watchdog_recovered` counts only with the message `recovered_after` (the problem cleared within the
    verify window of an action) and when the action before it was real: the watchdog also writes it in
    dry-run mode, where it did nothing, and writes `recovered` (not counted) when the connection came
    back after the verify window, possibly on its own."""
    last_action = None
    out = []
    for e in sorted(events, key=lambda e: (e["ts"], e.get("id", 0))):
        if e["kind"] in ("watchdog_action", "watchdog_dry_run"):
            last_action = e["kind"]
        elif e["kind"] == "watchdog_recovered":
            if _key(e) == RECOVERED_AFTER and last_action == "watchdog_action":
                duration = _param(e, "duration")
                out.append(int(duration) if isinstance(duration, (int, float)) else 0)
            last_action = None
    return out


def summarize(storage: Any, now: float, changes: list[dict], days: int = WINDOW_DAYS) -> dict[str, Any]:
    """`changes`: app.impact.changes() (tweaks turned on and manual steps done, each with its impact)."""
    since = int(now - days * 86400)
    watchdog = storage.query_events(since=since, kinds=list(WATCHDOG_KINDS), limit=10000)
    durations = recoveries(watchdog)
    # A tweak turned off since no longer does anything for the user.
    off = {c["id"] for c in changes if c["kind"] == "tweak" and c.get("undone")}
    verified = [e for e in storage.query_events(since=since, kinds=["tweak_verified"], limit=1000)
                if (_key(e) or "").endswith(".verified_helped") and _param(e, "tweak_id") not in off]
    verified_ids = {_param(e, "tweak_id") for e in verified}
    tweaks = [{"title": e["message"], "ts": e["ts"]} for e in verified]
    # Before/after (ADR-0007) of the tweaks without a measurement of their own; still on, done in the window.
    for c in changes:
        if (c["kind"] == "tweak" and c["ts"] >= since and not c.get("undone") and c["id"] not in verified_ids
                and (c.get("impact") or {}).get("status") == "better"):
            tweaks.append({"title": c["title"], "ts": c["ts"], "impact": c["impact"]})
    steps = [{"title": c["title"], "ts": c["ts"], "impact": c["impact"]} for c in changes
             if c["kind"] == "manual" and c["ts"] >= since and (c.get("impact") or {}).get("status") == "better"]
    switches = len(storage.query_events(since=since, kinds=["failover_switched"], limit=1000))
    first = storage.first_minute_ts()
    return {"days": days,
            "recovered": {"count": len(durations), "avg_s": sum(durations) / len(durations) if durations else None},
            "tweaks_helped": sorted(tweaks, key=lambda x: x["ts"], reverse=True),
            "steps_helped": sorted(steps, key=lambda x: x["ts"], reverse=True),
            "failover_switches": switches,
            "watching_since": first,
            "empty": not (durations or tweaks or steps or switches)}
