import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from app import config, winutil

NETSH_SAMPLE = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : MediaTek Wi-Fi 6E MT7922 (RZ616) 160MHz Wireless LAN Card
    GUID                   : 11111111-2222-3333-4444-555555555555
    Physical address       : aa:bb:cc:dd:ee:ff
    State                  : connected
    SSID                   : Home
    BSSID                  : 11:22:33:44:55:66
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA3-Personal
    Channel                : 36
    Receive rate (Mbps)    : 6
    Transmit rate (Mbps)   : 1201
    Signal                 : 60%
    Rssi                   : -73
    Profile                : Home
"""

NETSH_TWO = """
There are 2 interfaces on the system:

    Name                   : Wi-Fi
    State                  : disconnected

    Name                   : Wi-Fi 2
    State                  : connected
    SSID                   : Lab
    AP BSSID               : de:ad:be:ef:00:01
    Signal                 : 88%
    Rssi                   : -55
"""


class ParseNetshTests(unittest.TestCase):
    def test_single_interface(self):
        (iface,) = winutil.parse_netsh_interfaces(NETSH_SAMPLE)
        self.assertEqual(iface["Name"], "Wi-Fi")
        self.assertEqual(iface["Physical address"], "aa:bb:cc:dd:ee:ff")  # inner colons kept
        self.assertEqual(iface["Signal"], "60%")

    def test_header_line_is_not_an_interface(self):
        self.assertEqual(winutil.parse_netsh_interfaces("There is 1 interface on the system:\n"), [])

    def test_multiple_interfaces_do_not_leak_keys(self):
        first, second = winutil.parse_netsh_interfaces(NETSH_TWO)
        self.assertEqual(first["State"], "disconnected")
        self.assertNotIn("SSID", first)
        self.assertEqual(second["SSID"], "Lab")

    def test_first_occurrence_wins(self):
        text = "    Name : Wi-Fi\n    State : connected\n    State : other\n"
        (iface,) = winutil.parse_netsh_interfaces(text)
        self.assertEqual(iface["State"], "connected")

    def test_empty(self):
        self.assertEqual(winutil.parse_netsh_interfaces(""), [])


class WifiStateTests(unittest.TestCase):
    def test_fields_normalised(self):
        (fields,) = winutil.parse_netsh_interfaces(NETSH_TWO.split("    Name                   : Wi-Fi 2")[0] + "")
        st = winutil.wifi_state_from(fields)
        self.assertFalse(st.connected)
        self.assertIsNone(st.rssi)

    def test_connected_state(self):
        st = winutil.wifi_state_from(winutil.parse_netsh_interfaces(NETSH_TWO)[1])
        self.assertTrue(st.connected)
        self.assertEqual((st.signal, st.rssi, st.bssid), (88, -55, "de:ad:be:ef:00:01"))

    def test_rates_and_channel(self):
        st = winutil.wifi_state_from(winutil.parse_netsh_interfaces(NETSH_SAMPLE)[0])
        self.assertEqual((st.channel, st.rx_mbps, st.tx_mbps, st.radio_type), (36, 6, 1201, "802.11ax"))
        self.assertEqual(st.bssid, "")  # sample uses "BSSID", not "AP BSSID"

    def test_bad_numbers_become_none(self):
        self.assertIsNone(winutil._to_int(""))
        self.assertIsNone(winutil._to_int(None))
        self.assertIsNone(winutil._to_int("n/a"))


SCAN_SAMPLE = """
Interface name : Wi-Fi
There are 4 networks currently visible.

SSID 1 : NeighborB 5G
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : C4:2C:7B:00:50:29
         Signal             : 72%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 36
         Bss Load:
             Connected Stations:         0
             Channel Utilization:        9 (3 %)
         Basic rates (Mbps) : 6 12 24

SSID 2 :
    Network type            : Infrastructure
    BSSID 1                 : 82:2d:1a:0d:0d:5d
         Signal             : 3%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 124
    BSSID 2                 : 6a:77:da:0f:0f:58
         Signal             : 41%
         Radio type         : 802.11ac
         Channel            : 11

SSID 3 : HomeNet
    Network type            : Infrastructure
    BSSID 1                 : 02:5e:00:9a:40:20
         Signal             : 86%
         Radio type         : 802.11be
         Band               : 2.4 GHz
         Channel            : 11
