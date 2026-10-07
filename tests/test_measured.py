"""Measured tweaks, calibrations, the path MTU probe and check #15 (ADR-0015)."""
import unittest

from app import calibration, diagnostics as d, i18n, pathmtu, winsys
from app.bufferbloat import Measurement, Phase
from app.tweaks import PathMtuTweak, TweakManager, UploadLimitTweak
from tests.test_tweaks import FakeSystem, MemoryBackup
from tests.test_winsys import FakePs

NOW = 1_791_331_200
NET = "6|192.0.2.1|HomeNet"
DAY = 86400


def entry(value, network=NET, age_days=1):
    return {"value": value, "measured_at": NOW - age_days * DAY, "network": network, "detail": {}}


def setup(cal, **sys_attrs):
    s = FakeSystem()
    for k, v in sys_attrs.items():
        setattr(s, k, v)
    backup = MemoryBackup()
    kw = dict(load_calibration=lambda: cal, clock=lambda: NOW)
    tw = [UploadLimitTweak("upload_shaping", "Upload limit", "medium", **kw),
          PathMtuTweak("mtu_path", "MTU", "medium", **kw)]
    mgr = TweakManager(s, tw, load_backup=backup.load, save_backup=backup.save, is_admin=lambda: True,
                       clock=lambda: NOW)
    return mgr, s


class CalibrationStoreTests(unittest.TestCase):
    def store(self):
        data = {}
        return data, dict(load=lambda: dict(data), save=data.update)

    def test_records_with_network_and_time(self):
        data, io = self.store()
        self.assertTrue(calibration.record("upload_mbps", 41.8, NET, NOW, detail={"x": 1}, **io))
        self.assertEqual(data["upload_mbps"], {"value": 41.8, "measured_at": NOW, "network": NET, "detail": {"x": 1}})

    def test_measurement_with_the_tweak_on_is_not_recorded(self):
        # With the limit active the test measures the limit: recording it would ratchet the value down.
        data, io = self.store()
        self.assertFalse(calibration.record("upload_mbps", 30.0, NET, NOW, tweak_active=True, **io))
        self.assertFalse(calibration.record("upload_mbps", 30.0, None, NOW, **io))   # no network to tie it to
        self.assertEqual(data, {})

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            calibration.record("download_mbps", 1.0, NET, NOW, load=dict, save=lambda _: None)

    def test_usable_needs_same_network_and_recent(self):
        self.assertTrue(calibration.usable(entry(40), NET, NOW))
        self.assertFalse(calibration.usable(entry(40, network="7|198.51.100.1|Cafe"), NET, NOW))
        self.assertFalse(calibration.usable(entry(40, age_days=31), NET, NOW))
        self.assertFalse(calibration.usable(None, NET, NOW))

    def test_malformed_entry_reads_as_none(self):
        self.assertIsNone(calibration.get("upload_mbps", lambda: {"upload_mbps": {"value": "fast"}}))
        self.assertIsNone(calibration.get("upload_mbps", lambda: {"upload_mbps": []}))


class UploadLimitTests(unittest.TestCase):
    def test_not_applied_without_a_measurement(self):
        mgr, s = setup({})
        st = mgr.state("upload_shaping")
        self.assertFalse(st.supported)
        self.assertIn("bufferbloat", i18n.render(st.reason, "en"))
        self.assertFalse(mgr.enable("upload_shaping").ok)
        self.assertEqual(s.writes(), [])

    def test_measured_on_another_network_or_too_old_is_refused(self):
        for cal in ({"upload_mbps": entry(40, network="7|198.51.100.1|Cafe")}, {"upload_mbps": entry(40, age_days=40)}):
            mgr, s = setup(cal)
            self.assertFalse(mgr.enable("upload_shaping").ok)
            self.assertEqual(s.writes(), [])

    def test_limit_is_85_percent_of_the_measured_upload_and_restores_by_removal(self):
        mgr, s = setup({"upload_mbps": entry(40.0)})
        st = mgr.state("upload_shaping")
        self.assertTrue(st.supported)
        self.assertIn("34.0", i18n.render(st.reason, "en"))           # the value shows its source
        out = mgr.enable("upload_shaping")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s.qos, {"AmbystoSteadyUpload": 34_000_000})
        self.assertTrue(mgr.disable("upload_shaping").ok)
        self.assertEqual(s.qos, {})

    def test_values_are_clamped_whatever_the_file_says(self):
        for mbps, rate in ((0.5, 2_000_000), (5000.0, 1_000_000_000)):
            mgr, s = setup({"upload_mbps": entry(mbps)})
            self.assertTrue(mgr.enable("upload_shaping").ok)
            self.assertEqual(s.qos["AmbystoSteadyUpload"], rate)

    def test_without_backup_disable_only_removes_the_apps_own_policy(self):
        mgr, s = setup({}, qos={"AmbystoSteadyUpload": 15_000_000, "SomeoneElse": 1_000_000})
        self.assertTrue(mgr.state("upload_shaping").enabled)
        self.assertTrue(mgr.disable("upload_shaping").ok)
        self.assertEqual(s.qos, {"SomeoneElse": 1_000_000})

    def test_drift_is_reported_not_corrected(self):
        mgr, s = setup({"upload_mbps": entry(100.0)}, qos={"AmbystoSteadyUpload": 15_000_000})
        st = mgr.state("upload_shaping")
        self.assertTrue(st.enabled)
        self.assertEqual(st.current, 15.0)
        self.assertIn("changed", i18n.render(st.reason, "en"))
        self.assertEqual(s.writes(), [])


