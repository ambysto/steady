"""Tweaks of the network stack: TCP ECN, RSC, packet coalescing and the measured DNS tweak (ADR-0015)."""
import unittest

from app import dnsprobe, i18n, tweaks
from app.dnswatch import app_changed
from app.winsys import SystemWriteError
from tests.test_tweaks import manager

CANDIDATES = tuple(tweaks.DOH_ADDRESSES)


def bench_of(ms_by_server, failing=()):
    """A benchmark result for every candidate: `ms_by_server` gives the median, `failing` ones lost a query."""
    out = []
    for server in CANDIDATES:
        failures = 1 if server in failing else 0
        ms = ms_by_server.get(server)
        out.append(dnsprobe.ServerBenchmark(server, 5, 5 - failures, failures, [] if ms is None else [ms] * 5))
    return out


FAST_CLOUDFLARE = {"1.1.1.1": 10, "1.0.0.1": 15, "8.8.8.8": 20, "8.8.4.4": 25, "9.9.9.9": 40, "149.112.112.112": 45}


class Network:
    """What the tests change: the benchmark answer and whether the network looks like a captive portal."""

    def __init__(self, bench=None, captive=False):
        self.bench, self.captive, self.benchmarks, self.captive_checks = bench or bench_of(FAST_CLOUDFLARE), captive, 0, 0

    def benchmark(self, servers):
        self.benchmarks += 1
        return self.bench

    def is_captive(self):
        self.captive_checks += 1
        return self.captive


def setup(ids, system=None, network=None, **kw):
    network = network or Network()
    catalog = tweaks.build_catalog(dns_benchmark=network.benchmark, captive=network.is_captive)
    mgr, system, backup, events = manager(system, tweak_list=[t for t in catalog if t.id in ids], **kw)
    return mgr, system, backup, events, network


def text(message):
    return i18n.render(message, "en")


class TcpEcnTests(unittest.TestCase):
    def test_round_trip_through_the_backup(self):
        mgr, s, backup, _, _ = setup(["tcp_ecn"])
        before = s.snapshot()
        out = mgr.enable("tcp_ecn")
        self.assertTrue(out.ok and out.changed, out.message)
        self.assertEqual(s.tcp_global["ecncapability"], "enabled")
        self.assertEqual(backup.data["tcp_ecn"]["original"], {"setting": "ecncapability", "value": "disabled"})
        self.assertTrue(mgr.disable("tcp_ecn").ok)
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(backup.data, {})

    def test_the_original_value_comes_back_even_when_it_was_default(self):
        mgr, s, _, _, _ = setup(["tcp_ecn"])
        s.tcp_global["ecncapability"] = "default"
        mgr.enable("tcp_ecn")
        mgr.disable("tcp_ecn")
        self.assertEqual(s.tcp_global["ecncapability"], "default")

    def test_turned_on_by_hand_is_a_noop_and_off_goes_to_the_windows_default(self):
        mgr, s, backup, _, _ = setup(["tcp_ecn"])
        s.tcp_global["ecncapability"] = "enabled"                      # the dev PC after the manual session
        out = mgr.enable("tcp_ecn")
        self.assertTrue(out.ok and not out.changed)
        self.assertEqual((s.writes(), backup.data), ([], {}))
        self.assertTrue(mgr.disable("tcp_ecn").ok)                      # no backup: Windows' own value is known
        self.assertEqual(s.tcp_global["ecncapability"], "disabled")

    def test_not_reported_by_windows_is_unsupported(self):
        mgr, s, _, _, _ = setup(["tcp_ecn"])
        s.tcp_global.clear()
        st = mgr.state("tcp_ecn")
        self.assertFalse(st.supported)
        self.assertIn("ecncapability", text(st.reason))
        self.assertEqual(s.writes(), [])


