"""Before/after comparison of one change (a tweak turned on, a manual step done), ADR-0007.

    compare(storage, t, now)        metrics for equal windows before and after t, and a verdict
    changes(storage, backup, now)   the recent changes worth reporting, each with its comparison

Only monitored time counts: a sleeping PC is neither good nor bad. Results carry messages
({"key", "params"}), rendered by the API like everything else (ADR-0006).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .diagnostics import is_link_bad, join_minutes
from .i18n import msg

WINDOW_MAX_S = 24 * 3600
MIN_MONITORED_S = 2 * 3600
RECENT_DAYS = 14
OUTAGE_KINDS = ("router_down", "internet_down")
CHANGE_KINDS = ("tweak_enabled", "tweak_disabled", "manual_step_done")
DAY = 86400.0

# A metric counts as changed only when it at least halves (or doubles) AND the larger side is
# big enough to mean something. Below these floors the honest answer is "no clear difference".
OUTAGE_FLOOR = 3            # outages in the window
OUTAGE_TIME_FLOOR_S = 300   # seconds offline in the window
LOSS_FLOOR_PCT = 1.0
BAD_LINK_FLOOR = 0.05
MIN_ROUTER_SENT = 600
MIN_LINK_MINUTES = 60


@dataclass
class Metrics:
    monitored_s: int = 0
    outages: int = 0
    outage_s: float = 0.0
    router_sent: int = 0
    router_lost: int = 0
    link_minutes: int = 0
    bad_link_minutes: int = 0

    def per_day(self, value: float) -> float:
        return value * DAY / self.monitored_s if self.monitored_s else 0.0

    @property
    def outages_per_day(self) -> float:
        return self.per_day(self.outages)

    @property
    def offline_minutes_per_day(self) -> float:
        return self.per_day(self.outage_s) / 60

    @property
    def router_loss_pct(self) -> float:
        return 100.0 * self.router_lost / self.router_sent if self.router_sent else 0.0

    @property
    def bad_link_fraction(self) -> float:
        return self.bad_link_minutes / self.link_minutes if self.link_minutes else 0.0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.update(outages_per_day=round(self.outages_per_day, 2),
                   offline_minutes_per_day=round(self.offline_minutes_per_day, 1),
                   router_loss_pct=round(self.router_loss_pct, 2), bad_link_fraction=round(self.bad_link_fraction, 4))
        return out


def measure(storage: Any, start: int, end: int) -> Metrics:
    """Metrics for [start, end). Outages count where they started; their time is clipped to the window."""
    m = Metrics()
    if end <= start:
        return m
    rows = storage.query_minute_stats(start, end)
    m.monitored_s = 60 * len({r["ts"] for r in rows})
    router = [r for r in rows if r["target"] == "router"]
    m.router_sent = sum(r["sent"] for r in router)
    m.router_lost = sum(r["lost"] for r in router)
    minutes = join_minutes(storage.query_wifi_stats(start, end), rows)
    m.link_minutes = len(minutes)
    m.bad_link_minutes = sum(1 for x in minutes if is_link_bad(x))
    # Outage events are written at recovery (ts) with their duration; fetch the ones that end after
    # the window starts, then keep those that overlap it.
    for e in storage.query_events(since=start, kinds=OUTAGE_KINDS, limit=100000):
        began = e["ts"] - (e.get("duration") or 0)
        if began >= end:
            continue
        if began >= start:
            m.outages += 1
        m.outage_s += max(0.0, min(e["ts"], end) - max(began, start))
    return m


def _judge(before: float, after: float, floor: float, enough: bool) -> str:
    if not enough:
        return "same"
    if before >= floor and after <= before / 2:
        return "better"
    if after >= floor and after >= 2 * before:
        return "worse"
    return "same"


def compare(storage: Any, t: float, now: float, until: float | None = None) -> dict[str, Any]:
    """Compare the window after `t` (up to 24 h, `now` or `until` - when the change was undone)
    with an equally long window right before it."""
    t = int(t)
    end = int(min(now, until if until is not None else now, t + WINDOW_MAX_S))
    span = max(0, end - t)
    before, after = measure(storage, t - span, t), measure(storage, t, end)
    result: dict[str, Any] = {"ts": t, "until": end, "window_s": span, "preliminary": span < WINDOW_MAX_S,
                              "before": before.to_dict(), "after": after.to_dict(), "findings": [], "details": []}
    if after.monitored_s < MIN_MONITORED_S:
        result.update(status="collecting", summary=msg("impact.collecting", hours=round(after.monitored_s / 3600, 1),
                                                       needed=MIN_MONITORED_S // 3600))
        return result
    if before.monitored_s < MIN_MONITORED_S:
        result.update(status="no_baseline", summary=msg("impact.no_baseline"))
        return result

    scale = before.monitored_s / after.monitored_s   # after counts rescaled to the before window
    checks = [
        ("outages", before.outages_per_day, after.outages_per_day,
         _judge(before.outages, after.outages * scale, OUTAGE_FLOOR, True)),
        ("offline_minutes", before.offline_minutes_per_day, after.offline_minutes_per_day,
         _judge(before.outage_s, after.outage_s * scale, OUTAGE_TIME_FLOOR_S, True)),
        ("router_loss", before.router_loss_pct, after.router_loss_pct,
         _judge(before.router_loss_pct, after.router_loss_pct, LOSS_FLOOR_PCT,
                min(before.router_sent, after.router_sent) >= MIN_ROUTER_SENT)),
        ("bad_link", before.bad_link_fraction, after.bad_link_fraction,
         _judge(before.bad_link_fraction, after.bad_link_fraction, BAD_LINK_FLOOR,
                min(before.link_minutes, after.link_minutes) >= MIN_LINK_MINUTES)),
    ]
    verdicts = {v for *_, v in checks}
    status = ("mixed" if {"better", "worse"} <= verdicts else "better" if "better" in verdicts
              else "worse" if "worse" in verdicts else "no_change")
    result["status"] = status
    result["summary"] = msg(f"impact.{status}")
    for name, b, a, verdict in checks:
        result["findings"].append({"metric": name, "before": round(b, 4), "after": round(a, 4), "verdict": verdict})
        result["details"].append(msg(f"impact.metric.{name}", before=float(b), after=float(a)))
    if result["preliminary"]:
        result["details"].append(msg("impact.preliminary", hours=round(span / 3600, 1)))
    result["details"].append(msg("impact.caveat"))
    return result


def _param(event: dict, name: str) -> Any:
    message = event.get("message")
    return message.get("params", {}).get(name) if isinstance(message, dict) else None


def changes(storage: Any, backup: dict[str, Any], now: float, *, tweak_names: dict[str, Any] | None = None,
            step_names: dict[str, Any] | None = None, days: int = RECENT_DAYS) -> list[dict[str, Any]]:
    """Tweaks turned on and manual steps done in the last `days`, newest first, each compared.

    A tweak turned off again is measured only up to that moment. Tweaks turned on before the log
    recorded tweak ids fall back to backup.json's captured_at (own captures only, not adopted ones)."""
    since = int(now - days * 86400)
    events = sorted(storage.query_events(since=since, kinds=CHANGE_KINDS, limit=10000), key=lambda e: (e["ts"], e["id"]))
    tweak_names, step_names = tweak_names or {}, step_names or {}
    found: list[dict[str, Any]] = []
    for i, e in enumerate(events):
        if e["kind"] == "tweak_enabled":
            tid = _param(e, "tweak_id")
            if not tid:
                continue
            undone = next((x["ts"] for x in events[i + 1:]
                           if x["kind"] == "tweak_disabled" and _param(x, "tweak_id") == tid), None)
            found.append({"kind": "tweak", "id": tid, "title": tweak_names.get(tid, tid), "ts": e["ts"], "undone": undone})
        elif e["kind"] == "manual_step_done":
            sid = _param(e, "step_id")
            if sid:
                found.append({"kind": "manual", "id": sid, "title": step_names.get(sid, sid), "ts": e["ts"], "undone": None})
    logged = {c["id"] for c in found if c["kind"] == "tweak"}
    for tid, entry in (backup or {}).items():
        captured = entry.get("captured_at") if isinstance(entry, dict) else None
        if tid in logged or entry.get("source") != "capture" or not isinstance(captured, (int, float)) or captured < since:
            continue
        found.append({"kind": "tweak", "id": tid, "title": tweak_names.get(tid, tid), "ts": int(captured), "undone": None})
    found.sort(key=lambda c: c["ts"], reverse=True)
    for c in found:
        c["impact"] = compare(storage, c["ts"], now, c["undone"])
        c["overlaps"] = any(o is not c and abs(o["ts"] - c["ts"]) < WINDOW_MAX_S for o in found)
        if c["overlaps"] and c["impact"]["status"] not in ("collecting", "no_baseline"):
            c["impact"]["details"].insert(-1, msg("impact.overlap"))   # before the caveat
    return found
