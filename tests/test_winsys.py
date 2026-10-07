import re
import sys
import unittest
from types import SimpleNamespace

from app import winsys
from app.winutil import run_powershell

NASTY = ["Wi-Fi", "a'b", "a’b‘c", "$(Remove-Item x)", "`n`r", 'q"q', "Tên card ‑ Wi‑Fi 2", "; exit 1"]


class FakePs:
    def __init__(self, result=""):
        self.scripts = []
        self.result = result

    def __call__(self, script, **kw):
        self.scripts.append(script)
        return self.result


class AsListTests(unittest.TestCase):
    def test_powershell_51_array_wrapper_is_unwrapped(self):
        self.assertEqual(winsys._as_list({"value": ["Disabled", "Auto"], "Count": 2}), ["Disabled", "Auto"])
        self.assertEqual(winsys._as_list(["0"]), ["0"])
        self.assertEqual(winsys._as_list("0"), ["0"])
        self.assertEqual(winsys._as_list(None), [])


class ProtocolTests(unittest.TestCase):
    def test_the_real_system_implements_every_operation(self):
        # Code is written and tested against System with fakes; a method that exists only on the
        # Protocol would pass every test and fail on the real machine.
        wanted = [n for n in vars(winsys.System) if not n.startswith("_") and callable(getattr(winsys.System, n))]
        self.assertIn("interface_metric_set", wanted)
        self.assertEqual([n for n in wanted if n not in vars(winsys.WindowsSystem)], [])

    def test_interface_metric_scripts(self):
        ps = FakePs()
        sys_ = winsys.WindowsSystem(ps=ps, ps_json=lambda *a, **k: [], run=None)
        sys_.interface_metric_set(12, 5)
        sys_.interface_metric_set("12", None)
        self.assertIn("-InterfaceIndex 12 -AddressFamily IPv4 -InterfaceMetric 5", ps.scripts[0])
        self.assertIn("-AutomaticMetric Enabled", ps.scripts[1])
        for bad in (0, 10000):
            with self.assertRaises(ValueError):
                sys_.interface_metric_set(12, bad)
        with self.assertRaises(ValueError):
            sys_.interface_metric_set("12; rm x", 5)


