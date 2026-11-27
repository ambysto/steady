import csv
import io
import time
import unittest
from datetime import date, datetime, timedelta, timezone

from app import report
from app.i18n import msg
from app.storage import Storage

UTC = timezone.utc
DAY = 86400


def ts(year, month, day, hour=0, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


class PeriodTests(unittest.TestCase):
    def bounds(self, kind, day):
        start, end, label = report.period_bounds(kind, day, UTC)
        return (datetime.fromtimestamp(start, UTC).date(), datetime.fromtimestamp(end, UTC).date(), label)

    def test_week_runs_monday_to_monday(self):
        self.assertEqual(self.bounds("week", date(2026, 10, 7)), (date(2026, 10, 5), date(2026, 10, 12), "2026-W41"))
        self.assertEqual(self.bounds("week", date(2026, 10, 5))[0], date(2026, 10, 5))
        self.assertEqual(self.bounds("week", date(2026, 10, 11))[0], date(2026, 10, 5))

    def test_month_including_december(self):
        self.assertEqual(self.bounds("month", date(2026, 9, 30)), (date(2026, 9, 1), date(2026, 10, 1), "2026-09"))
        self.assertEqual(self.bounds("month", date(2026, 12, 15)), (date(2026, 12, 1), date(2027, 1, 1), "2026-12"))

    def test_quarters(self):
        self.assertEqual(self.bounds("quarter", date(2026, 2, 3)), (date(2026, 1, 1), date(2026, 4, 1), "2026-Q1"))
        self.assertEqual(self.bounds("quarter", date(2026, 9, 30)), (date(2026, 7, 1), date(2026, 10, 1), "2026-Q3"))
        self.assertEqual(self.bounds("quarter", date(2026, 11, 1)), (date(2026, 10, 1), date(2027, 1, 1), "2026-Q4"))
        self.assertEqual(self.bounds("quarter", date(2026, 12, 31))[1], date(2027, 1, 1))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            report.period_bounds("year", date(2026, 1, 1))


class ZoneNameTests(unittest.TestCase):
    def test_this_computers_own_offset_is_named_when_no_zone_is_given(self):
        moment = ts(2026, 10, 7, 12)
        offset = time.localtime(moment).tm_gmtoff // 60
        expected = f"UTC{'+' if offset >= 0 else '-'}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"
        self.assertEqual(report._zone_name(None, moment), expected)

    def test_an_explicit_zone(self):
        plus7 = timezone(timedelta(hours=7))
        self.assertEqual(report._zone_name(plus7, ts(2026, 10, 7)), "UTC+07:00")
        self.assertEqual(report._zone_name(UTC, ts(2026, 10, 7)), "UTC+00:00")
        self.assertEqual(report._zone_name(timezone(timedelta(hours=-3, minutes=-30)), 0), "UTC-03:30")


class IntervalTests(unittest.TestCase):
    def test_union_counts_overlaps_once_and_clips(self):
        self.assertEqual(report.union_seconds([(0, 10), (5, 15), (20, 30)], 0, 100), 25)
        self.assertEqual(report.union_seconds([(-50, 5), (95, 200)], 0, 100), 10)
        self.assertEqual(report.union_seconds([], 0, 100), 0)

    def test_unmonitored_time(self):
        events = [{"kind": "monitor_start", "ts": 100}, {"kind": "monitor_stop", "ts": 300},
                  {"kind": "monitor_start", "ts": 500},
                  {"kind": "monitor_gap", "ts": 900, "duration": 100}]
        off = report.unmonitored(events, 0, 1000)
        self.assertEqual(report.union_seconds(off, 0, 1000), 100 + 200 + 100)   # before the first start, 300-500, the gap
        self.assertEqual(report.union_seconds(report.unmonitored(events, 600, 1000), 600, 1000), 100)

    def test_a_stop_with_no_start_after_it_lasts_to_the_end(self):
        events = [{"kind": "monitor_start", "ts": 0}, {"kind": "monitor_stop", "ts": 800}]
        self.assertEqual(report.union_seconds(report.unmonitored(events, 0, 1000), 0, 1000), 200)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.start, self.end, self.label = report.period_bounds("month", date(2026, 9, 10), UTC)
        self.now = self.end + DAY

    def add_outage(self, start, seconds, kind="internet_down"):
        self.db.add_event(start + seconds, kind, msg("event.internet_down.wan"), duration=seconds)

    def add_slowdown(self, start, seconds, direction="download", verdict="outside", avg=5.0, evidence=None):
        sid = self.db.add_slowdown(start, direction, 300.0, avg, verdict=verdict)
        self.db.update_slowdown(sid, end_ts=start + seconds, min_mbps=avg, avg_mbps=avg, samples=4, verdict=verdict,
                                evidence={"open": evidence or {"router_loss_pct": 0.0, "up_mbps": 122.0}})

    def build(self, **kw):
        return report.build(self.db, self.start, self.end, now=self.now, kind="month", label=self.label, tz=UTC, **kw)

    def test_totals_count_outages_and_slowdowns_by_verdict(self):
        self.db.add_event(self.start - 10, "monitor_start")
        self.add_outage(self.start + 1000, 300)
        self.add_outage(self.start + 5000, 100, "router_down")
        self.add_slowdown(self.start + 10_000, 3600)
        self.add_slowdown(self.start + 20_000, 1800, "upload", "local")
        data = self.build()
        tot = data["totals"]
        self.assertEqual((tot["outage_count"], tot["outage_s"]), (2, 400))
        self.assertEqual((tot["slowdown_outside_count"], tot["slowdown_outside_s"]), (1, 3600))
        self.assertEqual((tot["slowdown_local_count"], tot["slowdown_local_s"]), (1, 1800))
        self.assertEqual(tot["unmonitored_s"], 0)
        period = self.end - self.start
        self.assertAlmostEqual(tot["availability_pct"], 100 * (1 - 400 / period), places=3)
        self.assertEqual([o["kind"] for o in data["outages"]], ["internet_down", "router_down"])
        self.assertEqual(data["outages"][0]["start_ts"], self.start + 1000)

    def test_overlapping_slowdowns_in_both_directions_are_not_counted_twice(self):
        self.add_slowdown(self.start + 100, 1000, "download")
        self.add_slowdown(self.start + 600, 1000, "upload")
        self.assertEqual(self.build()["totals"]["slowdown_outside_s"], 1500)

    def test_time_when_nothing_watched_is_taken_out_of_availability(self):
        self.db.add_event(self.start + DAY, "monitor_start")       # installed a day into the period
        self.add_outage(self.start + 2 * DAY, 600)
        data = self.build()
        self.assertEqual(data["totals"]["unmonitored_s"], DAY)
        watched = (self.end - self.start) - DAY
        self.assertAlmostEqual(data["totals"]["availability_pct"], 100 * (1 - 600 / watched), places=3)

    def test_a_period_still_running_is_reported_up_to_now(self):
        self.now = self.start + 10 * DAY
        self.db.add_event(self.start - 1, "monitor_start")
        data = self.build()
        self.assertEqual(data["covered_until_ts"], self.now)
        self.assertEqual(data["totals"]["period_s"], 10 * DAY)

    def test_speed_summary_hourly_and_skipped(self):
        for i in range(20):
            self.db.add_speed_sample(self.start + i * 3600, down_mbps=300.0 if i < 16 else 5.0, up_mbps=120.0)
        self.db.add_speed_sample(self.start + 30 * 3600, skipped="own_traffic", down_mbps=900.0)
        data = self.build()
        down = data["speed"]["download"]
        self.assertEqual((down["samples"], down["median_mbps"], down["p10_mbps"], down["min_mbps"]), (20, 300.0, 5.0, 5.0))
        self.assertEqual((down["baseline_mbps"], down["below_half"]), (300.0, 4))
        self.assertEqual(data["speed"]["upload"]["median_mbps"], 120.0)
        self.assertEqual(data["samples_skipped"], {"own_traffic": 1})
        self.assertEqual(data["samples_taken"], 21)
        self.assertEqual(data["hourly_download"][0], 300.0)
        self.assertEqual(data["hourly_download"][19], 5.0)

    def test_plan_speed_is_the_baseline_when_given(self):
        for i in range(8):
            self.db.add_speed_sample(self.start + i * 3600, down_mbps=100.0)
        self.assertEqual(self.build(plan_down=300)["speed"]["download"]["baseline_mbps"], 300.0)

    def test_empty_period(self):
        data = self.build()
        self.assertEqual((data["outages"], data["slowdowns"], data["speed"]), ([], [], {}))
        self.assertEqual(data["totals"]["outage_count"], 0)

    def test_digest_changes_when_a_row_changes(self):
        self.add_slowdown(self.start + 100, 1000)
        first = self.build()
        self.assertEqual(first["digest"], self.build()["digest"])
        first["slowdowns"][0]["duration_s"] += 1
        self.assertNotEqual(report.digest(first), first["digest"])

    def test_the_digest_does_not_depend_on_the_language(self):
        self.add_outage(self.start + 1000, 300)
        english = report.build(self.db, self.start, self.end, now=self.now, tz=UTC, lang="en")
        vietnamese = report.build(self.db, self.start, self.end, now=self.now, tz=UTC, lang="vi")
        self.assertEqual(english["digest"], vietnamese["digest"])

    def test_outages_of_other_periods_are_left_out(self):
        self.add_outage(self.start - 5000, 100)
        self.add_outage(self.end + 5000, 100)
        self.now = self.end + 10 * DAY
        self.assertEqual(self.build()["totals"]["outage_count"], 0)

    def test_summary_drops_the_sample_rows(self):
        self.db.add_speed_sample(self.start + 10, down_mbps=300.0)
        self.assertNotIn("samples", report.summary(self.build()))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        start, end, label = report.period_bounds("month", date(2026, 9, 10), UTC)
        self.db.add_event(start - 1, "monitor_start")
        self.db.add_event(start + 3000, "internet_down", msg("event.internet_down.wan"), duration=120)
        for i in range(10):
            self.db.add_speed_sample(start + i * 3600, down_mbps=300.0, up_mbps=120.0)
        sid = self.db.add_slowdown(start + 7200, "download", 300.0, 5.0, verdict="outside")
        self.db.update_slowdown(sid, end_ts=start + 9000, min_mbps=4.0, avg_mbps=5.0, samples=3, verdict="outside",
                                evidence={"open": {"router_loss_pct": 0.0, "router_ms": 4.7, "rx_mbps": 216.0,
                                                   "tx_mbps": 480.0, "rssi": -65, "up_mbps": 122.2, "vpn": False}})
        self.data = report.build(self.db, start, end, now=end + 1, kind="month", label=label, tz=UTC, lang="en",
                                 provider="Example ISP", public_ip="203.0.113.10")

    def test_html_is_self_contained_and_has_the_numbers(self):
        page = report.render_html(self.data, "en", UTC)
        self.assertTrue(page.startswith("<!doctype html>"))
        for needle in ("2026-09", "Example ISP", "203.0.113.10", self.data["digest"], "<svg", "122.2"):
            self.assertIn(needle, page)
        for forbidden in ("<script", "http://", "https://", "<link", "src="):
            self.assertNotIn(forbidden, page)

    def test_html_escapes_what_the_user_typed(self):
        self.data["provider"] = "<b>x</b>"
        page = report.render_html(self.data, "en", UTC)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)

    def test_no_local_addresses_in_the_page_or_the_csvs(self):
        everything = report.render_html(self.data, "en", UTC) + report.render_csv(self.data) + report.render_samples_csv(self.data)
        for private in ("192.168.", "02:5e:00", "aa:bb:cc"):
            self.assertNotIn(private, everything)

    def test_episodes_csv(self):
        rows = list(csv.DictReader(io.StringIO(report.render_csv(self.data))))
        self.assertEqual([r["type"] for r in rows], ["internet_down", "slowdown"])
        self.assertEqual(rows[1]["verdict"], "outside")
        self.assertEqual(rows[1]["start_utc"], "2026-09-01T02:00:00Z")
        self.assertEqual((rows[1]["duration_s"], rows[1]["baseline_mbps"], rows[1]["router_loss_pct"]), ("1800", "300", "0.0"))

    def test_samples_csv_lists_every_sample_with_the_reason_when_skipped(self):
        self.db.add_speed_sample(self.data["start_ts"] + 40_000, skipped="outage")
        data = report.build(self.db, self.data["start_ts"], self.data["end_ts"], now=self.data["end_ts"] + 1, tz=UTC)
        rows = list(csv.DictReader(io.StringIO(report.render_samples_csv(data))))
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[-1]["skipped"], "outage")
        self.assertEqual(rows[-1]["download_mbps"], "")

    def test_an_empty_report_still_renders(self):
        empty = report.build(Storage(), 0, DAY, now=DAY, tz=UTC)
        page = report.render_html(empty, "en", UTC)
        self.assertIn(empty["digest"], page)


if __name__ == "__main__":
    unittest.main()
