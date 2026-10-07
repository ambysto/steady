"""mtu_path (ADR-0016): the path-MTU measurement, the MTU derived from it, confirming it against the machine,
backup/restore by interface GUID and the measure -> enable -> measure-again flow. Fakes only: nothing touches
the network or the machine."""
import unittest
from types import SimpleNamespace
from unittest import mock

from app import calibration, diagnostics, i18n, pmtu, tweaks, winsys
from app.tweaks import MtuPathTweak, Refused
from tests.test_tweaks import WIFI_GUID, FakeSystem, MemoryBackup, manager as base_manager

NOW = 1000.0
NET = "0123456789abcdef"
ETH_GUID = "{7C1E2A90-5B3D-4E6F-9A8B-0C1D2E3F4A5B}"


def en(message):
    return i18n.render(message, "en")


def path(interface_mtu=1500, path_mtu=1492, answered=3, targets=3, too_big=False, guid=WIFI_GUID, at=NOW,
         network=NET):
    return {"kind": "path_mtu", "guid": guid, "interface_mtu": interface_mtu, "path_mtu": path_mtu,
            "answered": answered, "targets": targets, "too_big": too_big, "measured_at": int(at), "network": network}


def mtu_tweak():
    return MtuPathTweak("mtu_path", "Lower the MTU", "medium")


def manager(**kw):
    return base_manager(tweak_list=[mtu_tweak()], **kw)


def ethernet(mtu=1500, index=9):
    return {"index": index, "guid": ETH_GUID, "alias": "Ethernet", "mtu": mtu, "vpn": False}


class CheckTests(unittest.TestCase):
    def test_a_good_measurement_is_kept_to_its_known_fields(self):
        m = {**path(), "extra": "dropped"}
        self.assertEqual(calibration.check_path_mtu(m, NOW), path())

    def test_bad_measurements_are_refused(self):
        bad = [path(guid="Wi-Fi"), path(interface_mtu=100), path(path_mtu=1600), path(path_mtu=1500, interface_mtu=1492),
               path(answered=4), path(answered=0), path(path_mtu=None), path(at=NOW - 601), path(at=NOW + 120),
               path(network="HomeNet"), {**path(), "too_big": "no"}, {**path(), "answered": True},
               {**path(), "kind": "upload"}, "not a dict"]
        for m in bad:
            with self.assertRaises(ValueError, msg=m):
                calibration.check_path_mtu(m, NOW)
        self.assertIsNone(calibration.check_path_mtu(path(path_mtu=None, answered=0), NOW)["path_mtu"])

    def test_record_from_the_probe_results(self):
        results = [pmtu.PathResult("1.1.1.1", 1492, 12), pmtu.PathResult("8.8.8.8", 1400, 12, True),
                   pmtu.PathResult("9.9.9.9", None, 2)]
        interface = {"index": 6, "guid": WIFI_GUID, "alias": "Wi-Fi", "mtu": 1500, "vpn": False}
        self.assertEqual(calibration.path_mtu_record(interface, results, NET, NOW),
                         path(answered=2, too_big=True))
        self.assertEqual(calibration.check_path_mtu(calibration.path_mtu_record(interface, results, NET, NOW), NOW)
                         ["path_mtu"], 1492)

    def test_measure_reads_the_uplink_and_probes_up_to_its_mtu(self):
        s = FakeSystem()
        seen = []
        with mock.patch.object(pmtu, "measure", lambda ceiling: seen.append(ceiling) or
                               [pmtu.PathResult("1.1.1.1", 1492, 10), pmtu.PathResult("8.8.8.8", 1492, 10)]), \
                mock.patch.object(calibration, "current_network_id", lambda: NET):
            m = calibration.measure_path_mtu(lambda: NOW, system=s)
        self.assertEqual(seen, [1500])
        self.assertEqual(m, path(answered=2, targets=2))
        s.mtu_uplink = None
        self.assertIsNone(calibration.measure_path_mtu(lambda: NOW, system=s))
        self.assertEqual(s.writes(), [])