class SsidBandsTests(unittest.TestCase):
    """What an adapter's connection looks like, from `netsh wlan show interfaces` and `... networks mode=bssid`."""

    @staticmethod
    def iface(name="Wi-Fi", state="connected", ssid="HomeNet", bssid="02:5e:00:9a:40:24", band="2.4 GHz"):
        return SimpleNamespace(interface=name, connected=state == "connected", ssid=ssid, bssid=bssid, band=band)

    @staticmethod
    def system(*ifaces, scan=()):
        entries = [SimpleNamespace(ssid=s, bssid=b, band=band) for s, b, band in scan]
        return winsys.WindowsSystem(ps=None, ps_json=None, run=None, wifi_states=lambda: list(ifaces),
                                    scan=lambda: entries)

    def test_current_band_and_the_bands_of_the_same_network(self):
        sys_ = self.system(self.iface(), scan=[("HomeNet", "02:5e:00:9a:40:24", "2.4 GHz"),
                                               ("HomeNet", "02:5e:00:9a:40:25", "5 GHz"),
                                               ("HomeNet", "02:5e:00:9a:40:26", "5 GHz"),
                                               ("Neighbour", "02:5e:00:11:22:33", "6 GHz")])
        self.assertEqual(sys_.wifi_ssid_bands("Wi-Fi"),
                         winsys.WifiBands("HomeNet", "2.4 GHz", frozenset({"2.4 GHz", "5 GHz"})))

    def test_band_comes_from_netsh_when_it_says_so_not_from_the_channel(self):
        sys_ = self.system(self.iface(band="6 GHz"), scan=[("HomeNet", "02:5e:00:9a:40:24", "6 GHz")])
        self.assertEqual(sys_.wifi_ssid_bands("Wi-Fi").current_band, "6 GHz")

    def test_band_falls_back_to_the_scan_entry_of_the_connected_bssid(self):
        sys_ = self.system(self.iface(band=""), scan=[("HomeNet", "02:5E:00:9A:40:24", "5 GHz")])
        self.assertEqual(sys_.wifi_ssid_bands("Wi-Fi").current_band, "5 GHz")

    def test_band_unknown_when_neither_netsh_nor_the_scan_says(self):
        got = self.system(self.iface(band=""), scan=[("Other", "02:5e:00:00:00:01", "5 GHz")]).wifi_ssid_bands("Wi-Fi")
        self.assertEqual(got, winsys.WifiBands("HomeNet", "", frozenset()))

    def test_hidden_network_is_matched_by_the_connected_bssid(self):
        sys_ = self.system(self.iface(band=""), scan=[("", "02:5e:00:9a:40:24", "2.4 GHz"),
                                                      ("", "02:5e:00:ff:ff:ff", "5 GHz")])
        self.assertEqual(sys_.wifi_ssid_bands("Wi-Fi"),
                         winsys.WifiBands("HomeNet", "2.4 GHz", frozenset({"2.4 GHz"})))

    def test_the_interface_with_the_adapters_name_is_used(self):
        sys_ = self.system(self.iface(name="Wi-Fi 2", ssid="Other", band="5 GHz"), self.iface(name="Wi-Fi"))
        self.assertEqual(sys_.wifi_ssid_bands("wi-fi").ssid, "HomeNet")
        self.assertEqual(sys_.wifi_ssid_bands("Wi-Fi 2").current_band, "5 GHz")

    def test_entries_without_a_band_are_ignored(self):
        got = self.system(self.iface(), scan=[("HomeNet", "02:5e:00:9a:40:99", "")]).wifi_ssid_bands("Wi-Fi")
        self.assertEqual(got.bands, frozenset())

    def test_none_only_when_that_interface_is_not_connected(self):
        self.assertIsNone(self.system(self.iface(state="disconnected")).wifi_ssid_bands("Wi-Fi"))

    def test_an_interface_netsh_does_not_list_is_unknown_not_disconnected(self):
        self.assertEqual(self.system().wifi_ssid_bands("Wi-Fi"), winsys.WifiBands("", "", frozenset()))
        self.assertEqual(self.system(self.iface(name="Ethernet")).wifi_ssid_bands("Wi-Fi"),
                         winsys.WifiBands("", "", frozenset()))


class ScriptConstructionTests(unittest.TestCase):
    """No caller-supplied string may appear raw in a PowerShell script."""

    def test_write_scripts_only_carry_base64(self):
        ps = FakePs()
        sys_ = winsys.WindowsSystem(ps=ps, ps_json=lambda *a, **k: [], run=None)
        for nasty in NASTY:
            sys_.adapter_property_set(nasty, nasty, nasty)
            sys_.adapter_property_reset(nasty, nasty)
            sys_.binding_set(nasty, "ms_tcpip6", False)
        for script in ps.scripts:
            for nasty in NASTY[1:]:
                self.assertNotIn(nasty, script)

    def test_binding_component_is_validated(self):
        sys_ = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=None)
        for bad in ("ms_tcpip6; rm x", "a b", ""):
            with self.assertRaises(ValueError):
                sys_.binding_set("Wi-Fi", bad, True)
            with self.assertRaises(ValueError):
                sys_.binding_get("Wi-Fi", bad)

    def test_power_guids_are_validated(self):
        sys_ = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=lambda *a, **k: None)
        with self.assertRaises(ValueError):
            sys_.power_set("SCHEME_CURRENT & calc", "ee12f906-d277-404b-b6da-e5fa1a576df5", 0, 0)
        with self.assertRaises(ValueError):
            sys_.power_get("501a4d13-42af-4429-9fd1-a8218c268e20", "x")

    def test_hklm_paths_are_validated(self):
        for bad in ("", r"\SYSTEM\x", r"SYSTEM\..\SAM"):
            with self.assertRaises(ValueError):
                winsys._check_hklm_path(bad)

    def test_power_set_runs_ac_dc_then_setactive_and_stops_on_error(self):
        calls = []

        def run(args, **kw):
            calls.append(args[1])
            return SimpleNamespace(returncode=1 if args[1] == "/setdcvalueindex" else 0, stdout=b"")
        sys_ = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=run)
        with self.assertRaises(winsys.SystemWriteError):
            sys_.power_set("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 0, 0)
        self.assertEqual(calls, ["/setacvalueindex", "/setdcvalueindex"])  # never activated a half-set plan

    def test_power_get_parses_and_rejects_partial_output(self):
        text = ("Power Setting GUID: ee12f906 (Link State Power Management)\n"
                "    Current AC Power Setting Index: 0x00000001\n    Current DC Power Setting Index: 0x00000002\n")
        sys_ = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [],
                                    run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=text.encode()))
        self.assertEqual(sys_.power_get("501a4d13-42af-4429-9fd1-a8218c268e20",
                                        "ee12f906-d277-404b-b6da-e5fa1a576df5"), (1, 2))
        partial = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [],
                                       run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=text.split("    Current DC")[0].encode()))
        with self.assertRaises(winsys.SystemReadError):
            partial.power_get("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5")

    def test_registry_dword_range(self):
        sys_ = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=None)
        with self.assertRaises(ValueError):
            sys_.registry_set_dword(r"SOFTWARE\x", "v", -1)


