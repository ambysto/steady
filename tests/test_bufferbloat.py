import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import bufferbloat as bb, i18n
from tests.localized import DiagnosticsIn
from app.bufferbloat import Measurement, Phase

d = DiagnosticsIn("vi")   # evaluate_* return Vietnamese text; see tests/localized.py


def phase(name, rtts, mbps=50.0, error=""):
    return Phase(name, rtts, mbps, error)


def meas(idle_inet=20, down_inet=25, up_inet=25, idle_router=3, down_router=4, up_router=4, n=40, mbps=50.0):
    def series(v):
        return [v] * n if v is not None else [None] * n
    return Measurement(
        phase("idle", {"router": series(idle_router), "internet": series(idle_inet)}, None),
        phase("download", {"router": series(down_router), "internet": series(down_inet)}, mbps),
        phase("upload", {"router": series(up_router), "internet": series(up_inet)}, mbps))


class EvaluateTests(unittest.TestCase):
    def test_thresholds_on_the_increase(self):
        for extra, expected in [(0, d.OK), (29, d.OK), (30, d.WARN), (100, d.WARN), (101, d.BAD), (400, d.BAD)]:
            r = d.evaluate_bufferbloat(meas(idle_inet=20, down_inet=20 + extra, up_inet=20))
            self.assertEqual(r.status, expected, extra)

    def test_worst_direction_wins(self):
        r = d.evaluate_bufferbloat(meas(down_inet=22, up_inet=200))
        self.assertEqual(r.status, d.BAD)
        self.assertIn("thêm 180 ms", r.summary)

    def test_upload_bloat_points_at_the_upload_limit_tweak(self):
        self.assertEqual(d.evaluate_bufferbloat(meas(up_inet=200)).tweak, "upload_shaping")
        self.assertIsNone(d.evaluate_bufferbloat(meas(down_inet=300)).tweak)    # download: only the router can
        self.assertIsNone(d.evaluate_bufferbloat(meas()).tweak)

    def test_with_the_upload_limit_on_check_14_says_it_measures_through_it(self):
        on = {"enabled": True, "current": {"limit_mbps": 34.0, "local_exempt": True}}
        m = meas()
        m.upload.mbps = 20.0
        self.assertEqual(d.shaping_notes(m, None), [])
        self.assertEqual(d.shaping_notes(m, {"enabled": False, "current": None}), [])
        self.assertEqual(i18n.render(d.shaping_notes(m, on)[0], "en"),
                         "Upload limited by this app to 34.0 Mbps (Optimize), so this is measured through the limit")
        m.upload.mbps = 33.5                          # at the limit: the line may have become faster
        self.assertIn("turn the limit off and on again", i18n.render(d.shaping_notes(m, on)[0], "en"))

    def test_healthy_summary_and_no_advice(self):
        r = d.evaluate_bufferbloat(meas())
        self.assertEqual(r.status, d.OK)
        self.assertEqual(r.advice, "")

    def test_wan_queue_when_only_the_internet_grows(self):
        r = d.evaluate_bufferbloat(meas(down_inet=300))
        self.assertIn("chặng WAN", r.summary)
        self.assertIn("SQM", r.advice)

    def test_lan_queue_when_the_router_grows_too(self):
        r = d.evaluate_bufferbloat(meas(down_inet=300, down_router=200))
        self.assertIn("PC ↔ router", r.summary)

    def test_losing_pings_under_load_is_bad_even_if_survivors_are_fast(self):
        series = [20] * 28 + [None] * 12      # 30% lost
        m = meas()
        m.download.rtts["internet"] = series
        self.assertEqual(d.evaluate_bufferbloat(m).status, d.BAD)

    def test_loss_without_delay_is_explained_as_loss(self):
        # Seen on the dev PC: median latency unchanged but 20% of pings dropped while downloading.
        m = meas()
        m.download.rtts["internet"] = [20] * 32 + [None] * 8      # 20% lost, survivors unchanged
        r = d.evaluate_bufferbloat(m)
        self.assertEqual(r.status, d.BAD)
        self.assertIn("mất 20%", r.summary)
        self.assertNotIn("tăng thêm 0 ms", r.summary)

    def test_just_under_the_loss_threshold_is_ok(self):
        m = meas()
        m.download.rtts["internet"] = [20] * 33 + [None] * 7      # 17.5%
        self.assertEqual(d.evaluate_bufferbloat(m).status, d.OK)

    def test_all_pings_lost_under_load(self):
        r = d.evaluate_bufferbloat(meas(down_inet=None))
        self.assertEqual(r.status, d.BAD)
        self.assertIn("mất ping", r.summary)

    def test_no_idle_replies_is_info_not_ok(self):
        r = d.evaluate_bufferbloat(meas(idle_inet=None))
        self.assertEqual(r.status, d.INFO)

    def test_load_that_did_not_load_the_line_is_info(self):
        r = d.evaluate_bufferbloat(meas(mbps=0.2))
        self.assertEqual(r.status, d.INFO)

    def test_one_direction_failing_still_judges_the_other(self):
        m = meas(down_inet=200)
        m.upload = phase("upload", m.upload.rtts, 0.0, "OSError: blocked")
        r = d.evaluate_bufferbloat(m)
        self.assertEqual(r.status, d.BAD)
        self.assertTrue(any("Không tạo được tải chiều tải lên" in x for x in r.details))

    def test_missing_router_target_is_fine(self):
        m = meas()
        for p in (m.idle, m.download, m.upload):
            del p.rtts["router"]
        self.assertEqual(d.evaluate_bufferbloat(m).status, d.OK)

    def test_too_few_samples_are_ignored(self):
        self.assertEqual(d.evaluate_bufferbloat(meas(n=3, down_inet=500)).status, d.INFO)

    def test_p95_shown_in_details(self):
        m = meas()
        m.download.rtts["internet"] = [25] * 30 + [90] * 10
        self.assertTrue(any("p95" in x for x in d.evaluate_bufferbloat(m).details))

    def test_loss_and_delta_are_not_confused_with_idle_losses(self):
        m = meas()
        m.idle.rtts["internet"] = [20] * 30 + [None] * 10   # lossy while idle, fine under load
        self.assertEqual(d.evaluate_bufferbloat(m).status, d.OK)


