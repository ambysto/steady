import copy
import socket
import sys
import unittest

from app import config, i18n, winutil
from app.dnswatch import DnsWatch, network_key
from app.monitor import Monitor
from app.storage import Storage

HOME = network_key(6, "192.168.3.1", "Home")


class DnsWatchTests(unittest.TestCase):
    def test_first_look_is_recorded_quietly(self):
        w = DnsWatch()
        (kind, message, level), = w.observe(HOME, ["1.1.1.1", "8.8.8.8"])
        self.assertEqual((kind, level), ("dns_observed", "info"))
        self.assertEqual(i18n.render(message, "en"), "DNS servers on this network: 1.1.1.1, 8.8.8.8")
        self.assertEqual(w.observe(HOME, ["1.1.1.1", "8.8.8.8"]), [])

    def test_change_on_the_same_network_needs_two_looks(self):
        w = DnsWatch()
        w.observe(HOME, ["1.1.1.1"])
        self.assertEqual(w.observe(HOME, ["6.6.6.6"]), [])          # once: could be a half-done renew
        (kind, message, level), = w.observe(HOME, ["6.6.6.6"])
        self.assertEqual((kind, level), ("dns_changed", "warn"))
        self.assertEqual(i18n.render(message, "en"), "The DNS servers of this network changed: 1.1.1.1 → 6.6.6.6")
        self.assertEqual(w.observe(HOME, ["6.6.6.6"]), [])           # reported once

    def test_flapping_back_is_not_reported(self):
        w = DnsWatch()
        w.observe(HOME, ["1.1.1.1"])
        w.observe(HOME, ["6.6.6.6"])
        self.assertEqual(w.observe(HOME, ["1.1.1.1"]), [])
        self.assertEqual(w.observe(HOME, ["6.6.6.6"]), [])           # the count started over

    def test_empty_list_and_other_networks(self):
        w = DnsWatch()
        w.observe(HOME, ["1.1.1.1"])
        self.assertEqual(w.observe(HOME, []), [])
        cafe = network_key(6, "10.0.0.1", "Cafe")
        self.assertEqual([k for k, _, _ in w.observe(cafe, ["10.0.0.1"])], ["dns_observed"])   # new network: no alarm
        self.assertEqual(w.observe(HOME, ["1.1.1.1"]), [])           # back home, still the same

    def test_order_change_counts(self):
        w = DnsWatch(confirm=1)
        w.observe(HOME, ["1.1.1.1", "8.8.8.8"])
        self.assertEqual([k for k, _, _ in w.observe(HOME, ["8.8.8.8", "1.1.1.1"])], ["dns_changed"])

    def test_seeded_from_stored_events_survives_a_restart(self):
        with Storage() as db:
            first = DnsWatch(confirm=1)
            for observed in (["1.1.1.1"], ["6.6.6.6"]):
                for kind, message, level in first.observe(HOME, observed):
                    db.add_event(100, kind, message, level=level)
            second = DnsWatch(confirm=1)
            second.seed(reversed(db.query_events(kinds=["dns_observed", "dns_changed"])))
        self.assertEqual(second.known[HOME], ["6.6.6.6"])
        self.assertEqual(second.observe(HOME, ["6.6.6.6"]), [])


class MonitorDnsTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.addCleanup(self.db.close)
        self.toasts, self.table = [], {6: ["1.1.1.1"]}
        settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        settings["probes"]["enabled"] = False
        wifi = winutil.WifiState("Wi-Fi", "connected", "Home", "aa:aa", "802.11ax", 36, 80, -55, 100, 100)
        self.mon = Monitor(self.db, settings, wifi_fn=lambda: wifi, gateway_fn=lambda: "192.168.3.1",
                           notify=lambda title, body: self.toasts.append((title, body)),
                           route_fn=lambda: {"gateway": "192.168.3.1", "interface_index": 6},
                           dns_fn=lambda: self.table)
        self.addCleanup(self.mon._pool.shutdown)
        self.mon.poll_wifi(baseline=True)

    def test_change_is_logged_and_toasted(self):
        self.mon.check_dns()
        self.table = {6: ["6.6.6.6"]}
        self.mon.check_dns()
        self.mon.check_dns()
        kinds = [e["kind"] for e in self.db.query_events()]
        self.assertEqual(kinds, ["dns_changed", "dns_observed"])
        self.assertEqual(self.db.query_events(kinds=["dns_changed"])[0]["level"], "warn")
        self.assertEqual(len(self.toasts), 1)
        self.assertEqual(self.toasts[0][0], i18n.t("notify.dns_changed.title"))
        self.assertIn("6.6.6.6", self.toasts[0][1])

    def test_lookup_failure_is_contained(self):
        def boom():
            raise OSError("iphlpapi gone")
        self.mon._dns_fn = boom
        with self.assertLogs("stableinternet.monitor", "WARNING"):
            self.mon.check_dns()
        self.assertEqual(self.db.query_events(), [])


class NativeDnsTests(unittest.TestCase):
    def test_sockaddr_parsing(self):
        import ctypes
        v4 = (socket.AF_INET).to_bytes(2, "little") + b"\x00\x35" + socket.inet_aton("1.1.1.1") + b"\x00" * 8
        v6 = (socket.AF_INET6).to_bytes(2, "little") + b"\x00\x35" + b"\x00" * 4 + \
            socket.inet_pton(socket.AF_INET6, "2606:4700:4700::1111") + b"\x00" * 4
        for raw, text in ((v4, "1.1.1.1"), (v6, "2606:4700:4700::1111")):
            buf = ctypes.create_string_buffer(raw, len(raw))
            sa = winutil._SocketAddress(ctypes.cast(buf, ctypes.c_void_p), len(raw))
            self.assertEqual(winutil._sockaddr_text(sa), text)
        self.assertIsNone(winutil._sockaddr_text(winutil._SocketAddress(None, 0)))

    @unittest.skipUnless(sys.platform == "win32", "iphlpapi")
    def test_real_table_has_the_default_route_interface(self):
        route = winutil.default_route_native()
        table = winutil.dns_servers_native()
        self.assertIsInstance(table, dict)
        if route:   # online: the uplink interface is listed (it may have no DNS set)
            self.assertIn(route["interface_index"], table)
        for servers in table.values():
            self.assertFalse([s for s in servers if s.startswith("fec0:0:0:ffff::")])


if __name__ == "__main__":
    unittest.main()
