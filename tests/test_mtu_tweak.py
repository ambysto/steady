"""The mtu_pmtu tweak (ADR-0016): the path MTU measurement, the value derived from it, the manager
round trip and the measure -> enable -> measure-again flow. Everything runs on fakes."""
import unittest
from types import SimpleNamespace

from app import calibration, diagnostics, i18n, pmtu, tweaks, winsys
from app.tweaks import MtuTweak, Refused
from tests.test_tweaks import WIFI_GUID, manager as base_manager

NOW = 1000.0
NET = "0123456789abcdef"
DOCK_GUID = "{00000000-0000-4000-8000-000000000001}"


def en(message):
    return i18n.render(message, "en")


def path(interface=1500, path_mtu=1492, answered=3, too_big=False, tunnel=False, at=NOW, network=NET):
    return {"kind": "path_mtu", "interface_mtu": interface, "path_mtu": path_mtu, "answered": answered,
            "too_big": too_big, "tunnel": tunnel, "measured_at": int(at), "network": network}


def mtu_tweak():
    return MtuTweak("mtu_pmtu", "Match the MTU", "medium")


def manager(**kw):
    return base_manager(tweak_list=[mtu_tweak()], **kw)


def dock():
    return {"index": 9, "guid": DOCK_GUID, "alias": "Ethernet", "servers": [], "static": False, "static_v6": False,
            "suffix": "", "domain_joined": False, "vpn_up": False}