class OnDemandTests(unittest.TestCase):
    def test_not_in_the_default_run_and_only_when_named(self):
        ctx = d.Context(now=0, loaders={"bufferbloat": lambda: self.fail("must not run by default")})
        keys = [r.key for r in d.run_all(ctx, only={2}).results]   # unrelated selection
        self.assertNotIn("bufferbloat", keys)
        self.assertNotIn(14, [c[0] for c in d.CHECKS])
        self.assertEqual([r.id for r in d.run_all(d.Context(now=0, loaders={"bufferbloat": lambda: meas()}),
                                                  only={14}).results], [14])

    def test_failure_to_measure_is_reported_not_raised(self):
        def boom():
            raise OSError("no network")
        r = d.run_all(d.Context(now=0, loaders={"bufferbloat": boom}), only={14}).results[0]
        self.assertEqual((r.status, r.id), (d.INFO, 14))
        self.assertIn("no network", r.error)

    def test_cli_flag_runs_only_this_check_and_never_saves(self):
        import contextlib
        import io
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["STABLEINTERNET_DATA"] = tmp
            try:
                orig = d.Context._load_bufferbloat
                d.Context._load_bufferbloat = lambda self: meas(down_inet=300)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = d.main(["--bufferbloat", "--json"])
                from app.storage import Storage
                with Storage(os.path.join(tmp, "metrics.db")) as db:
                    saved = db.list_diagnostic_runs()
            finally:
                d.Context._load_bufferbloat = orig
                os.environ.pop("STABLEINTERNET_DATA", None)
        payload = json.loads(out.getvalue())
        self.assertEqual((code, [r["id"] for r in payload["results"]], payload["run_id"], saved), (0, [14], None, []))


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass   # clients hang up mid-transfer on purpose (that is how the load stops)


