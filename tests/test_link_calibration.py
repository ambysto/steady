"""scripts/link_calibration.py: the share of bad minutes per period and rate threshold."""
import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("link_calibration", ROOT / "scripts" / "link_calibration.py")
calibration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibration)

START = int(datetime(2026, 10, 6, 9, 0).timestamp())


class LinkCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / "metrics.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE minute_stats (ts INTEGER, target TEXT, sent INTEGER, lost INTEGER)")
            conn.execute("CREATE TABLE wifi_stats (ts INTEGER PRIMARY KEY, state TEXT, rssi INTEGER,"
                         " rx_mbps REAL, tx_mbps REAL)")
            for i in range(20):   # 09:00-09:19 at the desk: fast and clean
                ts = START + 60 * i
                conn.execute("INSERT INTO minute_stats VALUES (?, 'router', 60, 0)", (ts,))
                conn.execute("INSERT INTO wifi_stats VALUES (?, 'connected', -55, NULL, 720)", (ts,))
            for i in range(20):   # 10:00-10:19 far away: half the minutes slow and lossy (Mac: Tx only)
                ts = START + 3600 + 60 * i
                conn.execute("INSERT INTO minute_stats VALUES (?, 'router', 60, ?)", (ts, 6 if i % 2 else 0))
                conn.execute("INSERT INTO wifi_stats VALUES (?, 'connected', -78, NULL, ?)", (ts, 24 if i % 2 else 200))
            ts = START + 7200   # too few router pings, and a Windows row with Rx: Rx wins
            conn.execute("INSERT INTO minute_stats VALUES (?, 'router', 10, 5)", (ts,))
            conn.execute("INSERT INTO wifi_stats VALUES (?, 'connected', -60, 5, 700)", (ts,))

    def tearDown(self):
        self.dir.cleanup()

    def test_minutes_join_the_router_and_skip_thin_ones(self):
        data = calibration.minutes(self.db)
        self.assertEqual(len(data), 40)
        self.assertEqual(data[0], (START, -55, 720.0, 0.0))

    def test_shares_per_period_and_threshold(self):
        periods = [calibration.parse_period("desk=2026-10-06 09:00..09:30"),
                   calibration.parse_period("far=2026-10-06 10:00..2026-10-06 10:30")]
        rows = calibration.table(calibration.minutes(self.db), periods)
        self.assertEqual(rows[1][:4], ["desk", "20", "-55 dBm", "720 Mbps"])
        self.assertEqual(set(rows[1][4:]), {"0.0%"})
        far = dict(zip(rows[0], rows[2]))
        self.assertEqual((far["≤12 & loss≥5%"], far["≤24 & loss≥5%"], far["≤30 & loss≥5%"]), ("0.0%", "50.0%", "50.0%"))

    def test_without_periods_every_hour_is_one(self):
        self.assertEqual([p[0] for p in calibration.hourly(calibration.minutes(self.db))],
                         ["2026-10-06 09:00", "2026-10-06 10:00"])

    def test_a_bad_period_is_refused(self):
        with self.assertRaises(Exception):
            calibration.parse_period("late=2026-10-06 10:00..09:00")


if __name__ == "__main__":
    unittest.main()