class PathMtuTweakTests(unittest.TestCase):
    def test_lowers_the_mtu_and_restores_it(self):
        mgr, s = setup({"path_mtu": entry(1492)})
        self.assertFalse(mgr.state("mtu_path").enabled)
        out = mgr.enable("mtu_path")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s.mtu[6], 1492)
        self.assertTrue(mgr.disable("mtu_path").ok)
        self.assertEqual(s.mtu[6], 1500)

    def test_a_line_that_takes_full_packets_needs_nothing(self):
        mgr, s = setup({"path_mtu": entry(1500)})
        self.assertTrue(mgr.state("mtu_path").enabled)
        self.assertFalse(mgr.enable("mtu_path").changed)
        self.assertEqual(s.writes(), [])

    def test_clamped_and_never_raised(self):
        mgr, s = setup({"path_mtu": entry(600)})
        self.assertTrue(mgr.enable("mtu_path").ok)
        self.assertEqual(s.mtu[6], 1280)
        mgr, s = setup({"path_mtu": entry(9000)}, mtu={6: 1400})
        self.assertTrue(mgr.state("mtu_path").enabled)                # 1400 already fits a 1500 target
        mgr.enable("mtu_path")
        self.assertEqual(s.mtu[6], 1400)

    def test_no_safe_default_without_backup(self):
        mgr, s = setup({}, mtu={6: 1492})
        st = mgr.state("mtu_path")
        self.assertTrue(st.enabled)                                   # lowered by hand
        self.assertFalse(mgr.disable("mtu_path").ok)
        self.assertEqual(s.writes(), [])

    def test_no_measurement_and_full_mtu_is_unsupported(self):
        mgr, s = setup({})
        self.assertFalse(mgr.state("mtu_path").supported)
        self.assertFalse(mgr.enable("mtu_path").ok)
        self.assertEqual(s.writes(), [])


class PathMtuProbeTests(unittest.TestCase):
    def line(self, limit, blocked=()):
        calls = []

        def ping(target, payload):
            calls.append((target, payload))
            return target not in blocked and payload + 28 <= limit
        return ping, calls

    def test_finds_the_limit(self):
        for limit in (1500, 1492, 1493, 1460, 1400, 1280):
            ping, _ = self.line(limit)
            self.assertEqual(pathmtu.measure(ping).mtu, limit, limit)

    def test_pppoe_and_full_lines_take_few_probes(self):
        for limit, most in ((1500, 2), (1492, 4)):
            ping, _ = self.line(limit)
            self.assertLessEqual(pathmtu.measure(ping, ("1.1.1.1",)).probes, most)

    def test_nothing_gets_through(self):
        ping, _ = self.line(1279)
        self.assertIsNone(pathmtu.measure(ping).mtu)

    def test_one_target_filtering_big_echoes_does_not_drag_the_result_down(self):
        def ping(target, payload):
            return payload + 28 <= (1300 if target == "1.1.1.1" else 1492)
        self.assertEqual(pathmtu.measure(ping).mtu, 1492)