class DeriveTests(unittest.TestCase):
    def derive(self, **kw):
        return mtu_tweak().derive(calibration.check_path_mtu(path(**kw), NOW))

    def test_pppoe_behind_wifi_gives_1492(self):
        self.assertEqual(self.derive(), 1492)

    def test_clamped_to_1280_and_never_raised(self):
        self.assertEqual(self.derive(path_mtu=900), 1280)
        self.assertEqual(self.derive(interface_mtu=1300, path_mtu=1200), 1280)
        with self.assertRaises(Refused):
            self.derive(interface_mtu=1280, path_mtu=1000)       # 1280 would not be lower
        with self.assertRaises(Refused):
            self.derive(interface_mtu=1200, path_mtu=1000)       # an MTU already below 1280 stays

    def test_nothing_to_fix(self):
        with self.assertRaises(Refused) as cm:
            self.derive(path_mtu=1500)
        self.assertIn("nothing to fix", en(cm.exception.message))
        with self.assertRaises(Refused):
            self.derive(interface_mtu=9000, path_mtu=1500)       # jumbo frames on the LAN: check #16 says ok
        self.assertEqual(self.derive(interface_mtu=9000, path_mtu=1492), 1492)

    def test_one_target_is_not_enough(self):
        with self.assertRaises(Refused) as cm:
            self.derive(answered=1)
        self.assertIn("Only 1 of 3", en(cm.exception.message))
        with self.assertRaises(Refused):
            self.derive(path_mtu=None, answered=0)

    def test_pmtud_that_works_still_allows_it(self):
        self.assertEqual(self.derive(too_big=True), 1492)   # info in check #16, but lowering is still the fix