"""

PNPUTIL_SAMPLE = """Microsoft PnP Utility

Published Name:     oem18.inf
Original Name:      mtkwl6ex.inf
Provider Name:      MediaTek, Inc.
Class Name:         Net
Class GUID:         {4d36e972-e325-11ce-bfc1-08002be10318}
Driver Version:     12/05/2025 25.40.2.585
Signer Name:        Microsoft Windows Hardware Compatibility Publisher

Published Name:     oem32.inf
Original Name:      netaapl64.inf
Provider Name:      Apple
Class Name:         Net
Driver Version:     07/15/2013 1.8.5.1
Attributes:         Legacy
                    Attested
"""


class ScanParseTests(unittest.TestCase):
    def setUp(self):
        self.entries = winutil.parse_netsh_networks(SCAN_SAMPLE)

    def test_one_entry_per_bssid(self):
        self.assertEqual(len(self.entries), 4)

    def test_fields_and_lowercased_bssid(self):
        e = self.entries[0]
        self.assertEqual((e.ssid, e.bssid, e.signal, e.radio_type, e.band, e.channel),
                         ("NeighborB 5G", "c4:2c:7b:00:50:29", 72, "802.11ax", "5 GHz", 36))

    def test_hidden_ssid_and_multiple_bssids_share_ssid(self):
        hidden = [e for e in self.entries if e.ssid == ""]
        self.assertEqual([e.channel for e in hidden], [124, 11])

    def test_band_derived_from_channel_when_missing(self):
        self.assertEqual(self.entries[2].band, "2.4 GHz")  # channel 11, no Band line

    def test_be_radio_and_nested_bss_load_do_not_leak(self):
        self.assertEqual(self.entries[3].radio_type, "802.11be")
        self.assertEqual(self.entries[0].signal, 72)  # "Channel Utilization : 9 (3 %)" must not override

    def test_empty(self):
        self.assertEqual(winutil.parse_netsh_networks(""), [])
        self.assertEqual(winutil.parse_netsh_networks("The Wireless AutoConfig Service is not running."), [])


class PnputilParseTests(unittest.TestCase):
    def test_parse(self):
        a, b = winutil.parse_pnputil_drivers(PNPUTIL_SAMPLE)
        self.assertEqual((a.published, a.original, a.provider, a.version, a.date),
                         ("oem18.inf", "mtkwl6ex.inf", "MediaTek, Inc.", "25.40.2.585", "2025-12-05"))
        self.assertEqual((b.provider, b.version, b.date), ("Apple", "1.8.5.1", "2013-07-15"))

    def test_empty_and_header_only(self):
        self.assertEqual(winutil.parse_pnputil_drivers(""), [])
        self.assertEqual(winutil.parse_pnputil_drivers("Microsoft PnP Utility\n"), [])


class AdapterTests(unittest.TestCase):
    def test_is_wifi_adapter(self):
        self.assertTrue(winutil.is_wifi_adapter({"PhysicalMediaType": "Native 802.11"}))
        self.assertFalse(winutil.is_wifi_adapter({"PhysicalMediaType": "802.3"}))
        self.assertFalse(winutil.is_wifi_adapter({}))


class EncodeTests(unittest.TestCase):
    def test_roundtrip_utf16le(self):
        script = "Write-Output 'Xin chào'"
        decoded = base64.b64decode(winutil.encode_command(script)).decode("utf-16-le")
        self.assertEqual(decoded, script)


@unittest.skipUnless(sys.platform == "win32", "needs Windows PowerShell")
class PowerShellIntegrationTests(unittest.TestCase):
    """Read-only smoke tests against the real powershell.exe."""

    def test_json_wraps_single_object_in_list(self):
        self.assertEqual(winutil.run_powershell_json("[pscustomobject]@{a=1}"), [{"a": 1}])

    def test_json_empty_pipeline(self):
        self.assertEqual(winutil.run_powershell_json("@() | Where-Object { $false }"), [])

    def test_unicode_survives(self):
        self.assertEqual(winutil.run_powershell_json("'Xin chào'"), ["Xin chào"])

    def test_error_raises(self):
        with self.assertRaises(winutil.PowerShellError):
            winutil.run_powershell("exit 3")

    def test_timeout_raises(self):
        with self.assertRaises(winutil.PowerShellError):
            winutil.run_powershell("Start-Sleep -Seconds 10", timeout=1)

    def test_is_admin_returns_bool(self):
        self.assertIsInstance(winutil.is_admin(), bool)

    def test_diag_events_shape_and_no_hidden_errors(self):
        ev = winutil.get_diag_events(days=3)
        for key in ("disconnects", "limited_connectivity", "ihv_stops", "port_exhaustion", "errors"):
            self.assertIsInstance(ev[key], list, key)
        self.assertEqual(ev["errors"], [], "an unreadable event log must be reported, not treated as empty")
        for d in ev["disconnects"]:
            self.assertIsInstance(d["ts"], int)
            self.assertIsInstance(d["reason"], str)

    def test_adapters_all_include_driver_date_as_iso_string(self):
        for a in winutil.get_adapters(physical_only=False):
            if a["DriverDate"]:
                self.assertRegex(a["DriverDate"], r"^\d{4}-\d{2}-\d{2}$")

    def test_scan_and_driver_store_do_not_raise(self):
        self.assertIsInstance(winutil.get_scan(), list)
        self.assertIsInstance(winutil.get_driver_store(), list)

    def test_native_default_route_matches_powershell(self):
        native = winutil.default_route_native()
        uplink = winutil.get_uplink()
        if uplink is None:
            self.assertIsNone(native)  # offline: nothing to compare
            return
        self.assertIsNotNone(native)
        self.assertEqual(native["gateway"], uplink["gateway"])
        self.assertEqual(native["interface_index"], uplink["interface_index"])

    def test_internet_route_is_a_real_interface(self):
        route = winutil.internet_route_native()
        if route is None:
            self.skipTest("no route to the Internet (offline)")
        self.assertGreater(route["interface_index"], 0)
        self.assertTrue(route["gateway"] is None or route["gateway"].count(".") == 3)

    def test_default_route_is_found_while_a_full_tunnel_vpn_hides_it(self):
        # No skip: this is the case the monitor must handle with the VPN on or off.
        native = winutil.default_route_native()
        uplink = winutil.get_uplink()
        if uplink is None:
            self.skipTest("offline")
        self.assertIsNotNone(native)
        self.assertEqual((native["gateway"], native["interface_index"]),
                         (uplink["gateway"], uplink["interface_index"]))

    def test_get_gateway_uses_native_without_spawning_powershell(self):
        from unittest import mock
        with mock.patch.object(winutil, "default_route_native", return_value={"gateway": "10.1.1.1"}), \
                mock.patch.object(winutil, "run_powershell_json", side_effect=AssertionError("spawned")):
            self.assertEqual(winutil.get_gateway(), "10.1.1.1")

    def test_get_gateway_falls_back_to_powershell(self):
        from unittest import mock
        with mock.patch.object(winutil, "default_route_native", return_value=None), \
                mock.patch.object(winutil, "get_uplink", return_value={"gateway": "10.2.2.2"}):
            self.assertEqual(winutil.get_gateway(), "10.2.2.2")

    def test_get_gateway_none_when_everything_fails(self):
        from unittest import mock
        with mock.patch.object(winutil, "default_route_native", return_value=None), \
                mock.patch.object(winutil, "get_uplink", side_effect=winutil.PowerShellError("x")):
            self.assertIsNone(winutil.get_gateway())


def ip(text):
    import socket
    return int.from_bytes(socket.inet_aton(text), "little")


def forward_table(*rows):
    """rows: (dest, mask, next_hop, if_index, metric) as text/ints -> bytes of a MIB_IPFORWARDTABLE."""
    def dword(v):
        return (ip(v) if isinstance(v, str) else v).to_bytes(4, "little")
    out = len(rows).to_bytes(4, "little")
    for dest, mask, hop, index, metric in rows:
        fields = [dword(dest), dword(mask), dword(0), dword(hop), dword(index), dword(4), dword(3), dword(0),
                  dword(0), dword(metric), dword(0), dword(0), dword(0), dword(0)]
        out += b"".join(fields)
    return out


class ForwardTableTests(unittest.TestCase):
    WIFI = ("0.0.0.0", "0.0.0.0", "192.168.3.1", 6, 2)
    TUNNEL_LOW = ("0.0.0.0", "128.0.0.0", "0.0.0.0", 44, 5)
    TUNNEL_HIGH = ("128.0.0.0", "128.0.0.0", "0.0.0.0", 44, 5)
    LOCAL = ("192.168.3.0", "255.255.255.0", "0.0.0.0", 6, 281)

    def best(self, *rows):
        return winutil.best_default_route(winutil.parse_forward_table(forward_table(*rows)))

    def test_parses_every_row(self):
        rows = winutil.parse_forward_table(forward_table(self.WIFI, self.LOCAL))
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["next_hop"], rows[0]["interface_index"], rows[0]["metric"]),
                         (ip("192.168.3.1"), 6, 2))

    def test_the_default_route_is_found_beside_a_wireguard_style_split(self):
        route = self.best(self.TUNNEL_LOW, self.TUNNEL_HIGH, self.WIFI, self.LOCAL)
        self.assertEqual(route, {"gateway": "192.168.3.1", "interface_index": 6, "metric": 2})

    def test_the_lowest_metric_wins_between_default_routes(self):
        wired = ("0.0.0.0", "0.0.0.0", "10.0.0.1", 3, 25)
        self.assertEqual(self.best(wired, self.WIFI)["interface_index"], 6)
        self.assertEqual(self.best(self.WIFI, ("0.0.0.0", "0.0.0.0", "10.0.0.1", 3, 1))["interface_index"], 3)

    def test_an_on_link_default_route_and_no_default_route_are_not_gateways(self):
        self.assertIsNone(self.best(("0.0.0.0", "0.0.0.0", "0.0.0.0", 44, 5), self.LOCAL))
        self.assertIsNone(self.best(self.LOCAL))
        self.assertIsNone(self.best())

    def test_a_truncated_table_does_not_raise(self):
        data = forward_table(self.WIFI, self.LOCAL)
        self.assertEqual(len(winutil.parse_forward_table(data[:-10])), 1)
        self.assertEqual(winutil.parse_forward_table(b""), [])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("STABLEINTERNET_DATA")
        os.environ["STABLEINTERNET_DATA"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("STABLEINTERNET_DATA", None)
        else:
            os.environ["STABLEINTERNET_DATA"] = self._old
        self._tmp.cleanup()

    def test_defaults_when_missing(self):
        self.assertEqual(config.load_settings(), config.DEFAULT_SETTINGS)

    def test_partial_settings_merge_with_defaults(self):
        config.save_settings({"targets": {"quad9": "9.9.9.9"}, "watchdog": {"enabled": True}})
        s = config.load_settings()
        self.assertEqual(s["targets"]["quad9"], "9.9.9.9")
        self.assertEqual(s["targets"]["google"], "8.8.8.8")  # default kept
        self.assertTrue(s["watchdog"]["enabled"])
        self.assertEqual(s["retention_days"], 30)

    def test_corrupt_settings_fall_back_to_defaults(self):
        config.settings_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(config.load_settings(), config.DEFAULT_SETTINGS)

    def test_defaults_not_mutated(self):
        s = config.load_settings()
        s["targets"]["x"] = "1.2.3.4"
        self.assertNotIn("x", config.DEFAULT_SETTINGS["targets"])

    def test_backup_roundtrip(self):
        config.save_backup({"wifi_power_saving": {"value": "Auto"}})
        self.assertEqual(config.load_backup()["wifi_power_saving"], {"value": "Auto"})

    def test_backup_missing_is_empty(self):
        self.assertEqual(config.load_backup(), {})

    def test_corrupt_backup_raises_instead_of_resetting(self):
        config.backup_path().write_text("{not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            config.load_backup()

    def test_non_object_backup_raises(self):
        config.backup_path().write_text("[1]", encoding="utf-8")
        with self.assertRaises(ValueError):
            config.load_backup()

    def test_atomic_write_leaves_no_temp_files(self):
        config.save_backup({"a": 1})
        names = [p.name for p in Path(self._tmp.name).iterdir()]
        self.assertEqual(names, ["backup.json"])


if __name__ == "__main__":
    unittest.main()
