"""What to do next: tweaks and manual steps (move the antenna, turn off the modem's Wi-Fi...)
suggested by the latest diagnostics run, most serious first.

A manual step is something only the user can do. "I did this" records a `manual_step_done`
event; from then on the step shows a before/after comparison (app/impact.py, ADR-0007) instead of
the suggestion. Text lives in app/locales under manual.<id>.title / manual.<id>.body.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import impact
from .i18n import msg

SEVERITY = {"bad": 3, "warn": 2, "info": 1, "ok": 0}
DONE_DAYS = impact.RECENT_DAYS   # a step done longer ago than this can be suggested again


def _summary_key(result: dict) -> str | None:
    summary = result.get("summary")
    return summary.get("key") if isinstance(summary, dict) else None


def _status_in(*statuses: str) -> Callable[[dict], bool]:
    return lambda r: r.get("status") in statuses


@dataclass(frozen=True)
class ManualStep:
    id: str
    check: str                          # diagnostics check key that can call for it
    applies: Callable[[dict], bool]     # given that check's stored result

    @property
    def title(self) -> Any:
        return msg(f"manual.{self.id}.title")

    @property
    def body(self) -> Any:
        return msg(f"manual.{self.id}.body")


MANUAL_STEPS: list[ManualStep] = [
    ManualStep("antenna", "physical_link",
               lambda r: r.get("status") == "warn" or _summary_key(r) == "diag.physical_link.info"),
    ManualStep("move_closer", "signal", _status_in("warn", "bad")),
    ManualStep("modem_wifi_off", "modem_wifi", _status_in("warn", "info")),
    ManualStep("router_channel", "interference", _status_in("warn")),
    ManualStep("router_mlo_off", "wifi7_mlo", _status_in("warn")),
    ManualStep("driver_update", "driver", _status_in("warn", "bad")),
    ManualStep("use_cable", "wired", lambda r: _summary_key(r) == "diag.wired.idle_port"),
    ManualStep("router_sqm", "bufferbloat", _status_in("warn", "bad")),
    ManualStep("dns_server", "dns", _status_in("warn")),
    ManualStep("tunnel_route", "route", lambda r: _summary_key(r) == "diag.route.detour"),
]
STEPS = {s.id: s for s in MANUAL_STEPS}


def done_steps(storage: Any, now: float, days: int = DONE_DAYS) -> dict[str, int]:
    """step id -> when it was last marked done (within `days`)."""
    out: dict[str, int] = {}
    for e in storage.query_events(since=int(now - days * 86400), kinds=["manual_step_done"], limit=1000):
        message = e.get("message")
        sid = message.get("params", {}).get("step_id") if isinstance(message, dict) else None
        if sid in STEPS and sid not in out:   # newest first
            out[sid] = e["ts"]
    return out


def suggest(results: list[dict], tweak_states: dict[str, dict] | None, done: dict[str, int],
            tweak_meta: dict[str, dict]) -> list[dict[str, Any]]:
    """Pure: suggestions from one stored diagnostics run (list of CheckResult dicts).

    tweak_states: id -> {"enabled", "supported"} (None if unknown: then tweaks are suggested
    unless known to be on). tweak_meta: id -> {"name", "note", "risk"}."""
    by_key = {r.get("key"): r for r in results}
    items: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, item_id: str, result: dict, **fields: Any) -> None:
        severity = SEVERITY.get(result.get("status"), 0)
        current = items.get((kind, item_id))
        if current is None or severity > current["severity"]:
            items[(kind, item_id)] = {"kind": kind, "id": item_id, "severity": severity,
                                      "reason": {"check": result.get("key"), "title": result.get("title"),
                                                 "status": result.get("status"), "summary": result.get("summary")},
                                      **fields}

    for step in MANUAL_STEPS:
        result = by_key.get(step.check)
        if result is not None and step.applies(result):
            add("manual", step.id, result, title=step.title, body=step.body, done_at=done.get(step.id))

    def tweak_open(tid: str) -> bool:
        state = (tweak_states or {}).get(tid)
        return tid in tweak_meta and not (state and (state.get("enabled") or state.get("supported") is False))

    for result in results:
        tid = result.get("tweak")
        if tid and result.get("status") in ("warn", "bad") and tweak_open(tid):
            meta = tweak_meta[tid]
            add("tweak", tid, result, title=meta["name"], body=meta.get("note") or "", risk=meta.get("risk"),
                measured=bool(meta.get("measured")), disrupts=bool(meta.get("disrupts")))
    tweaks_check = by_key.get("tweaks")
    if tweaks_check and tweaks_check.get("status") == "info":
        for tid in tweaks_check.get("details") or []:
            if isinstance(tid, str) and tweak_open(tid) and tweak_meta[tid].get("risk") == "low":
                meta = tweak_meta[tid]
                add("tweak", tid, tweaks_check, title=meta["name"], body=meta.get("note") or "", risk="low",
                    measured=bool(meta.get("measured")), disrupts=bool(meta.get("disrupts")))

    return sorted(items.values(), key=order)


PROBLEM_STATUSES = ("bad", "warn")
# Checks that judge the past (hours or days of events and measurements): checking again right after a
# change cannot show its effect, so the result says "measuring" for them, never "no change" (ADR-0020 point 7).
HISTORY_CHECKS = frozenset({"driver", "drops", "ping", "tcp_ports", "physical_link"})


def check_summary(results: list[dict], items: list[dict]) -> dict[str, Any]:
    """What a run found, as the Overview's check card shows it (ADR-0020).

    Only warn/bad results are problems; info and ok never count. Each problem lists the suggestions
    that came from its check and says who can fix it: "app" (a tweak), "you" (a manual step) or
    "none". `batch` names the tweaks the Fix button may turn on together: low risk, not measured
    (a measured tweak takes its own ~15 s measurement and may refuse); `also` the other low-risk
    tweaks the run suggests, offered in the same sheet. Pure."""
    problems = []
    ranked = sorted((r for r in results if r.get("status") in PROBLEM_STATUSES),
                    key=lambda r: (-SEVERITY[r["status"]], r.get("id") or 0))
    for r in ranked:
        actions = [i for i in items if (i.get("reason") or {}).get("check") == r.get("key")]
        tweaks = [i for i in actions if i["kind"] == "tweak"]
        kind = "app" if tweaks else "you" if actions else "none"
        problems.append({"key": r.get("key"), "title": r.get("title"), "status": r["status"],
                         "summary": r.get("summary"), "advice": r.get("advice") or "", "kind": kind,
                         "history": r.get("key") in HISTORY_CHECKS,
                         "actions": actions,
                         "batch": [i["id"] for i in tweaks if i.get("risk") == "low" and not i.get("measured")]})
    # Low-risk tweaks the run suggests for no counted problem (check #10 lists what is not on yet): the Fix
    # sheet offers them too, separately, and they never count as problems.
    counted = {p["key"] for p in problems}
    also = [i["id"] for i in items if i["kind"] == "tweak" and not i.get("done_at") and i.get("risk") == "low"
            and not i.get("measured") and (i.get("reason") or {}).get("check") not in counted]
    return {"problems": problems, "count": len(problems), "fixable": sum(1 for p in problems if p["batch"]),
            "also": also, "ok": sum(1 for r in results if r.get("status") == "ok"), "total": len(results)}


def order(item: dict) -> tuple:
    """Most serious first; within a level, things still to do before things done."""
    return -item["severity"], item.get("done_at") is not None, item["kind"], item["id"]


def build(storage: Any, now: float, tweak_states: dict[str, dict] | None, tweak_meta: dict[str, dict]) -> dict[str, Any]:
    """Suggestions from the latest saved run, with before/after results for steps already done."""
    run = storage.latest_diagnostic_run()
    done = done_steps(storage, now)
    items = suggest(run["results"] if run else [], tweak_states, done, tweak_meta)
    listed = {i["id"] for i in items if i["kind"] == "manual"}
    for sid, ts in done.items():     # done steps stay visible with their result, even if no longer suggested
        if sid not in listed:
            step = STEPS[sid]
            items.append({"kind": "manual", "id": sid, "severity": 0, "reason": None, "title": step.title,
                          "body": step.body, "done_at": ts})
    for item in items:
        if item["kind"] == "manual" and item.get("done_at"):
            item["impact"] = impact.compare(storage, item["done_at"], now)
    return {"run": {"id": run["id"], "ts": run["ts"]} if run else None, "items": items,
            "check": check_summary(run["results"], items) if run else None,
            "hint": None if run else msg("manual.no_run")}


def mark_done(storage: Any, step_id: str, now: float) -> dict[str, Any]:
    step = STEPS[step_id]   # KeyError for an unknown id
    storage.add_event(int(now), "manual_step_done", msg("manual.event.done", step=step.title, step_id=step.id),
                      level="info")
    return {"ok": True, "id": step.id, "ts": int(now)}