class ManagerTests(unittest.TestCase):
    def test_enable_lowers_the_mtu_keeps_the_measurement_and_disable_restores_it(self):
        mgr, s, backup, events = manager()
        self.assertFalse(mgr.state("mtu_path").enabled)
        out = mgr.enable("mtu_path", path())
        self.assertTrue(out.ok and out.changed, en(out.message))
        self.assertEqual(s.mtus[WIFI_GUID]["mtu"], 1492)
        self.assertEqual(s.writes(), [("write", "interface_mtu_set", (6, 1492))])
        entry = backup.data["mtu_path"]
        self.assertEqual(entry["original"], {"guid": WIFI_GUID, "interface_index": 6, "alias": "Wi-Fi", "mtu": 1500})
        self.assertEqual(entry["measurement"], {**path(), "value": 1492})
        st = mgr.state("mtu_path")
        self.assertTrue(st.enabled and st.measured)
        self.assertEqual(st.measurement["value"], 1492)
        self.assertEqual(st.warning, "")
        self.assertTrue(mgr.recently_changed(60))

        out = mgr.disable("mtu_path")
        self.assertTrue(out.ok, en(out.message))
        self.assertEqual(s.mtus[WIFI_GUID]["mtu"], 1500)
        self.assertNotIn("mtu_path", backup.data)
        self.assertFalse(mgr.state("mtu_path").enabled)

    def test_measurement_from_another_mtu_or_interface_is_refused_before_any_write(self):
        mgr, s, backup, _ = manager()
        s.mtus[WIFI_GUID]["mtu"] = 1492                 # lowered since (by hand): the probe could not see past it
        out = mgr.enable("mtu_path", path())
        self.assertFalse(out.ok)
        self.assertIn("measure again", en(out.message))
        s.mtus[WIFI_GUID]["mtu"] = 1500
        s.mtus[ETH_GUID] = ethernet()
        s.mtu_uplink = ETH_GUID                          # docked: another interface carries the traffic
        out = mgr.enable("mtu_path", path())
        self.assertFalse(out.ok)
        self.assertEqual((s.writes(), backup.data), ([], {}))

    def test_derive_refuses_through_the_manager_too(self):
        mgr, s, _, _ = manager()
        with self.assertRaises(Refused):
            mgr.derive("mtu_path", path(interface_mtu=1400, path_mtu=1300))   # confirm: the uplink has 1500
        s.mtu_uplink = None
        with self.assertRaises(Refused):
            mgr.derive("mtu_path", path())
        with self.assertRaises(Refused):
            mgr.derive("mtu_path", {**path(), "guid": "nope"})

    def test_a_forged_measurement_can_only_reach_the_bounds(self):
        mgr, s, _, _ = manager()
        self.assertTrue(mgr.enable("mtu_path", path(path_mtu=576)).ok)
        self.assertEqual(s.mtus[WIFI_GUID]["mtu"], 1280)

    def test_apply_never_raises_the_mtu(self):
        s = FakeSystem()
        with self.assertRaises(Refused):
            mtu_tweak().apply_value(s, 1500)
        with self.assertRaises(Refused):
            mtu_tweak().apply_value(s, 1000)
        self.assertEqual(s.writes(), [])

    def test_a_write_that_does_nothing_is_rolled_back(self):
        mgr, s, backup, _ = manager()
        s.noop.add("interface_mtu_set")
        out = mgr.enable("mtu_path", path())
        self.assertFalse(out.ok)
        self.assertIn("rolled back", en(out.message))
        self.assertEqual((s.mtus[WIFI_GUID]["mtu"], backup.data), (1500, {}))

    def test_vpn_uplink_is_not_supported(self):
        mgr, s, _, _ = manager()
        s.mtus[WIFI_GUID]["vpn"] = True
        st = mgr.state("mtu_path")
        self.assertFalse(st.supported)
        self.assertIn("VPN", en(st.reason))
        self.assertFalse(mgr.enable("mtu_path", path()).ok)
        self.assertEqual(s.writes(), [])

    def test_offline_is_not_supported(self):
        mgr, s, _, _ = manager()
        s.mtu_uplink = None
        self.assertFalse(mgr.state("mtu_path").supported)

    def test_a_lowered_mtu_without_backup_is_not_ours(self):
        mgr, s, _, _ = manager()
        s.mtus[WIFI_GUID]["mtu"] = 1492                  # set by hand, or a PPPoE interface's own
        self.assertFalse(mgr.state("mtu_path").enabled)
        out = mgr.disable("mtu_path")
        self.assertTrue(out.ok and not out.changed)
        self.assertEqual(s.writes(), [])
        self.assertFalse(mgr.get("mtu_path").has_default_restore)
        with self.assertRaises(tweaks.NoDefaultRestore):
            mgr.get("mtu_path").restore(s, None)

    def test_backup_of_another_interface_reads_on_and_restores_that_interface(self):
        mgr, s, backup, _ = manager()
        self.assertTrue(mgr.enable("mtu_path", path()).ok)
        s.mtus[ETH_GUID] = ethernet()
        s.mtu_uplink = ETH_GUID
        st = mgr.state("mtu_path")
        self.assertTrue(st.enabled)
        self.assertIn("Wi-Fi", en(st.warning))
        self.assertEqual(st.current, {"interface": "Wi-Fi", "mtu": 1492})
        # turning it on for Ethernet must wait: the Wi-Fi original would be lost
        out = mgr.enable("mtu_path", path(guid=ETH_GUID))
        self.assertFalse(out.ok)
        self.assertEqual(s.mtus[ETH_GUID]["mtu"], 1500)
        out = mgr.disable("mtu_path")
        self.assertTrue(out.ok, en(out.message))
        self.assertEqual((s.mtus[WIFI_GUID]["mtu"], s.mtus[ETH_GUID]["mtu"]), (1500, 1500))
        self.assertNotIn("mtu_path", backup.data)

    def test_leftover_backup_of_another_interface_can_be_cleared(self):
        mgr, s, backup, _ = manager(backup=MemoryBackup({"mtu_path": {
            "original": {"guid": ETH_GUID, "interface_index": 9, "alias": "Ethernet", "mtu": 1500}}}))
        s.mtus[ETH_GUID] = ethernet()                    # not lowered any more, and not the uplink
        st = mgr.state("mtu_path")
        self.assertTrue(st.enabled)
        self.assertFalse(mgr.enable("mtu_path", path()).ok)
        self.assertTrue(mgr.disable("mtu_path").ok)
        self.assertEqual(s.writes(), [])                 # already as it was: nothing rewritten
        self.assertNotIn("mtu_path", backup.data)

    def test_already_lowered_by_us_refuses_a_second_enable(self):
        mgr, s, backup, _ = manager()
        self.assertTrue(mgr.enable("mtu_path", path()).ok)
        original = dict(backup.data["mtu_path"]["original"])
        out = mgr.enable("mtu_path", path(interface_mtu=1492, path_mtu=1400))
        self.assertFalse(out.ok)
        self.assertEqual(backup.data["mtu_path"]["original"], original)   # the 1500 original is never lost
        self.assertEqual(s.mtus[WIFI_GUID]["mtu"], 1492)

    def test_gone_interface_keeps_the_backup(self):
        mgr, s, backup, _ = manager()
        self.assertTrue(mgr.enable("mtu_path", path()).ok)
        del s.mtus[WIFI_GUID]
        s.mtu_uplink = None
        out = mgr.disable("mtu_path")
        self.assertFalse(out.ok)
        self.assertIn("mtu_path", backup.data)


