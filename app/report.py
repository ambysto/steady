"""Reports of outages and slowdowns for a week, month or quarter, to attach to a complaint (ADR-0021).

`build` gathers the numbers from storage into a plain dict; `render_html` turns that into one
self-contained page that prints well (the user saves it as PDF from the print dialog) and `render_csv`
into rows for a spreadsheet. Nothing here touches the network, and a report never leaves the computer
unless the user sends it. Local addresses (MAC, BSSID, private IPs) are never put in it.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from . import __version__, slowdown
from .i18n import format_duration, render, t
from .storage import Storage

KINDS = ("week", "month", "quarter")
OUTAGE_KINDS = ("internet_down", "router_down")
MONITOR_KINDS = ("monitor_start", "monitor_stop", "monitor_gap")
VERDICT_ORDER = (slowdown.OUTSIDE, slowdown.LOCAL, slowdown.UNKNOWN)


# --- periods -----------------------------------------------------------------------------------

def _midnight(day: date, tz: tzinfo | None) -> int:
    moment = datetime(day.year, day.month, day.day)
    return int(moment.replace(tzinfo=tz).timestamp() if tz else moment.timestamp())


def period_bounds(kind: str, day: date, tz: tzinfo | None = None) -> tuple[int, int, str]:
    """(start, end, label) of the calendar week (Monday first), month or quarter that contains `day`,
    in local time (`tz`, None = this computer's). `end` is the start of the next period."""
    if kind == "week":
        first = day - timedelta(days=day.weekday())
        after = first + timedelta(days=7)
        iso = first.isocalendar()
        label = f"{iso[0]}-W{iso[1]:02d}"
    elif kind == "month":
        first = day.replace(day=1)
        after = (first + timedelta(days=32)).replace(day=1)
        label = f"{first.year}-{first.month:02d}"
    elif kind == "quarter":
        month = 3 * ((day.month - 1) // 3) + 1
        first = date(day.year, month, 1)
        after = date(day.year + (month == 10), (month + 3 - 1) % 12 + 1, 1)
        label = f"{first.year}-Q{(month - 1) // 3 + 1}"
    else:
        raise ValueError(f"kind must be one of {KINDS}")
    return _midnight(first, tz), _midnight(after, tz), label


# --- the numbers -------------------------------------------------------------------------------

def union_seconds(intervals: list[tuple[float, float]], low: float, high: float) -> float:
    """Total length of the union of the intervals, clipped to [low, high]."""
    clipped = sorted((max(a, low), min(b, high)) for a, b in intervals if min(b, high) > max(a, low))
    total, edge = 0.0, low
    for a, b in clipped:
        a = max(a, edge)
        if b > a:
            total += b - a
            edge = b
    return total


def unmonitored(events: list[dict], low: float, high: float) -> list[tuple[float, float]]:
    """When nothing was watching, inside [low, high]: before the first start the app ever recorded, between
    a stop and the next start, and the gaps the monitor noticed itself (sleep, hang)."""
    starts = sorted(e["ts"] for e in events if e["kind"] == "monitor_start")
    stops = sorted(e["ts"] for e in events if e["kind"] == "monitor_stop")
    out: list[tuple[float, float]] = []
    if starts and starts[0] > low:
        out.append((low, starts[0]))
    for stop in stops:
        later = [s for s in starts if s >= stop]
        out.append((stop, later[0] if later else high))
    for e in events:
        if e["kind"] == "monitor_gap" and e.get("duration"):
            out.append((e["ts"] - e["duration"], e["ts"]))
    return [(max(a, low), min(b, high)) for a, b in out if min(b, high) > max(a, low)]


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def _speed_summary(rows: list[dict], column: str, plan: float) -> dict[str, Any] | None:
    values = [r[column] for r in rows if r["skipped"] is None and r[column] is not None]
    if not values:
        return None
    base = slowdown.baseline(values, plan or None)
    out: dict[str, Any] = {"samples": len(values), "median_mbps": round(statistics.median(values), 1),
                           "p10_mbps": round(_percentile(values, 0.1), 1), "min_mbps": round(min(values), 1),
                           "baseline_mbps": round(base, 1) if base else None}
    if base:
        out["below_half"] = sum(v < slowdown.OPEN_RATIO * base for v in values)
        out["below_70"] = sum(v < slowdown.CLOSE_RATIO * base for v in values)
    return out


def _hourly(rows: list[dict], column: str, tz: tzinfo | None) -> list[float | None]:
    buckets: list[list[float]] = [[] for _ in range(24)]
    for r in rows:
        if r["skipped"] is None and r[column] is not None:
            buckets[datetime.fromtimestamp(r["ts"], tz).hour].append(r[column])
    return [round(statistics.median(b), 1) if b else None for b in buckets]


def build(storage: Storage, start: int, end: int, *, now: float, kind: str = "custom", label: str = "",
          plan_down: float = 0, plan_up: float = 0, provider: str = "", public_ip: str = "",
          tz: tzinfo | None = None, lang: str | None = None) -> dict[str, Any]:
    """Everything a report says, as plain data. `end` is clamped to `now`: a month still going on is
    reported up to this moment, never promised beyond it."""
    end_seen = int(min(end, now))
    events = storage.query_events(since=start, until=end_seen, kinds=OUTAGE_KINDS, limit=100_000)
    outages = sorted(({"kind": e["kind"], "start_ts": int(e["ts"] - (e["duration"] or 0)), "end_ts": int(e["ts"]),
                       "duration_s": int(e["duration"] or 0), "text": render(e["message"], lang)} for e in events),
                     key=lambda o: o["start_ts"])
    monitor_events = storage.query_events(kinds=MONITOR_KINDS, limit=100_000)
    off = unmonitored(monitor_events, start, end_seen)

    slow_rows = storage.query_slowdowns(since=start, until=end_seen)
    slowdowns = []
    for row in slow_rows:
        stop = row["end_ts"] if row["end_ts"] is not None else end_seen
        slowdowns.append({"direction": row["direction"], "start_ts": row["start_ts"], "end_ts": row["end_ts"],
                          "duration_s": int(max(0, min(stop, end_seen) - row["start_ts"])),
                          "avg_mbps": round(row["avg_mbps"], 1), "min_mbps": round(row["min_mbps"], 1),
                          "baseline_mbps": round(row["baseline_mbps"]), "samples": row["samples"],
                          "verdict": row["verdict"], "evidence": (row["evidence"] or {}).get("open") or {}})

    samples = storage.query_speed_samples(start, end_seen)
    skipped: dict[str, int] = {}
    for r in samples:
        if r["skipped"]:
            skipped[r["skipped"]] = skipped.get(r["skipped"], 0) + 1
    speed = {d: s for d, s in (("download", _speed_summary(samples, "down_mbps", plan_down)),
                               ("upload", _speed_summary(samples, "up_mbps", plan_up))) if s}

    watched_s = max(0.0, (end_seen - start) - union_seconds(off, start, end_seen))
    outage_s = union_seconds([(o["start_ts"], o["end_ts"]) for o in outages], start, end_seen)
    by_verdict = {v: [s for s in slowdowns if s["verdict"] == v] for v in VERDICT_ORDER}
    totals = {
        "period_s": end_seen - start, "unmonitored_s": int(union_seconds(off, start, end_seen)),
        "outage_count": len(outages), "outage_s": int(outage_s),
        "availability_pct": round(100 * (1 - min(outage_s, watched_s) / watched_s), 3) if watched_s else None,
        **{f"slowdown_{v}_count": len(by_verdict[v]) for v in VERDICT_ORDER},
        **{f"slowdown_{v}_s": int(union_seconds([(s["start_ts"], s["start_ts"] + s["duration_s"])
                                                 for s in by_verdict[v]], start, end_seen))
           for v in VERDICT_ORDER},
    }
    report: dict[str, Any] = {
        "kind": kind, "label": label, "start_ts": start, "end_ts": end, "covered_until_ts": end_seen,
        "generated_ts": int(now), "version": __version__, "provider": provider, "public_ip": public_ip,
        "timezone": _zone_name(tz, start), "plan": {"down_mbps": plan_down or None, "up_mbps": plan_up or None},
        "totals": totals, "outages": outages, "slowdowns": slowdowns, "speed": speed,
        "hourly_download": _hourly(samples, "down_mbps", tz),
        "samples_taken": len(samples), "samples_skipped": skipped,
        "samples": [[r["ts"], r["down_mbps"], r["up_mbps"], r["skipped"]] for r in samples],
    }
    report["digest"] = digest(report)
    return report


def digest(report: dict[str, Any]) -> str:
    """SHA-256 over the rows a report lists: any row edited afterwards no longer matches."""
    body = {k: report[k] for k in ("start_ts", "covered_until_ts", "slowdowns", "samples")}
    # the outage text is in the language of the day: the same rows must give the same fingerprint in any language
    body["outages"] = [{k: v for k, v in o.items() if k != "text"} for o in report["outages"]]
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def summary(report: dict[str, Any]) -> dict[str, Any]:
    """The report without its raw sample rows, for an API response."""
    return {k: v for k, v in report.items() if k != "samples"}


def _zone_name(tz: tzinfo | None, ts: float) -> str:
    moment = datetime.fromtimestamp(ts, tz)
    if tz is None:
        moment = moment.astimezone()   # a naive local time has no offset; this computer's own is wanted
    offset = moment.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


# --- csv ---------------------------------------------------------------------------------------

def _iso(ts: float | None) -> str:
    return "" if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_csv(report: dict[str, Any]) -> str:
    """One row per outage and slowdown, times in UTC (the same numbers as the report)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["type", "start_utc", "end_utc", "duration_s", "direction", "avg_mbps", "min_mbps",
                     "baseline_mbps", "verdict", "router_loss_pct", "router_ms"])
    for o in report["outages"]:
        writer.writerow([o["kind"], _iso(o["start_ts"]), _iso(o["end_ts"]), o["duration_s"], "", "", "", "", "", "", ""])
    for s in report["slowdowns"]:
        ev = s["evidence"]
        writer.writerow(["slowdown", _iso(s["start_ts"]), _iso(s["end_ts"]), s["duration_s"], s["direction"],
                         s["avg_mbps"], s["min_mbps"], s["baseline_mbps"], s["verdict"],
                         ev.get("router_loss_pct", ""), ev.get("router_ms", "")])
    return out.getvalue()


def render_samples_csv(report: dict[str, Any]) -> str:
    """Every throughput sample of the period, including those that were skipped and why."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["time_utc", "download_mbps", "upload_mbps", "skipped"])
    for ts, down, up, skipped in report["samples"]:
        writer.writerow([_iso(ts), "" if down is None else down, "" if up is None else up, skipped or ""])
    return out.getvalue()


# --- html --------------------------------------------------------------------------------------

_CSS = """
body{font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;color:#1d1d1f;margin:0;background:#fff}
main{max-width:860px;margin:0 auto;padding:32px 24px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #d2d2d7;padding-bottom:4px}
.meta{color:#6e6e73;margin:0}table{border-collapse:collapse;width:100%;margin:6px 0}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #e5e5ea;vertical-align:top}th{font-weight:600;color:#6e6e73}
.num{text-align:right;font-variant-numeric:tabular-nums}.small{font-size:12px;color:#6e6e73}
.out{color:#b3261e;font-weight:600}.loc{color:#8a6100}.unk{color:#6e6e73}
.digest{font:12px/1.4 Consolas,monospace;word-break:break-all}
svg{width:100%;height:auto}.bar{fill:#0a63c9}.base{stroke:#b3261e;stroke-dasharray:4 3}
@media print{main{padding:0}h2{break-after:avoid}tr{break-inside:avoid}}
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _clock(ts: float | None, tz: tzinfo | None) -> str:
    if ts is None:
        return "…"
    local = datetime.fromtimestamp(ts, tz)
    utc = datetime.fromtimestamp(ts, timezone.utc)
    return f"{local:%Y-%m-%d %H:%M} ({utc:%H:%M} UTC)"


def _evidence_text(ev: dict[str, Any], direction: str, lang: str | None) -> str:
    parts = []
    if ev.get("router_loss_pct") is not None:
        parts.append(t("report.evidence.router", lang, loss=ev["router_loss_pct"], ms=ev.get("router_ms") or "–"))
    link = ev.get("rx_mbps" if direction == "download" else "tx_mbps")
    if link is not None:
        parts.append(t("report.evidence.link", lang, mbps=link, rssi=ev.get("rssi") if ev.get("rssi") is not None else "–"))
    other = "up_mbps" if direction == "download" else "down_mbps"
    if ev.get(other) is not None:
        parts.append(t("report.evidence.other", lang, direction=t(f"report.direction.{'upload' if direction == 'download' else 'download'}", lang),
                       mbps=ev[other]))
    if ev.get("vpn"):
        parts.append(t("report.evidence.tunnel", lang))
    return " · ".join(parts)


def _hour_chart(values: list[float | None], base: float | None) -> str:
    top = max([v for v in values if v is not None] + ([base] if base else []) + [1.0])
    width, height, pad = 720, 150, 22
    step = (width - 2 * pad) / 24
    bars = []
    for hour, v in enumerate(values):
        label = f'<text x="{pad + step * hour + step / 2:.1f}" y="{height - 6}" font-size="9" text-anchor="middle" fill="#6e6e73">{hour}</text>'
        if v is not None:
            h = (height - 2 * pad) * v / top
            bars.append(f'<rect class="bar" x="{pad + step * hour + 2:.1f}" y="{height - pad - h:.1f}" '
                        f'width="{step - 4:.1f}" height="{h:.1f}"><title>{hour}:00  {v} Mb/s</title></rect>')
        bars.append(label)
    line = ""
    if base:
        y = height - pad - (height - 2 * pad) * base / top
        line = f'<line class="base" x1="{pad}" x2="{width - pad}" y1="{y:.1f}" y2="{y:.1f}"/>'
    return f'<svg viewBox="0 0 {width} {height}" role="img">{"".join(bars)}{line}</svg>'


def render_html(report: dict[str, Any], lang: str | None = None, tz: tzinfo | None = None) -> str:
    """The report as one self-contained page: no scripts, no external files; styles inline."""
    tot, speed = report["totals"], report["speed"]
    tr = lambda key, **p: t(key, lang, **p)   # noqa: E731
    dur = lambda s: format_duration(s, lang)  # noqa: E731
    verdict_class = {slowdown.OUTSIDE: "out", slowdown.LOCAL: "loc", slowdown.UNKNOWN: "unk"}
    plan = report["plan"]
    plan_text = (tr("report.plan.value", down=plan["down_mbps"] or "–", up=plan["up_mbps"] or "–")
                 if plan["down_mbps"] or plan["up_mbps"] else tr("report.plan.unknown"))
    rows_meta = [(tr("report.period"), f'{_clock(report["start_ts"], tz)} → {_clock(report["covered_until_ts"], tz)}'),
                 (tr("report.plan"), plan_text)]
    if report["provider"]:
        rows_meta.append((tr("report.provider"), report["provider"]))
    if report["public_ip"]:
        rows_meta.append((tr("report.public_ip"), report["public_ip"]))
    meta = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in rows_meta)

    avail = (tr("report.summary.availability.value", percent=tot["availability_pct"])
             if tot["availability_pct"] is not None else "–")
    summary_rows = [
        (tr("report.summary.availability"), avail),
        (tr("report.summary.outages"), tr("report.summary.count_total", count=tot["outage_count"], duration=dur(tot["outage_s"]))),
        (tr("report.summary.slowdowns_outside"), tr("report.summary.count_total", count=tot["slowdown_outside_count"],
                                                    duration=dur(tot["slowdown_outside_s"]))),
        (tr("report.summary.slowdowns_local"), tr("report.summary.count_total", count=tot["slowdown_local_count"],
                                                  duration=dur(tot["slowdown_local_s"]))),
        (tr("report.summary.slowdowns_unknown"), tr("report.summary.count_total", count=tot["slowdown_unknown_count"],
                                                    duration=dur(tot["slowdown_unknown_s"]))),
        (tr("report.summary.unmonitored"), dur(tot["unmonitored_s"])),
    ]
    summary_html = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in summary_rows)

    speed_html = ""
    for direction in ("download", "upload"):
        s = speed.get(direction)
        if not s:
            continue
        below = (tr("report.speed.below", half=s["below_half"], total=s["samples"], seventy=s["below_70"])
                 if "below_half" in s else "")
        speed_html += (f"<tr><th>{_e(tr(f'report.direction.{direction}'))}</th>"
                       f"<td class='num'>{s['median_mbps']}</td><td class='num'>{s['p10_mbps']}</td>"
                       f"<td class='num'>{s['baseline_mbps'] if s['baseline_mbps'] else '–'}</td>"
                       f"<td>{_e(below)}</td></tr>")
    speed_table = (f"<table><tr><th></th><th class='num'>{_e(tr('report.speed.median'))}</th>"
                   f"<th class='num'>{_e(tr('report.speed.p10'))}</th><th class='num'>{_e(tr('report.speed.baseline'))}</th>"
                   f"<th>{_e(tr('report.speed.samples_below'))}</th></tr>{speed_html}</table>") if speed_html else \
        f"<p class='small'>{_e(tr('report.speed.none'))}</p>"

    slow_rows = ""
    for s in report["slowdowns"]:
        slow_rows += (f"<tr><td>{_e(_clock(s['start_ts'], tz))}</td><td>{_e(dur(s['duration_s']))}</td>"
                      f"<td>{_e(tr('report.direction.' + s['direction']))}</td>"
                      f"<td>{_e(tr('report.slowdown.speed', avg=s['avg_mbps'], baseline=s['baseline_mbps']))}</td>"
                      f"<td class='{verdict_class[s['verdict']]}'>{_e(tr('report.verdict.' + s['verdict']))}</td>"
                      f"<td class='small'>{_e(_evidence_text(s['evidence'], s['direction'], lang))}</td></tr>")
    slow_html = (f"<table><tr><th>{_e(tr('report.col.start'))}</th><th>{_e(tr('report.col.duration'))}</th>"
                 f"<th>{_e(tr('report.col.direction'))}</th><th>{_e(tr('report.col.speed'))}</th>"
                 f"<th>{_e(tr('report.col.verdict'))}</th><th>{_e(tr('report.col.evidence'))}</th></tr>{slow_rows}</table>"
                 ) if slow_rows else f"<p class='small'>{_e(tr('report.none'))}</p>"

    out_rows = "".join(f"<tr><td>{_e(_clock(o['start_ts'], tz))}</td><td>{_e(dur(o['duration_s']))}</td>"
                       f"<td>{_e(tr('report.outage.' + o['kind']))}</td><td class='small'>{_e(o['text'])}</td></tr>"
                       for o in report["outages"])
    out_html = (f"<table><tr><th>{_e(tr('report.col.start'))}</th><th>{_e(tr('report.col.duration'))}</th>"
                f"<th>{_e(tr('report.col.kind'))}</th><th>{_e(tr('report.col.details'))}</th></tr>{out_rows}</table>"
                ) if out_rows else f"<p class='small'>{_e(tr('report.none'))}</p>"

    hourly = report["hourly_download"]
    chart = ""
    if any(v is not None for v in hourly):
        base = (speed.get("download") or {}).get("baseline_mbps")
        chart = (f"<h2>{_e(tr('report.hourly'))}</h2>{_hour_chart(hourly, base)}"
                 f"<p class='small'>{_e(tr('report.hourly.note', zone=report['timezone']))}</p>")

    skipped = report["samples_skipped"]
    taken_text = tr("report.samples", count=report["samples_taken"], skipped=sum(skipped.values()))
    return (
        f"<!doctype html><html lang='{_e(lang or 'en')}'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(tr('report.title'))} {_e(report['label'])}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{_e(tr('report.title'))}</h1><p class='meta'>{_e(report['label'])} · "
        f"{_e(tr('report.timezone', zone=report['timezone']))} · {_e(tr('report.generated'))} "
        f"{_e(_clock(report['generated_ts'], tz))}</p>"
        f"<table>{meta}</table>"
        f"<h2>{_e(tr('report.summary'))}</h2><table>{summary_html}</table>"
        f"<h2>{_e(tr('report.speed'))}</h2>{speed_table}<p class='small'>{_e(taken_text)}</p>"
        f"<h2>{_e(tr('report.slowdowns'))}</h2>{slow_html}"
        f"<h2>{_e(tr('report.outages'))}</h2>{out_html}"
        f"{chart}"
        f"<h2>{_e(tr('report.method'))}</h2><p class='small'>{_e(tr('report.method.text'))}</p>"
        f"<p class='small'>{_e(tr('report.method.verdicts'))}</p>"
        f"<h2>{_e(tr('report.digest'))}</h2><p class='digest'>{_e(report['digest'])}</p>"
        f"<p class='small'>{_e(tr('report.digest.note'))}</p>"
        f"<p class='small'>{_e(tr('report.footer', app=tr('app.name'), version=report['version']))}</p>"
        f"</main></body></html>")
