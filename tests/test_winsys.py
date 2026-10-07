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


NETSH_TCP_GLOBAL = """
Querying active state...

TCP Global Parameters
----------------------------------------------
Receive-Side Scaling State          : enabled 
Receive Window Auto-Tuning Level    : normal 
Add-On Congestion Control Provider  : default 
ECN Capability                      : enabled 
RFC 1323 Timestamps                 : disabled 
Initial RTO                         : 2000 
Receive Segment Coalescing State    : enabled 
Non Sack Rtt Resiliency             : disabled 
Max SYN Retransmissions             : 4 
Fast Open                           : enabled 
Fast Open Fallback                  : enabled 
HyStart                             : enabled 
Proportional Rate Reduction         : enabled 
Pacing Profile                      : off 

"""


class StackSwitchSystemTests(unittest.TestCase):
    """TCP ECN (netsh), RSC and the packet coalescing filter (PowerShell), SIC-88."""

    @staticmethod
    def netsh(text="", returncode=0, calls=None):
        def run(args, **kw):
            if calls is not None:
                calls.append(args)
            return SimpleNamespace(returncode=returncode, stdout=text.encode("cp437"))
        return winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=run)

    def test_ecn_is_parsed_from_the_real_netsh_output(self):
        calls = []
        self.assertEqual(self.netsh(NETSH_TCP_GLOBAL, calls=calls).tcp_ecn_get(), "enabled")
        self.assertEqual(calls, [["netsh", "int", "tcp", "show", "global"]])
        for value in ("disabled", "default"):
            text = NETSH_TCP_GLOBAL.replace("ECN Capability                      : enabled",
                                            f"ECN Capability                      : {value}")
            self.assertEqual(self.netsh(text).tcp_ecn_get(), value)

    def test_ecn_read_fails_loudly_on_unexpected_output(self):
        for text, code in (("", 0), ("The following command was not found: int tcp show global.", 1),
                           (NETSH_TCP_GLOBAL.replace("ECN Capability", "ECN Fähigkeit"), 0),
                           (NETSH_TCP_GLOBAL, 1)):
            with self.assertRaises(winsys.SystemReadError):
                self.netsh(text, code).tcp_ecn_get()

    def test_ecn_write_command_and_validation(self):
        calls = []
        sys_ = self.netsh(calls=calls)
        sys_.tcp_ecn_set("enabled")
        sys_.tcp_ecn_set("default")
        self.assertEqual(calls, [["netsh", "int", "tcp", "set", "global", "ecncapability=enabled"],
                                 ["netsh", "int", "tcp", "set", "global", "ecncapability=default"]])
        for bad in ("Enabled ", "enabled disabled", "on", "", "enabled & calc"):
            with self.assertRaises(ValueError):
                sys_.tcp_ecn_set(bad)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(winsys.SystemWriteError):
            self.netsh(returncode=1).tcp_ecn_set("enabled")

    def test_rsc_read_script_and_result(self):
        scripts = []

        def ps_json(script, **kw):
            scripts.append(script)
            return rows
        sys_ = winsys.WindowsSystem(ps=FakePs(), ps_json=ps_json, run=None)
        rows = [{"IPv4": False, "IPv6": False, "IPv4Supported": False, "IPv6Supported": False}]
        self.assertEqual(sys_.rsc_get("Wi-Fi"), {"ipv4": False, "ipv6": False, "ipv4_supported": False,
                                                 "ipv6_supported": False})
        self.assertIn("Get-NetAdapterRsc", scripts[0])
        self.assertIn("RscHardwareCapabilities", scripts[0])
        self.assertNotIn("Wi-Fi", scripts[0])               # only base64
        rows = [{"IPv4": True, "IPv6": False, "IPv4Supported": True, "IPv6Supported": True}]
        self.assertEqual(sys_.rsc_get("Wi-Fi")["ipv4"], True)
        rows = []                                          # "no object" (SilentlyContinue): driver without RSC
        self.assertIsNone(sys_.rsc_get("Wi-Fi"))

    def test_rsc_write_scripts(self):
        ps = FakePs()
        sys_ = winsys.WindowsSystem(ps=ps, ps_json=lambda *a, **k: [], run=None)
        sys_.rsc_set("Wi-Fi", False, False)
        sys_.rsc_set("Wi-Fi", True, None)
        sys_.rsc_set("Wi-Fi", None, None)
        self.assertEqual(len(ps.scripts), 2)                # nothing to do, nothing run
        self.assertEqual(ps.scripts[0].count("Disable-NetAdapterRsc"), 2)
        self.assertIn("-IPv4", ps.scripts[0])
        self.assertIn("-IPv6", ps.scripts[0])
        self.assertIn("Enable-NetAdapterRsc", ps.scripts[1])
        self.assertNotIn("-IPv6", ps.scripts[1])
        for nasty in NASTY[1:]:
            sys_.rsc_set(nasty, False, False)
            self.assertNotIn(nasty, ps.scripts[-1])

    def test_rsc_write_failure_is_a_write_error(self):
        def failing(script, **kw):
            raise winsys.PowerShellError("Disable-NetAdapterRsc : not supported")
        sys_ = winsys.WindowsSystem(ps=failing, ps_json=lambda *a, **k: [], run=None)
        with self.assertRaises(winsys.SystemWriteError):
            sys_.rsc_set("Wi-Fi", False, None)

    def test_packet_coalescing_read_and_write(self):
        scripts = []
        sys_ = winsys.WindowsSystem(ps=FakePs(), ps_json=lambda s, **k: scripts.append(s) or [{"Value": "Disabled"}],
                                    run=None)
        self.assertEqual(sys_.packet_coalescing_get(), "Disabled")
        self.assertIn("Get-NetOffloadGlobalSetting", scripts[0])
        self.assertIn("[string]", scripts[0])
        with self.assertRaises(winsys.SystemReadError):
            winsys.WindowsSystem(ps=FakePs(), ps_json=lambda *a, **k: [], run=None).packet_coalescing_get()
        ps = FakePs()
        sys_ = winsys.WindowsSystem(ps=ps, ps_json=lambda *a, **k: [], run=None)
        sys_.packet_coalescing_set("Disabled")
        sys_.packet_coalescing_set("Default")
        self.assertIn("Set-NetOffloadGlobalSetting -PacketCoalescingFilter Disabled ", ps.scripts[0])
        for bad in ("disabled", "Disabled; calc", "", "NotSet"):
            with self.assertRaises(ValueError):
                sys_.packet_coalescing_set(bad)
        self.assertEqual(len(ps.scripts), 2)


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


if __name__ == "__main__":
    unittest.main()