class EnableMeasuredTests(unittest.TestCase):
    def run_flow(self, measurements, enable=None):
        mgr, s, _, _ = manager()
        queue, events = list(measurements), []
        res = tweaks.enable_measured(mgr, "mtu_path", lambda: queue.pop(0) if queue else None,
                                     enable or (lambda m: mgr.enable("mtu_path", m)), lambda *e: events.append(e))
        return res, events, s, queue

    def test_probe_again_at_the_new_mtu_and_record_that_it_helped(self):
        res, events, s, queue = self.run_flow([path(), path(interface_mtu=1492, path_mtu=1492)])
        self.assertTrue(res["ok"] and res["changed"])
        self.assertEqual(queue, [])
        self.assertEqual(res["verdict"], {"mtu": 1492, "path_mtu": 1492, "helped": True})
        self.assertEqual((events[0][0], events[0][1]["key"], events[0][2]),
                         ("tweak_verified", "tweak.mtu_path.event.verified_helped", "info"))
        self.assertIn("MTU 1492 bytes (was 1500; the path carries 1492)", en(res["message"]))
        self.assertIn("now get through", en(res["message"]))

    def test_no_help_is_reported_not_undone(self):
        res, events, s, _ = self.run_flow([path(), path(interface_mtu=1492, path_mtu=1400)])
        self.assertTrue(res["ok"])
        self.assertFalse(res["verdict"]["helped"])
        self.assertEqual(events[0][2], "warn")
        self.assertIn("consider turning it off", en(res["message"]))
        self.assertEqual(s.mtus[WIFI_GUID]["mtu"], 1492)

    def test_one_target_after_applying_is_not_proof(self):
        res, _, _, _ = self.run_flow([path(), path(interface_mtu=1492, path_mtu=1492, answered=1)])
        self.assertFalse(res["verdict"]["helped"])

    def test_second_probe_failing_is_unknown(self):
        res, events, _, _ = self.run_flow([path()])
        self.assertIsNone(res["verdict"]["helped"])
        self.assertEqual(events[0][1]["key"], "tweak.mtu_path.event.verified_unknown")

    def test_nothing_to_fix_never_reaches_enable(self):
        res, events, s, _ = self.run_flow([path(path_mtu=1500)], enable=lambda m: self.fail("enable must not run"))
        self.assertFalse(res["ok"])
        self.assertEqual((events, s.writes()), ([], []))

    def test_cancelled_uac(self):
        res, events, _, queue = self.run_flow(
            [path(), path()], enable=lambda m: SimpleNamespace(ok=False, message="cancelled", cancelled=True))
        self.assertTrue(res["cancelled"])
        self.assertEqual((events, len(queue)), ([], 1))

    def test_measure_for_knows_the_probe(self):
        self.assertIs(tweaks.measure_for(mtu_tweak()), calibration.measure_path_mtu)

    def test_measurement_round_trips_through_the_helper_encoding(self):
        self.assertEqual(calibration.decode(calibration.encode(path())), path())


