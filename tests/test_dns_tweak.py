"""The measured `dns_fastest` tweak, its calibration from check #6, the winsys primitives and the DNS watch (SIC-87)."""
import copy
import time
import unittest

from app import config, diagnostics as d, dnsprobe, i18n, tweaks, winsys
from app.i18n import msg
from app.monitor import Monitor
from app.storage import Storage
from app.tweaks import DnsTweak, TweakManager
from tests.test_tweaks import FakeSystem, MemoryBackup
from tests.test_winsys import FakePs

NOW = 1_791_331_200
NET = "6|192.0.2.1|HomeNet"
DAY = 86400
RANKING = ["1.1.1.1", "1.0.0.1", "9.9.9.9", "8.8.8.8"]     # fastest first; 1.0.0.1 shares a provider with 1.1.1.1


def entry(servers=None, network=NET, age_days=1, value=12.0):
    return {"value": value, "measured_at": NOW - age_days * DAY, "network": network,
            "detail": {"servers": RANKING if servers is None else servers, "in_use_ms": 30.0}}


def setup(cal, **sys_attrs):
    s = FakeSystem()
    for k, v in sys_attrs.items():
        setattr(s, k, v)
    backup = MemoryBackup()
    tw = [DnsTweak("dns_fastest", "Fastest DNS", "medium", load_calibration=lambda: cal, clock=lambda: NOW)]
    mgr = TweakManager(s, tw, load_backup=backup.load, save_backup=backup.save, is_admin=lambda: True,
                       clock=lambda: NOW)
    return mgr, s, backup


def on(mgr):
    return mgr.state("dns_fastest")


class TargetTests(unittest.TestCase):
    def test_two_providers_fastest_first(self):
        mgr, s, _ = setup({"dns_ranking": entry()})
        self.assertTrue(on(mgr).supported)
        out = mgr.enable("dns_fastest")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s.dns[6]["static"], ["1.1.1.1", "9.9.9.9"])           # not 1.0.0.1: same provider
        self.assertEqual(s.doh["1.1.1.1"], {"auto_upgrade": True, "fallback": True})
        self.assertEqual(s.doh["9.9.9.9"], {"auto_upgrade": True, "fallback": True})
        self.assertEqual(s.doh["8.8.8.8"], {"auto_upgrade": False, "fallback": False})   # others untouched
        self.assertTrue(on(mgr).enabled)
        self.assertEqual(on(mgr).current, ["1.1.1.1", "9.9.9.9"])
        self.assertIn("1.1.1.1", i18n.render(on(mgr).reason, "en"))             # the value shows its source

    def test_servers_outside_the_allow_list_are_ignored(self):
        cal = {"dns_ranking": entry(["6.6.6.6", "1.1.1.1", "192.168.0.53", "8.8.4.4", "evil.example", 7, "8.8.8.8"])}
        mgr, s, _ = setup(cal)
        self.assertTrue(mgr.enable("dns_fastest").ok)
        self.assertEqual(s.dns[6]["static"], ["1.1.1.1", "8.8.4.4"])

    def test_one_provider_or_nothing_listed_means_measure_first(self):
        for servers in (["1.1.1.1", "1.0.0.1"], ["6.6.6.6", "7.7.7.7"], ["1.1.1.1"], [], None):
            cal = entry(servers)
            if servers is None:
                cal["detail"] = "garbage"
            mgr, s, _ = setup({"dns_ranking": cal})
            st = on(mgr)
            self.assertFalse(st.supported, servers)
            self.assertIn("diagnostics", i18n.render(st.reason, "en"))
            self.assertFalse(mgr.enable("dns_fastest").ok)
            self.assertEqual(s.writes(), [], servers)

    def test_no_calibration_other_network_or_old_is_refused(self):
        for cal in ({}, {"dns_ranking": entry(network="7|198.51.100.1|Cafe")}, {"dns_ranking": entry(age_days=40)}):
            mgr, s, _ = setup(cal)
            self.assertFalse(on(mgr).supported)
            self.assertFalse(mgr.enable("dns_fastest").ok)
            self.assertEqual(s.writes(), [])