class RscTests(unittest.TestCase):
    def test_round_trip_restores_each_family(self):
        mgr, s, backup, _, _ = setup(["rsc_off"])
        s.rsc["Wi-Fi"].update(ipv4=True, ipv6=False)
        before = s.snapshot()
        self.assertTrue(mgr.enable("rsc_off").ok)
        self.assertEqual((s.rsc["Wi-Fi"]["ipv4"], s.rsc["Wi-Fi"]["ipv6"]), (False, False))
        self.assertEqual(backup.data["rsc_off"]["original"], {"adapter": "Wi-Fi", "ipv4": True, "ipv6": False})
        self.assertTrue(mgr.disable("rsc_off").ok)
        self.assertEqual(s.snapshot(), before)                          # IPv6 was off and stays off

    def test_only_the_families_the_card_supports_are_touched(self):
        mgr, s, backup, _, _ = setup(["rsc_off"])
        s.rsc["Wi-Fi"].update(ipv6_supported=False, ipv6=False)
        self.assertTrue(mgr.enable("rsc_off").ok)
        self.assertEqual(s.writes(), [("write", "rsc_set", ("Wi-Fi", False, None))])
        self.assertEqual(backup.data["rsc_off"]["original"]["ipv6"], None)
        self.assertTrue(mgr.disable("rsc_off").ok)
        self.assertTrue(s.rsc["Wi-Fi"]["ipv4"])

    def test_a_card_without_rsc_is_unsupported_and_untouched(self):
        mgr, s, _, _, _ = setup(["rsc_off"])
        s.rsc["Wi-Fi"].update(ipv4_supported=False, ipv6_supported=False, ipv4=False, ipv6=False)   # the dev PC
        out = mgr.enable("rsc_off")
        self.assertFalse(out.ok)
        self.assertIn("does not support", text(mgr.state("rsc_off").reason))
        s.rsc.clear()                                                   # Windows has no RSC data at all
        self.assertFalse(mgr.state("rsc_off").supported)
        self.assertEqual(s.writes(), [])

    def test_without_a_backup_it_is_not_guessed(self):
        mgr, s, _, _, _ = setup(["rsc_off"])
        s.rsc["Wi-Fi"].update(ipv4=False, ipv6=False)                   # turned off by hand
        self.assertTrue(mgr.state("rsc_off").enabled)
        out = mgr.disable("rsc_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.writes(), [])

    def test_the_adapter_restart_is_announced(self):
        mgr, *_ = setup(["rsc_off"])
        self.assertTrue(mgr.state("rsc_off").disrupts_network)


class PacketCoalescingTests(unittest.TestCase):
    def test_round_trip_for_every_original_value(self):
        for original in ("Enabled", "Default"):
            with self.subTest(original=original):
                mgr, s, _, _, _ = setup(["packet_coalescing_off"])
                s.offload["PacketCoalescingFilter"] = original
                self.assertTrue(mgr.enable("packet_coalescing_off").ok)
                self.assertEqual(s.offload["PacketCoalescingFilter"], "Disabled")
                self.assertTrue(mgr.disable("packet_coalescing_off").ok)
                self.assertEqual(s.offload["PacketCoalescingFilter"], original)

    def test_turned_off_by_hand_cannot_be_restored_blindly(self):
        mgr, s, _, _, _ = setup(["packet_coalescing_off"])
        s.offload["PacketCoalescingFilter"] = "Disabled"
        self.assertTrue(mgr.enable("packet_coalescing_off").ok)         # a no-op
        out = mgr.disable("packet_coalescing_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.offload["PacketCoalescingFilter"], "Disabled")
        self.assertEqual(s.writes(), [])


class ExperimentalTests(unittest.TestCase):
    def test_the_three_are_experimental_and_need_admin(self):
        by = {t.id: t for t in tweaks.CATALOG}
        for tid in ("tcp_ecn", "rsc_off", "packet_coalescing_off"):
            self.assertEqual(by[tid].risk, "experimental", tid)
            self.assertTrue(by[tid].needs_admin, tid)
        self.assertTrue(by["tcp_ecn"].has_default_restore)
        self.assertFalse(by["rsc_off"].has_default_restore)
        self.assertFalse(by["packet_coalescing_off"].has_default_restore)


class ChooseServersTests(unittest.TestCase):
    def test_two_providers_the_winner_first_then_the_runner_up_then_its_second_server(self):
        self.assertEqual(tweaks.choose_dns_servers(bench_of(FAST_CLOUDFLARE)), ["1.1.1.1", "8.8.8.8", "1.0.0.1"])

    def test_the_first_two_never_share_a_provider(self):
        ms = {**FAST_CLOUDFLARE, "1.0.0.1": 11}                          # Cloudflare's second server beats Google's first
        first_two = tweaks.choose_dns_servers(bench_of(ms))[:2]
        self.assertEqual([tweaks.DOH_ADDRESSES[a][0] for a in first_two], ["cloudflare", "google"])

    def test_a_server_that_failed_a_query_is_out(self):
        ms = {**FAST_CLOUDFLARE, "1.1.1.1": 1}
        chosen = tweaks.choose_dns_servers(bench_of(ms, failing={"1.1.1.1"}))
        self.assertNotIn("1.1.1.1", chosen)
        self.assertEqual(chosen[:2], ["1.0.0.1", "8.8.8.8"])

    def test_the_winner_with_one_working_server_gives_two_servers(self):
        chosen = tweaks.choose_dns_servers(bench_of(FAST_CLOUDFLARE, failing={"1.0.0.1"}))
        self.assertEqual(chosen, ["1.1.1.1", "8.8.8.8"])

    def test_a_slow_provider_does_not_win_on_its_second_server(self):
        ms = {"1.1.1.1": 90, "1.0.0.1": 5, "8.8.8.8": 30, "8.8.4.4": 31, "9.9.9.9": 60, "149.112.112.112": 61}
        self.assertEqual(tweaks.choose_dns_servers(bench_of(ms))[:2], ["1.0.0.1", "8.8.8.8"])   # ranked by the best server

    def test_fewer_than_two_working_providers_is_refused(self):
        only_cloudflare = {"1.1.1.1": 10, "1.0.0.1": 12}
        with self.assertRaises(tweaks.NoFastServers):
            tweaks.choose_dns_servers(bench_of(only_cloudflare))
        with self.assertRaises(tweaks.NoFastServers):
            tweaks.choose_dns_servers([])

    def test_unknown_servers_in_the_benchmark_are_ignored(self):
        extra = dnsprobe.ServerBenchmark("192.168.1.1", 5, 5, 0, [1.0] * 5)
        self.assertEqual(tweaks.choose_dns_servers(bench_of(FAST_CLOUDFLARE) + [extra])[0], "1.1.1.1")


class DnsFastestTests(unittest.TestCase):
    def test_enable_sets_the_chosen_servers_with_doh_and_disable_restores_dhcp(self):
        mgr, s, backup, events, net = setup(["dns_fastest"])
        before = s.snapshot()
        out = mgr.enable("dns_fastest")
        self.assertTrue(out.ok and out.changed, out.message)
        self.assertEqual(s.dns["servers"], ["1.1.1.1", "8.8.8.8", "1.0.0.1"])
        self.assertTrue(s.dns["static"])
        for address in ("1.1.1.1", "8.8.8.8", "1.0.0.1"):
            self.assertEqual(s.doh[address]["auto_upgrade"], True, address)
            self.assertEqual(s.doh[address]["fallback_to_udp"], True, address)
        self.assertFalse(s.doh["9.9.9.9"]["auto_upgrade"])             # not chosen: left alone
        original = backup.data["dns_fastest"]["original"]
        self.assertEqual((original["static"], original["servers"], original["interface_index"]), (False, ["192.168.1.1"], 6))
        self.assertEqual(set(original["doh"]), set(CANDIDATES))        # every candidate is saved, chosen or not
        self.assertEqual(net.benchmarks, 1)
        self.assertTrue(mgr.disable("dns_fastest").ok)
        self.assertEqual(s.snapshot(), before)
        self.assertIn(("write", "dns_servers_set", (6, None)), s.writes())     # -ResetServerAddresses
        self.assertEqual(backup.data, {})

    def test_doh_is_set_before_the_servers_are_switched(self):
        mgr, s, *_ = setup(["dns_fastest"])
        mgr.enable("dns_fastest")
        order = [name for _, name, _ in s.writes()]
        self.assertEqual(order, ["doh_set", "doh_set", "doh_set", "dns_servers_set"])

    def test_static_servers_and_doh_entries_come_back_as_they_were(self):
        mgr, s, *_ = setup(["dns_fastest"])
        s.dns.update(servers=["10.0.0.53", "10.0.0.54"], static=True)
        s.doh["8.8.8.8"].update(auto_upgrade=True, fallback_to_udp=True)      # somebody had set one up
        s.doh.pop("149.112.112.112")                                           # and one was missing altogether
        before = s.snapshot()
        self.assertTrue(mgr.enable("dns_fastest").ok)
        self.assertTrue(mgr.disable("dns_fastest").ok)
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(s.dns["servers"], ["10.0.0.53", "10.0.0.54"])
        self.assertNotIn("149.112.112.112", s.doh)

    def test_a_hand_made_setup_counts_as_on_and_enable_changes_nothing(self):
        mgr, s, backup, _, net = setup(["dns_fastest"])
        s.dns.update(servers=["1.1.1.1", "1.0.0.1", "8.8.8.8"], static=True)   # the dev PC after the manual session
        for address in ("1.1.1.1", "1.0.0.1", "8.8.8.8"):
            s.doh[address]["auto_upgrade"] = True
        self.assertTrue(mgr.state("dns_fastest").enabled)
        out = mgr.enable("dns_fastest")
        self.assertTrue(out.ok and not out.changed)
        self.assertEqual((s.writes(), backup.data, net.benchmarks), ([], {}, 0))
        self.assertFalse(mgr.disable("dns_fastest").ok)                        # without a backup nothing is guessed
        self.assertEqual(s.writes(), [])

    def test_not_on_when_the_servers_are_not_over_doh_or_not_from_two_providers(self):
        mgr, s, *_ = setup(["dns_fastest"])
        s.dns.update(servers=["1.1.1.1", "1.0.0.1", "8.8.8.8"], static=True)   # DoH not enabled yet
        self.assertFalse(mgr.state("dns_fastest").enabled)
        s.dns.update(servers=["1.1.1.1", "1.0.0.1"])
        for address in ("1.1.1.1", "1.0.0.1"):
            s.doh[address]["auto_upgrade"] = True
        self.assertFalse(mgr.state("dns_fastest").enabled)                     # one provider is not enough
        s.dns.update(servers=["1.1.1.1", "8.8.8.8"], static=False)
        s.doh["8.8.8.8"]["auto_upgrade"] = True
        self.assertFalse(mgr.state("dns_fastest").enabled)                     # DHCP-assigned, however it looks

    def test_reading_never_measures(self):
        mgr, s, _, _, net = setup(["dns_fastest"])
        mgr.states()
        mgr.plan("dns_fastest", True)
        self.assertEqual(net.benchmarks, 0)
        self.assertEqual(s.writes(), [])

    def test_the_network_that_should_be_left_alone(self):
        cases = {"a VPN is up": dict(vpn_up=True), "a domain": dict(domain_joined=True),
                 "a DNS suffix": dict(suffix="corp.example"), "a static IPv6 DNS": dict(static_v6=True)}
        for name, change in cases.items():
            with self.subTest(name):
                mgr, s, _, _, net = setup(["dns_fastest"])
                s.dns.update(change)
                st = mgr.state("dns_fastest")
                self.assertFalse(st.supported)
                self.assertTrue(text(st.reason))
                self.assertFalse(mgr.enable("dns_fastest").ok)
                self.assertEqual((s.writes(), net.benchmarks, net.captive_checks), ([], 0, 0))   # the probe is the last resort

    def test_a_captive_portal_is_not_changed(self):
        mgr, s, backup, _, net = setup(["dns_fastest"], network=Network(captive=True))
        st = mgr.state("dns_fastest")
        self.assertFalse(st.supported)
        self.assertIn("captive portal", text(st.reason))
        self.assertFalse(mgr.enable("dns_fastest").ok)
        self.assertEqual((s.writes(), backup.data, net.benchmarks), ([], {}, 0))

    def test_a_portal_that_appears_between_the_read_and_the_apply_stops_the_change(self):
        mgr, s, backup, _, net = setup(["dns_fastest"])
        first = [True]

        def captive_after_the_first_look():
            net.captive_checks += 1
            ask, first[0] = not first[0], False
            return ask
        tweak = mgr.get("dns_fastest")
        tweak._captive = captive_after_the_first_look
        before = s.snapshot()
        out = mgr.enable("dns_fastest")
        self.assertFalse(out.ok)
        self.assertEqual((s.snapshot(), backup.data, net.benchmarks), (before, {}, 0))

    def test_no_two_working_providers_changes_nothing(self):
        bench = bench_of({"1.1.1.1": 10, "1.0.0.1": 12, "8.8.8.8": 20}, failing={"8.8.8.8"})
        mgr, s, backup, _, _ = setup(["dns_fastest"], network=Network(bench))
        before = s.snapshot()
        out = mgr.enable("dns_fastest")
        self.assertFalse(out.ok)
        self.assertIn("two public DNS providers", text(out.message))
        self.assertEqual((s.snapshot(), backup.data), (before, {}))

    def test_a_failure_halfway_rolls_everything_back(self):
        mgr, s, backup, events, _ = setup(["dns_fastest"])
        s.dns.update(servers=["10.0.0.53"], static=True)
        before = s.snapshot()
        real_set, calls = s.dns_servers_set, []

        def flaky(interface_index, servers):       # the three DoH entries go in, then the first server switch fails
            calls.append(servers)
            if len(calls) == 1:
                raise SystemWriteError("injected")
            real_set(interface_index, servers)
        s.dns_servers_set = flaky
        out = mgr.enable("dns_fastest")
        self.assertFalse(out.ok)
        self.assertEqual((s.snapshot(), backup.data), (before, {}))     # DoH entries back as they were, no backup left
        self.assertEqual(events[-1][0], "tweak_failed")

    def test_the_app_announces_its_change_to_dnswatch(self):
        mgr, s, _, events, _ = setup(["dns_fastest"])
        mgr.enable("dns_fastest")
        kind, message, _ = events[-1]
        self.assertEqual((kind, message["params"]["tweak_id"]), ("tweak_enabled", "dns_fastest"))
        stored = [{"ts": 1000, "kind": kind, "message": message}]
        self.assertTrue(app_changed(stored, 1100))
        mgr.disable("dns_fastest")
        kind, message, _ = events[-1]
        self.assertEqual(kind, "tweak_disabled")
        self.assertTrue(app_changed([{"ts": 1000, "kind": kind, "message": message}], 1100))

    def test_it_is_medium_risk_needs_admin_and_has_no_blind_default(self):
        t = next(t for t in tweaks.CATALOG if t.id == "dns_fastest")
        self.assertEqual((t.risk, t.needs_admin, t.has_default_restore), ("medium", True, False))

    def test_no_uplink_is_unsupported(self):
        mgr, s, *_ = setup(["dns_fastest"])
        s.dns = None
        st = mgr.state("dns_fastest")
        self.assertFalse(st.supported)
        self.assertIsNone(st.error)
        self.assertEqual(s.writes(), [])


if __name__ == "__main__":
    unittest.main()