class PathMtuCheckTests(unittest.TestCase):
    def result(self, mtu, current):
        return d.evaluate_path_mtu(pathmtu.PathMtu(mtu, {"1.1.1.1": mtu}), current)

    def test_statuses(self):
        r = self.result(1492, 1500)
        self.assertEqual((r.status, r.tweak), (d.WARN, "mtu_path"))
        self.assertIn("1492", i18n.render(r.summary, "en"))
        self.assertEqual(self.result(1500, 1500).status, d.OK)
        self.assertEqual(self.result(1492, 1492).status, d.OK)
        self.assertEqual(self.result(None, 1500).status, d.INFO)
        self.assertEqual(self.result(1492, None).status, d.INFO)

    def test_only_an_uncapped_probe_calibrates(self):
        cal = d.path_mtu_calibrates
        self.assertTrue(cal(pathmtu.PathMtu(1492), 1500))
        self.assertTrue(cal(pathmtu.PathMtu(1500), 1500))
        self.assertTrue(cal(pathmtu.PathMtu(1460), 1492))      # below a lowered MTU: still a real limit
        self.assertFalse(cal(pathmtu.PathMtu(1492), 1492))     # capped by the interface: says nothing
        self.assertFalse(cal(pathmtu.PathMtu(None), 1500))

    def test_check_records_through_the_context(self):
        recorded = []
        ctx = d.Context(now=NOW, loaders={"path_mtu": lambda: pathmtu.PathMtu(1492, {"1.1.1.1": 1492}),
                                          "interface_mtu": lambda: 1500,
                                          "calibrate": lambda: (lambda *a: recorded.append(a))})
        self.assertEqual(d.check_path_mtu(ctx).status, d.WARN)
        self.assertEqual([(k, v) for k, v, *_ in recorded], [("path_mtu", 1492)])

    def test_a_failing_store_does_not_change_the_result(self):
        def boom(*a):
            raise OSError("disk full")
        ctx = d.Context(now=NOW, loaders={"path_mtu": lambda: pathmtu.PathMtu(1492), "interface_mtu": lambda: 1500,
                                          "calibrate": lambda: boom})
        self.assertEqual(d.check_path_mtu(ctx).status, d.WARN)

    def test_bare_context_never_writes_a_calibration(self):
        self.assertFalse(d.Context(now=NOW)._calibrate("path_mtu", 1492, {}, False))


def bloat(up_rtt, up_mbps=30.0, down_rtt=20.0):
    idle = Phase("idle", {"internet": [20.0] * 20})
    return Measurement(idle, Phase("download", {"internet": [down_rtt] * 20}, mbps=90.0),
                       Phase("upload", {"internet": [up_rtt] * 20}, mbps=up_mbps))


class BufferbloatCalibrationTests(unittest.TestCase):
    def run_check(self, m, active=False):
        recorded = []
        ctx = d.Context(now=NOW, loaders={"bufferbloat": lambda: m, "upload_limit_active": lambda: active,
                                          "calibrate": lambda: (lambda *a: recorded.append(a))})
        return d.check_bufferbloat(ctx), recorded

    def test_upload_rate_is_recorded_and_flags_the_limit_as_active(self):
        _, rec = self.run_check(bloat(300.0), active=True)
        self.assertEqual([(k, v, act) for k, v, _, act in rec], [("upload_mbps", 30.0, True)])

    def test_upload_bloat_links_the_upload_limit(self):
        r, _ = self.run_check(bloat(300.0))
        self.assertEqual((r.status, r.tweak), (d.BAD, "upload_shaping"))

    def test_download_only_bloat_does_not_suggest_the_upload_limit(self):
        r, _ = self.run_check(bloat(25.0, down_rtt=300.0))
        self.assertEqual(r.status, d.BAD)
        self.assertIsNone(r.tweak)

    def test_no_real_upload_records_nothing(self):
        _, rec = self.run_check(bloat(25.0, up_mbps=0.2))
        self.assertEqual(rec, [])


class WinsysScriptTests(unittest.TestCase):
    def test_qos_and_mtu_scripts(self):
        ps = FakePs()
        s = winsys.WindowsSystem(ps=ps, ps_json=lambda *a, **k: [], run=None)
        s.qos_throttle_set("AmbystoSteadyUpload", 34_000_000)
        s.qos_policy_remove("AmbystoSteadyUpload")
        s.interface_mtu_set(6, 1492)
        self.assertIn("-IPProtocolMatchCondition Both -ThrottleRateActionBitsPerSecond 34000000", ps.scripts[0])
        self.assertIn("Remove-NetQosPolicy -Confirm:$false", ps.scripts[1])
        self.assertIn("-InterfaceIndex 6 -AddressFamily IPv4 -NlMtuBytes 1492", ps.scripts[2])

    def test_bounds(self):
        s = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=None)
        for rate in (1_000, 2_000_000_000):
            with self.assertRaises(ValueError):
                s.qos_throttle_set("AmbystoSteadyUpload", rate)
        for mtu in (100, 65535):
            with self.assertRaises(ValueError):
                s.interface_mtu_set(6, mtu)
        for name in ("a b", "x;rm", "$(x)"):
            with self.assertRaises(ValueError):
                s.qos_policy_remove(name)

    def test_reads(self):
        s = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [{"ThrottleRateActionBitsPerSecond": 34000000,
                                                                         "NlMtu": 1492}], run=None)
        self.assertEqual(s.qos_throttle_get("AmbystoSteadyUpload"), 34_000_000)
        self.assertEqual(s.interface_mtu_get(6), 1492)
        self.assertIsNone(winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=None)
                          .qos_throttle_get("AmbystoSteadyUpload"))


if __name__ == "__main__":
    unittest.main()
