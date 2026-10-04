"""SQLite storage: per-minute ping stats, per-minute Wi-Fi state, and events.

Timestamps are integer Unix seconds (UTC). minute_stats/wifi_stats rows are keyed
by the start of their minute. An *_down event is recorded when the outage ends,
so its ts is the recovery time and `duration` (seconds) tells where it started.

One connection is shared by all threads and serialised with a lock; the monitor's
write rate (a handful of rows per minute) makes that a non-issue.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import i18n

# v2: diagnostic_runs. v3: events.message_key/message_params (ADR-0006). Every statement in _SCHEMA is
# IF NOT EXISTS, so upgrading re-runs it; columns added to existing tables are listed in _ADDED_COLUMNS.
SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_stats (
    ts     INTEGER NOT NULL,
    target TEXT    NOT NULL,
    ip     TEXT,
    sent   INTEGER NOT NULL,
    lost   INTEGER NOT NULL,
    avg    REAL,
    max    REAL,
    jitter REAL,
    PRIMARY KEY (ts, target)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS wifi_stats (
    ts      INTEGER PRIMARY KEY,
    state   TEXT,
    ssid    TEXT,
    bssid   TEXT,
    channel INTEGER,
    signal  REAL,
    rssi    INTEGER,
    rx_mbps REAL,
    tx_mbps REAL
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    kind     TEXT    NOT NULL,
    level    TEXT    NOT NULL,
    message  TEXT    NOT NULL DEFAULT '',   -- English text, so the DB stays readable on its own
    duration REAL,
    message_key    TEXT,                   -- set when the message is translatable (ADR-0006)
    message_params TEXT                    -- JSON object
);
CREATE INDEX IF NOT EXISTS events_ts   ON events (ts);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind, ts);

CREATE TABLE IF NOT EXISTS diagnostic_runs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    worst   TEXT    NOT NULL,
    results TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS diagnostic_runs_ts ON diagnostic_runs (ts);
"""

_ADDED_COLUMNS = {"events": [("message_key", "TEXT"), ("message_params", "TEXT")]}   # v3

LEVELS = ("info", "warn", "bad")


def level_for_event(kind: str, message: str = "") -> str:
    """Default severity for an event kind (used when importing old logs)."""
    if kind in ("internet_down", "router_down"):
        return "bad"
    if kind == "wifi_state" and "-> disconnected" in message:
        return "warn"
    return "info"


def _event_out(row: dict[str, Any]) -> dict[str, Any]:
    """`message` comes back as the stored message dict when there is one, else as plain text."""
    key, params = row.pop("message_key", None), row.pop("message_params", None)
    if key:
        try:
            row["message"] = {"key": key, "params": json.loads(params) if params else {}}
        except ValueError:
            pass   # keep the English text
    return row


@dataclass
class ImportReport:
    minute_stats: int = 0
    wifi_stats: int = 0
    events: int = 0
    skipped: int = 0   # unparseable rows, and events already present