class MeasurementTests(unittest.TestCase):
    def test_record_keeps_the_largest_path_and_how_it_failed(self):
        R = pmtu.PathResult
        rec = calibration.path_mtu_record(1500, [R("a", 1492, 24), R("b", 1480, 12, True), R("c", None, 2)],
                                          False, NET, NOW)
        self.assertEqual(rec, path(answered=2, too_big=True))
        self.assertIsNone(calibration.path_mtu_record(1500, [R("a", None, 2)], False, NET, NOW))

    def test_check_keeps_only_known_fields(self):
        self.assertEqual(calibration.check_path_mtu({**path(), "extra": 1}, NOW), path())

    def test_check_rejects_malformed_or_old(self):
        for bad in (path(at=NOW - 601), path(path_mtu=1501), path(path_mtu=500), path(answered=0),
                    {**path(), "too_big": 1}, {**path(), "tunnel": None}, path(network="nope"),
                    {**path(), "kind": "upload"}, {**path(), "interface_mtu": True}, "x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                calibration.check_path_mtu(bad, NOW)

    def test_survives_the_command_line(self):
        self.assertEqual(calibration.decode(calibration.encode(path())), path())

    def test_verdict(self):
        self.assertEqual(calibration.path_mtu_verdict(path(), path(interface=1492, path_mtu=1492)),
                         {"mtu": 1492, "path_after": 1492, "helped": True})
        self.assertFalse(calibration.path_mtu_verdict(path(), path(interface=1492, path_mtu=1400))["helped"])
        self.assertIsNone(calibration.path_mtu_verdict(path(), None)["helped"])


class DeriveTests(unittest.TestCase):
    def test_value_is_the_path_mtu(self):
        self.assertEqual(mtu_tweak().derive(path()), 1492)
        self.assertEqual(mtu_tweak().derive(path(path_mtu=1280)), 1280)

    def test_refusals(self):
        cases = {"refused.tunnel": path(tunnel=True), "refused.jumbo": path(interface=9000),
                 "refused.not_needed": path(path_mtu=1500), "refused.pmtud_works": path(too_big=True),
                 "refused.one_target": path(answered=1), "refused.bounds": path(path_mtu=1200)}
        for key, m in cases.items():
            with self.subTest(key), self.assertRaises(Refused) as cm:
                mtu_tweak().derive(m)
            self.assertEqual(cm.exception.message["key"], f"tweak.mtu_pmtu.{key}")

    def test_an_mtu_lowered_by_hand_has_nothing_to_fix(self):
        """The dev PC: 1492 set on Wi-Fi on 2026-08-31, so the path carries the whole interface MTU."""
        with self.assertRaises(Refused):
            mtu_tweak().derive(path(interface=1492, path_mtu=1492))


class ManagerTests(unittest.TestCase):
    def test_enable_lowers_the_mtu_and_keeps_the_measurement(self):
        mgr, s, backup, events = manager()
        out = mgr.enable("mtu_pmtu", path())
        self.assertTrue(out.ok and out.changed, en(out.message))
        self.assertEqual(s.mtu[WIFI_GUID], 1492)
        self.assertEqual([w[1:] for w in s.writes()], [("ipv4_mtu_set", (6, 1492))])
        entry = backup.data["mtu_pmtu"]
        self.assertEqual(entry["original"], {"guid": WIFI_GUID, "interface_index": 6, "alias": "Wi-Fi", "mtu": 1500})
        self.assertEqual(entry["measurement"], {**path(), "value": 1492})
        st = mgr.state("mtu_pmtu")
        self.assertEqual((st.enabled, st.measured, st.current), (True, True, {"interface": "Wi-Fi", "mtu": 1492}))
        self.assertEqual(st.warning, "")
        self.assertEqual([k for k, *_ in events], ["tweak_enabled"])

    def test_disable_puts_the_saved_mtu_back(self):
        mgr, s, backup, _ = manager()
        mgr.enable("mtu_pmtu", path())
        out = mgr.disable("mtu_pmtu")
        self.assertTrue(out.ok and out.changed, en(out.message))
        self.assertNotIn(WIFI_GUID, s.mtu)                                  # 1500 again
        self.assertNotIn("mtu_pmtu", backup.data)
        self.assertFalse(mgr.state("mtu_pmtu").enabled)

    def test_an_mtu_set_by_hand_reads_off_and_is_never_reset(self):
        mgr, s, _, _ = manager()
        s.mtu[WIFI_GUID] = 1492
        st = mgr.state("mtu_pmtu")
        self.assertEqual((st.supported, st.enabled, st.current["mtu"]), (True, False, 1492))
        out = mgr.disable("mtu_pmtu")
        self.assertTrue(out.ok)
        self.assertFalse(out.changed)
        self.assertEqual(s.writes(), [])

    def test_refused_measurements_write_nothing(self):
        mgr, s, backup, _ = manager()
        for m in (None, path(path_mtu=1500), path(at=NOW - 3600)):
            self.assertFalse(mgr.enable("mtu_pmtu", m).ok)
        self.assertEqual((s.writes(), backup.data), ([], {}))

    def test_interface_already_at_the_value_is_rolled_back_not_raised(self):
        mgr, s, backup, _ = manager()
        s.mtu[WIFI_GUID] = 1400   # changed since the measurement
        out = mgr.enable("mtu_pmtu", path())
        self.assertFalse(out.ok)
        self.assertEqual((s.writes(), s.mtu[WIFI_GUID], backup.data), ([], 1400, {}))

    def test_backup_follows_its_interface(self):
        mgr, s, _, _ = manager()
        s.extra_interfaces.append(dock())
        mgr.enable("mtu_pmtu", path())
        s.uplink = s.extra_interfaces[0]          # the dock's Ethernet now carries the traffic
        st = mgr.state("mtu_pmtu")
        self.assertTrue(st.enabled)                # the Wi-Fi card still has the lowered MTU
        self.assertIn("Wi-Fi", en(st.warning))
        out = mgr.enable("mtu_pmtu", path())
        self.assertFalse(out.ok)                    # the backup is the Wi-Fi card's
        self.assertTrue(mgr.disable("mtu_pmtu").ok)
        self.assertNotIn(WIFI_GUID, s.mtu)
        self.assertNotIn(DOCK_GUID, s.mtu)

    def test_no_backup_no_guess(self):
        self.assertFalse(mtu_tweak().has_default_restore)
        with self.assertRaises(tweaks.NoDefaultRestore):
            mtu_tweak().restore(None, None)

    def test_offline_is_unsupported(self):
        mgr, s, _, _ = manager()
        s.uplink = None
        st = mgr.state("mtu_pmtu")
        self.assertFalse(st.supported)
        self.assertEqual(st.reason["key"], "tweak.mtu_pmtu.reason.no_uplink")


class EnableMeasuredTests(unittest.TestCase):
    def run_flow(self, measurements, mgr=None):
        mgr = mgr or manager()[0]
        queue, events = list(measurements), []
        res = tweaks.enable_measured(mgr, "mtu_pmtu", lambda: queue.pop(0) if queue else None,
                                     lambda m: mgr.enable("mtu_pmtu", m), lambda *e: events.append(e))
        return res, events, queue

    def test_helped_when_the_path_carries_the_new_mtu(self):
        res, events, queue = self.run_flow([path(), path(interface=1492, path_mtu=1492)])
        self.assertTrue(res["ok"] and res["changed"])
        self.assertEqual(queue, [])
        self.assertTrue(res["verdict"]["helped"])
        self.assertEqual(events[0][1]["key"], "tweak.mtu_pmtu.event.verified_helped")
        self.assertEqual(en(res["message"]), "On: MTU 1492 (was 1500, the largest packet the path carried). "
                                             "Packets of the new MTU (1492 bytes) now cross the path")

    def test_no_help_is_reported(self):
        res, events, _ = self.run_flow([path(), path(interface=1492, path_mtu=1400)])
        self.assertFalse(res["verdict"]["helped"])
        self.assertEqual(events[0][2], "warn")
        self.assertIn("consider turning it off", en(res["message"]))

    def test_unknown_when_it_cannot_measure_again(self):
        res, events, _ = self.run_flow([path()])
        self.assertEqual(events[0][1]["key"], "tweak.mtu_pmtu.event.verified_unknown")

    def test_working_pmtud_never_reaches_enable(self):
        res, events, _ = self.run_flow([path(too_big=True)])
        self.assertFalse(res["ok"])
        self.assertEqual(events, [])

    def test_upload_shaping_keeps_its_own_messages(self):
        self.assertEqual(tweaks.UploadShapingTweak("upload_shaping", "x", "medium").verdict_prefix, "tweak")


class WindowsSystemTests(unittest.TestCase):
    def test_mtu_is_written_with_netsh_by_index_and_kept(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=b"Ok.")
        sys_ = winsys.WindowsSystem(ps=None, ps_json=lambda *a, **k: [], run=run)
        sys_.ipv4_mtu_set(6, 1492)
        self.assertEqual(calls, [["netsh", "interface", "ipv4", "set", "subinterface", "6", "mtu=1492",
                                  "store=persistent"]])
        for bad in ((6, 575), (6, 9001), ("6 & calc", 1492)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sys_.ipv4_mtu_set(*bad)
        failing = winsys.WindowsSystem(ps=None, ps_json=None,
                                       run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"Element not found."))
        with self.assertRaises(winsys.SystemWriteError):
            failing.ipv4_mtu_set(6, 1492)

    def test_interface_is_read_from_the_default_route(self):
        scripts = []

        def ps_json(script, **kw):
            scripts.append(script)
            return [{"Index": 6, "Guid": DOCK_GUID, "Alias": "Wi-Fi", "Mtu": 1492}]
        sys_ = winsys.WindowsSystem(ps=None, ps_json=ps_json, run=None, route=lambda: {"interface_index": 6})
        self.assertEqual(sys_.ipv4_interface(), {"index": 6, "guid": DOCK_GUID, "alias": "Wi-Fi", "mtu": 1492})
        self.assertIn("-InterfaceIndex 6", scripts[0])
        self.assertIn("-AddressFamily IPv4", scripts[0])
        sys_.ipv4_interface(DOCK_GUID)
        self.assertIn(winsys.ps_literal(DOCK_GUID), scripts[1])   # never spliced in as text
        with self.assertRaises(ValueError):
            sys_.ipv4_interface("{not-a-guid}'; calc")
        self.assertIsNone(winsys.WindowsSystem(ps=None, ps_json=ps_json, run=None, route=lambda: None).ipv4_interface())


class DiagnosticLinkTests(unittest.TestCase):
    R = pmtu.PathResult
    WIFI = {"alias": "Wi-Fi", "mtu": 1500}

    def test_a_blackhole_suggests_the_tweak(self):
        r = diagnostics.evaluate_path_mtu(self.WIFI, [self.R("1.1.1.1", 1492, 24), self.R("8.8.8.8", 1492, 24)])
        self.assertEqual((r.status, r.tweak), (diagnostics.WARN, "mtu_pmtu"))

    def test_info_results_do_not_suggest_a_tweak_that_would_refuse(self):
        r = diagnostics.evaluate_path_mtu(self.WIFI, [self.R("1.1.1.1", 1492, 12, True), self.R("8.8.8.8", 1492, 24)])
        self.assertIsNone(r.tweak)

    def test_advice_drops_the_ipv6_command_below_1280(self):
        small = diagnostics.evaluate_path_mtu(self.WIFI, [self.R("1.1.1.1", 1200, 24), self.R("8.8.8.8", 1200, 24)])
        self.assertEqual(small.advice["key"], "diag.path_mtu.advice_v4")
        self.assertNotIn("ipv6", en(small.advice))
        normal = diagnostics.evaluate_path_mtu(self.WIFI, [self.R("1.1.1.1", 1492, 24), self.R("8.8.8.8", 1492, 24)])
        self.assertIn("ipv6", en(normal.advice))

    def test_no_ipv4_mentions_being_offline(self):
        self.assertIn("offline", en(diagnostics.evaluate_path_mtu(None, [], ipv4_route=False).summary))


if __name__ == "__main__":
    unittest.main()
