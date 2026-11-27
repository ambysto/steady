import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from app import i18n
from app.storage import SCHEMA_VERSION, Storage, level_for_event

DAY = 86400


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.addCleanup(self.db.close)


class SchemaTests(unittest.TestCase):
    def test_file_db_persists_and_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.db"
            with Storage(path) as db:
                db.add_event(100, "logger_start", "hi")
            with Storage(path) as db:
                self.assertEqual(db.query_events()[0]["message"], "hi")
            raw = sqlite3.connect(path)
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(raw.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            raw.close()

    def test_upgrade_from_v1_keeps_data_and_adds_diagnostic_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.db"
            raw = sqlite3.connect(path)  # what a v1 database looked like
            raw.executescript("""
                CREATE TABLE minute_stats (ts INTEGER NOT NULL, target TEXT NOT NULL, ip TEXT, sent INTEGER NOT NULL,
                    lost INTEGER NOT NULL, avg REAL, max REAL, jitter REAL, PRIMARY KEY (ts, target)) WITHOUT ROWID;
                CREATE TABLE wifi_stats (ts INTEGER PRIMARY KEY, state TEXT, ssid TEXT, bssid TEXT, channel INTEGER,
                    signal REAL, rssi INTEGER, rx_mbps REAL, tx_mbps REAL);
                CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL,
                    level TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', duration REAL);
                INSERT INTO events (ts, kind, level, message) VALUES (5, 'old', 'info', 'kept');
                PRAGMA user_version = 1;
            """)
            raw.commit()
            raw.close()
            with Storage(path) as db:
                self.assertEqual(db.query_events()[0]["message"], "kept")
                rid = db.save_diagnostic_run(10, "ok", [{"key": "a"}])
                self.assertEqual(db.get_diagnostic_run(rid)["results"], [{"key": "a"}])
            raw = sqlite3.connect(path)
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            raw.close()

    def test_upgrade_from_v2_adds_message_columns_and_keeps_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.db"
            with Storage(path):
                pass
            raw = sqlite3.connect(path)   # turn it back into a v2 database: events without message_key/params
            raw.executescript("""
                DROP TABLE events;
                CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL,
                    level TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', duration REAL);
                INSERT INTO events (ts, kind, level, message) VALUES (5, 'old', 'info', 'Bật tối ưu: x');
                PRAGMA user_version = 2;
            """)
            raw.commit()
            raw.close()
            with Storage(path) as db:
                db.add_event(6, "watchdog_action", i18n.msg("watchdog.event.recovered", duration=12), level="info")
                events = {e["kind"]: e["message"] for e in db.query_events()}
            self.assertEqual(events["old"], "Bật tối ưu: x")              # legacy text untouched
            self.assertEqual(events["watchdog_action"], i18n.msg("watchdog.event.recovered", duration=12))
            raw = sqlite3.connect(path)
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            columns = {row[1] for row in raw.execute("PRAGMA table_info(events)")}
            self.assertLessEqual({"message_key", "message_params"}, columns)
            raw.close()

    def test_newer_schema_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.db"
            raw = sqlite3.connect(path)
            raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            raw.commit()
            raw.close()
            with self.assertRaises(RuntimeError):
                Storage(path)


class MinuteStatsTests(StorageTestCase):
    def test_range_is_half_open_and_ordered(self):
        for ts in (60, 120, 180):
            self.db.add_minute_stat(ts, "router", 60, 0, avg=2.0)
        rows = self.db.query_minute_stats(60, 180)
        self.assertEqual([r["ts"] for r in rows], [60, 120])

    def test_target_filter_and_fields(self):
        self.db.add_minute_stat(60, "router", 60, 3, ip="192.168.3.1", avg=11.9, max=71, jitter=9.6)
        self.db.add_minute_stat(60, "google", 60, 0)
        (row,) = self.db.query_minute_stats(0, 1000, target="router")
        self.assertEqual((row["ip"], row["sent"], row["lost"], row["avg"], row["max"], row["jitter"]),
                         ("192.168.3.1", 60, 3, 11.9, 71, 9.6))
        self.assertEqual(len(self.db.query_minute_stats(0, 1000)), 2)

    def test_same_minute_and_target_is_replaced(self):
        self.db.add_minute_stat(60, "router", 10, 1)
        self.db.add_minute_stat(60, "router", 20, 2)
        (row,) = self.db.query_minute_stats(0, 1000)
        self.assertEqual((row["sent"], row["lost"]), (20, 2))

    def test_null_stats_when_every_ping_lost(self):
        self.db.add_minute_stat(60, "router", 60, 60)
        (row,) = self.db.query_minute_stats(0, 1000)
        self.assertIsNone(row["avg"])


class WifiTests(StorageTestCase):
    def test_roundtrip(self):
        self.db.add_wifi_stat(60, state="connected", ssid="Home", bssid="aa:bb", channel=36,
                              signal=72.2, rssi=-67, rx_mbps=6, tx_mbps=432)
        (row,) = self.db.query_wifi_stats(0, 1000)
        self.assertEqual((row["ssid"], row["channel"], row["signal"], row["rssi"], row["rx_mbps"]),
                         ("Home", 36, 72.2, -67, 6))

    def test_disconnected_row_with_nulls(self):
        self.db.add_wifi_stat(60, state="disconnected")
        (row,) = self.db.query_wifi_stats(0, 1000)
        self.assertIsNone(row["rssi"])


class EventTests(StorageTestCase):
    def test_default_levels(self):
        self.assertEqual(level_for_event("router_down"), "bad")
        self.assertEqual(level_for_event("internet_down"), "bad")
        self.assertEqual(level_for_event("wifi_state", "connected -> disconnected (Home)"), "warn")
        self.assertEqual(level_for_event("wifi_state", "disconnected -> connected (Home)"), "info")
        self.assertEqual(level_for_event("roam"), "info")

    def test_add_and_order_newest_first(self):
        self.db.add_event(10, "a")
        self.db.add_event(30, "b")
        self.db.add_event(20, "c", duration=5)
        self.assertEqual([e["kind"] for e in self.db.query_events()], ["b", "c", "a"])
        self.assertEqual(self.db.query_events(kinds=["c"])[0]["duration"], 5)

    def test_same_timestamp_keeps_insert_order_newest_first(self):
        self.db.add_event(10, "first")
        self.db.add_event(10, "second")
        self.assertEqual([e["kind"] for e in self.db.query_events()], ["second", "first"])

    def test_filters_and_limit(self):
        for i in range(10):
            self.db.add_event(i, "tick" if i % 2 else "tock")
        self.assertEqual(len(self.db.query_events(limit=3)), 3)
        self.assertEqual({e["kind"] for e in self.db.query_events(kinds=["tick"])}, {"tick"})
        self.assertEqual([e["ts"] for e in self.db.query_events(since=3, until=6)], [5, 4, 3])
        self.assertEqual(self.db.query_events(kinds=[]), [])

    def test_explicit_level_and_validation(self):
        self.db.add_event(1, "custom", level="warn")
        self.assertEqual(self.db.query_events()[0]["level"], "warn")
        with self.assertRaises(ValueError):
            self.db.add_event(1, "x", level="fatal")

    def test_sql_injection_in_values_is_inert(self):
        self.db.add_event(1, "k'; DROP TABLE events;--", "m'); DROP TABLE events;--")
        self.assertEqual(len(self.db.query_events()), 1)


class DiagnosticRunTests(StorageTestCase):
    def test_save_list_get_roundtrip_with_unicode(self):
        r1 = self.db.save_diagnostic_run(100, "warn", [{"key": "signal", "summary": "Tín hiệu yếu"}])
        r2 = self.db.save_diagnostic_run(200, "ok", [])
        runs = self.db.list_diagnostic_runs()
        self.assertEqual([r["id"] for r in runs], [r2, r1])  # newest first
        self.assertNotIn("results", runs[0])
        self.assertEqual(self.db.get_diagnostic_run(r1)["results"][0]["summary"], "Tín hiệu yếu")
        self.assertEqual(self.db.latest_diagnostic_run()["id"], r2)

    def test_missing_run(self):
        self.assertIsNone(self.db.get_diagnostic_run(999))
        self.assertIsNone(self.db.latest_diagnostic_run())


class PurgeTests(StorageTestCase):
    def test_deletes_only_older_than_retention(self):
        now = 100 * DAY
        for table_add in (
            lambda ts: self.db.add_minute_stat(ts, "router", 1, 0),
            lambda ts: self.db.add_wifi_stat(ts, state="connected"),
            lambda ts: self.db.add_event(ts, "x"),
        ):
            table_add(now - 31 * DAY)
            table_add(now - 29 * DAY)
        deleted = self.db.purge(30, now=now)
        self.assertEqual(deleted, {"minute_stats": 1, "wifi_stats": 1, "events": 1,
                                   "speed_samples": 0, "slowdowns": 0})
        self.assertEqual(len(self.db.query_minute_stats(0, now)), 1)
        self.assertEqual(len(self.db.query_wifi_stats(0, now)), 1)
        self.assertEqual(len(self.db.query_events()), 1)

    def test_purge_empty_db(self):
        self.assertEqual(self.db.purge(30, now=DAY * 50), {"minute_stats": 0, "wifi_stats": 0, "events": 0,
                                                           "speed_samples": 0, "slowdowns": 0})

    def test_what_a_report_is_made_of_is_kept_for_two_years(self):
        now = 1000 * DAY
        old, older = now - 90 * DAY, now - 800 * DAY
        for kind in ("internet_down", "router_down", "slowdown", "wifi_state"):
            self.db.add_event(old, kind)
            self.db.add_event(older, kind)
        self.db.add_speed_sample(old, down_mbps=300.0)
        self.db.add_speed_sample(older, down_mbps=300.0)
        for start in (old, older):
            sid = self.db.add_slowdown(start, "download", 300.0, 5.0)
            self.db.update_slowdown(sid, end_ts=start + 600, min_mbps=5.0, avg_mbps=5.0, samples=2,
                                    verdict="outside", evidence=None)
        open_id = self.db.add_slowdown(older, "download", 300.0, 5.0)   # still open: never purged
        deleted = self.db.purge(30, now=now)
        self.assertEqual(deleted["events"], 1 + 4)       # wifi_state at 90 days, and the four 800-day events
        self.assertEqual(deleted["speed_samples"], 1)
        self.assertEqual(deleted["slowdowns"], 1)
        kinds = sorted(e["kind"] for e in self.db.query_events(limit=50))
        self.assertEqual(kinds, ["internet_down", "router_down", "slowdown"])
        self.assertIn(open_id, [r["id"] for r in self.db.query_slowdowns()])

    def test_speed_samples_and_slowdowns_round_trip(self):
        self.db.add_speed_sample(1000, network="gw|net", down_mbps=5.3, up_mbps=122.0, evidence={"a": 1})
        self.db.add_speed_sample(2000, network="gw|net", skipped="own_traffic")
        rows = self.db.query_speed_samples(0, 3000)
        self.assertEqual([r["ts"] for r in rows], [1000, 2000])
        self.assertEqual(rows[0]["evidence"], {"a": 1})
        self.assertEqual(rows[1]["skipped"], "own_traffic")
        self.assertEqual(self.db.query_speed_samples(0, 3000, network="other"), [])
        sid = self.db.add_slowdown(1000, "download", 300.0, 5.0, network="gw|net", evidence={"open": {}})
        self.assertIsNone(self.db.query_slowdowns()[0]["end_ts"])
        self.assertEqual(self.db.query_slowdowns(since=5000), [self.db.query_slowdowns()[0]])   # open: overlaps
        self.db.update_slowdown(sid, end_ts=1900, min_mbps=4.0, avg_mbps=4.5, samples=3, verdict="outside",
                                evidence={"open": {}, "close": {}})
        row = self.db.query_slowdowns()[0]
        self.assertEqual((row["end_ts"], row["samples"], row["verdict"]), (1900, 3, "outside"))
        self.assertEqual(self.db.query_slowdowns(since=2000), [])
        self.assertEqual(self.db.query_slowdowns(until=1000), [])


class ConcurrencyTests(unittest.TestCase):
    def test_parallel_writers_and_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Storage(Path(tmp) / "m.db")
            errors = []

            def writer(n):
                try:
                    for i in range(150):
                        db.add_minute_stat(i * 60, f"t{n}", 60, 0, avg=1.0)
                        db.add_event(n * 1000 + i, "tick")
                        db.query_events(limit=5)
                except Exception as exc:  # pragma: no cover - failure path
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            self.assertEqual(errors, [])
            self.assertEqual(len(db.query_minute_stats(0, 10**9)), 8 * 150)
            self.assertEqual(len(db.query_events(limit=10**6)), 8 * 150)
            db.close()


PINGLOG = """time,target,ip,sent,lost,avg_ms,max_ms,jitter_ms
2026-10-03 11:03,router,192.168.3.1,21,4,11.9,71,9.6
2026-10-03 11:03,cloudflare,1.1.1.1,21,21,,,
bad-time,router,192.168.3.1,21,4,11.9,71,9.6
2026-10-03 11:04,router,192.168.3.1,x,4,1,1,1
"""
WIFI = """time,state,ssid,bssid,channel,signal_avg,rssi,rx_mbps,tx_mbps
2026-10-03 11:03,connected,"HomeNet",02:5e:00:9a:40:24,36,72,-67,6,432
2026-10-03 11:04,connected,"HomeNet",02:5e:00:9a:40:24,36,72.2,-68,34.4,360
2026-10-03 11:05,disconnected,"",,,,,,
"""
EVENTS = """time,kind,duration_s,detail
2026-10-03 11:03:39,logger_start,,"PID 20208, gateway 192.168.3.1"
2026-10-03 11:04:56,internet_down,56,"router also unreachable"
2026-10-03 11:06:00,wifi_state,,"connected -> disconnected (HomeNet)"
,roam,,"no time"
"""


class ImportTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        (d / "pinglog-20261003.csv").write_text(PINGLOG, encoding="utf-8")
        (d / "wifi-20261003.csv").write_text(WIFI, encoding="utf-8")
        (d / "events-20261003.csv").write_text(EVENTS, encoding="utf-8-sig")  # PowerShell writes a BOM
        self.dir = d

    def test_import_counts_and_values(self):
        rep = self.db.import_csv_dir(self.dir)
        self.assertEqual((rep.minute_stats, rep.wifi_stats, rep.events, rep.skipped), (2, 3, 3, 3))
        t0 = int(datetime(2026, 10, 3, 11, 3).astimezone().timestamp())
        rows = self.db.query_minute_stats(t0, t0 + 60)
        self.assertEqual({r["target"] for r in rows}, {"router", "cloudflare"})
        lost_all = next(r for r in rows if r["target"] == "cloudflare")
        self.assertEqual((lost_all["sent"], lost_all["lost"], lost_all["avg"]), (21, 21, None))

    def test_wifi_quoted_ssid_and_empty_fields(self):
        self.db.import_csv_dir(self.dir)
        rows = self.db.query_wifi_stats(0, 10**10)
        self.assertEqual(rows[0]["ssid"], "HomeNet")
        self.assertEqual((rows[1]["signal"], rows[1]["rx_mbps"]), (72.2, 34.4))
        self.assertEqual(rows[2]["state"], "disconnected")
        self.assertIsNone(rows[2]["rssi"])
        self.assertIsNone(rows[2]["channel"])

    def test_translatable_event_keeps_english_text_in_the_message_column(self):
        message = i18n.msg("event.router_down", router="192.168.3.1")
        self.db.add_event(10, "router_down", message, duration=30)
        self.assertEqual(self.db.query_events()[0]["message"], message)
        stored = self.db._rows("SELECT message, message_key, message_params FROM events", [])[0]
        self.assertEqual(stored["message"], "PC could not reach router 192.168.3.1")   # readable straight from the DB
        self.assertEqual(stored["message_key"], "event.router_down")
        self.assertNotIn("message_key", self.db.query_events()[0])

    def test_level_is_derived_from_the_english_text(self):
        self.db.add_event(10, "router_down", i18n.msg("event.router_down", router="x"))
        self.assertEqual(self.db.query_events()[0]["level"], "bad")

    def test_events_get_levels_and_duration(self):
        self.db.import_csv_dir(self.dir)
        by_kind = {e["kind"]: e for e in self.db.query_events()}
        self.assertEqual(by_kind["internet_down"]["level"], "bad")
        self.assertEqual(by_kind["internet_down"]["duration"], 56)
        self.assertEqual(by_kind["wifi_state"]["level"], "warn")
        self.assertIsNone(by_kind["logger_start"]["duration"])
        self.assertEqual(by_kind["logger_start"]["message"], "PID 20208, gateway 192.168.3.1")

    def test_import_is_idempotent(self):
        self.db.import_csv_dir(self.dir)
        rep = self.db.import_csv_dir(self.dir)
        self.assertEqual(rep.events, 0)
        self.assertEqual(len(self.db.query_events()), 3)
        self.assertEqual(len(self.db.query_minute_stats(0, 10**10)), 2)
        self.assertEqual(len(self.db.query_wifi_stats(0, 10**10)), 3)

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as empty:
            rep = self.db.import_csv_dir(empty)
        self.assertEqual((rep.minute_stats, rep.wifi_stats, rep.events, rep.skipped), (0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