class WindowsSystemTests(unittest.TestCase):
    def system(self, rows, route=lambda: {"interface_index": 6, "gateway": "192.168.1.1"}):
        scripts, writes = [], []

        def ps_json(script, **kw):
            scripts.append(script)
            return rows

        sys_ = winsys.WindowsSystem(ps=lambda script, **kw: writes.append(script) or "", ps_json=ps_json, run=None,
                                    route=route)
        return sys_, scripts, writes

    def test_reads_the_uplink_and_an_interface_by_guid(self):
        sys_, scripts, _ = self.system([{"Index": 6, "Guid": WIFI_GUID, "Alias": "Wi-Fi",
                                         "Description": "MediaTek Wi-Fi 6E MT7922", "Mtu": 1500}])
        self.assertEqual(sys_.mtu_interface(), {"index": 6, "guid": WIFI_GUID, "alias": "Wi-Fi", "mtu": 1500,
                                                "vpn": False})
        self.assertIn("Get-NetAdapter -InterfaceIndex 6", scripts[0])
        self.assertIn("-AddressFamily IPv4", scripts[0])
        sys_.mtu_interface(WIFI_GUID)
        self.assertNotIn(WIFI_GUID, scripts[1])            # only as base64 (ps_literal)
        with self.assertRaises(ValueError):
            sys_.mtu_interface("{x}'; calc; '")

    def test_vpn_and_missing_interfaces(self):
        sys_, _, _ = self.system([{"Index": 20, "Guid": WIFI_GUID, "Alias": "WireGuard Tunnel",
                                   "Description": "WireGuard Tunnel", "Mtu": 1420}])
        self.assertTrue(sys_.mtu_interface()["vpn"])
        self.assertIsNone(self.system([])[0].mtu_interface())
        self.assertIsNone(self.system([], route=lambda: None)[0].mtu_interface())

    def test_mtu_write_script_and_bounds(self):
        sys_, _, writes = self.system([])
        sys_.interface_mtu_set(6, 1492)
        self.assertEqual(writes, ["Set-NetIPInterface -InterfaceIndex 6 -AddressFamily IPv4 -NlMtuBytes 1492 "
                                  "-ErrorAction Stop"])
        for bad in (575, 65536):
            with self.assertRaises(ValueError):
                sys_.interface_mtu_set(6, bad)
        with self.assertRaises(ValueError):
            sys_.interface_mtu_set("6; calc", 1492)


class DiagnosticLinkTests(unittest.TestCase):
    def test_only_the_warn_result_offers_the_tweak(self):
        iface = {"alias": "Wi-Fi", "mtu": 1500}
        silent = [pmtu.PathResult("1.1.1.1", 1492, 12), pmtu.PathResult("8.8.8.8", 1492, 12)]
        warn = diagnostics.evaluate_path_mtu(iface, silent)
        self.assertEqual((warn.status, warn.tweak), (diagnostics.WARN, "mtu_path"))
        reported = [pmtu.PathResult("1.1.1.1", 1492, 12, True), pmtu.PathResult("8.8.8.8", 1492, 12)]
        self.assertIsNone(diagnostics.evaluate_path_mtu(iface, reported).tweak)
        ok = [pmtu.PathResult("1.1.1.1", 1500, 2), pmtu.PathResult("8.8.8.8", 1500, 2)]
        self.assertIsNone(diagnostics.evaluate_path_mtu(iface, ok).tweak)


if __name__ == "__main__":
    unittest.main()