class RefusalTests(unittest.TestCase):
    def test_connection_specific_suffix(self):
        mgr, s, _ = setup({"dns_ranking": entry()}, dns_suffix={6: "corp.example.com"})
        st = on(mgr)
        self.assertFalse(st.supported)
        self.assertIn("suffix", i18n.render(st.reason, "en"))
        self.assertFalse(mgr.enable("dns_fastest").ok)
        self.assertEqual(s.writes(), [])

    def test_private_dns_that_is_not_the_router(self):
        for servers in (["10.0.0.53"], ["192.0.2.1", "192.168.1.53"], ["127.0.0.1"], ["100.100.100.100"]):
            mgr, s, _ = setup({"dns_ranking": entry()}, dns={6: {"static": [], "effective": servers}})
            st = on(mgr)
            self.assertFalse(st.supported, servers)
            self.assertIn("own DNS server", i18n.render(st.reason, "en"))
            self.assertEqual(s.writes(), [])

    def test_the_router_as_dns_is_the_normal_home_case(self):
        mgr, s, _ = setup({"dns_ranking": entry()}, dns={6: {"static": [], "effective": ["192.0.2.1"]}})
        self.assertTrue(on(mgr).supported)
        self.assertTrue(mgr.enable("dns_fastest").ok)

    def test_apply_rechecks_the_network(self):
        # Another process turned a private DNS on between the read and the write.
        mgr, s, _ = setup({"dns_ranking": entry()})
        tweak = mgr.get("dns_fastest")
        s.dns[6] = {"static": ["10.1.1.1"], "effective": ["10.1.1.1"]}
        with self.assertRaises(tweaks.Unsupported):
            tweak.apply(s)
        self.assertEqual(s.writes(), [])

    def test_no_doh_template_is_unsupported(self):
        mgr, s, _ = setup({"dns_ranking": entry()})
        del s.doh["9.9.9.9"]
        self.assertFalse(on(mgr).supported)
        self.assertEqual(s.writes(), [])


class RestoreTests(unittest.TestCase):
    def test_dhcp_original_is_reset_to_dhcp(self):
        mgr, s, backup = setup({"dns_ranking": entry()})
        self.assertTrue(mgr.enable("dns_fastest").ok)
        self.assertEqual(backup.load()["dns_fastest"]["original"]["static"], [])
        out = mgr.disable("dns_fastest")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s.dns[6], {"static": [], "effective": ["192.0.2.1"]})
        self.assertIn(("write", "dns_servers_reset", (6,)), s.log)
        self.assertEqual(s.doh["1.1.1.1"], {"auto_upgrade": False, "fallback": False})

    def test_static_original_is_restored_exactly(self):
        mgr, s, _ = setup({"dns_ranking": entry()}, dns={6: {"static": ["192.0.2.1", "4.4.4.4"],
                                                              "effective": ["192.0.2.1", "4.4.4.4"]}})
        self.assertTrue(mgr.enable("dns_fastest").ok)
        self.assertEqual(s.dns[6]["static"], ["1.1.1.1", "9.9.9.9"])
        self.assertTrue(mgr.disable("dns_fastest").ok)
        self.assertEqual(s.dns[6]["static"], ["192.0.2.1", "4.4.4.4"])
        self.assertNotIn("dns_servers_reset", [w[1] for w in s.writes()])

    def test_original_doh_flags_come_back(self):
        mgr, s, _ = setup({"dns_ranking": entry()})
        s.doh["1.1.1.1"] = {"auto_upgrade": True, "fallback": False}      # set by hand before
        before = copy.deepcopy(s.doh)
        self.assertTrue(mgr.enable("dns_fastest").ok)
        self.assertEqual(s.doh["1.1.1.1"], {"auto_upgrade": True, "fallback": True})
        self.assertTrue(mgr.disable("dns_fastest").ok)
        self.assertEqual(s.doh, before)

    def test_restore_works_after_the_calibration_changed(self):
        cal = {"dns_ranking": entry()}
        mgr, s, _ = setup(cal)
        self.assertTrue(mgr.enable("dns_fastest").ok)
        cal.clear()                                                        # measured elsewhere / gone since
        self.assertTrue(mgr.disable("dns_fastest").ok)
        self.assertEqual(s.dns[6]["static"], [])

    def test_no_default_restore_without_a_backup(self):
        mgr, s, _ = setup({}, dns={6: {"static": ["1.1.1.1", "8.8.8.8"], "effective": ["1.1.1.1", "8.8.8.8"]}})
        st = on(mgr)
        self.assertTrue(st.enabled)                                        # set by hand: allow-listed only
        self.assertFalse(st.can_restore_without_backup)
        self.assertFalse(mgr.disable("dns_fastest").ok)
        self.assertEqual(s.writes(), [])
        with self.assertRaises(tweaks.NoDefaultRestore):
            mgr.get("dns_fastest").restore(s, None)

    def test_failed_apply_rolls_back(self):
        mgr, s, backup = setup({"dns_ranking": entry()}, dns={6: {"static": ["192.0.2.1"], "effective": ["192.0.2.1"]}})
        s.fail = {"doh_set"}
        out = mgr.enable("dns_fastest")
        self.assertFalse(out.ok)
        self.assertEqual(s.dns[6]["static"], ["192.0.2.1"])
        self.assertNotIn("dns_fastest", backup.load())

    def test_restore_key_covers_interface_servers_and_flags(self):
        t = DnsTweak("dns_fastest", "x", "medium")
        base = {"interface_index": 6, "static": ["1.1.1.1"], "doh": {"1.1.1.1": {"auto_upgrade": True, "fallback": True}}}
        keys = {t.restore_key(base)}
        for change in ({"interface_index": 7}, {"static": []},
                       {"doh": {"1.1.1.1": {"auto_upgrade": True, "fallback": False}}}):
            keys.add(t.restore_key({**base, **change}))
        self.assertEqual(len(keys), 4)
        with self.assertRaises(ValueError):
            t.validate_original({**base, "static": ["not an ip"]})


