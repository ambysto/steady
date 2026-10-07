"""Measured tweaks (ADR-0016): the measurement, the upload limit derived from it, and the
measure -> enable -> measure-again flow. Everything runs on fakes; nothing touches the network."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from app import bufferbloat, calibration, config, elevated, elevation, i18n, tweaks
from app.bufferbloat import Measurement, Phase
from app.tweaks import Refused, UploadShapingTweak
from tests.test_tweaks import FakeSystem, MemoryBackup, manager as base_manager

NOW = 1000.0
NET = "0123456789abcdef"


def en(message):
    return i18n.render(message, "en")


def upload(mbps=40.0, idle=20.0, loaded=200.0, samples=40, at=NOW, network=NET, loss=0.0):
    return {"kind": "upload", "upload_mbps": mbps, "idle_ms": idle, "loaded_ms": loaded, "samples": samples,
            "loss_pct": loss, "measured_at": int(at), "network": network}


def shaping():
    return UploadShapingTweak("upload_shaping", "Limit upload", "medium")


def manager(**kw):
    return base_manager(tweak_list=[shaping()], **kw)


class NetworkIdTests(unittest.TestCase):
    def test_names_the_router_without_keeping_its_addresses(self):
        a = calibration.network_id("192.168.1.1", "02:5E:00:9A:40:24")
        self.assertRegex(a, r"^[0-9a-f]{16}$")
        self.assertNotEqual(a, calibration.network_id("192.168.1.2", "02:5e:00:9a:40:24"))   # another address
        self.assertEqual(a, calibration.network_id("192.168.1.1", "02:5e:00:9a:40:24"))   # case-insensitive
        self.assertNotEqual(a, calibration.network_id("192.168.1.1", "02:5e:00:9a:40:25"))  # another router
        self.assertIsNone(calibration.network_id(None, None))

    def test_current_network_reads_route_then_neighbour(self):
        seen = []
        got = calibration.current_network_id(route=lambda: {"gateway": "192.168.3.1"},
                                             physical=lambda ip: seen.append(ip) or "02:5e:00:9a:40:24")
        self.assertEqual(seen, ["192.168.3.1"])
        self.assertEqual(got, calibration.network_id("192.168.3.1", "02:5e:00:9a:40:24"))
        self.assertIsNone(calibration.current_network_id(route=lambda: None, physical=lambda ip: self.fail()))


class CheckTests(unittest.TestCase):
    def test_valid_measurement_keeps_only_known_fields(self):
        m = {**upload(), "value": 999_000_000, "extra": "x"}
        out = calibration.check_upload(m, NOW)
        self.assertNotIn("value", out)
        self.assertNotIn("extra", out)
        self.assertEqual(out["upload_mbps"], 40.0)

    def test_rejects_malformed_or_old(self):
        bad = [None, [], {"kind": "download"}, upload(mbps=-1), upload(mbps=True), upload(idle="20"),
               upload(loaded=float("nan")), upload(samples=3.5), upload(samples=True), upload(loss=101),
               upload(at=NOW - calibration.MAX_AGE_S - 1), upload(at=NOW + 3600), upload(network="ZZ"),
               {**upload(), "measured_at": None}]
        for m in bad:
            with self.assertRaises(ValueError, msg=repr(m)):
                calibration.check_upload(m, NOW)

    def test_encode_round_trip_has_no_quotes_or_spaces(self):
        text = calibration.encode(upload())
        self.assertRegex(text, r"^[A-Za-z0-9_=-]+$")
        self.assertEqual(calibration.decode(text), upload())
        for garbage in ("not base64!", "e30", "x" * 5000):   # e30 = "{}" without padding
            with self.assertRaises(ValueError):
                calibration.decode(garbage)

    def test_staleness(self):
        self.assertEqual(calibration.staleness(None, NET, NOW), [])
        self.assertEqual(calibration.staleness(upload(), NET, NOW), [])
        self.assertEqual(calibration.staleness(upload(), "fedcba9876543210", NOW), ["other_network"])
        self.assertEqual(calibration.staleness(upload(), None, NOW), [])          # unknown network: no claim
        old = NOW + calibration.STALE_AFTER_S + 1
        self.assertEqual(calibration.staleness(upload(), "fedcba9876543210", old), ["other_network", "old"])

    def test_verdict(self):
        self.assertTrue(calibration.verdict(upload(loaded=200), upload(loaded=60))["helped"])     # 180 -> 40
        self.assertFalse(calibration.verdict(upload(loaded=200), upload(loaded=170))["helped"])   # -30 ms, 17%
        self.assertFalse(calibration.verdict(upload(loaded=60), upload(loaded=45))["helped"])     # 40 -> 25: < 20 ms
        self.assertEqual(calibration.verdict(upload(), None), {"before_ms": 180.0, "after_ms": None, "helped": None})


class DeriveTests(unittest.TestCase):
    def test_cap_is_85_percent_rounded_down(self):
        self.assertEqual(shaping().derive(upload(mbps=40.0)), 34_000_000)
        self.assertEqual(shaping().derive(upload(mbps=17.77)), 15_100_000)   # 15.1045 -> 15.1

    def test_refusals(self):
        cases = {"weak": upload(mbps=0.5), "few samples": upload(samples=5),
                 "nothing to fix": upload(loaded=45.0), "below floor": upload(mbps=1.1),
                 "above ceiling": upload(mbps=2000.0)}
        for name, m in cases.items():
            with self.assertRaises(Refused, msg=name):
                shaping().derive(m)
        with self.assertRaises(Refused) as ctx:
            shaping().derive(upload(loaded=45.0))
        self.assertIn("rises only 25 ms", en(ctx.exception.message))


class ManagerTests(unittest.TestCase):
    def test_enable_writes_derived_cap_and_keeps_the_measurement(self):
        mgr, s, backup, events = manager()
        out = mgr.enable("upload_shaping", upload())
        self.assertTrue(out.ok and out.changed, en(out.message))
        self.assertEqual(s.qos["StableInternet-Upload"], 34_000_000)
        self.assertEqual(s.qos["Backup-Agent"], 5_000_000)                     # someone else's policy untouched
        entry = backup.data["upload_shaping"]
        self.assertEqual(entry["original"], {"policy": "StableInternet-Upload", "rate_bps": None})
        self.assertEqual(entry["measurement"], {**upload(), "value": 34_000_000})
        st = mgr.state("upload_shaping")
        self.assertEqual((st.enabled, st.measured, st.current), (True, True, {"limit_mbps": 34.0}))
        self.assertEqual(st.measurement["value"], 34_000_000)
        self.assertEqual([k for k, *_ in events], ["tweak_enabled"])

    def test_backup_is_written_before_the_policy(self):
        mgr, s, backup, _ = manager()
        saved_before = []
        s.qos_policy_set = (lambda inner: lambda name, bps: (saved_before.append("upload_shaping" in backup.data),
                                                               inner(name, bps)))(s.qos_policy_set)
        mgr.enable("upload_shaping", upload())
        self.assertEqual(saved_before, [True])

    def test_without_measurement_or_with_a_refused_one_nothing_is_written(self):
        for m in (None, upload(loaded=30.0), upload(at=NOW - 3600), {"kind": "upload"}):
            mgr, s, backup, events = manager()
            out = mgr.enable("upload_shaping", m)
            self.assertFalse(out.ok)
            self.assertEqual((s.writes(), backup.data, events), ([], {}, []))

    def test_refusal_comes_before_the_admin_check(self):
        mgr, s, _, _ = manager(admin=False)
        self.assertIn("nothing to fix", en(mgr.enable("upload_shaping", upload(loaded=30.0)).message))
        self.assertIn("Administrator", en(mgr.enable("upload_shaping", upload()).message))
        self.assertEqual(s.writes(), [])

    def test_rounding_by_windows_is_accepted(self):
        mgr, s, _, _ = manager()
        s.qos_rounding = 4_000
        self.assertTrue(mgr.enable("upload_shaping", upload()).ok)

    def test_a_policy_that_does_not_stick_is_rolled_back(self):
        mgr, s, backup, events = manager()
        s.qos_rounding = 3_000_000              # read back far from the cap
        out = mgr.enable("upload_shaping", upload())
        self.assertFalse(out.ok)
        self.assertNotIn("StableInternet-Upload", s.qos)
        self.assertNotIn("upload_shaping", backup.data)
        self.assertEqual(events[-1][0], "tweak_failed")

    def test_disable_removes_exactly_our_policy(self):
        mgr, s, backup, _ = manager()
        before = s.snapshot()
        mgr.enable("upload_shaping", upload())
        out = mgr.disable("upload_shaping")
        self.assertTrue(out.ok, en(out.message))
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(backup.data, {})
        self.assertIsNone(mgr.state("upload_shaping").measurement)

    def test_disable_without_backup_is_safe_because_the_name_is_ours(self):
        mgr, s, _, _ = manager()
        s.qos["StableInternet-Upload"] = 15_000_000                            # the hand-made 2026-08-31 policy
        self.assertTrue(mgr.disable("upload_shaping").ok)
        self.assertEqual(s.qos, {"Backup-Agent": 5_000_000})

    def test_already_on_is_a_noop(self):
        mgr, s, _, _ = manager()
        s.qos["StableInternet-Upload"] = 15_000_000
        out = mgr.enable("upload_shaping", upload())
        self.assertTrue(out.ok and not out.changed)
        self.assertEqual(s.writes(), [])

    def test_plain_apply_refuses(self):
        with self.assertRaises(Refused):
            shaping().apply(FakeSystem())

    def test_plan_mentions_the_measurement(self):
        mgr, s, _, _ = manager()
        self.assertIn("measure the connection first", mgr.plan("upload_shaping", True, "en"))
        self.assertEqual(s.writes(), [])


class EnableMeasuredTests(unittest.TestCase):
    def run_flow(self, measurements, enable=None, mgr=None):
        mgr = mgr or manager()[0]
        queue, events, enabled = list(measurements), [], []

        def default_enable(m):
            enabled.append(m)
            return mgr.enable("upload_shaping", m)
        res = tweaks.enable_measured(mgr, "upload_shaping", lambda: queue.pop(0) if queue else None,
                                     enable or default_enable, lambda *e: events.append(e))
        return res, events, enabled, queue

    def test_measures_enables_measures_again_and_records_the_verdict(self):
        res, events, enabled, queue = self.run_flow([upload(), upload(loaded=50.0)])
        self.assertTrue(res["ok"] and res["changed"])
        self.assertEqual(len(enabled), 1)
        self.assertEqual(queue, [])                       # both measurements used
        self.assertEqual(res["verdict"], {"before_ms": 180.0, "after_ms": 30.0, "helped": True})
        self.assertEqual(events[0][0], "tweak_verified")
        self.assertEqual(events[0][2], "info")
        self.assertIn("34.0 Mbps", en(res["message"]))
        self.assertIn("from 180 to 30 ms", en(res["message"]))

    def test_no_improvement_is_reported_not_undone(self):
        res, events, _, _ = self.run_flow([upload(), upload(loaded=190.0)])
        self.assertTrue(res["ok"])
        self.assertFalse(res["verdict"]["helped"])
        self.assertEqual(events[0][2], "warn")
        self.assertIn("consider turning it off", en(res["message"]))

    def test_nothing_to_fix_never_reaches_enable(self):
        res, events, enabled, _ = self.run_flow([upload(loaded=35.0)],
                                                enable=lambda m: self.fail("enable must not run"))
        self.assertFalse(res["ok"])
        self.assertEqual((events, enabled), ([], []))

    def test_failed_measurement(self):
        res, _, _, _ = self.run_flow([], enable=lambda m: self.fail("enable must not run"))
        self.assertIn("Could not measure", en(res["message"]))

    def test_cancelled_uac_is_passed_on_without_measuring_again(self):
        res, events, _, queue = self.run_flow(
            [upload(), upload()], enable=lambda m: SimpleNamespace(ok=False, message="cancelled", cancelled=True))
        self.assertTrue(res["cancelled"])
        self.assertEqual((events, len(queue)), ([], 1))

    def test_already_on_does_not_measure(self):
        mgr, s, _, _ = manager()
        s.qos["StableInternet-Upload"] = 15_000_000
        res, _, _, queue = self.run_flow([upload()], mgr=mgr)
        self.assertEqual((res["ok"], res["changed"], len(queue)), (True, False, 1))

    def test_second_measurement_failing_is_unknown(self):
        res, events, _, _ = self.run_flow([upload()])
        self.assertIsNone(res["verdict"]["helped"])
        self.assertEqual(events[0][1]["key"], "tweak.event.verified_unknown")


class UploadSummaryTests(unittest.TestCase):
    def test_summary_and_skipped_download(self):
        m = Measurement(Phase("idle", {"internet": [20.0, 22.0, None]}),
                        Phase("download", error="skipped"),
                        Phase("upload", {"internet": [200.0, None, 210.0, 190.0]}, mbps=41.234))
        self.assertEqual(bufferbloat.upload_summary(m), {"upload_mbps": 41.23, "idle_ms": 21.0, "loaded_ms": 200.0,
                                                         "samples": 3, "loss_pct": 25.0})
        m.upload.rtts["internet"] = [None, None]
        self.assertIsNone(bufferbloat.upload_summary(m))
        m.upload = Phase("upload", {}, None, "OSError: refused")
        self.assertIsNone(bufferbloat.upload_summary(m))

    def test_measure_without_download_never_starts_it(self):
        clock = [0.0]

        def sleep(s):
            clock[0] += max(s, 0.01)
        m = bufferbloat.measure(lambda addr: 20.0, {"internet": "1.1.1.1"}, None, lambda s, stop: 1_000_000,
                                idle_s=0.2, load_s=0.2, interval=0.1, ramp_s=0, sleep=sleep, clock=lambda: clock[0])
        self.assertEqual(m.download.error, "skipped")
        self.assertIsNotNone(m.upload.mbps)


class ElevatedMeasurementTests(unittest.TestCase):
    def test_measurement_goes_on_the_command_line_encoded(self):
        seen = {}

        def launcher(file, params, directory, verb, timeout):
            seen["params"] = params
            return elevation.Launch(False, error=1223)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["STABLEINTERNET_USERDIR"] = tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_USERDIR", None)
        elevation.run_elevated("tweak-enable", "upload_shaping", measurement=upload(), launcher=launcher)
        args = seen["params"].split()
        encoded = args[args.index("--measurement") + 1]
        self.assertEqual(calibration.decode(encoded), upload())

    def test_helper_refuses_a_measurement_for_other_operations(self):
        out = elevated.run_op("tweak-disable", "upload_shaping", calibration.encode(upload()))
        self.assertFalse(out["ok"])
        self.assertIn("takes no measurement", out["message"])


if __name__ == "__main__":
    unittest.main()