class _LoadHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/bad"):
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "50000000")
        self.end_headers()
        block = bytes(65536)
        try:
            for _ in range(50_000_000 // 65536):
                self.wfile.write(block)
        except OSError:
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        got = 0
        try:
            while got < n:
                chunk = self.rfile.read(min(65536, n - got))
                if not chunk:
                    break
                got += len(chunk)
        except OSError:
            pass
        try:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except OSError:
            pass


class LoadGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = _QuietServer(("127.0.0.1", 0), _LoadHandler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_download_stops_at_the_deadline_and_counts_bytes(self):
        started = time.monotonic()
        got = bb.http_download(self.base + "/down", 1.0, threading.Event(), connections=2)
        self.assertLess(time.monotonic() - started, 6)
        self.assertGreater(got, 1_000_000)

    def test_download_respects_the_byte_cap(self):
        got = bb.http_download(self.base + "/down", 5.0, threading.Event(), connections=2, max_bytes=3_000_000)
        self.assertLess(got, 3_000_000 + 4 * 65536 * 2)

    def test_download_stop_event(self):
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        started = time.monotonic()
        bb.http_download(self.base + "/down", 20.0, stop, connections=2)
        self.assertLess(time.monotonic() - started, 6)

    def test_download_error_status_raises(self):
        with self.assertRaises(OSError):
            bb.http_download(self.base + "/bad", 1.0, threading.Event(), connections=1)

    def test_upload_counts_bytes_and_stops(self):
        started = time.monotonic()
        sent = bb.http_upload(self.base + "/up", 1.0, threading.Event(), connections=2)
        self.assertLess(time.monotonic() - started, 6)
        self.assertGreater(sent, 500_000)

    def test_connection_refused_raises(self):
        with self.assertRaises(OSError):
            bb.http_download("http://127.0.0.1:1/x", 1.0, threading.Event(), connections=1)


class MeasureTests(unittest.TestCase):
    def test_orchestration_with_fakes(self):
        # Fake clock: each sleep advances time, so 4 s idle + 2 x 10 s loaded runs instantly.
        t = [0.0]
        clock = lambda: t[0]                                   # noqa: E731
        sleep = lambda s: t.__setitem__(0, t[0] + s)           # noqa: E731
        loaded = {"on": False}

        def ping(addr):
            t[0] += 0.01
            return (200.0 if loaded["on"] else 20.0) if addr == "1.1.1.1" else 4.0

        def load(seconds, stop):
            loaded["on"] = True
            return 25_000_000

        def down(s, stop):
            loaded["on"] = True
            try:
                return load(s, stop)
            finally:
                pass

        m = bb.measure(ping, {"router": "192.168.3.1", "internet": "1.1.1.1"}, down, load,
                       idle_s=4, load_s=10, interval=0.2, sleep=sleep, clock=clock)
        self.assertEqual(len(m.idle.rtts["internet"]), 20)
        self.assertGreaterEqual(len(m.download.rtts["internet"]), 40)
        self.assertEqual(m.download.rtts["router"][0], 4.0)
        self.assertGreater(m.download.mbps, 0)

    def test_load_failure_is_recorded_on_the_phase(self):
        t = [0.0]

        def boom(seconds, stop):
            raise OSError("blocked")
        m = bb.measure(lambda a: 5.0, {"internet": "1.1.1.1"}, boom, boom, idle_s=1, load_s=3, interval=0.5,
                       sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
        self.assertIn("blocked", m.download.error)
        self.assertEqual(d.evaluate_bufferbloat(m).status, d.INFO)

    def test_ramp_samples_are_dropped(self):
        t = [0.0]
        calls = []

        def ping(addr):
            calls.append(t[0])
            # The download phase starts at t=4 (after 4 s idle); its first 2 s are the slow "ramp".
            return 500.0 if 4.0 <= t[0] < 5.95 else 30.0

        m = bb.measure(ping, {"internet": "1.1.1.1"}, lambda s, e: 10_000_000, lambda s, e: 10_000_000,
                       idle_s=4, load_s=10, interval=0.2, ramp_s=2.0,
                       sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
        self.assertTrue(all(v == 30.0 for v in m.download.rtts["internet"] if v is not None))

    def test_a_failing_ping_is_a_lost_sample(self):
        t = [0.0]

        def ping(addr):
            raise OSError("handle gone")
        m = bb.measure(ping, {"internet": "1.1.1.1"}, lambda s, e: 1, lambda s, e: 1, idle_s=1, load_s=3,
                       interval=0.5, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
        self.assertTrue(all(v is None for v in m.idle.rtts["internet"]))


class ServerRouteTests(unittest.TestCase):
    def test_endpoint_runs_a_job_and_requires_post(self):
        from app.server import Api, ApiError
        from app.storage import Storage
        with Storage() as db:
            api = Api(monitor=None, storage=db, run_bufferbloat=lambda: {"worst": "ok", "results": []},
                      sync_jobs=True)
            status, job = api.handle("POST", "/api/diagnostics/bufferbloat", {}, {})
            self.assertEqual((status, job["status"], job["result"]["worst"]), (200, "done", "ok"))
            with self.assertRaises(ApiError) as cm:
                api.handle("GET", "/api/diagnostics/bufferbloat", {}, None)
            self.assertEqual(cm.exception.status, 405)


if __name__ == "__main__":
    unittest.main()
