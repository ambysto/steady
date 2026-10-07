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
    """What the connected network offers, from `netsh wlan show interfaces` and `... networks mode=bssid`."""

    @staticmethod
    def system(ssid="HomeNet", state="connected", scan=()):
        wifi = None if state is None else SimpleNamespace(connected=state == "connected", ssid=ssid)
        entries = [SimpleNamespace(ssid=s, band=b) for s, b in scan]
        return winsys.WindowsSystem(ps=None, ps_json=None, run=None, wifi_state=lambda: wifi, scan=lambda: entries)

    def test_bands_of_the_connected_ssid_only(self):
        sys_ = self.system(scan=[("HomeNet", "2.4 GHz"), ("HomeNet", "5 GHz"), ("HomeNet", "5 GHz"),
                                 ("Neighbour", "6 GHz")])
        self.assertEqual(sys_.wifi_ssid_bands(), ("HomeNet", frozenset({"2.4 GHz", "5 GHz"})))

    def test_ssid_missing_from_the_scan_gives_no_bands(self):
        self.assertEqual(self.system(scan=[("Other", "5 GHz")]).wifi_ssid_bands(), ("HomeNet", frozenset()))

    def test_entries_without_a_band_are_ignored(self):
        self.assertEqual(self.system(scan=[("HomeNet", "")]).wifi_ssid_bands(), ("HomeNet", frozenset()))

    def test_none_when_not_connected(self):
        self.assertIsNone(self.system(state="disconnected").wifi_ssid_bands())
        self.assertIsNone(self.system(state=None).wifi_ssid_bands())
        self.assertIsNone(self.system(ssid="").wifi_ssid_bands())


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