NETSH_GLOBAL = (b"\r\nQuerying active state...\r\n\r\nTCP Global Parameters\r\n----------------------------------------------\r\n"
                b"Receive-Side Scaling State          : enabled\r\nECN Capability                      : Enabled \r\n"
                b"Receive Segment Coalescing State    : enabled\r\n")


class NetworkStackSystemTests(unittest.TestCase):
    """The system calls behind tcp_ecn, rsc_off, packet_coalescing_off and dns_fastest."""

    @staticmethod
    def system(ps=None, rows=None, run=None, route=lambda: {"interface_index": 6, "gateway": "192.168.1.1"}):
        return winsys.WindowsSystem(ps=ps or FakePs(), ps_json=lambda script, **k: rows or [],
                                    run=run or (lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"Ok.")), route=route)

    def test_netsh_table_is_parsed_whatever_the_case_and_padding(self):
        table = winsys.parse_netsh_table(NETSH_GLOBAL.decode())
        self.assertEqual(table["ECN Capability"], "enabled")
        self.assertEqual(table["Receive-Side Scaling State"], "enabled")
        self.assertNotIn("TCP Global Parameters", table)

    def test_tcp_global_read_and_write_go_through_netsh(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=NETSH_GLOBAL)
        sys_ = self.system(run=run)
        self.assertEqual(sys_.tcp_global_get("ecncapability"), "enabled")
        sys_.tcp_global_set("ecncapability", "disabled")
        self.assertEqual(calls, [["netsh", "int", "tcp", "show", "global"],
                                 ["netsh", "int", "tcp", "set", "global", "ecncapability=disabled"]])

    def test_tcp_global_is_validated_and_a_failed_netsh_is_reported(self):
        sys_ = self.system()
        for bad in ("ecncapability; calc", "timestamps", ""):
            with self.assertRaises(ValueError):
                sys_.tcp_global_get(bad)
        for bad in ("enabled & calc", "on"):
            with self.assertRaises(ValueError):
                sys_.tcp_global_set("ecncapability", bad)
        failing = self.system(run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"The requested operation requires elevation."))
        with self.assertRaises(winsys.SystemWriteError):
            failing.tcp_global_set("ecncapability", "enabled")
        with self.assertRaises(winsys.SystemReadError):
            failing.tcp_global_get("ecncapability")

    def test_a_setting_netsh_does_not_list_is_none(self):
        sys_ = self.system(run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"TCP Global Parameters\r\n"))
        self.assertIsNone(sys_.tcp_global_get("ecncapability"))

    def test_rsc_read_and_write(self):
        row = {"IPv4": True, "IPv6": False, "IPv4Supported": True, "IPv6Supported": False}
        sys_ = self.system(rows=[row])
        self.assertEqual(sys_.rsc_get("Wi-Fi"), {"ipv4": True, "ipv6": False, "ipv4_supported": True, "ipv6_supported": False})
        self.assertIsNone(self.system().rsc_get("Wi-Fi"))
        ps = FakePs()
        self.system(ps=ps).rsc_set("Wi-Fi", False, None)
        self.system(ps=ps).rsc_set("Wi-Fi", True, True)
        self.assertEqual(len(ps.scripts), 3)                          # a family set to None is not touched
        self.assertIn("Disable-NetAdapterRsc", ps.scripts[0])
        self.assertIn("-IPv4", ps.scripts[0])
        self.assertNotIn("-IPv6", ps.scripts[0])
        self.assertTrue(ps.scripts[1].startswith("Enable-NetAdapterRsc") and ps.scripts[2].startswith("Enable-NetAdapterRsc"))

    def test_rsc_adapter_name_only_reaches_powershell_as_base64(self):
        ps = FakePs()
        for nasty in NASTY:
            self.system(ps=ps).rsc_set(nasty, False, False)
        for script in ps.scripts:
            for nasty in NASTY[1:]:
                self.assertNotIn(nasty, script)

    def test_offload_setting_read_and_write(self):
        ps = FakePs("Disabled\r\n")
        sys_ = self.system(ps=ps)
        self.assertEqual(sys_.offload_global_get("PacketCoalescingFilter"), "Disabled")
        sys_.offload_global_set("PacketCoalescingFilter", "Enabled")
        self.assertIn("Set-NetOffloadGlobalSetting -PacketCoalescingFilter Enabled", ps.scripts[-1])
        self.assertIsNone(self.system(ps=FakePs("")).offload_global_get("PacketCoalescingFilter"))
        for bad in ("PacketCoalescingFilter; calc", "ReceiveSideScaling"):
            with self.assertRaises(ValueError):
                sys_.offload_global_get(bad)
        with self.assertRaises(ValueError):
            sys_.offload_global_set("PacketCoalescingFilter", "Disabled; calc")

    def test_dns_interface_reads_the_uplink_and_spots_a_vpn(self):
        row = {"Alias": "Wi-Fi", "StaticV4": True, "StaticV6": False, "Servers": {"value": ["1.1.1.1", "8.8.8.8"], "Count": 2},
               "Suffix": "", "Domain": False, "Up": ["Wi-Fi TP-Link Wi-Fi 6 PCIe Adapter", "WARP Cloudflare WARP Interface Tunnel"]}
        info = self.system(rows=[row]).dns_interface()
        self.assertEqual(info, {"index": 6, "alias": "Wi-Fi", "servers": ["1.1.1.1", "8.8.8.8"], "static": True,
                                "static_v6": False, "suffix": "", "domain_joined": False, "vpn_up": True})
        row["Up"] = ["Wi-Fi TP-Link Wi-Fi 6 PCIe Adapter"]
        self.assertFalse(self.system(rows=[row]).dns_interface()["vpn_up"])

    def test_dns_interface_is_none_without_an_uplink(self):
        self.assertIsNone(self.system(route=lambda: None).dns_interface())
        self.assertIsNone(self.system(rows=[]).dns_interface())

    def test_doh_list_is_read_into_flags(self):
        rows = [{"ServerAddress": "1.1.1.1", "DohTemplate": "https://cloudflare-dns.com/dns-query", "AutoUpgrade": True,
                 "Fallback": False}]
        self.assertEqual(self.system(rows=rows).doh_get(),
                         {"1.1.1.1": {"template": "https://cloudflare-dns.com/dns-query", "auto_upgrade": True,
                                      "fallback_to_udp": False}})

    def test_dns_and_doh_writes(self):
        ps = FakePs()
        sys_ = self.system(ps=ps)
        sys_.dns_servers_set(6, ["1.1.1.1", "8.8.8.8"])
        sys_.dns_servers_set("6", None)
        sys_.doh_set("1.1.1.1", "https://cloudflare-dns.com/dns-query", True, False)
        sys_.doh_remove("1.1.1.1")
        self.assertIn("Set-DnsClientServerAddress -InterfaceIndex 6 -ServerAddresses '1.1.1.1','8.8.8.8'", ps.scripts[0])
        self.assertIn("-ResetServerAddresses", ps.scripts[1])
        self.assertIn("Set-DnsClientDohServerAddress -ServerAddress '1.1.1.1' -AutoUpgrade $true -AllowFallbackToUdp $false",
                      ps.scripts[2])
        self.assertIn("Add-DnsClientDohServerAddress -ServerAddress '1.1.1.1' -DohTemplate 'https://cloudflare-dns.com/dns-query'",
                      ps.scripts[2])
        self.assertIn("Remove-DnsClientDohServerAddress", ps.scripts[3])

    def test_dns_and_doh_arguments_are_validated(self):
        ps = FakePs()
        sys_ = self.system(ps=ps)
        for bad in (["1.1.1.1'; calc; '"], ["not an ip"], ["1.1.1.1", ""], []):
            with self.assertRaises(ValueError):
                sys_.dns_servers_set(6, bad)
        with self.assertRaises(ValueError):
            sys_.dns_servers_set("6; calc", ["1.1.1.1"])
        for template in ("http://insecure.example/dns", "https://x.example/'; calc", "javascript:alert(1)", ""):
            with self.assertRaises(ValueError):
                sys_.doh_set("1.1.1.1", template, True, True)
        for address in ("1.1.1.1'; calc", "one.one.one.one"):
            with self.assertRaises(ValueError):
                sys_.doh_set(address, "https://cloudflare-dns.com/dns-query", True, True)
            with self.assertRaises(ValueError):
                sys_.doh_remove(address)
        self.assertEqual(ps.scripts, [])                                # nothing reached PowerShell