class ReadTests(unittest.TestCase):
    def test_a_newer_ranking_is_reported_not_applied(self):
        mgr, s, _ = setup({"dns_ranking": entry(["8.8.8.8", "9.9.9.9", "1.1.1.1"])},
                          dns={6: {"static": ["1.1.1.1", "9.9.9.9"], "effective": ["1.1.1.1", "9.9.9.9"]}})
        st = on(mgr)
        self.assertTrue(st.enabled)
        self.assertIn("8.8.8.8", i18n.render(st.reason, "en"))
        self.assertIn("turn this off, measure again", i18n.render(st.reason, "en"))
        self.assertEqual(s.writes(), [])
        self.assertFalse(mgr.enable("dns_fastest").changed)

    def test_hand_set_servers_without_a_calibration_count_as_on_with_a_note(self):
        mgr, s, _ = setup({}, dns={6: {"static": ["9.9.9.9", "8.8.8.8"], "effective": ["9.9.9.9", "8.8.8.8"]}})
        st = on(mgr)
        self.assertEqual((st.supported, st.enabled), (True, True))
        self.assertIn("diagnostics", i18n.render(st.reason, "en"))

    def test_other_static_servers_are_not_on(self):
        mgr, s, _ = setup({"dns_ranking": entry()}, dns={6: {"static": ["1.1.1.1", "4.4.4.4"],
                                                              "effective": ["1.1.1.1", "4.4.4.4"]}})
        self.assertFalse(on(mgr).enabled)

    def test_catalog_declaration(self):
        by = {t.id: t for t in tweaks.CATALOG}
        t = by["dns_fastest"]
        self.assertEqual((t.risk, t.has_default_restore, t.needs_admin, t.kind), ("medium", False, True, "dns_ranking"))
        self.assertIn("dns_ranking", __import__("app.calibration", fromlist=["KINDS"]).KINDS)
        self.assertIn("dns_fastest", [x.id for x in tweaks.build_catalog(load_calibration=dict)])


