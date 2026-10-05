"""Calibration table for check #13 (poor physical link) from a measurement database.

Reads the monitor's history, the same `minute_stats` and `wifi_stats` tables on Windows
(`metrics.db`) and on the Mac (`metrics.sqlite`, ADR-0011). It joins each connected Wi-Fi minute
with the router's pings of that minute (as `join_minutes` does: the Rx rate where the platform
reports it, else Tx, the Mac's only rate). Then it prints, per period, the share of minutes
whose rate is at or below each candidate threshold while router loss is at or above 5%. That is
how the Windows thresholds were chosen (docs/DIAGNOSTICS.md, "Threshold calibration"); SIC-70
does the same for the Mac's Tx rate.

    python scripts/link_calibration.py metrics.sqlite --period "desk=2026-10-06 09:00..10:00" \\
        --period "far room=2026-10-06 10:10..11:10"

Without --period, every hour that has minutes is a period. Times are local.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RATES = (6, 12, 24, 30, 54, 100)   # Mbps thresholds to compare; 30 is the current one
LOSS_PCT = 5.0
MIN_SENT = 30                      # router pings needed in a minute, as join_minutes


def minutes(db: Path) -> list[tuple[int, int, float, float]]:
    """(ts, rssi, rate Mbps, router loss %) per connected Wi-Fi minute with enough router pings."""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("""
            SELECT w.ts, w.rssi, COALESCE(w.rx_mbps, w.tx_mbps), 100.0 * m.lost / m.sent
            FROM wifi_stats w JOIN minute_stats m ON m.ts = w.ts AND m.target = 'router'
            WHERE w.state = 'connected' AND w.rssi IS NOT NULL AND COALESCE(w.rx_mbps, w.tx_mbps) IS NOT NULL
              AND m.sent >= ?
            ORDER BY w.ts""", (MIN_SENT,)).fetchall()
    return [(int(ts), int(rssi), float(rate), float(loss)) for ts, rssi, rate, loss in rows]


def parse_period(text: str) -> tuple[str, int, int]:
    """"label=YYYY-MM-DD HH:MM..HH:MM" or with a full date on both sides; local time."""
    label, _, span = text.partition("=")
    start, _, end = span.partition("..")
    first = datetime.strptime(start.strip(), "%Y-%m-%d %H:%M")
    end = end.strip()
    last = (datetime.strptime(end, "%Y-%m-%d %H:%M") if len(end) > 5
            else datetime.combine(first.date(), datetime.strptime(end, "%H:%M").time()))
    if not label or last <= first:
        raise argparse.ArgumentTypeError(f"bad period {text!r}: use label=YYYY-MM-DD HH:MM..HH:MM")
    return label.strip(), int(first.timestamp()), int(last.timestamp())


def hourly(data: list[tuple[int, int, float, float]]) -> list[tuple[str, int, int]]:
    hours = sorted({ts - ts % 3600 for ts, *_ in data})
    return [(datetime.fromtimestamp(h).strftime("%Y-%m-%d %H:00"), h, h + 3600) for h in hours]


def table(data: list[tuple[int, int, float, float]], periods: list[tuple[str, int, int]]) -> list[list[str]]:
    header = ["period", "minutes", "median RSSI", "median rate"] + [f"≤{r} & loss≥5%" for r in RATES]
    out = [header]
    for label, start, end in periods:
        part = [m for m in data if start <= m[0] < end]
        if not part:
            out.append([label, "0"] + ["–"] * (len(header) - 2))
            continue
        rssi = sorted(m[1] for m in part)[len(part) // 2]
        rate = sorted(m[2] for m in part)[len(part) // 2]
        shares = [sum(1 for m in part if m[2] <= r and m[3] >= LOSS_PCT) / len(part) for r in RATES]
        out.append([label, str(len(part)), f"{rssi} dBm", f"{rate:.0f} Mbps"] + [f"{s:.1%}" for s in shares])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("database", type=Path)
    parser.add_argument("--period", action="append", type=parse_period, default=[],
                        help="label=YYYY-MM-DD HH:MM..HH:MM (repeatable); default: one per hour")
    args = parser.parse_args(argv)
    data = minutes(args.database)
    if not data:
        print("no connected Wi-Fi minutes with router pings in this database", file=sys.stderr)
        return 1
    rows = table(data, args.period or hourly(data))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
