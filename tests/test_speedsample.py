import unittest

from app import speedsample
from app.bufferbloat import ServerRefused
from app.speedsample import Sample, take_sample, traffic_delta


class Clock:
    def __init__(self, step=1.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


def fake_download(mbps, nbytes=speedsample.DOWN_BYTES):
    return lambda n, t: (nbytes, nbytes * 8 / mbps / 1e6)


def fake_upload(mbps, nbytes=speedsample.UP_BYTES):
    return lambda n, t: (nbytes, nbytes * 8 / mbps / 1e6)


class TakeSampleTests(unittest.TestCase):
    def test_speeds_come_from_bytes_over_seconds(self):
        s = take_sample(download=fake_download(300), upload=fake_upload(120), clock=Clock())
        self.assertAlmostEqual(s.down_mbps, 300.0)
        self.assertAlmostEqual(s.up_mbps, 120.0)
        self.assertIsNone(s.foreign_mbps)           # no interface counters given
        self.assertFalse(s.own_traffic)

    def test_a_refusal_stops_the_sample_and_keeps_what_was_measured(self):
        def refused(n, t):
            raise ServerRefused(429, 120.0)
        s = take_sample(download=fake_download(300), upload=refused, clock=Clock())
        self.assertAlmostEqual(s.down_mbps, 300.0)
        self.assertIsNone(s.up_mbps)
        self.assertEqual((s.refused, s.retry_after_s), (429, 120.0))

    def test_a_network_error_is_recorded_not_raised(self):
        def broken(n, t):
            raise OSError("timed out")
        s = take_sample(download=broken, upload=fake_upload(120), clock=Clock())
        self.assertIsNone(s.down_mbps)
        self.assertIn("timed out", s.error)

    def test_nothing_read_is_no_speed(self):
        s = take_sample(download=lambda n, t: (0, 1.0), upload=fake_upload(120), clock=Clock())
        self.assertIsNone(s.down_mbps)
        self.assertAlmostEqual(s.up_mbps, 120.0)

    def test_traffic_of_the_sample_itself_is_not_foreign(self):
        moved = speedsample.DOWN_BYTES + speedsample.UP_BYTES
        readings = iter([1_000, 1_000 + int(moved * speedsample.OVERHEAD)])
        s = take_sample(download=fake_download(300), upload=fake_upload(120), octets=lambda: next(readings),
                        clock=Clock())
        self.assertAlmostEqual(s.foreign_mbps, 0.0, places=3)
        self.assertFalse(s.own_traffic)

    def test_another_download_on_this_pc_is_flagged(self):
        moved = speedsample.DOWN_BYTES + speedsample.UP_BYTES
        extra = 40_000_000                           # 40 MB moved by something else in a ~2 s sample
        readings = iter([0, int(moved * speedsample.OVERHEAD) + extra])
        s = take_sample(download=fake_download(300), upload=fake_upload(120), octets=lambda: next(readings),
                        clock=Clock(step=1.0))
        self.assertGreater(s.foreign_mbps, 50)
        self.assertTrue(s.own_traffic)

    def test_light_background_traffic_is_not_flagged(self):
        moved = speedsample.DOWN_BYTES + speedsample.UP_BYTES
        readings = iter([0, int(moved * speedsample.OVERHEAD) + 100_000])
        s = take_sample(download=fake_download(5), upload=fake_upload(120), octets=lambda: next(readings),
                        clock=Clock())
        self.assertFalse(s.own_traffic)


class OwnTrafficTests(unittest.TestCase):
    def test_threshold_needs_both_the_floor_and_the_share(self):
        self.assertFalse(Sample(down_mbps=300, foreign_mbps=20).own_traffic)    # 20 < 25% of 300
        self.assertTrue(Sample(down_mbps=300, foreign_mbps=80).own_traffic)
        self.assertFalse(Sample(down_mbps=5, foreign_mbps=2).own_traffic)       # under the 5 Mb/s floor
        self.assertTrue(Sample(down_mbps=5, foreign_mbps=6).own_traffic)
        self.assertFalse(Sample(foreign_mbps=100).own_traffic)                   # nothing was measured


class TrafficDeltaTests(unittest.TestCase):
    def test_delta(self):
        self.assertEqual(traffic_delta(100, 350), 250)
        self.assertEqual(traffic_delta(2 ** 32 - 10, 5), 15)      # the counter wrapped
        self.assertIsNone(traffic_delta(None, 5))
        self.assertIsNone(traffic_delta(5, None))


if __name__ == "__main__":
    unittest.main()
