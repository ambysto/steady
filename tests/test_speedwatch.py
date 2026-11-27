import unittest

from app import slowdown, speedwatch
from app.speedsample import Sample
from app.speedwatch import SpeedWatch
from app.storage import Storage

NET = "192.168.3.1|HomeNet"
CLEAN = {"router_loss_pct": 0.0, "router_ms": 4.7, "rx_mbps": 216.0, "tx_mbps": 480.0, "rssi": -65, "vpn": False}
INTERVAL_S = 1800


class Clock:
    def __init__(self, now=1_000_000):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds=INTERVAL_S):
        self.now += seconds


class Harness:
    """A SpeedWatch with fake measurements; `sample` is what the next cycle will measure."""

    def __init__(self, **settings):
        self.storage = Storage()
        self.clock = Clock()
        self.sample = Sample(down_mbps=300.0, up_mbps=120.0)
        self.skip: str | None = None
        self.network = NET
        self.evidence = dict(CLEAN)
        self.calls = 0
        speed = {"enabled": True, "interval_min": 30, "plan_down_mbps": 0, "plan_up_mbps": 0, **settings}
        self.watch = SpeedWatch(self.storage, load_settings=lambda: {"speed": speed},
                                skip_reason=lambda: self.skip, evidence=lambda: dict(self.evidence),
                                network=lambda: self.network, sample_fn=self._take, clock=self.clock)

    def _take(self, **_):
        self.calls += 1
        return self.sample

    def seed_baseline(self, count=10, down=300.0, up=120.0):
        for i in range(count):
            self.storage.add_speed_sample(self.clock.now - (count - i) * INTERVAL_S, network=NET,
                                          down_mbps=down, up_mbps=up)

    def cycle(self, down: float | None = 300.0, up: float | None = 120.0, **sample):
        self.clock.advance()
        self.sample = Sample(down_mbps=down, up_mbps=up, **sample)
        return self.watch.run_once()

    def slowdowns(self):
        return self.storage.query_slowdowns()

    def slowdown_events(self):
        return self.storage.query_events(kinds=["slowdown"])


class SlowdownFlowTests(unittest.TestCase):
    def test_the_2026_10_07_case_is_recorded_with_its_evidence_and_closed_when_the_line_recovers(self):
        h = Harness()
        h.seed_baseline()
        h.cycle(down=5.3, up=122.2)
        self.assertEqual(h.slowdowns(), [])                      # one low sample is not an episode yet
        h.cycle(down=5.0, up=121.0)
        (row,) = h.slowdowns()
        self.assertEqual((row["direction"], row["verdict"], row["network"]), ("download", "outside", NET))
        self.assertIsNone(row["end_ts"])
        self.assertEqual(row["baseline_mbps"], 300.0)
        self.assertEqual(row["evidence"]["open"]["up_mbps"], 122.2)   # the upload was fine at that moment
        self.assertEqual(h.slowdown_events(), [])                # the Log gets it when it is over
        h.cycle(down=5.2)
        h.cycle(down=290.0)                                       # recovered
        (row,) = h.slowdowns()
        self.assertEqual((row["samples"], row["min_mbps"]), (3, 5.0))
        self.assertEqual(row["end_ts"], h.clock.now)
        (event,) = h.slowdown_events()
        self.assertEqual(event["message"]["key"], "event.slowdown.outside")
        self.assertEqual(event["message"]["params"]["direction"], {"key": "event.slowdown.download", "params": {}})
        self.assertEqual(event["message"]["params"]["baseline"], 300)
        self.assertEqual(event["duration"], row["end_ts"] - row["start_ts"])
        self.assertEqual(event["level"], "warn")

    def test_a_poor_wifi_link_makes_the_slowdown_local(self):
        h = Harness()
        h.seed_baseline()
        h.evidence.update(router_loss_pct=12.0)
        h.cycle(down=5.0)
        h.cycle(down=5.0)
        self.assertEqual(h.slowdowns()[0]["verdict"], "local")

    def test_no_baseline_yet_means_no_episode(self):
        h = Harness()
        h.seed_baseline(count=2)
        for _ in range(slowdown.MIN_BASELINE_SAMPLES - 3):   # the samples taken now count too: stay under the minimum
            h.cycle(down=5.0)
        self.assertEqual(h.slowdowns(), [])

    def test_the_plan_speed_is_the_baseline_from_the_first_sample(self):
        h = Harness(plan_down_mbps=300)
        h.cycle(down=5.0)
        h.cycle(down=5.0)
        self.assertEqual(len(h.slowdowns()), 1)

    def test_upload_is_tracked_on_its_own(self):
        h = Harness()
        h.seed_baseline()
        h.cycle(up=3.0)
        h.cycle(up=3.0)
        (row,) = h.slowdowns()
        self.assertEqual(row["direction"], "upload")

    def test_a_baseline_belongs_to_one_network(self):
        h = Harness()
        h.seed_baseline()
        h.network = "10.0.0.1|Cafe"                  # nothing learned here yet: 5 Mb/s may be normal
        h.cycle(down=5.0)
        h.cycle(down=5.0)
        self.assertEqual(h.slowdowns(), [])

    def test_changing_network_ends_an_open_episode(self):
        h = Harness()
        h.seed_baseline()
        h.cycle(down=5.0)
        h.cycle(down=5.0)
        h.network = "10.0.0.1|Cafe"
        h.cycle(down=300.0)
        (row,) = h.slowdowns()
        self.assertIsNotNone(row["end_ts"])
        self.assertEqual(len(h.slowdown_events()), 1)


