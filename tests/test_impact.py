import unittest

from app import i18n, impact
from app.i18n import msg
from app.storage import Storage

H = 3600
T = 1_791_000_000 - 1_791_000_000 % 60   # the change, on a minute boundary


def fill(db, start, end, loss=0, rx=500.0, rssi=-60, step=60):
    """One monitored minute every `step` seconds: router pings (60 sent, `loss` lost) + Wi-Fi state."""
    for ts in range(int(start), int(end), step):
        db.add_minute_stat(ts, "router", 60, loss)
        db.add_minute_stat(ts, "cloudflare", 60, 0)
        db.add_wifi_stat(ts, state="connected", rssi=rssi, rx_mbps=rx)


def outages(db, start, end, every, length=120, kind="router_down"):
    for began in range(int(start), int(end), every):
        db.add_event(began + length, kind, msg("event.router_down", router="192.168.3.1"), duration=length)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.addCleanup(self.db.close)

    def test_collecting_until_two_hours_after(self):
        fill(self.db, T - 24 * H, T + H)
        r = impact.compare(self.db, T, T + H)
        self.assertEqual(r["status"], "collecting")
        self.assertEqual(i18n.render(r["summary"], "en"), "Collecting data: 1.0 h measured after the change, needs 2 h")

    def test_no_baseline_without_data_before(self):
        fill(self.db, T, T + 5 * H)
        self.assertEqual(impact.compare(self.db, T, T + 5 * H)["status"], "no_baseline")

    def test_fewer_outages_and_less_loss_is_better(self):
        fill(self.db, T - 24 * H, T, loss=3)            # 5% loss before
        fill(self.db, T, T + 24 * H, loss=0)
        outages(self.db, T - 24 * H, T, every=2 * H)      # 12 outages before, none after
        r = impact.compare(self.db, T, T + 30 * H)
        self.assertEqual(r["status"], "better")
        self.assertFalse(r["preliminary"])
        verdicts = {f["metric"]: f["verdict"] for f in r["findings"]}
        self.assertEqual(verdicts, {"outages": "better", "offline_minutes": "better", "router_loss": "better",
                                    "bad_link": "same"})
        self.assertEqual(r["before"]["outages_per_day"], 12.0)
        text = [i18n.render(d, "en") for d in r["details"]]
        self.assertIn("Outages: 12.0 → 0.0 per day", text)
        self.assertTrue(text[-1].startswith("Measured, not proven"))

    def test_more_outages_is_worse(self):
        fill(self.db, T - 24 * H, T + 24 * H)
        outages(self.db, T, T + 24 * H, every=3 * H)       # 8 after, none before
        self.assertEqual(impact.compare(self.db, T, T + 24 * H)["status"], "worse")

    def test_small_numbers_are_no_change(self):
        fill(self.db, T - 24 * H, T + 24 * H)
        outages(self.db, T - 10 * H, T - 9 * H, every=H)   # a single outage before, none after
        r = impact.compare(self.db, T, T + 24 * H)
        self.assertEqual(r["status"], "no_change")
        self.assertEqual(i18n.render(r["summary"], "vi"), "Chưa thấy khác biệt rõ kể từ khi thay đổi")

    def test_mixed(self):
        fill(self.db, T - 24 * H, T, loss=0)
        fill(self.db, T, T + 24 * H, loss=3)
        outages(self.db, T - 24 * H, T, every=2 * H)
        self.assertEqual(impact.compare(self.db, T, T + 24 * H)["status"], "mixed")

    def test_collapsed_link_minutes(self):
        fill(self.db, T - 24 * H, T, loss=6, rx=6.0)       # Rx <= 30 with >= 5% loss: collapsed
        fill(self.db, T, T + 24 * H, loss=0, rx=600.0)
        r = impact.compare(self.db, T, T + 24 * H)
        self.assertEqual({f["metric"]: f["verdict"] for f in r["findings"]}["bad_link"], "better")

    def test_equal_windows_while_preliminary(self):
        fill(self.db, T - 24 * H, T + 6 * H)
        r = impact.compare(self.db, T, T + 6 * H)
        self.assertEqual(r["window_s"], 6 * H)
        self.assertTrue(r["preliminary"])
        self.assertEqual(r["before"]["monitored_s"], 6 * H)    # not the whole day before
        self.assertIn("impact.preliminary", [d["key"] for d in r["details"]])

    def test_unmonitored_time_is_not_counted(self):
        fill(self.db, T - 24 * H, T)
        fill(self.db, T + 12 * H, T + 24 * H)                  # PC asleep for the first 12 h after
        outages(self.db, T - 24 * H, T, every=4 * H)          # 6 per monitored day before
        outages(self.db, T + 12 * H, T + 24 * H, every=4 * H)  # 3 in 12 monitored hours = 6 per day
        r = impact.compare(self.db, T, T + 24 * H)
        self.assertEqual(r["after"]["monitored_s"], 12 * H)
        self.assertEqual(r["after"]["outages_per_day"], 6.0)
        self.assertEqual(r["status"], "no_change")

    def test_measurement_stops_when_the_change_is_undone(self):
        fill(self.db, T - 24 * H, T + 24 * H)
        outages(self.db, T + 10 * H, T + 24 * H, every=H)       # all after the undo at T + 10 h
        r = impact.compare(self.db, T, T + 24 * H, until=T + 10 * H)
        self.assertEqual((r["until"], r["after"]["outages"]), (T + 10 * H, 0))

    def test_outage_across_the_change_counts_where_it_started(self):
        fill(self.db, T - 24 * H, T + 24 * H)
        self.db.add_event(T + 300, "router_down", "x", duration=600)   # began 5 min before T
        r = impact.compare(self.db, T, T + 24 * H)
        self.assertEqual((r["before"]["outages"], r["after"]["outages"]), (1, 0))
        self.assertEqual((r["before"]["outage_s"], r["after"]["outage_s"]), (300, 300))


class ChangesTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.addCleanup(self.db.close)
        fill(self.db, T - 72 * H, T + 48 * H, step=600)   # sparse but enough monitored time

    def test_tweaks_and_manual_steps_are_found_with_their_window(self):
        name = msg("tweak.power_pcie_aspm_off.name")
        self.db.add_event(T, "tweak_enabled", msg("tweak.event.enabled", name=name, tweak_id="power_pcie_aspm_off"))
        self.db.add_event(T + 30 * H, "tweak_disabled", msg("tweak.event.disabled", name=name, tweak_id="power_pcie_aspm_off"))
        self.db.add_event(T - 48 * H, "manual_step_done", msg("manual.event.done", step=msg("manual.antenna.title"),
                                                              step_id="antenna"))
        self.db.add_event(T - 50 * H, "tweak_enabled", "Bật tối ưu: x")    # pre-v3 text: no id, skipped
        found = impact.changes(self.db, {}, T + 48 * H, tweak_names={"power_pcie_aspm_off": name})
        self.assertEqual([(c["kind"], c["id"]) for c in found], [("tweak", "power_pcie_aspm_off"), ("manual", "antenna")])
        self.assertEqual(found[0]["undone"], T + 30 * H)
        self.assertEqual(found[0]["impact"]["until"], T + 24 * H)       # 24 h cap comes first here
        self.assertEqual(found[0]["title"], name)
        self.assertFalse(found[0]["overlaps"])

    def test_backup_captured_at_is_the_fallback_for_own_captures_only(self):
        backup = {"wifi_power_saving": {"original": {}, "captured_at": T, "source": "capture"},
                  "device_power_off": {"original": {}, "captured_at": T, "source": "EXP-001"},
                  "tcp_timedwait": {"original": {}, "captured_at": T - 40 * 86400, "source": "capture"}}
        found = impact.changes(self.db, backup, T + 48 * H)
        self.assertEqual([c["id"] for c in found], ["wifi_power_saving"])

    def test_changes_close_together_are_flagged(self):
        for i, tid in enumerate(("a", "b")):
            self.db.add_event(T + i * H, "tweak_enabled", msg("tweak.event.enabled", name=tid, tweak_id=tid))
        found = impact.changes(self.db, {}, T + 48 * H)
        self.assertTrue(all(c["overlaps"] for c in found))
        self.assertIn("impact.overlap", [d["key"] for d in found[0]["impact"]["details"]])

    def test_old_changes_are_left_out(self):
        self.db.add_event(T - 20 * 86400, "tweak_enabled", msg("tweak.event.enabled", name="x", tweak_id="x"))
        self.assertEqual(impact.changes(self.db, {}, T), [])


if __name__ == "__main__":
    unittest.main()
