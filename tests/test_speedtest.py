"""The speed test (ADR-0024): the pure figures, the live view, the saved result, the engine's events and
the API. The cases follow apple/SteadyKit/Tests/SteadyKitTests/SpeedTestTests.swift, so both platforms
read one run the same way."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app import bufferbloat as bb
from app import speedtest as st
from app.diagnostics import evaluate_bufferbloat

AT = 1_800_000_000.0


def phase(internet, mbps=None, capped=False, refused=None, retry_after_s=None, steady=None, series=None, name="x"):
    return bb.Phase(name, {"router": [1.0, 1.0, 1.0], "internet": list(internet)}, mbps, "", capped, refused,
                    retry_after_s, steady, list(series or []))


class LiveRateTests(unittest.TestCase):
    def test_the_first_reading_has_no_rate(self):
        rate = bb.LiveRate()
        self.assertIsNone(rate.add(0, 0))
        self.assertIsNone(rate.add(0, 10))     # no time has passed

    def test_the_rate_covers_about_the_last_second(self):
        rate = bb.LiveRate()
        rate.add(0, 0)
        # 12.5 MB/s = 100 Mbit/s for the first second, then 25 MB/s = 200 Mbit/s.
        self.assertEqual(rate.add(0.5, 6_250_000), 100)
        rate.add(1.0, 12_500_000)
        rate.add(1.5, 25_000_000)
        self.assertEqual(rate.add(2.0, 37_500_000), 200)

    def test_a_total_that_goes_back_reads_as_zero(self):
        rate = bb.LiveRate()
        rate.add(0, 1000)
        self.assertEqual(rate.add(1, 0), 0)


class FigureTests(unittest.TestCase):
    def test_the_steady_rate_is_what_moved_after_the_ramp(self):
        # 25 MB in the 8 s after a reading at 2 s: 25 Mbit/s.
        self.assertEqual(bb.steady_mbps((2, 5_000_000), 30_000_000, 10), 25)
        self.assertIsNone(bb.steady_mbps(None, 30_000_000, 10))
        self.assertIsNone(bb.steady_mbps((2, 5_000_000), 6_000_000, 2.3))
        self.assertIsNone(bb.steady_mbps((2, 5_000_000), 5_000_000, 10))

    def test_jitter_is_the_mean_step_between_replies(self):
        self.assertAlmostEqual(st.jitter([10, 12, None, 11, 15]), 7.0 / 3)   # |2| + |-1| + |4| over 3 steps
        self.assertIsNone(st.jitter([10, None]))
        self.assertEqual(st.loss([10, None, 12, None]), 50)
        self.assertIsNone(st.loss([]))
        self.assertIsNone(st.median([None, None]))

    def test_the_server_location_comes_from_colo_or_cf_ray(self):
        self.assertEqual(bb.server_code("HKG", "8c1f-SIN"), "HKG")
        self.assertEqual(bb.server_code(None, "8c1f2a3b4c5d6e7f-SIN"), "SIN")
        self.assertIsNone(bb.server_code(None, None))
        self.assertIsNone(bb.server_code("", "nodash"))

    def test_a_full_run_gives_every_figure(self):
        series = [(0.25, 100.0), (0.5, 200.0)]
        m = bb.Measurement(phase([10, 12, 11, None]), phase([30, 40, 50], mbps=180, steady=210, series=series),
                           phase([60, 70, 80, None], mbps=20, steady=22, series=series), "HKG")
        r = st.result(m, AT)
        self.assertEqual((r["download_mbps"], r["upload_mbps"]), (210, 22))
        self.assertEqual(r["idle"], {"median_ms": 11, "jitter_ms": 1.5})
        self.assertEqual(r["during_download"], {"median_ms": 40, "jitter_ms": 10})
        self.assertEqual(r["during_upload"]["median_ms"], 70)
        self.assertAlmostEqual(r["loss_pct"], 200.0 / 11)     # 2 of the 11 Internet pings
        self.assertEqual(r["download_series"], [[0.25, 100.0], [0.5, 200.0]])
        self.assertEqual(r["server"], "HKG")
        self.assertTrue(st.has_figures(r))

    def test_without_a_steady_rate_the_phase_average_is_used(self):
        m = bb.Measurement(phase([10]), phase([20], mbps=800, capped=True), phase([20], mbps=5))
        r = st.result(m, AT)
        self.assertEqual((r["download_mbps"], r["download_capped"], r["upload_mbps"], r["upload_capped"]),
                         (800, True, 5, False))

    def test_a_refused_direction_has_no_figure_and_says_when_to_come_back(self):
        m = bb.Measurement(phase([10]), phase([30], mbps=100, steady=110),
                           phase([500, None], mbps=3, refused=429, retry_after_s=120, steady=4, series=[(1, 3)]))
        r = st.result(m, AT)
        self.assertEqual(r["download_mbps"], 110)
        self.assertIsNone(r["upload_mbps"])
        self.assertIsNone(r["during_upload"])      # the refused direction's pings are not a loaded line's
        self.assertIsNone(r["upload_series"])
        self.assertEqual(r["loss_pct"], 0)
        self.assertEqual((r["refused_status"], r["retry_until"]), (429, AT + 120))

    def test_a_run_that_moved_nothing_has_no_figures(self):
        m = bb.Measurement(phase([None, None]), phase([None], mbps=0), phase([None], mbps=0))
        r = st.result(m, AT)
        self.assertFalse(st.has_figures(r))
        self.assertIsNone(r["idle"]["median_ms"])
        self.assertEqual(r["loss_pct"], 100)
        self.assertIsNone(r["refused_status"])

    def test_a_load_under_one_mbps_has_no_figure(self):
        m = bb.Measurement(phase([10]), phase([20], mbps=0.4, steady=0.5), phase([20], mbps=30, steady=31))
        r = st.result(m, AT)
        self.assertIsNone(r["download_mbps"])
        self.assertIsNone(r["during_download"])
        self.assertEqual(r["upload_mbps"], 31)

    def test_a_long_series_is_thinned_keeping_its_ends(self):
        series = [(i / 20, float(i)) for i in range(200)]
        thin = st.thinned(series)
        self.assertEqual((len(thin), thin[0], thin[-1]), (st.MAX_POINTS, series[0], series[-1]))
        self.assertEqual(st.thinned(series[:10]), series[:10])

    def test_only_mobile_data_asks(self):
        self.assertTrue(st.needs_confirmation(True))
        self.assertFalse(st.needs_confirmation(False))


class LiveTests(unittest.TestCase):
    def test_the_live_view_follows_the_events(self):
        live = st.Live()
        live.apply(("stage", "idle"))
        live.apply(("ping", "idle", 0, 20.0))
        live.apply(("ping", "idle", 0.2, 24.0))
        self.assertEqual(live.latency("idle"), {"median_ms": 22, "jitter_ms": 4})
        self.assertIsNone(live.mbps("download"))
        self.assertAlmostEqual(live.fraction, 0.2 / st.DURATION_S)

        live.apply(("stage", "download"))
        live.apply(("rate", "download", 1, 100.0))
        live.apply(("ping", "download", 1, 300.0))     # still in the ramp
        live.apply(("rate", "download", 3, 500.0))
        self.assertEqual(live.mbps("download"), 500)    # the latest while it loads
        self.assertEqual(live.latency("download")["median_ms"], 300)   # nothing after the ramp yet: all of it
        live.apply(("ping", "download", 3, 60.0))
        live.apply(("ping", "download", 3.2, None))
        self.assertEqual(live.latency("download")["median_ms"], 60)    # after the ramp only
        self.assertEqual(live.loss_pct, 25)            # 1 of 4 replies kept (2 idle, 2 after the ramp)
        self.assertAlmostEqual(live.fraction, (4 + 3.2) / st.DURATION_S)

        live.apply(("stage", "upload"))
        live.apply(("rate", "download", 9, 700.0))
        self.assertEqual(live.mbps("download"), 600)   # ended: the mean after the ramp
        self.assertIsNone(live.mbps("upload"))
        self.assertEqual(len(live.series["download"]), 3)
        snap = live.snapshot()
        self.assertEqual((snap["stage"], snap["download_mbps"], len(snap["download_series"])), ("upload", 600, 3))

    def test_an_ended_direction_shows_the_results_figure(self):
        live = st.Live()
        live.apply(("stage", "download"))
        live.apply(("rate", "download", 3, 650.0))
        live.apply(("rate", "download", 9, 700.0))
        live.apply(("ended", "download", phase([], mbps=690, steady=706)))
        live.apply(("stage", "upload"))
        self.assertEqual(live.mbps("download"), 706)  # not the rates' mean, so nothing changes when the result comes
        live.apply(("rate", "upload", 3, 0.4))
        live.apply(("ended", "upload", phase([], mbps=0.4)))   # under 1 Mbit/s: the result has no figure either
        self.assertIsNone(live.mbps("upload"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = st.Store(Path(tmp.name) / "speedtest.json")

    def test_a_result_and_its_check_14_survive_a_restart(self):
        m = bb.Measurement(phase([20.0] * 20, name="idle"), phase([90.0] * 40, mbps=700, steady=706, name="download"),
                           phase([30, None] + [25.0] * 38, mbps=650, steady=702, name="upload"), "HKG")
        r = st.result(m, AT)
        check = [evaluate_bufferbloat(m).to_dict()]
        self.store.save(r, m, check)
        last, restored = st.Store(self.store.path).last()
        self.assertEqual(last, r)
        self.assertEqual(evaluate_bufferbloat(restored), evaluate_bufferbloat(m))
        self.assertEqual(self.store.load()["check"], check)

    def test_a_file_that_no_longer_decodes_is_ignored(self):
        self.store.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.last(), (None, None))
        self.store.path.write_text('{"last": {"old": 1}, "measurement": {"idle": 3}}', encoding="utf-8")
        self.assertEqual(self.store.last(), (None, None))
        self.store.path.write_text('{"last": {"measured_at": 5}, "measurement": {"idle": 3}}', encoding="utf-8")
        self.assertEqual(self.store.last(), ({"measured_at": 5}, None))


class EngineEventTests(unittest.TestCase):
    def test_the_meter_reports_a_live_rate_on_its_beat_and_stops_with_the_load(self):
        meter, done, t = bb.Meter(), threading.Event(), [0.0]
        reported = []

        def wait(seconds):                     # a fake clock: every wait moves time and the load
            t[0] += seconds
            meter.add(int(12_500_000 * seconds))   # 100 Mbit/s
            if t[0] >= 3.0:
                done.set()
            return done.is_set()
        at_ramp, series = bb.run_meter(meter, 0.0, done, lambda s, mbps: reported.append((s, mbps)),
                                       clock=lambda: t[0], wait=wait)
        self.assertEqual(series, reported)
        self.assertEqual(len(series), 11)                         # 0.25 .. 2.75 s: it stops when the load ends
        self.assertTrue(all(abs(mbps - 100) < 1e-6 for _, mbps in series))
        self.assertEqual(at_ramp, (2.0, 25_000_000))

    def test_measure_reports_phases_pings_rates_and_the_steady_figure(self):
        events = []

        def load(seconds, stop, meter=None):
            meter.note_server("HKG")
            end = time.monotonic() + 1.5
            while time.monotonic() < end and not stop.is_set():
                meter.add(125_000)              # 1 Mbit per 8 ms ~ 125 Mbit/s
                time.sleep(0.008)
            return meter.bytes

        m = bb.measure(lambda a: 10.0, {"internet": "1.1.1.1"}, load, load, idle_s=0.3, load_s=2.0,
                       interval=0.1, ramp_s=0.3, events=events.append)
        kinds = [e[0] for e in events]
        self.assertEqual([e[1] for e in events if e[0] == "stage"], ["idle", "download", "upload"])
        self.assertEqual([e[1] for e in events if e[0] == "ended"], ["download", "upload"])
        self.assertIn("rate", kinds)
        self.assertTrue(any(e[0] == "ping" and e[1] == "download" for e in events))
        self.assertEqual(m.server, "HKG")
        self.assertIsNotNone(m.download.steady_mbps)
        self.assertGreater(len(m.download.series), 0)
        # the meter stopped with the load (1.5 s), before the pings ended (2.0 s)
        self.assertLess(max(s for s, _ in m.download.series), 1.8)

    def test_the_loads_stop_at_the_limit_they_are_given(self):
        from unittest import mock
        seen = []
        with mock.patch.object(bb, "http_download", lambda *a, **k: seen.append(k["max_bytes"]) or 0),                 mock.patch.object(bb, "http_upload", lambda *a, **k: seen.append(k["max_bytes"]) or 0):
            for load in bb.real_loads(bb.METERED_MAX_BYTES) + bb.real_loads():
                load(1, threading.Event())
        self.assertEqual(seen, [250_000_000, 250_000_000, bb.MAX_BYTES, bb.MAX_BYTES])

    def test_without_events_the_loads_are_called_as_before(self):
        m = bb.measure(lambda a: 10.0, {"internet": "1.1.1.1"}, lambda s, e: 1_000_000, lambda s, e: 1_000_000,
                       idle_s=0.1, load_s=0.2, interval=0.1)
        self.assertEqual((m.download.steady_mbps, m.download.series, m.server), (None, [], None))


class ApiTests(unittest.TestCase):
    def setUp(self):
        from app.server import Api
        from app.storage import Storage
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Storage()
        self.addCleanup(self.db.close)
        self.store = st.Store(Path(tmp.name) / "speedtest.json")
        self.now = [AT]
        self.runs = 0
        self.cellular = [False]
        m = bb.Measurement(phase([20.0] * 20, name="idle"), phase([90.0] * 40, mbps=700, steady=706, name="download"),
                           phase([25.0] * 40, mbps=650, steady=702, name="upload"), "HKG")
        self.measurement = m
        self.limits = []

        def run(progress, limit):
            self.runs += 1
            self.limits.append(limit)
            progress({"stage": "idle"})
            return {"ts": int(self.now[0]), "worst": "bad", "results": [evaluate_bufferbloat(m).to_dict()],
                    "speed": st.result(m, self.now[0]), "measurement": m}
        self.make = lambda: Api(monitor=None, storage=self.db, run_speedtest=run, speedtest_store=self.store,
                                clock=lambda: self.now[0], on_cellular=lambda: self.cellular[0], sync_jobs=True)
        self.api = self.make()

    def test_on_wifi_or_ethernet_it_runs_without_asking(self):
        _, got = self.api.handle("GET", "/api/speedtest", {}, None)
        self.assertFalse(got["ask"])
        _, job = self.api.handle("POST", "/api/speedtest", {}, {})
        self.assertEqual((job["status"], job["result"]["speed"]["download_mbps"], self.runs), ("done", 706, 1))
        self.assertNotIn("measurement", job["result"])

    def test_the_limit_follows_the_route(self):
        self.api.handle("POST", "/api/speedtest", {}, {})
        self.cellular[0] = True
        self.api.handle("POST", "/api/speedtest", {}, {"confirmed": True})
        self.assertEqual(self.limits, [bb.MAX_BYTES, bb.METERED_MAX_BYTES])

    def test_a_run_that_waited_does_not_start_on_a_route_that_became_mobile_data(self):
        reads = iter([False, True])            # asked on Wi-Fi; mobile data once it has the line
        self.cellular = None
        api = Api_with(self, lambda: next(reads))
        _, job = api.handle("POST", "/api/speedtest", {}, {})
        self.assertEqual((job["status"], job["result"].get("needs_confirmation"), self.runs), ("done", True, 0))

    def test_check_14_passes_the_yes_on(self):
        from app.server import ApiError
        self.cellular[0] = True
        with self.assertRaises(ApiError):
            self.api.handle("POST", "/api/diagnostics/bufferbloat", {}, {})
        self.api.handle("POST", "/api/diagnostics/bufferbloat", {}, {"confirmed": True})
        self.assertEqual(self.runs, 1)

    def test_the_real_runner_measures_with_the_limit_it_is_given(self):
        from unittest import mock
        from app import diagnostics
        from app.server import Api
        seen = {}

        def fake_measure(ping, targets, down, up, **kw):
            seen.update(kw)
            return self.measurement
        with mock.patch.object(bb, "measure", fake_measure), \
                mock.patch.object(bb, "real_ping", lambda: (lambda a: 1.0)), \
                mock.patch.object(diagnostics.Context, "_load_tweak_states", lambda self: None), \
                mock.patch.object(diagnostics.winutil, "get_gateway", lambda: None):
            api = Api(monitor=None, storage=self.db, speedtest_store=self.store, clock=lambda: AT)
            out = api._default_speedtest(lambda p: None, bb.METERED_MAX_BYTES)
        self.assertEqual(seen["max_bytes"], bb.METERED_MAX_BYTES)
        self.assertEqual((out["speed"]["download_mbps"], out["results"][0]["id"]), (706, 14))

    def test_a_mobile_broadband_route_reads_as_mobile_data(self):
        from unittest import mock
        from app import traffic, winutil
        from app.server import Api
        self.assertEqual({traffic._KIND_BY_IFTYPE[243], traffic._KIND_BY_IFTYPE[244]}, {"cellular"})
        for kind, expected in (("cellular", True), ("wifi", False), ("tunnel", False)):
            with mock.patch.object(winutil, "internet_route_native", lambda dest: {"interface_index": 9}), \
                    mock.patch.object(traffic, "interface_row", lambda index: {"kind": kind}):
                self.assertEqual(Api._default_on_cellular(), expected)
        with mock.patch.object(winutil, "internet_route_native", lambda dest: None):
            self.assertFalse(Api._default_on_cellular())       # no IPv4 route: reads as no

    def test_on_mobile_data_every_run_needs_a_yes(self):
        from app.server import ApiError
        self.cellular[0] = True
        _, got = self.api.handle("GET", "/api/speedtest", {}, None)
        self.assertTrue(got["ask"])
        with self.assertRaises(ApiError) as cm:
            self.api.handle("POST", "/api/speedtest", {}, {})
        self.assertEqual((cm.exception.status, self.runs), (409, 0))
        self.api.handle("POST", "/api/speedtest", {}, {"confirmed": True})
        with self.assertRaises(ApiError):              # not remembered: the next run asks again
            self.api.handle("POST", "/api/speedtest", {}, {})
        self.assertEqual(self.runs, 1)

    def test_the_result_and_check_14_come_back_after_a_restart(self):
        self.api.handle("POST", "/api/speedtest", {}, {"confirmed": True})
        _, got = self.make().handle("GET", "/api/speedtest", {}, None)
        self.assertEqual((got["last"]["download_mbps"], got["last"]["server"], got["ask"]), (706, "HKG", False))
        self.assertEqual(got["bufferbloat"]["id"], 14)
        self.assertIsInstance(got["bufferbloat"]["summary"], str)      # rendered for the UI
        self.assertIsNone(got["job"])

    def test_check_14_starts_the_same_run(self):
        _, job = self.api.handle("POST", "/api/diagnostics/bufferbloat", {}, {})
        self.assertEqual((job["kind"], job["result"]["results"][0]["id"], self.runs), ("speedtest", 14, 1))

    def test_while_the_server_asks_to_wait_nothing_runs(self):
        from app.server import ApiError
        refused = bb.Measurement(phase([10.0]), phase([None], mbps=0, refused=429, retry_after_s=600),
                                 phase([None], mbps=0, refused=429, retry_after_s=600))
        self.store.save(st.result(refused, AT), refused, [])
        with self.assertRaises(ApiError) as cm:
            self.api.handle("POST", "/api/speedtest", {}, {})
        self.assertEqual((cm.exception.status, self.runs), (409, 0))
        self.now[0] = AT + 601
        self.api.handle("POST", "/api/speedtest", {}, {})
        self.assertEqual(self.runs, 1)

    def test_join_follows_a_running_test_and_never_starts_one(self):
        from app.server import ApiError
        with self.assertRaises(ApiError) as cm:
            self.api.handle("POST", "/api/speedtest", {}, {"join": True})
        self.assertEqual((cm.exception.status, self.runs), (409, 0))
        _, job = self.api.handle("POST", "/api/speedtest", {}, {})
        with self.assertRaises(ApiError):      # it has ended (sync jobs): joining starts nothing
            self.api.handle("POST", "/api/speedtest", {}, {"join": True})
        self.assertEqual(self.runs, 1)

    def test_a_result_that_cannot_be_kept_still_ends_the_run(self):

        def broken(*a, **k):
            raise PermissionError("held by another reader")
        self.store.save = broken
        _, job = self.api.handle("POST", "/api/speedtest", {}, {})
        self.assertEqual((job["status"], job["result"]["speed"]["download_mbps"]), ("done", 706))

    def test_an_old_check_14_stops_adding_suggestions(self):
        self.api.handle("POST", "/api/speedtest", {}, {"confirmed": True})
        self.assertTrue(self.api._last_bufferbloat)
        seen = []
        import app.suggestions as sugg
        original = sugg.suggest
        sugg.suggest = lambda results, *a, **k: seen.extend(r for r in results if r.get("id") == 14) or []
        try:
            self.api.handle("GET", "/api/suggestions", {}, None)
            self.now[0] = AT + 8 * 86400
            self.make().handle("GET", "/api/suggestions", {}, None)
        finally:
            sugg.suggest = original
        self.assertEqual(len(seen), 1)



def Api_with(case, on_cellular):
    """An Api like ApiTests' but with its own reading of the route."""
    from app.server import Api

    def run(progress, limit):
        case.runs += 1
        return {"ts": int(AT), "worst": "ok", "results": [], "speed": None, "measurement": None}
    return Api(monitor=None, storage=case.db, run_speedtest=run, speedtest_store=case.store, clock=lambda: AT,
               on_cellular=on_cellular, sync_jobs=True)


if __name__ == "__main__":
    unittest.main()