class SkippedSampleTests(unittest.TestCase):
    def test_a_skipped_cycle_is_recorded_with_the_reason_and_measures_nothing(self):
        h = Harness()
        h.skip = "outage"
        h.clock.advance()
        self.assertEqual(h.watch.run_once(), "outage")
        self.assertEqual(h.calls, 0)
        (row,) = h.storage.query_speed_samples(0, 10 ** 9)
        self.assertEqual((row["skipped"], row["down_mbps"]), ("outage", None))

    def test_own_traffic_is_stored_but_never_opens_an_episode(self):
        h = Harness()
        h.seed_baseline()
        for _ in range(3):
            h.cycle(down=5.0, foreign_mbps=80.0)
        self.assertEqual(h.slowdowns(), [])
        rows = h.storage.query_speed_samples(h.clock.now - 3 * INTERVAL_S, h.clock.now + 1)
        self.assertEqual({r["skipped"] for r in rows}, {"own_traffic"})
        self.assertEqual(rows[0]["down_mbps"], 5.0)               # what was measured stays visible

    def test_a_refusal_backs_off_to_what_the_server_asked_or_twice_the_interval(self):
        h = Harness()
        h.clock.advance()
        h.sample = Sample(refused=429, retry_after_s=10_000.0)
        self.assertEqual(h.watch.run_once(), "refused")
        self.assertEqual(h.watch._next_at, h.clock.now + 10_000)
        h.sample = Sample(refused=403)
        h.watch.run_once()
        self.assertEqual(h.watch._next_at, h.clock.now + 2 * INTERVAL_S)

    def test_a_failed_measurement_is_an_error_sample(self):
        h = Harness()
        self.assertEqual(h.cycle(down=None, up=None, error="OSError: timed out"), "error")
        (row,) = h.storage.query_speed_samples(0, 10 ** 9)
        self.assertEqual(row["evidence"]["error"], "OSError: timed out")

    def test_skipped_samples_do_not_feed_the_baseline(self):
        h = Harness()
        h.seed_baseline(count=slowdown.MIN_BASELINE_SAMPLES - 1)
        for i in range(10):
            h.storage.add_speed_sample(h.clock.now - i * 60, network=NET, down_mbps=900.0, skipped="own_traffic")
        h.cycle(down=5.0)
        h.cycle(down=5.0)
        self.assertEqual(h.slowdowns(), [])                       # still no baseline: they did not count


class LifecycleTests(unittest.TestCase):
    def test_stop_ends_an_open_episode(self):
        h = Harness()
        h.seed_baseline()
        h.cycle(down=5.0)
        h.cycle(down=5.0)
        h.watch.stop()
        (row,) = h.slowdowns()
        self.assertEqual(row["end_ts"], h.clock.now)

    def test_start_closes_what_a_previous_run_left_open(self):
        h = Harness()
        h.storage.add_speed_sample(h.clock.now - 600, network=NET, down_mbps=5.0)
        h.storage.add_slowdown(h.clock.now - 3000, "download", 300.0, 5.0)
        h.watch._close_left_open(h.clock.now)
        self.assertEqual(h.slowdowns()[0]["end_ts"], h.clock.now - 600)

    def test_a_disabled_watch_takes_no_sample(self):
        h = Harness(enabled=False)
        h.watch.start()
        try:
            h.watch._stop.wait(0.1)
        finally:
            h.watch.stop()
        self.assertEqual(h.calls, 0)

    def test_first_sample_waits_for_the_delay(self):
        h = Harness()
        h.watch.start()
        try:
            self.assertEqual(h.watch._next_at, h.clock.now + speedwatch.FIRST_SAMPLE_DELAY_S)
        finally:
            h.watch.stop()


if __name__ == "__main__":
    unittest.main()
