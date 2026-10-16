"""Whether the speed test's connection is metered (app/netcost.py, ADR-0024 decision 4): the decision from
plain readings, and the reading itself with its Windows calls replaced."""
import sys
import unittest
from unittest import mock

from app import netcost as nc
from app.netcost import Connection, Interface, Verdict

WIFI, ETHERNET, WWAN, VPN, TETHER, IPHONE = 6, 12, 20, 40, 30, 31
INTERFACES = {
    WIFI: Interface(WIFI, "wifi", "Intel(R) Wi-Fi 6E AX211 160MHz"),
    ETHERNET: Interface(ETHERNET, "ethernet", "Realtek PCIe 2.5GbE Family Controller"),
    WWAN: Interface(WWAN, "cellular", "Generic Mobile Broadband Adapter"),
    VPN: Interface(VPN, "tunnel", "WireGuard Tunnel"),
    TETHER: Interface(TETHER, "ethernet", "Remote NDIS based Internet Sharing Device"),
    IPHONE: Interface(IPHONE, "ethernet", "Apple Mobile Device Ethernet"),
}
FREE, FIXED, VARIABLE = nc.COST_UNRESTRICTED, nc.COST_FIXED, nc.COST_VARIABLE


def conn(index, cost, internet=True, network_id=None):
    return Connection(index, internet, cost, network_id or f"net-{index}")


def decide(routes, *connections):
    return nc.verdict(routes, INTERFACES, list(connections))


class CostTests(unittest.TestCase):
    def test_what_costs_money(self):
        for cost in (FIXED, VARIABLE, nc.COST_ROAMING | FREE, nc.COST_OVERDATALIMIT | FIXED,
                     nc.COST_APPROACHINGDATALIMIT | VARIABLE):
            self.assertTrue(nc.costs_money(cost), hex(cost))
        for cost in (None, nc.COST_UNKNOWN, FREE, FREE | nc.COST_CONGESTED):
            self.assertFalse(nc.costs_money(cost), cost)

    def test_tether_adapters(self):
        self.assertTrue(nc.is_tether("Remote NDIS based Internet Sharing Device #2"))
        self.assertTrue(nc.is_tether("Apple Mobile Device Ethernet"))
        self.assertFalse(nc.is_tether("Realtek USB GbE Family Controller"))
        self.assertFalse(nc.is_tether(None))


class VerdictTests(unittest.TestCase):
    def test_unmetered_wifi_and_ethernet_run_at_once(self):
        self.assertEqual(decide([WIFI], conn(WIFI, FREE)), Verdict(False))
        self.assertEqual(decide([ETHERNET], conn(ETHERNET, FREE)), Verdict(False))

    def test_a_network_set_as_metered_asks_every_time(self):
        self.assertEqual(decide([WIFI], conn(WIFI, FIXED)), Verdict(True))
        self.assertEqual(decide([ETHERNET], conn(ETHERNET, VARIABLE)), Verdict(True))
        self.assertEqual(decide([WIFI], conn(WIFI, nc.COST_ROAMING)), Verdict(True))

    def test_an_unknown_cost_reads_as_not_metered(self):
        self.assertEqual(decide([WIFI], conn(WIFI, nc.COST_UNKNOWN)), Verdict(False))
        self.assertEqual(decide([WIFI], conn(WIFI, None)), Verdict(False))
        self.assertEqual(decide([WIFI]), Verdict(False))           # no connection reading at all

    def test_mobile_broadband_and_tethers_are_metered_whatever_the_cost_says(self):
        self.assertEqual(decide([WWAN], conn(WWAN, nc.COST_UNKNOWN)), Verdict(True))
        self.assertEqual(decide([TETHER], conn(TETHER, FREE)), Verdict(True))
        self.assertEqual(decide([IPHONE], conn(IPHONE, FREE)), Verdict(True))

    def test_the_ipv6_route_counts_too(self):
        self.assertEqual(decide([WWAN]), Verdict(True))                         # IPv6-only mobile network
        self.assertEqual(decide([WIFI, ETHERNET], conn(WIFI, FREE), conn(ETHERNET, FIXED)), Verdict(True))

    def test_a_metered_network_off_the_route_does_not_count(self):
        self.assertEqual(decide([WIFI], conn(WIFI, FREE), conn(WWAN, VARIABLE)), Verdict(False))

    def test_behind_a_vpn_the_networks_underneath_count_as_a_guess(self):
        got = decide([VPN], conn(VPN, FREE), conn(WIFI, FIXED, network_id="home"))
        self.assertEqual(got, Verdict(True, ("home",)))
        self.assertTrue(got.guessed)
        self.assertEqual(decide([VPN], conn(VPN, None), conn(TETHER, FREE, network_id="phone")),
                         Verdict(True, ("phone",)))
        self.assertEqual(decide([VPN], conn(VPN, FREE), conn(ETHERNET, FREE)), Verdict(False))
        unmapped = Connection(None, True, VARIABLE, "hotspot")       # adapter not found: its cost still counts
        self.assertEqual(decide([VPN], conn(VPN, FREE), unmapped), Verdict(True, ("hotspot",)))

    def test_behind_a_vpn_a_metered_network_without_internet_does_not_count(self):
        self.assertEqual(decide([VPN], conn(VPN, FREE), conn(ETHERNET, FREE), conn(WWAN, VARIABLE, internet=False)),
                         Verdict(False))

    def test_a_tunnel_windows_does_not_list_is_no_vpn(self):
        # IPv6 on without an IPv6 route: the IPv6 route can be a Teredo tunnel or the loopback.
        teredo = {**INTERFACES, 50: Interface(50, "tunnel", "Teredo Tunneling Pseudo-Interface")}
        self.assertEqual(nc.verdict([WIFI, 50], teredo, [conn(WIFI, FREE), conn(WWAN, VARIABLE)]), Verdict(False))

    def test_behind_a_vpn_a_network_without_an_id_asks_every_time(self):
        self.assertEqual(decide([VPN], conn(VPN, FREE), Connection(WIFI, True, FIXED, None)), Verdict(True))

    def test_a_vpn_that_is_metered_itself_is_certain(self):
        self.assertEqual(decide([VPN], conn(VPN, FIXED), conn(WIFI, FIXED)), Verdict(True))

    def test_no_route_is_not_metered(self):
        self.assertEqual(decide([], conn(WWAN, FIXED)), Verdict(False))
        self.assertEqual(decide([99], conn(99, FIXED)), Verdict(True))           # unknown kind, its own cost
        self.assertEqual(decide([99]), Verdict(False))