class WinsysTests(unittest.TestCase):
    def system(self, rows=None, ps=None):
        return winsys.WindowsSystem(ps=ps or FakePs(), ps_json=lambda *a, **k: rows if rows is not None else [], run=None)

    def test_the_real_system_has_every_dns_operation(self):
        for name in ("dns_get", "dns_suffix_get", "doh_get", "dns_servers_set", "dns_servers_reset", "doh_set"):
            self.assertIn(name, vars(winsys.WindowsSystem))
        for name in ("dns_get", "dns_suffix_get", "doh_get"):
            self.assertIn(name, tweaks._READ_METHODS)

    def test_write_scripts(self):
        ps = FakePs()
        s = self.system(ps=ps)
        s.dns_servers_set(6, ["1.1.1.1", "9.9.9.9"])
        s.dns_servers_reset("6")
        s.doh_set("1.1.1.1", True, True)
        s.doh_set("9.9.9.9", False, True)
        self.assertEqual(ps.scripts[0], "Set-DnsClientServerAddress -InterfaceIndex 6 -ServerAddresses "
                                        "('1.1.1.1', '9.9.9.9') -ErrorAction Stop")
        self.assertEqual(ps.scripts[1], "Set-DnsClientServerAddress -InterfaceIndex 6 -ResetServerAddresses "
                                        "-ErrorAction Stop")
        self.assertEqual(ps.scripts[2], "Set-DnsClientDohServerAddress -ServerAddress 1.1.1.1 -AutoUpgrade $true "
                                        "-AllowFallbackToUdp $true -ErrorAction Stop")
        self.assertIn("-AutoUpgrade $false -AllowFallbackToUdp $true", ps.scripts[3])

    def test_everything_is_validated(self):
        ps = FakePs()
        s = self.system(ps=ps)
        for bad in (["1.1.1.1; calc"], ["2606:4700:4700::1111"], ["1.1.1.256"], ["$(x)"], [], ["1.1.1.1"] * 9, ["a b"]):
            with self.assertRaises(ValueError, msg=bad):
                s.dns_servers_set(6, bad)
        with self.assertRaises(ValueError):
            s.dns_servers_set("6; calc", ["1.1.1.1"])
        for bad in ("1.1.1.1 -Foo", "::1", "x"):
            with self.assertRaises(ValueError):
                s.doh_set(bad, True, True)
            with self.assertRaises(ValueError):
                s.doh_get(bad)
        for bad in ("yes", 1, None):
            with self.assertRaises(ValueError):
                s.doh_set("1.1.1.1", bad, True)
        self.assertEqual(ps.scripts, [])

    def test_doh_get(self):
        self.assertEqual(self.system([{"AutoUpgrade": True, "Fallback": False}]).doh_get("1.1.1.1"),
                         {"auto_upgrade": True, "fallback": False})
        self.assertIsNone(self.system([]).doh_get("1.1.1.1"))

    def test_dns_get_reads_the_static_list_from_the_registry_value(self):
        class Fake(winsys.WindowsSystem):
            seen = None

            def _static_nameservers(self, guid):
                Fake.seen = guid
                return "192.0.2.1,1.1.1.1 ,fe80::1"

        ps = FakePs("{0C5B6C3E-4F6C-4A64-9E12-0123456789AB}\r\n")
        s = Fake(ps=ps, ps_json=lambda *a, **k: [{"Servers": {"value": ["192.0.2.1", "1.1.1.1", "fe80::1"], "Count": 3}}],
                 run=None)
        self.assertEqual(s.dns_get(6), {"effective": ["192.0.2.1", "1.1.1.1"], "static": ["192.0.2.1", "1.1.1.1"]})
        self.assertEqual(Fake.seen, "0C5B6C3E-4F6C-4A64-9E12-0123456789AB")

    def test_dhcp_means_an_empty_static_list(self):
        class Fake(winsys.WindowsSystem):
            def _static_nameservers(self, guid):
                return ""

        s = Fake(ps=FakePs("{0C5B6C3E-4F6C-4A64-9E12-0123456789AB}"),
                 ps_json=lambda *a, **k: [{"Servers": "192.0.2.1"}], run=None)
        self.assertEqual(s.dns_get(6), {"effective": ["192.0.2.1"], "static": []})

    def test_the_registry_value_name_is_not_taken_from_input(self):
        s = self.system()
        with self.assertRaises(ValueError):
            s._static_nameservers("..\\..\\x")

    def test_suffix(self):
        self.assertEqual(self.system(ps=FakePs("corp.example.com\r\n")).dns_suffix_get(6), "corp.example.com")
        self.assertEqual(self.system(ps=FakePs("\r\n")).dns_suffix_get(6), "")


def bench(server, rtts, failures=0):
    return dnsprobe.ServerBenchmark(server, sent=len(rtts) + failures, replies=len(rtts), failures=failures,
                                    rtts_ms=list(rtts))


def run_check(benches, in_use):
    recorded = []
    labels = {s: "in_use" for s in in_use}
    ctx = d.Context(now=NOW, loaders={"dns_bench": lambda: (benches, in_use, labels),
                                      "calibrate": lambda: (lambda *a: recorded.append(a))})
    return d.check_dns(ctx), recorded