class Storage:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            with self._lock:
                if self.path != ":memory:":
                    self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
                self._migrate()
        except BaseException:
            self._db.close()  # don't leave the file locked (Windows) when init is refused
            raise

    def _migrate(self) -> None:
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"{self.path} has schema v{version}, this build understands v{SCHEMA_VERSION}")
        if version < SCHEMA_VERSION:
            self._db.executescript(_SCHEMA)
            for table, columns in _ADDED_COLUMNS.items():
                have = {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns:
                    if name not in have:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- writes ---------------------------------------------------------------

    def add_minute_stat(self, ts: int, target: str, sent: int, lost: int, *, ip: str | None = None,
                        avg: float | None = None, max: float | None = None,
                        jitter: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO minute_stats (ts, target, ip, sent, lost, avg, max, jitter) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(ts), target, ip, sent, lost, avg, max, jitter),
            )

    def add_wifi_stat(self, ts: int, *, state: str | None = None, ssid: str | None = None,
                      bssid: str | None = None, channel: int | None = None, signal: float | None = None,
                      rssi: int | None = None, rx_mbps: float | None = None,
                      tx_mbps: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO wifi_stats (ts, state, ssid, bssid, channel, signal, rssi, rx_mbps, tx_mbps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(ts), state, ssid, bssid, channel, signal, rssi, rx_mbps, tx_mbps),
            )

    def add_event(self, ts: int, kind: str, message: Any = "", *, level: str | None = None,
                  duration: float | None = None) -> int:
        """`message`: plain text, or an i18n.msg() dict (stored as key + params, read back as the dict)."""
        key = params = None
        if i18n.is_message(message):
            key, params = message["key"], json.dumps(message["params"], ensure_ascii=False, default=str)
            message = i18n.render(message, i18n.DEFAULT)
        level = level or level_for_event(kind, message)
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}, got {level!r}")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (ts, kind, level, message, duration, message_key, message_params) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(ts), kind, level, message, duration, key, params),
            )
            return cur.lastrowid

    # --- reads ----------------------------------------------------------------

    def _rows(self, sql: str, params: Iterable[Any]) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, tuple(params)).fetchall()]

    def query_minute_stats(self, start: int, end: int, target: str | None = None) -> list[dict[str, Any]]:
        """Rows with start <= ts < end, oldest first."""
        sql = "SELECT * FROM minute_stats WHERE ts >= ? AND ts < ?"
        params: list[Any] = [int(start), int(end)]
        if target is not None:
            sql += " AND target = ?"
            params.append(target)
        return self._rows(sql + " ORDER BY ts, target", params)

    def query_wifi_stats(self, start: int, end: int) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM wifi_stats WHERE ts >= ? AND ts < ? ORDER BY ts",
                          [int(start), int(end)])

    def query_events(self, since: int | None = None, until: int | None = None,
                     kinds: Iterable[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Newest first. since <= ts < until."""
        sql, params = "SELECT * FROM events WHERE 1=1", []
        if since is not None:
            sql += " AND ts >= ?"
            params.append(int(since))
        if until is not None:
            sql += " AND ts < ?"
            params.append(int(until))
        kinds = list(kinds) if kinds is not None else None
        if kinds is not None:
            if not kinds:
                return []
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(int(limit))
        return [_event_out(r) for r in self._rows(sql, params)]

    # --- diagnostic runs ------------------------------------------------------

    def save_diagnostic_run(self, ts: int, worst: str, results: list[dict[str, Any]]) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO diagnostic_runs (ts, worst, results) VALUES (?, ?, ?)",
                (int(ts), worst, json.dumps(results, ensure_ascii=False)),
            )
            return cur.lastrowid

    def list_diagnostic_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Newest first, without the (large) results."""
        return self._rows("SELECT id, ts, worst FROM diagnostic_runs ORDER BY ts DESC, id DESC LIMIT ?", [int(limit)])

    def get_diagnostic_run(self, run_id: int) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM diagnostic_runs WHERE id = ?", [int(run_id)])
        if not rows:
            return None
        row = rows[0]
        row["results"] = json.loads(row["results"])
        return row

    def latest_diagnostic_run(self) -> dict[str, Any] | None:
        runs = self.list_diagnostic_runs(1)
        return self.get_diagnostic_run(runs[0]["id"]) if runs else None

    # --- maintenance ----------------------------------------------------------

    def purge(self, retention_days: float, now: float | None = None) -> dict[str, int]:
        """Delete everything older than retention_days. Returns rows deleted per table."""
        cutoff = int((time.time() if now is None else now) - retention_days * 86400)
        deleted = {}
        with self._lock:
            for table in ("minute_stats", "wifi_stats", "events"):
                deleted[table] = self._db.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,)).rowcount
        return deleted

    # --- import of the PowerShell logger's CSVs -------------------------------

    def import_csv_dir(self, directory: str | Path) -> ImportReport:
        """Import pinglog-*.csv, wifi-*.csv, events-*.csv. Safe to run repeatedly:
        stats rows are keyed and replaced, events already present are skipped."""
        report = ImportReport()
        directory = Path(directory)
        with self._lock:
            self._db.execute("BEGIN")
            try:
                for path in sorted(directory.glob("pinglog-*.csv")):
                    self._import_pinglog(path, report)
                for path in sorted(directory.glob("wifi-*.csv")):
                    self._import_wifi(path, report)
                for path in sorted(directory.glob("events-*.csv")):
                    self._import_events(path, report)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return report

    @staticmethod
    def _read_csv(path: Path) -> Iterable[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            yield from csv.DictReader(fh)

    def _import_pinglog(self, path: Path, report: ImportReport) -> None:
        for row in self._read_csv(path):
            ts = _parse_local_time(row.get("time"))
            sent, lost = _num(row.get("sent"), int), _num(row.get("lost"), int)
            if ts is None or not row.get("target") or sent is None or lost is None:
                report.skipped += 1
                continue
            self.add_minute_stat(ts, row["target"], sent, lost, ip=row.get("ip") or None,
                                 avg=_num(row.get("avg_ms")), max=_num(row.get("max_ms")),
                                 jitter=_num(row.get("jitter_ms")))
            report.minute_stats += 1

    def _import_wifi(self, path: Path, report: ImportReport) -> None:
        for row in self._read_csv(path):
            ts = _parse_local_time(row.get("time"))
            if ts is None:
                report.skipped += 1
                continue
            self.add_wifi_stat(ts, state=row.get("state") or None, ssid=row.get("ssid") or None,
                               bssid=row.get("bssid") or None, channel=_num(row.get("channel"), int),
                               signal=_num(row.get("signal_avg")), rssi=_num(row.get("rssi"), int),
                               rx_mbps=_num(row.get("rx_mbps")), tx_mbps=_num(row.get("tx_mbps")))
            report.wifi_stats += 1

    def _import_events(self, path: Path, report: ImportReport) -> None:
        for row in self._read_csv(path):
            ts = _parse_local_time(row.get("time"))
            kind, message = row.get("kind"), row.get("detail") or ""
            if ts is None or not kind:
                report.skipped += 1
                continue
            exists = self._db.execute(
                "SELECT 1 FROM events WHERE ts = ? AND kind = ? AND message = ?", (ts, kind, message),
            ).fetchone()
            if exists:
                report.skipped += 1
                continue
            self.add_event(ts, kind, message, duration=_num(row.get("duration_s")))
            report.events += 1


def _parse_local_time(text: str | None) -> int | None:
    """'2026-10-03 11:03' or '... 11:03:39' in local time -> Unix seconds."""
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(text.strip(), fmt).astimezone().timestamp())
        except ValueError:
            continue
    return None


def _num(text: str | None, kind: type = float):
    if text is None or text.strip() == "":
        return None
    try:
        return kind(float(text)) if kind is int else float(text)
    except ValueError:
        return None