@unittest.skipUnless(sys.platform == "win32", "needs PowerShell")
class PsLiteralRoundTripTests(unittest.TestCase):
    def test_nasty_strings_survive_unchanged(self):
        exprs = "; ".join(f"Write-Output {winsys.ps_literal(s)}" for s in NASTY)
        out = run_powershell(exprs).splitlines()
        self.assertEqual(out, NASTY)


@unittest.skipUnless(sys.platform == "win32", "reads the real machine")
class RealReadsTests(unittest.TestCase):
    """Read-only. Nothing in this class writes to the machine."""

    @classmethod
    def setUpClass(cls):
        cls.sys = winsys.WindowsSystem()
        cls.adapter = cls.sys.wifi_adapter_name()
        if cls.adapter is None:
            raise unittest.SkipTest("no Wi-Fi adapter")

    def test_adapter_properties(self):
        props = self.sys.adapter_properties(self.adapter)
        self.assertTrue(props)
        for p in props:
            self.assertIsInstance(p["RegistryValue"], list)
            self.assertEqual(len(p["ValidDisplayValues"]), len(p["ValidRegistryValues"]))
            for v in p["ValidDisplayValues"] + p["RegistryValue"]:
                self.assertNotIn("Count", v, "PowerShell 5.1 array wrapper leaked into a value")
        enumerated = [p for p in props if p["ValidDisplayValues"]]
        self.assertTrue(enumerated, "no property with an enumerated value list was parsed")

    def test_class_key_and_pnpcapabilities(self):
        key = self.sys.adapter_class_key(self.adapter)
        self.assertRegex(key, r"\\\d{4}$")
        value = self.sys.registry_get(key, "PnPCapabilities")
        self.assertTrue(value is None or isinstance(value, int))

    def test_missing_registry_value_is_none(self):
        self.assertIsNone(self.sys.registry_get(r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters",
                                                "StableInternetDoesNotExist"))

    def test_power_and_binding(self):
        ac, dc = self.sys.power_get("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5")
        self.assertIsInstance(ac, int)
        self.assertIsInstance(self.sys.binding_get(self.adapter, "ms_tcpip6"), bool)
        self.assertIsNone(self.sys.binding_get(self.adapter, "ms_doesnotexist"))

    def test_network_stack_settings(self):
        self.assertIn(self.sys.tcp_global_get("ecncapability"), ("enabled", "disabled", "default"))
        self.assertIn(self.sys.offload_global_get("PacketCoalescingFilter"), ("Enabled", "Disabled", "Default"))
        rsc = self.sys.rsc_get(self.adapter)
        self.assertTrue(rsc is None or {"ipv4", "ipv6", "ipv4_supported", "ipv6_supported"} == set(rsc))

    def test_dns_configuration_of_the_uplink(self):
        info = self.sys.dns_interface()
        if info is None:
            self.skipTest("no uplink")
        self.assertEqual(set(info), {"index", "alias", "servers", "static", "static_v6", "suffix", "domain_joined",
                                     "vpn_up"})
        self.assertIsInstance(info["servers"], list)
        for entry in self.sys.doh_get().values():
            self.assertEqual(set(entry), {"template", "auto_upgrade", "fallback_to_udp"})


if __name__ == "__main__":
    unittest.main()