class ReadTests(unittest.TestCase):
    """`read` with the Windows calls replaced: anything missing or failing reads as not metered."""

    def patched(self, v4, v6, connections, rows):
        from app import traffic, winutil
        return [mock.patch.object(winutil, "internet_route_native", lambda dest: v4),
                mock.patch.object(winutil, "internet_route6_native", lambda dest: v6),
                mock.patch.object(nc, "read_connections", connections),
                mock.patch.object(traffic, "interface_row", lambda index: rows.get(index))]

    def read(self, v4, v6, connections, rows):
        patches = self.patched(v4, v6, connections, rows)
        for p in patches:
            p.start()
        try:
            return nc.read()
        finally:
            for p in patches:
                p.stop()

    def test_it_reads_the_routes_the_connections_and_the_interfaces(self):
        rows = {WIFI: {"kind": "wifi", "description": "Wi-Fi"}, WWAN: {"kind": "cellular", "description": ""}}
        self.assertEqual(self.read({"interface_index": WIFI}, None, lambda: [conn(WIFI, FIXED)], rows),
                         Verdict(True))
        self.assertEqual(self.read({"interface_index": WIFI}, None, lambda: [conn(WIFI, FREE)], rows),
                         Verdict(False))
        self.assertEqual(self.read(None, {"interface_index": WWAN}, lambda: [], rows), Verdict(True))

    def test_no_route_or_a_failing_reading_is_not_metered(self):
        self.assertEqual(self.read(None, None, lambda: [conn(WWAN, FIXED)], {}), Verdict(False))

        def broken():
            raise RuntimeError("COM went away")
        self.assertEqual(self.read({"interface_index": WIFI}, None, broken, {}), Verdict(False))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_the_real_reading_answers_without_raising(self):
        got = nc.read_connections()
        self.assertIsInstance(got, list)
        self.assertTrue(all(isinstance(c, Connection) for c in got))
        self.assertIsInstance(nc.read(), Verdict)


class RouteHelperTests(unittest.TestCase):
    def test_failing_calls_read_as_none(self):
        from app import winutil
        with mock.patch.object(winutil.ctypes, "WinDLL", side_effect=OSError("no iphlpapi")):
            self.assertIsNone(winutil.internet_route6_native())
            self.assertIsNone(winutil.interface_index_of_guid(bytes(16)))
        self.assertIsNone(winutil.internet_route6_native("not an address"))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_an_unknown_adapter_has_no_interface(self):
        from app import winutil
        self.assertIsNone(winutil.interface_index_of_guid(b"\xff" * 16))


if __name__ == "__main__":
    unittest.main()
