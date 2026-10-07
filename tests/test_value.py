"""app/value.py: what the Overview's value card counts (ADR-0020 point 8)."""
import unittest

from app import value
from app.i18n import msg
from app.storage import Storage

NOW = 1_800_000_000
H = 3600


def recovered(after_action=True, duration=40):
    key = "watchdog.event.recovered_after" if after_action else "watchdog.event.recovered"
    return msg(key, after=12, action="x", duration=duration) if after_action else msg(key, duration=duration)


class RecoveriesTests(unittest.TestCase):
    def event(self, ts, kind, message=None):
        return {"id": ts, "ts": ts, "kind": kind, "message": message or msg("x")}

    def test_only_recoveries_after_a_real_action_count(self):
        events = [self.event(1, "watchdog_action"), self.event(2, "watchdog_recovered", recovered(duration=30)),
                  self.event(3, "watchdog_dry_run"), self.event(4, "watchdog_recovered", recovered(duration=50)),
                  self.event(5, "watchdog_action"), self.event(6, "watchdog_recovered", recovered(after_action=False)),
                  self.event(7, "watchdog_recovered", recovered(duration=70)),          # no action before it
                  self.event(8, "watchdog_action"), self.event(9, "watchdog_recovered", recovered(duration=20))]
        self.assertEqual(value.recoveries(events), [30, 20])

    def test_order_comes_from_the_timestamps(self):
        events = [self.event(2, "watchdog_recovered", recovered(duration=30)), self.event(1, "watchdog_action")]
        self.assertEqual(value.recoveries(events), [30])


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.addCleanup(self.db.close)

    def change(self, kind, cid, status, ts=NOW - H, undone=None):
        return {"kind": kind, "id": cid, "title": cid, "ts": ts, "undone": undone, "impact": {"status": status}}

    def test_nothing_happened_is_said_with_how_long_the_app_watched(self):
        out = value.summarize(self.db, NOW, [])
        self.assertTrue(out["empty"])
        self.assertIsNone(out["watching_since"])
        self.db.add_minute_stat(NOW - 3 * 86400, "router", 60, 0)
        self.assertEqual(value.summarize(self.db, NOW, [])["watching_since"], NOW - 3 * 86400)

    def test_counts_what_the_app_did_in_the_window(self):
        for ts, kind, message in ((NOW - 2 * H, "watchdog_action", msg("x")),
                                  (NOW - 2 * H + 20, "watchdog_recovered", recovered(duration=40)),
                                  (NOW - H, "watchdog_action", msg("x")),
                                  (NOW - H + 20, "watchdog_recovered", recovered(duration=20)),
                                  (NOW - 8 * 86400, "watchdog_action", msg("x")),                 # too old
                                  (NOW - 8 * 86400 + 20, "watchdog_recovered", recovered()),
                                  (NOW - H, "failover_switched", msg("x"))):
            self.db.add_event(ts, kind, message)
        out = value.summarize(self.db, NOW, [])
        self.assertEqual(out["recovered"], {"count": 2, "avg_s": 30})
        self.assertEqual(out["failover_switches"], 1)
        self.assertFalse(out["empty"])

    def test_only_changes_measured_to_help_and_still_on(self):
        self.db.add_event(NOW - H, "tweak_verified", msg("tweak.event.verified_helped", name="Upload", tweak_id="upload_shaping",
                                                         before=900, after=60))
        self.db.add_event(NOW - H, "tweak_verified", msg("tweak.event.verified_no_help", name="MTU", tweak_id="mtu_pmtu",
                                                         before=1, after=1))
        self.db.add_event(NOW - H, "tweak_verified", msg("tweak.event.verified_helped", name="Old", tweak_id="was_turned_off",
                                                         before=900, after=60))
        changes = [self.change("tweak", "wifi_power_saving", "better"), self.change("tweak", "device_power_off", "no_change"),
                   self.change("tweak", "power_pcie_aspm_off", "better", undone=NOW - 10),
                   self.change("tweak", "was_turned_off", "better", undone=NOW - 10),
                   self.change("tweak", "upload_shaping", "better"),                    # already counted by its own check
                   self.change("tweak", "wifi_wake_magic", "better", ts=NOW - 9 * 86400),
                   self.change("manual", "antenna", "better"), self.change("manual", "move_closer", "collecting")]
        out = value.summarize(self.db, NOW, changes)
        titles = [t["title"]["key"] if isinstance(t["title"], dict) else t["title"] for t in out["tweaks_helped"]]
        self.assertEqual(sorted(titles), ["tweak.event.verified_helped", "wifi_power_saving"])
        self.assertEqual([s["title"] for s in out["steps_helped"]], ["antenna"])
        self.assertFalse(out["empty"])


if __name__ == "__main__":
    unittest.main()