class CheckTests(unittest.TestCase):
    def test_ranking_is_recorded_and_linked_when_it_beats_the_dns_in_use(self):
        r, rec = run_check([bench("192.168.3.1", [60] * 5), bench("1.1.1.1", [12] * 5), bench("8.8.8.8", [20] * 5),
                            bench("9.9.9.9", [15] * 5)], ["192.168.3.1"])
        (kind, value, detail, active), = rec
        self.assertEqual((kind, value, active), ("dns_ranking", 12.0, False))
        self.assertEqual(detail, {"servers": ["1.1.1.1", "9.9.9.9", "8.8.8.8"], "in_use_ms": 60.0})
        self.assertEqual((r.status, r.tweak), (d.INFO, "dns_fastest"))

    def test_statuses_and_thresholds_are_unchanged(self):
        r, _ = run_check([bench("1.1.1.1", [40] * 5), bench("9.9.9.9", [35] * 5)], ["1.1.1.1"])
        self.assertEqual((r.status, r.tweak), (d.OK, None))
        r, _ = run_check([bench("192.168.3.1", [40] * 4, failures=1), bench("1.1.1.1", [20] * 5),
                          bench("9.9.9.9", [35] * 5)], ["192.168.3.1"])
        self.assertEqual((r.status, r.tweak), (d.WARN, "dns_fastest"))         # broken DNS: a fix is on offer

    def test_one_provider_or_errors_record_nothing(self):
        cases = ([bench("1.1.1.1", [12] * 5)],
                 [bench("1.1.1.1", [12] * 5), bench("9.9.9.9", [], failures=5)],
                 [bench("1.1.1.1", [12] * 5), bench("9.9.9.9", [15] * 4, failures=1)],
                 [bench("192.168.3.1", [3] * 5), bench("6.6.6.6", [4] * 5)])
        for benches in cases:
            r, rec = run_check(benches, [benches[0].server])
            self.assertEqual(rec, [])
            self.assertIsNone(r.tweak)

    def test_a_slower_public_pair_than_the_router_is_not_suggested(self):
        r, rec = run_check([bench("192.168.3.1", [5] * 5), bench("1.1.1.1", [30] * 5), bench("9.9.9.9", [35] * 5)],
                           ["1.1.1.1"])
        self.assertEqual(len(rec), 1)
        self.assertIsNone(r.tweak)

    def test_bare_context_never_writes_a_calibration(self):
        self.assertFalse(d.Context(now=NOW)._calibrate("dns_ranking", 12.0, {}, False))


class WatchSuppressionTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.addCleanup(self.db.close)
        self.toasts, self.table = [], {6: ["192.168.3.1"]}
        settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        settings["probes"]["enabled"] = False
        from app import winutil
        wifi = winutil.WifiState("Wi-Fi", "connected", "Home", "aa:aa", "802.11ax", 36, 80, -55, 100, 100)
        self.mon = Monitor(self.db, settings, wifi_fn=lambda: wifi, gateway_fn=lambda: "192.168.3.1",
                           notify=lambda title, body: self.toasts.append((title, body)),
                           route_fn=lambda: {"gateway": "192.168.3.1", "interface_index": 6},
                           dns_fn=lambda: self.table)
        self.addCleanup(self.mon._pool.shutdown)
        self.mon.poll_wifi(baseline=True)
        self.mon.check_dns()

    def change(self):
        self.table = {6: ["1.1.1.1", "9.9.9.9"]}
        self.mon.check_dns()
        self.mon.check_dns()

    def kinds(self):
        return [e["kind"] for e in self.db.query_events(kinds=["dns_observed", "dns_changed"])]

    def tweak_event(self, kind, tweak_id="dns_fastest", ago=30):
        self.db.add_event(int(time.time()) - ago, kind, msg("tweak.event.enabled", name="x", tweak_id=tweak_id),
                          level="info")

    def test_a_change_made_by_the_tweak_is_only_an_observation(self):
        self.tweak_event("tweak_enabled")
        self.change()
        self.assertEqual(self.kinds(), ["dns_observed", "dns_observed"])
        self.assertEqual(self.toasts, [])
        newest = self.db.query_events(kinds=["dns_observed"])[0]
        self.assertEqual((newest["level"], newest["message"]["params"]["servers"]), ("info", ["1.1.1.1", "9.9.9.9"]))
        self.table = {6: ["1.1.1.1", "9.9.9.9"]}
        self.mon.check_dns()                                  # and the new list is what the watch knows now
        self.assertEqual(len(self.kinds()), 2)

    def test_turning_it_off_is_just_as_innocent(self):
        self.tweak_event("tweak_disabled")
        self.change()
        self.assertEqual(self.kinds(), ["dns_observed", "dns_observed"])
        self.assertEqual(self.toasts, [])

    def test_other_tweaks_old_events_and_plain_changes_still_alarm(self):
        for kind, tweak_id, ago in (("tweak_enabled", "mtu_path", 30), ("tweak_enabled", "dns_fastest", 11 * 60),
                                    ("tweak_failed", "dns_fastest", 30)):
            self.tearDown_events()
            self.tweak_event(kind, tweak_id, ago)
            self.setUp_network()
            self.change()
            self.assertEqual(self.kinds()[0], "dns_changed", (kind, tweak_id, ago))
            self.assertEqual(len(self.toasts), 1)
            self.toasts.clear()

    def tearDown_events(self):
        self.db._db.execute("DELETE FROM events")

    def setUp_network(self):
        self.table = {6: ["192.168.3.1"]}
        self.mon._dns_watch.known.clear()
        self.mon.check_dns()


if __name__ == "__main__":
    unittest.main()
