import unittest

from app import slowdown
from app.slowdown import DOWNLOAD, UPLOAD, SlowdownTracker, baseline, combine, network_id, verdict

CLEAN = {"router_loss_pct": 0.0, "router_ms": 4.7, "rx_mbps": 216.0, "tx_mbps": 480.0, "rssi": -65, "vpn": False}


def run(tracker, samples, base=300.0, evidence=None):
    """Feeds (ts, mbps) pairs; returns the transitions as (kind, ts) in order."""
    out = []
    for ts, mbps in samples:
        out += [(t.kind, ts) for t in tracker.update(ts, mbps, base, evidence)]
    return out


class BaselineTests(unittest.TestCase):
    def test_plan_speed_wins(self):
        self.assertEqual(baseline([10.0] * 20, plan_mbps=300), 300.0)

    def test_none_until_enough_samples(self):
        self.assertIsNone(baseline([300.0] * (slowdown.MIN_BASELINE_SAMPLES - 1)))
        self.assertEqual(baseline([300.0] * slowdown.MIN_BASELINE_SAMPLES), 300.0)

    def test_best_quarter_is_not_dragged_down_by_a_long_slow_stretch(self):
        values = [300.0] * 8 + [5.0] * 24          # the line has been slow for 75% of the window
        self.assertEqual(baseline(values), 300.0)

    def test_ignores_missing_and_zero(self):
        self.assertEqual(baseline([None, 0.0] + [100.0] * 8), 100.0)


class VerdictTests(unittest.TestCase):
    def test_clean_hop_is_outside(self):
        self.assertEqual(verdict(CLEAN), "outside")

    def test_no_evidence_or_no_router_data_is_unknown(self):
        self.assertEqual(verdict(None), "unknown")
        self.assertEqual(verdict({**CLEAN, "router_loss_pct": None}), "unknown")

    def test_a_tunnel_makes_it_unknown(self):
        self.assertEqual(verdict({**CLEAN, "vpn": True}), "unknown")

    def test_router_loss_weak_signal_or_a_slow_link_are_local(self):
        self.assertEqual(verdict({**CLEAN, "router_loss_pct": 5.0}), "local")
        self.assertEqual(verdict({**CLEAN, "rssi": -80}), "local")
        self.assertEqual(verdict({**CLEAN, "rx_mbps": 24.0}), "local")

    def test_the_link_rate_that_counts_is_the_one_of_the_direction(self):
        evidence = {**CLEAN, "rx_mbps": 24.0}
        self.assertEqual(verdict(evidence, DOWNLOAD), "local")
        self.assertEqual(verdict(evidence, UPLOAD), "outside")

    def test_wired_has_no_wifi_numbers_and_can_still_be_outside(self):
        wired = {"router_loss_pct": 0.0, "router_ms": 1.0, "rx_mbps": None, "tx_mbps": None, "rssi": None}
        self.assertEqual(verdict(wired), "outside")

    def test_combine(self):
        self.assertEqual(combine("outside", None), "outside")
        self.assertEqual(combine("outside", "outside"), "outside")
        self.assertEqual(combine("outside", "local"), "unknown")


class TrackerTests(unittest.TestCase):
    def tracker(self):
        return SlowdownTracker(DOWNLOAD, max_gap_s=3600)

    def test_one_low_sample_is_noise(self):
        t = self.tracker()
        self.assertEqual(run(t, [(0, 5.0), (600, 300.0)]), [])
        self.assertIsNone(t.active)

    def test_two_low_samples_open_an_episode_starting_at_the_first(self):
        t = self.tracker()
        self.assertEqual(run(t, [(0, 300.0), (600, 5.0), (1200, 6.0)]), [("open", 1200)])
        assert t.active is not None
        self.assertEqual(t.active.start_ts, 600)
        self.assertEqual((t.active.samples, t.active.min_mbps, t.active.baseline_mbps), (2, 5.0, 300.0))

    def test_it_closes_at_the_first_sample_at_or_above_70_percent(self):
        t = self.tracker()
        events = run(t, [(0, 5.0), (600, 6.0), (1200, 4.0), (1800, 210.0)])
        self.assertEqual(events, [("open", 600), ("update", 1200), ("close", 1800)])

    def test_hysteresis_keeps_a_half_recovered_line_in_the_episode(self):
        t = self.tracker()
        events = run(t, [(0, 5.0), (600, 6.0), (1200, 180.0), (1800, 100.0), (2400, 250.0)])
        self.assertEqual(events, [("open", 600), ("update", 1200), ("update", 1800), ("close", 2400)])

    def test_a_sample_between_50_and_70_percent_does_not_open_one(self):
        t = self.tracker()
        self.assertEqual(run(t, [(0, 5.0), (600, 160.0), (1200, 5.0), (1800, 160.0)]), [])

    def test_episode_numbers(self):
        t = self.tracker()
        out = []
        for ts, mbps in [(0, 5.0), (600, 7.0), (1200, 3.0), (1800, 300.0)]:
            out += t.update(ts, mbps, 300.0, CLEAN)
        ep = out[-1].episode
        self.assertEqual((ep.start_ts, ep.end_ts, ep.samples, ep.min_mbps), (0, 1800, 3, 3.0))
        self.assertAlmostEqual(ep.avg_mbps, 5.0)
        self.assertEqual(ep.verdict, "outside")

    def test_a_gap_ends_the_episode_at_its_last_sample_and_starts_clean(self):
        t = self.tracker()
        run(t, [(0, 5.0), (600, 6.0)])
        out = t.update(600 + 7200, 5.0, 300.0)         # the monitor was off for two hours
        self.assertEqual([x.kind for x in out], ["close"])
        self.assertEqual(out[0].episode.end_ts, 600)
        self.assertIsNone(t.active)
        # the sample after the gap is the first of a possible new episode, not a continuation
        self.assertEqual(run(t, [(7800 + 600, 5.0)]), [("open", 8400)])

    def test_a_gap_forgets_a_pending_low_sample(self):
        t = self.tracker()
        run(t, [(0, 5.0)])
        self.assertEqual(run(t, [(7200, 5.0)]), [])    # not two in a row any more

    def test_interrupt_closes_an_open_episode(self):
        t = self.tracker()
        run(t, [(0, 5.0), (600, 6.0)])
        out = t.interrupt()
        self.assertEqual((out[0].kind, out[0].episode.end_ts), ("close", 600))
        self.assertEqual(t.interrupt(), [])

    def test_directions_are_separate_trackers(self):
        down, up = self.tracker(), SlowdownTracker(UPLOAD, max_gap_s=3600)
        run(down, [(0, 5.0), (600, 6.0)])
        run(up, [(0, 120.0), (600, 122.0)], base=120.0)
        self.assertIsNotNone(down.active)
        self.assertIsNone(up.active)


class NetworkIdTests(unittest.TestCase):
    def test_network_id(self):
        self.assertEqual(network_id("192.168.3.1", "HomeNet"), "192.168.3.1|HomeNet")
        self.assertEqual(network_id("192.168.3.1", None), "192.168.3.1|")
        self.assertEqual(network_id(None, None), "?|")


if __name__ == "__main__":
    unittest.main()
