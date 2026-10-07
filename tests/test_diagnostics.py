import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime

from app import diagnostics, dnsprobe, i18n, winutil
from tests.localized import DiagnosticsIn

d = DiagnosticsIn("vi")   # evaluate_* return Vietnamese text; see tests/localized.py
from app.storage import Storage

NOW = float(datetime(2026, 10, 4, 12, 0).astimezone().timestamp())
H = 3600


def wifi(rssi=-55, state="connected", ssid="Home_5G", bssid="02:5e:00:9a:40:24", channel=40, radio="802.11ax",
         rx=1201, tx=1201):
    return winutil.WifiState("Wi-Fi", state, ssid, bssid, radio, channel, 80, rssi, rx, tx)


def entry(ssid, bssid, signal=50, radio="802.11ax", band="5 GHz", channel=40):
    return winutil.ScanEntry(ssid, bssid, signal, radio, band, channel)


def adapter(name="Wi-Fi", desc="TP-Link Wi-Fi 6 PCIe Adapter", status="Up", media="Native 802.11", virtual=False,
            date="2025-12-05", version="25.40.2.585", provider="MediaTek"):
    return {"Name": name, "InterfaceDescription": desc, "Status": status, "PhysicalMediaType": media,
            "Virtual": virtual, "DriverDate": date, "DriverVersion": version, "DriverProvider": provider}


def minute(i, rssi=-65, rx=500, loss=0.0, ts0=NOW - 20 * H):
    return d.Minute(int(ts0 + i * 60), rssi, rx, loss)


class HelperTests(unittest.TestCase):
    def test_worst_order(self):
        self.assertEqual(d.worst([d.OK, d.INFO]), d.INFO)
        self.assertEqual(d.worst([d.INFO, d.WARN, d.OK]), d.WARN)
        self.assertEqual(d.worst([d.WARN, d.BAD]), d.BAD)
        self.assertEqual(d.worst([]), d.OK)

    def test_mac_keys(self):
        self.assertEqual(d.device_key("02:5E:00:9A:40:24"), d.device_key("02:5e:00:9a:40:31"))
        self.assertNotEqual(d.device_key("02:5e:00:9a:40:24"), d.device_key("02:5e:00:9b:40:24"))
        self.assertEqual(d.family_key("c4:2c:7b:d4:ea:9a"), d.family_key("c6:2c:7b:d8:ea:99"))  # locally-administered bit
        self.assertNotEqual(d.family_key("c4:2c:7b:00:00:00"), d.family_key("c4:2c:7c:00:00:00"))
        self.assertEqual(d.device_key("garbage"), "")

    def test_join_minutes_filters(self):
        wrows = [{"ts": 60, "state": "connected", "rssi": -60, "rx_mbps": 100},
                 {"ts": 120, "state": "disconnected", "rssi": None, "rx_mbps": None},
                 {"ts": 180, "state": "connected", "rssi": -60, "rx_mbps": 100},
                 {"ts": 240, "state": "connected", "rssi": -60, "rx_mbps": None},
                 {"ts": 300, "state": "connected", "rssi": -60, "rx_mbps": 100}]
        prows = [{"ts": 60, "target": "router", "sent": 60, "lost": 6},
                 {"ts": 60, "target": "google", "sent": 60, "lost": 60},
                 {"ts": 120, "target": "router", "sent": 60, "lost": 0},
                 {"ts": 180, "target": "router", "sent": 10, "lost": 0},   # too few pings
                 {"ts": 240, "target": "router", "sent": 60, "lost": 0}]
        (m,) = d.join_minutes(wrows, prows)
        self.assertEqual((m.ts, m.rssi, m.rx_mbps, m.router_loss_pct), (60, -60, 100, 10.0))

    def test_wifi_adapter_skips_wifi_direct_virtual_adapters(self):
        direct = adapter("Local Area Connection* 9", "Microsoft Wi-Fi Direct Virtual Adapter", "Disconnected",
                         virtual=True, version="10.0", provider="Microsoft")
        real = adapter()
        self.assertIs(d._wifi_adapter([direct, real]), real)
        self.assertIs(d._wifi_adapter([direct]), direct)  # better than nothing
        self.assertIsNone(d._wifi_adapter([adapter(media="802.3")]))

    def test_wifi_adapter_prefers_the_connected_interface(self):
        usb = adapter("Wi-Fi 2", "USB Wi-Fi dongle")
        self.assertIs(d._wifi_adapter([adapter(), usb], "Wi-Fi 2"), usb)

    def test_driver_check_reads_the_real_card_not_wifi_direct(self):
        direct = adapter("Local Area Connection* 9", "Microsoft Wi-Fi Direct Virtual Adapter", "Disconnected",
                         virtual=True, version="10.0.26100.1", provider="Microsoft")
        r = d.run_all(fake_context(adapters=lambda: [direct, adapter()]), only={1}).results[0].localized("vi")
        self.assertIn("TP-Link", r.details[0])

    def test_ago(self):
        for lang, expected in (("vi", ["2 phút trước", "5 giờ trước", "3 ngày trước"]),
                               ("en", ["2 minutes ago", "5 hours ago", "3 days ago"])):
            got = [i18n.render(d._ago(NOW, NOW - secs), lang) for secs in (120, 5 * H, 72 * H)]
            self.assertEqual(got, expected)
        self.assertEqual(i18n.render(d._ago(NOW, NOW - H), "en"), "1 hour ago")


class DriverTests(unittest.TestCase):
    store = [winutil.StoredDriver("oem18.inf", "mtk.inf", "MediaTek", "25.40.2.585", "2025-12-05"),
             winutil.StoredDriver("oem32.inf", "apple.inf", "Apple", "1.8.5.1", "2013-07-15")]

    def test_no_adapter(self):
        self.assertEqual(d.evaluate_driver(None, [], [], NOW).status, d.INFO)

    def test_ok(self):
        r = d.evaluate_driver(adapter(), [], self.store, NOW)
        self.assertEqual(r.status, d.OK)
        self.assertTrue(any("oem18.inf" in x for x in r.details))
        self.assertFalse(any("Apple" in x or "oem32" in x for x in r.details))  # only same provider

    def test_old_driver_warns(self):
        r = d.evaluate_driver(adapter(date="2024-01-01"), [], self.store, NOW)
        self.assertEqual(r.status, d.WARN)
        self.assertIn("33 tháng", r.summary)  # 2024-01-01 -> 2026-10-04

    def test_age_threshold_is_12_months(self):
        self.assertEqual(d.evaluate_driver(adapter(date="2025-11-04"), [], [], NOW).status, d.OK)    # 11 months
        self.assertEqual(d.evaluate_driver(adapter(date="2025-09-01"), [], [], NOW).status, d.WARN)  # 13 months

    def test_crash_is_bad_and_says_when(self):
        r = d.evaluate_driver(adapter(), [int(NOW - 5 * H), int(NOW - 50 * H)], self.store, NOW)
        self.assertEqual(r.status, d.BAD)
        self.assertIn("2 lần", r.summary)
        self.assertIn("5 giờ trước", r.summary)

    def test_crash_beats_old(self):
        self.assertEqual(d.evaluate_driver(adapter(date="2020-01-01"), [int(NOW)], [], NOW).status, d.BAD)

    def test_unreadable_log_is_info_not_ok(self):
        r = d.evaluate_driver(adapter(), [], self.store, NOW, ihv_error="ihv_stops: Access denied")
        self.assertEqual(r.status, d.INFO)

    def test_missing_driver_date(self):
        self.assertEqual(d.evaluate_driver(adapter(date=None), [], [], NOW).status, d.OK)


class SignalTests(unittest.TestCase):
    def test_threshold_boundaries(self):
        for rssi, expected in [(-30, d.OK), (-60, d.OK), (-61, d.WARN), (-70, d.WARN), (-71, d.BAD), (-90, d.BAD)]:
            self.assertEqual(d.evaluate_signal(wifi(rssi=rssi)).status, expected, rssi)

    def test_not_connected_or_missing(self):
        self.assertEqual(d.evaluate_signal(None).status, d.INFO)
        self.assertEqual(d.evaluate_signal(wifi(state="disconnected")).status, d.INFO)
        self.assertEqual(d.evaluate_signal(wifi(rssi=None)).status, d.INFO)


class InterferenceTests(unittest.TestCase):
    me = "02:5e:00:9a:40:24"

    def test_two_foreign_networks_same_channel_warns(self):
        scan = [entry("A", "aa:00:00:00:00:01"), entry("B", "bb:00:00:00:00:02"), entry("Home_5G", self.me)]
        r = d.evaluate_interference(wifi(), scan)
        self.assertEqual(r.status, d.WARN)
        self.assertIn("2 mạng", r.summary)

    def test_one_foreign_network_is_fine(self):
        scan = [entry("A", "aa:00:00:00:00:01"), entry("Home_5G", self.me)]
        self.assertEqual(d.evaluate_interference(wifi(), scan).status, d.OK)

    def test_own_router_other_ssids_excluded(self):
        scan = [entry("Home_5G", self.me), entry("Home_Wifi5", "02:5e:00:9a:40:32"),
                entry("Home_guest", "02:5e:00:9a:40:33")]
        self.assertEqual(d.evaluate_interference(wifi(), scan).status, d.OK)

    def test_weak_stale_networks_ignored(self):
        scan = [entry("A", "aa:00:00:00:00:01", signal=0), entry("B", "bb:00:00:00:00:02", signal=9)]
        self.assertEqual(d.evaluate_interference(wifi(), scan).status, d.OK)

    def test_same_channel_number_on_another_band_is_not_interference(self):
        scan = [entry("A", "aa:00:00:00:00:01", band="2.4 GHz", channel=40),
                entry("B", "bb:00:00:00:00:02", band="2.4 GHz", channel=40)]
        self.assertEqual(d.evaluate_interference(wifi(), scan).status, d.OK)

    def test_hidden_networks_counted_by_bssid(self):
        scan = [entry("", "aa:00:00:00:00:01"), entry("", "bb:00:00:00:00:02")]
        self.assertEqual(d.evaluate_interference(wifi(), scan).status, d.WARN)

    def test_5ghz_advice_picks_quieter_group(self):
        scan = [entry("A", "aa:00:00:00:00:01", channel=40), entry("B", "bb:00:00:00:00:02", channel=40),
                entry("C", "cc:00:00:00:00:03", channel=36), entry("D", "dd:00:00:00:00:04", channel=44),
                entry("E", "ee:00:00:00:00:05", channel=149)]
        r = d.evaluate_interference(wifi(), scan)
        self.assertIn("149–161", r.advice)
        self.assertIn("kênh 153", r.advice)  # emptiest channel in that group

    def test_24ghz_advice(self):
        scan = [entry("A", "aa:00:00:00:00:01", band="2.4 GHz", channel=6),
                entry("B", "bb:00:00:00:00:02", band="2.4 GHz", channel=6)]
        r = d.evaluate_interference(wifi(channel=6), scan)
        self.assertEqual(r.status, d.WARN)
        self.assertIn("5GHz", r.advice)

    def test_not_connected(self):
        self.assertEqual(d.evaluate_interference(None, []).status, d.INFO)
        self.assertEqual(d.evaluate_interference(wifi(state="disconnected"), []).status, d.INFO)


class DropsTests(unittest.TestCase):
    def ev(self, reason, n=1, ts=NOW - 3 * H):
        return [{"ts": int(ts), "reason": reason} for _ in range(n)]

    DRIVER = "The network is disconnected by the driver."
    USER = "The network is disconnected by the user."
    NEW = "The network is disconnected because the user wants to establish a new connection."

    def test_more_than_five_driver_drops_is_bad(self):
        r = d.evaluate_drops(self.ev(self.DRIVER, 6), [], None, NOW)
        self.assertEqual(r.status, d.BAD)
        self.assertIn("3 giờ trước", r.summary)

    def test_five_is_warn(self):
        self.assertEqual(d.evaluate_drops(self.ev(self.DRIVER, 5), [], None, NOW).status, d.WARN)

    def test_user_initiated_do_not_count(self):
        r = d.evaluate_drops(self.ev(self.USER, 30) + self.ev(self.NEW, 30), [], None, NOW)
        self.assertEqual(r.status, d.OK)

    def test_limited_connectivity_warns(self):
        self.assertEqual(d.evaluate_drops([], [int(NOW)], None, NOW).status, d.WARN)

    def test_none_is_ok(self):
        self.assertEqual(d.evaluate_drops([], [], None, NOW).status, d.OK)

    def test_unreadable_log_is_info(self):
        self.assertEqual(d.evaluate_drops([], [], "disconnects: Access denied", NOW).status, d.INFO)

    def test_per_day_breakdown(self):
        day1, day2 = datetime(2026, 10, 1, 10).timestamp(), datetime(2026, 10, 2, 10).timestamp()
        r = d.evaluate_drops(self.ev(self.DRIVER, 2, day1) + self.ev(self.DRIVER, 3, day2), [], None, NOW)
        self.assertTrue(any("10-01: 2" in x and "10-02: 3" in x for x in r.details))

    def test_empty_reason_is_labelled(self):
        r = d.evaluate_drops([{"ts": int(NOW), "reason": ""}], [], None, NOW)
        self.assertTrue(any("không rõ lý do" in x for x in r.details))


def prow(target, sent, lost, jitter=3.0, ts=NOW):
    return {"ts": int(ts), "target": target, "sent": sent, "lost": lost, "jitter": jitter}


class PingTests(unittest.TestCase):
    def test_not_enough_data(self):
        r = d.evaluate_ping([prow("router", 30, 0)], [])
        self.assertEqual(r.status, d.INFO)

    def test_all_good(self):
        rows = [prow(t, 3600, 10) for t in ("router", "cloudflare", "google")]
        self.assertEqual(d.evaluate_ping(rows, []).status, d.OK)

    def test_loss_thresholds(self):
        self.assertEqual(d.evaluate_ping([prow("router", 1000, 10)], []).status, d.OK)    # exactly 1%
        self.assertEqual(d.evaluate_ping([prow("router", 1000, 11)], []).status, d.WARN)
        self.assertEqual(d.evaluate_ping([prow("router", 1000, 30)], []).status, d.WARN)  # exactly 3%
        self.assertEqual(d.evaluate_ping([prow("router", 1000, 31)], []).status, d.BAD)

    def test_router_loss_is_local_problem(self):
        rows = [prow("router", 1000, 100), prow("google", 1000, 100)]
        r = d.evaluate_ping(rows, [])
        self.assertEqual(r.status, d.BAD)
        self.assertIn("nội bộ", r.summary)

    def test_router_ok_internet_loss_is_isp_side(self):
        rows = [prow("router", 3000, 12), prow("cloudflare", 3000, 240), prow("google", 3000, 270)]
        r = d.evaluate_ping(rows, [])
        self.assertEqual(r.status, d.BAD)
        self.assertIn("ISP", r.summary)

    def test_icmp_loss_with_healthy_probes_is_not_a_fault(self):
        # The false alarm SIC-36 fixes: ping to the Internet is lossy, real connections are fine.
        rows = [prow("router", 3000, 12), prow("cloudflare", 3000, 240), prow("google", 3000, 270),
                prow("tcp_cloudflare", 360, 0), prow("tcp_google", 360, 1), prow("http_cloudflare", 240, 0)]
        r = d.evaluate_ping(rows, [])
        self.assertEqual(r.status, d.INFO)
        self.assertIn("ICMP bị giới hạn", r.summary)
        self.assertIn("Không cần xử lý", r.advice)
        self.assertTrue(any("tcp_cloudflare" in x for x in r.details))

    def test_icmp_loss_and_probe_loss_is_real_isp_trouble(self):
        rows = [prow("router", 3000, 12), prow("cloudflare", 3000, 240), prow("google", 3000, 270),
                prow("tcp_cloudflare", 360, 100), prow("tcp_google", 360, 90), prow("http_cloudflare", 240, 60)]
        r = d.evaluate_ping(rows, [])
        self.assertEqual(r.status, d.BAD)
        self.assertIn("TCP/HTTP ra Internet mất gói", r.summary)

    def test_one_blocked_probe_target_does_not_alarm(self):
        rows = [prow("router", 3000, 0), prow("cloudflare", 3000, 0),
                prow("tcp_cloudflare", 360, 360), prow("tcp_google", 360, 0)]   # port 443 blocked to one host
        self.assertEqual(d.evaluate_ping(rows, []).status, d.OK)

    def test_router_loss_stays_local_even_with_probes(self):
        rows = [prow("router", 1000, 100), prow("tcp_cloudflare", 360, 0)]
        r = d.evaluate_ping(rows, [])
        self.assertEqual(r.status, d.BAD)
        self.assertIn("nội bộ", r.summary)

    def test_only_a_few_probe_samples_are_ignored(self):
        rows = [prow("router", 3000, 12), prow("cloudflare", 3000, 240), prow("tcp_cloudflare", 5, 0)]
        self.assertEqual(d.evaluate_ping(rows, []).status, d.BAD)   # 5 samples prove nothing: old behaviour

    def test_probes_only_data(self):
        self.assertEqual(d.evaluate_ping([prow("tcp_cloudflare", 360, 0)], []).status, d.OK)

    def test_probe_loss_alone_is_reported(self):
        r = d.evaluate_ping([prow("router", 3000, 0), prow("cloudflare", 3000, 0), prow("tcp_cloudflare", 360, 100)], [])
        self.assertEqual(r.status, d.BAD)

    def test_jitter_over_30_warns(self):
        self.assertEqual(d.evaluate_ping([prow("router", 1000, 0, jitter=31)], []).status, d.WARN)
        self.assertEqual(d.evaluate_ping([prow("router", 1000, 0, jitter=30)], []).status, d.OK)

    def test_recent_window_in_details(self):
        r = d.evaluate_ping([prow("router", 1000, 0)], [prow("router", 100, 50)])
        self.assertIn("5 phút gần nhất: mất 50,00%", r.details[0])   # decimal comma in vi

    def test_rows_aggregated_across_minutes(self):
        rows = [prow("router", 60, 6, ts=NOW - 60 * i) for i in range(10)]  # 10% loss each minute
        self.assertEqual(d.evaluate_ping(rows, []).status, d.BAD)


def bench(server, rtts, failures=0):
    return dnsprobe.ServerBenchmark(server, sent=len(rtts) + failures, replies=len(rtts), failures=failures,
                                    rtts_ms=list(rtts))


class DnsTests(unittest.TestCase):
    labels = {"1.1.1.1": "in_use", "8.8.8.8": "in_use", "192.168.3.1": "router", "9.9.9.9": "public"}

    def test_ok_when_close_to_fastest(self):
        r = d.evaluate_dns([bench("1.1.1.1", [40] * 5), bench("9.9.9.9", [35] * 5)], ["1.1.1.1"], self.labels)
        self.assertEqual(r.status, d.OK)

    def test_slower_by_more_than_20ms_is_info(self):
        r = d.evaluate_dns([bench("1.1.1.1", [60] * 5), bench("9.9.9.9", [30] * 5)], ["1.1.1.1"], self.labels)
        self.assertEqual(r.status, d.INFO)
        self.assertIn("30 ms", r.summary)

    def test_exactly_20ms_gap_is_ok(self):
        r = d.evaluate_dns([bench("1.1.1.1", [50] * 5), bench("9.9.9.9", [30] * 5)], ["1.1.1.1"], self.labels)
        self.assertEqual(r.status, d.OK)

    def test_faster_server_already_configured_gets_specific_advice(self):
        r = d.evaluate_dns([bench("1.1.1.1", [70] * 5), bench("8.8.8.8", [45] * 5)], ["1.1.1.1", "8.8.8.8"], self.labels)
        self.assertEqual(r.status, d.INFO)
        self.assertIn("đưa nó lên làm DNS chính", r.advice)

    def test_router_wins_gets_cache_caveat(self):
        r = d.evaluate_dns([bench("1.1.1.1", [70] * 5), bench("192.168.3.1", [7] * 5)], ["1.1.1.1"], self.labels)
        self.assertIn("bộ nhớ đệm", r.advice)

    def test_failure_on_server_in_use_warns(self):
        r = d.evaluate_dns([bench("1.1.1.1", [40] * 4, failures=1)], ["1.1.1.1"], self.labels)
        self.assertEqual(r.status, d.WARN)

    def test_failure_on_reference_server_only_does_not_warn(self):
        r = d.evaluate_dns([bench("1.1.1.1", [40] * 5), bench("9.9.9.9", [], failures=5)], ["1.1.1.1"], self.labels)
        self.assertEqual(r.status, d.OK)

    def test_no_bench_or_no_server_in_use(self):
        self.assertEqual(d.evaluate_dns([], [], {}).status, d.INFO)
        self.assertEqual(d.evaluate_dns([bench("9.9.9.9", [30] * 5)], [], self.labels).status, d.INFO)


class TcpVpnWiredTests(unittest.TestCase):
    def test_tcp(self):
        r = d.evaluate_tcp([int(NOW - H)] * 3, 120)
        self.assertEqual((r.status, r.tweak), (d.WARN, "tcp_timedwait"))
        self.assertEqual(d.evaluate_tcp([], 40).status, d.OK)
        self.assertEqual(d.evaluate_tcp([], None, "port_exhaustion: denied").status, d.INFO)

    def test_vpn_up_down_none(self):
        up = adapter("wt0", "WireGuard Tunnel", "Up", media="", virtual=True)
        down = adapter("OpenVPN Data Channel Offload for Surfshark", "OpenVPN Data Channel Offload", "Disconnected",
                       media="", virtual=True)
        self.assertEqual(d.evaluate_vpn([adapter(), up, down]).status, d.INFO)
        self.assertEqual(d.evaluate_vpn([adapter(), down]).status, d.OK)
        self.assertEqual(d.evaluate_vpn([adapter()]).status, d.OK)

    def test_vpn_regex_does_not_match_ordinary_cards(self):
        self.assertEqual(d.evaluate_vpn([adapter(desc="Intel(R) Ethernet Connection (7) I219-V", name="Ethernet")]).status,
                         d.OK)

    def test_wired(self):
        eth_down = adapter("Ethernet", "Intel I219-V", "Disconnected", media="802.3")
        eth_up = adapter("Ethernet", "Intel I219-V", "Up", media="802.3")
        bt = adapter("Bluetooth Network Connection", "Bluetooth Device (Personal Area Network)", "Disconnected",
                     media="802.3", virtual=True)
        self.assertEqual(d.evaluate_wired([eth_down, adapter()], wifi()).status, d.INFO)
        self.assertEqual(d.evaluate_wired([eth_up, adapter()], wifi()).status, d.OK)
        self.assertEqual(d.evaluate_wired([adapter(), bt], wifi()).status, d.OK)  # Bluetooth PAN is not a LAN port
        self.assertEqual(d.evaluate_wired([eth_down], None).status, d.INFO)


class TweaksTests(unittest.TestCase):
    def test_no_framework_is_info_not_ok(self):
        self.assertEqual(d.evaluate_tweaks(None).status, d.INFO)

    def test_lists_unsupported_and_enabled_are_skipped(self):
        states = {"a": {"risk": "low", "enabled": False}, "b": {"risk": "low", "enabled": True},
                  "c": {"risk": "low", "enabled": False, "supported": False},
                  "d": {"risk": "medium", "enabled": False}}
        r = d.evaluate_tweaks(states)
        self.assertEqual((r.status, r.details), (d.INFO, ["a"]))

    def test_all_done(self):
        self.assertEqual(d.evaluate_tweaks({"a": {"risk": "low", "enabled": True}}).status, d.OK)


class MloTests(unittest.TestCase):
    me = "02:5e:00:9a:40:24"
    scan_be = [entry("Home_5G", me), entry("Home", "02:5e:00:9a:40:20", radio="802.11be", band="2.4 GHz", channel=11)]

    def minutes(self, collapsed, total=100, recent_collapsed=None):
        out = []
        for i in range(total):
            bad = i < collapsed
            out.append(d.Minute(int(NOW - (total - i) * 60 * 10), -65, 6 if bad else 500, 20.0 if bad else 0.0))
        return out

    def test_not_connected(self):
        self.assertEqual(d.evaluate_mlo(None, None, [], []).status, d.INFO)

    def test_router_without_be_is_ok(self):
        self.assertEqual(d.evaluate_mlo(wifi(), adapter(), [entry("Home_5G", self.me)], []).status, d.OK)

    def test_foreign_be_router_is_not_ours(self):
        scan = [entry("Neighbour", "aa:bb:cc:dd:ee:ff", radio="802.11be")]
        self.assertEqual(d.evaluate_mlo(wifi(), adapter(), scan, []).status, d.OK)

    def test_wifi7_card_is_ok(self):
        r = d.evaluate_mlo(wifi(), adapter(desc="Intel Wi-Fi 7 BE200 320MHz"), self.scan_be, [])
        self.assertEqual(r.status, d.OK)
        r = d.evaluate_mlo(wifi(radio="802.11be"), adapter(), self.scan_be, [])
        self.assertEqual(r.status, d.OK)

    def test_be_present_but_no_collapse_is_info_and_explains_other_ssid(self):
        r = d.evaluate_mlo(wifi(), adapter(), self.scan_be, self.minutes(0))
        self.assertEqual(r.status, d.INFO)
        self.assertIn("SSID khác", r.summary)

    def test_collapse_with_loss_warns(self):
        r = d.evaluate_mlo(wifi(), adapter(), self.scan_be, self.minutes(40), now=None)
        self.assertEqual(r.status, d.WARN)
        self.assertIn("Tắt MLO", r.advice)

    def test_collapse_below_fraction_does_not_warn(self):
        self.assertEqual(d.evaluate_mlo(wifi(), adapter(), self.scan_be, self.minutes(10)).status, d.INFO)

    def test_collapse_below_minimum_minutes_does_not_warn(self):
        self.assertEqual(d.evaluate_mlo(wifi(), adapter(), self.scan_be, self.minutes(4, total=10)).status, d.INFO)

    def test_rx_collapse_without_loss_does_not_count(self):
        # EXP-009: Rx shown as 6 Mbps for many minutes with no packet loss.
        mins = [d.Minute(int(NOW - i * 60), -65, 6, 0.0) for i in range(100)]
        self.assertEqual(d.evaluate_mlo(wifi(), adapter(), self.scan_be, mins).status, d.INFO)

    def test_weak_signal_collapse_does_not_count(self):
        mins = [d.Minute(int(NOW - i * 60), -80, 6, 30.0) for i in range(100)]
        self.assertEqual(d.evaluate_mlo(wifi(), adapter(), self.scan_be, mins).status, d.INFO)

    def test_recently_clean_does_not_warn(self):
        old = [d.Minute(int(NOW - 10 * H + i * 60), -65, 6, 30.0) for i in range(60)]
        recent = [d.Minute(int(NOW - 2 * H + i * 60), -65, 500, 0.0) for i in range(60)]
        self.assertEqual(d.evaluate_mlo(wifi(), adapter(), self.scan_be, old + recent, now=NOW).status, d.INFO)

    def test_our_ssid_be_wording(self):
        scan = [entry("Home_5G", self.me, radio="802.11be")]
        r = d.evaluate_mlo(wifi(), adapter(), scan, self.minutes(40))
        self.assertIn("SSID đang dùng", r.summary)


class ModemWifiTests(unittest.TestCase):
    def test_strong_family_group_warns_even_with_hidden_ssid_and_la_bit(self):
        scan = [entry("OldModemNet", "c4:2c:7b:d4:ea:9a", signal=80, channel=36),
                entry("", "c6:2c:7b:d8:ea:99", signal=75, band="2.4 GHz", channel=4)]
        r = d.evaluate_modem_wifi(wifi(), scan)
        self.assertEqual(r.status, d.WARN)
        self.assertIn("c4:2c:7b", r.details[0])

    def test_medium_group_is_info(self):
        scan = [entry("A", "c4:2c:7b:00:00:01", signal=55), entry("B", "c4:2c:7b:00:00:02", signal=52)]
        self.assertEqual(d.evaluate_modem_wifi(wifi(), scan).status, d.INFO)

    def test_single_loud_network_is_info(self):
        scan = [entry("NeighborNet", "c4:2c:7b:d4:ea:9a", signal=72)]
        self.assertEqual(d.evaluate_modem_wifi(wifi(), scan).status, d.INFO)

    def test_own_router_ssids_never_count(self):
        scan = [entry("Home_5G", "02:5e:00:9a:40:24", signal=90), entry("Home_W5", "02:5e:00:9a:40:31", signal=90),
                entry("Home", "02:5e:00:9a:40:20", signal=90)]
        self.assertEqual(d.evaluate_modem_wifi(wifi(), scan).status, d.OK)

    def test_same_ssid_other_device_ignored(self):
        scan = [entry("Home_5G", "aa:bb:cc:00:00:01", signal=90), entry("Home_5G", "aa:bb:cc:00:00:02", signal=90)]
        self.assertEqual(d.evaluate_modem_wifi(wifi(), scan).status, d.OK)

    def test_stale_zero_signal_entries_ignored(self):
        scan = [entry("A", "c4:2c:7b:00:00:01", signal=0), entry("B", "c4:2c:7b:00:00:02", signal=0)]
        self.assertEqual(d.evaluate_modem_wifi(wifi(), scan).status, d.OK)

    def test_unrelated_strong_neighbours_in_different_families_are_ok(self):
        scan = [entry("A", "aa:00:00:00:00:01", signal=60), entry("B", "bb:00:00:00:00:02", signal=60)]
        self.assertEqual(d.evaluate_modem_wifi(wifi(), scan).status, d.OK)

    def test_when_not_connected_every_network_is_foreign(self):
        scan = [entry("A", "c4:2c:7b:00:00:01", signal=80), entry("B", "c4:2c:7b:00:00:02", signal=80)]
        self.assertEqual(d.evaluate_modem_wifi(None, scan).status, d.WARN)


class LinkTests(unittest.TestCase):
    def series(self, bad, total, rssi=-65, ts0=NOW - 20 * H):
        return [d.Minute(int(ts0 + i * 60), rssi, 10 if i < bad else 500, 20.0 if i < bad else 0.0)
                for i in range(total)]

    def test_insufficient_data(self):
        self.assertEqual(d.evaluate_link(self.series(0, 10)).status, d.INFO)

    def test_healthy(self):
        self.assertEqual(d.evaluate_link(self.series(0, 200)).status, d.OK)
        self.assertEqual(d.evaluate_link(self.series(9, 200)).status, d.OK)       # 4.5% < 5%

    def test_thresholds(self):
        self.assertEqual(d.evaluate_link(self.series(10, 200)).status, d.INFO)    # 5%
        self.assertEqual(d.evaluate_link(self.series(39, 200)).status, d.INFO)    # 19.5%
        self.assertEqual(d.evaluate_link(self.series(40, 200)).status, d.WARN)    # 20%

    def test_requires_loss_not_just_low_rx(self):
        mins = [d.Minute(int(NOW - i * 60), -65, 6, 0.0) for i in range(200)]
        self.assertEqual(d.evaluate_link(mins).status, d.OK)

    def test_requires_low_rx_not_just_loss(self):
        mins = [d.Minute(int(NOW - i * 60), -65, 500, 30.0) for i in range(200)]
        self.assertEqual(d.evaluate_link(mins).status, d.OK)

    def test_weak_signal_note(self):
        r = d.evaluate_link(self.series(100, 200, rssi=-80))
        self.assertTrue(any("khoảng cách" in x for x in r.details))

    def test_advice_lists_causes_to_exclude_first(self):
        r = d.evaluate_link(self.series(100, 200))
        self.assertEqual(r.status, d.WARN)
        self.assertIn("Loại trừ trước", r.advice)

    def test_recently_clean_means_improved(self):
        old = self.series(100, 100, ts0=NOW - 10 * H)           # all bad long ago
        recent = self.series(0, 60, ts0=NOW - 2 * H)             # clean in the last 3h
        r = d.evaluate_link(old + recent, now=NOW)
        self.assertEqual(r.status, d.INFO)
        self.assertIn("đã cải thiện", r.summary)

    def test_recent_window_too_short_keeps_overall_verdict(self):
        old = self.series(100, 100, ts0=NOW - 10 * H)
        recent = self.series(0, 5, ts0=NOW - 1 * H)
        self.assertEqual(d.evaluate_link(old + recent, now=NOW).status, d.WARN)

    def test_still_bad_recently_stays_warn(self):
        self.assertEqual(d.evaluate_link(self.series(150, 200, ts0=NOW - 3 * H + 60 * 10), now=NOW).status, d.WARN)

    def test_calibration_windows_from_2026_10_03(self):
        # Fractions measured on recorded data (docs/DIAGNOSTICS.md): broken 23-88%, healthy 0%.
        for bad, total in [(73, 100), (88, 100), (23, 100), (44, 100), (55, 100)]:
            self.assertEqual(d.evaluate_link(self.series(bad, total)).status, d.WARN, (bad, total))
        self.assertEqual(d.evaluate_link(self.series(0, 573)).status, d.OK)


class ContextTests(unittest.TestCase):
    def test_loader_called_once_and_cached(self):
        calls = []
        ctx = d.Context(now=NOW, loaders={"wifi": lambda: calls.append(1) or wifi()})
        ctx.get("wifi")
        ctx.get("wifi")
        self.assertEqual(len(calls), 1)

    def test_failing_loader_fails_the_same_way_every_time_without_retrying(self):
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("netsh died")
        ctx = d.Context(now=NOW, loaders={"scan": boom})
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                ctx.get("scan")
        self.assertEqual(len(calls), 1)

    def test_minutes_come_from_storage(self):
        with Storage() as db:
            for i in range(40):
                ts = int(NOW) - i * 60
                db.add_wifi_stat(ts, state="connected", rssi=-65, rx_mbps=10)
                db.add_minute_stat(ts, "router", 60, 6)
            minutes = d.Context(db, now=NOW).get("minutes")
        self.assertEqual(len(minutes), 40)
        self.assertEqual(minutes[0].router_loss_pct, 10.0)

    def test_storage_window_is_24h(self):
        with Storage() as db:
            db.add_wifi_stat(int(NOW) - 30 * H, state="connected", rssi=-65, rx_mbps=10)
            db.add_minute_stat(int(NOW) - 30 * H, "router", 60, 6)
            self.assertEqual(d.Context(db, now=NOW).get("minutes"), [])

    def test_without_storage_history_is_empty(self):
        ctx = d.Context(now=NOW)
        self.assertEqual((ctx.get("minutes"), ctx.get("ping_rows_1h")), ([], []))

    def test_ping_leaves_out_the_minutes_of_network_changes(self):
        with Storage() as db:
            change = int(NOW) - 600
            minute_of_change = change - change % 60
            for i in range(-4, 6):
                ts = minute_of_change + 60 * i
                db.add_minute_stat(ts, "router", 60, 30 if ts == minute_of_change else 0)
            db.add_event(change, "gateway_change", "192.0.2.1 -> 192.0.2.254")
            db.add_event(int(NOW) - 2 * H, "roam", "an hour too early")
            db.add_event(int(NOW) - 300, "internet_down", "not a network change")
            ctx = d.Context(db, now=NOW)
            self.assertEqual(ctx.get("network_changes"), [change])
            result = d.check_ping(ctx)
        self.assertEqual(result.status, d.OK)
        self.assertEqual(result.details[-1], i18n.msg("diag.ping.left_out", count=2))

    def test_a_vpn_route_change_leaves_out_its_minutes_too(self):
        with Storage() as db:
            change = int(NOW) - 600
            minute_of_change = change - change % 60
            for i in range(-4, 6):
                ts = minute_of_change + 60 * i
                db.add_minute_stat(ts, "router", 60, 30 if ts == minute_of_change else 0)
            db.add_event(change, "route_change", "if12 -> if31")
            ctx = d.Context(db, now=NOW)
            self.assertEqual(ctx.get("network_changes"), [change])
            self.assertEqual(d.check_ping(ctx).status, d.OK)

    def test_without_storage_there_are_no_network_changes(self):
        self.assertEqual(d.Context(now=NOW).get("network_changes"), [])


def fake_context(**overrides):
    loaders = {
        "wifi": lambda: wifi(), "adapters": lambda: [adapter()], "scan": lambda: [entry("Home_5G", "02:5e:00:9a:40:24")],
        "events": lambda: {"disconnects": [], "limited_connectivity": [], "ihv_stops": [], "port_exhaustion": [],
                           "time_wait": 30, "errors": []},
        "driver_store": lambda: [], "ping_rows_1h": lambda: [prow(t, 3600, 5) for t in ("router", "google")],
        "ping_rows_5m": lambda: [], "minutes": lambda: [minute(i) for i in range(100)],
        "dns_bench": lambda: ([bench("1.1.1.1", [40] * 5)], ["1.1.1.1"], {"1.1.1.1": "in_use"}),
        "tweak_states": lambda: None,
        "path_mtu": lambda: ({"alias": "Wi-Fi", "mtu": 1500}, [diagnostics.pmtu.PathResult("1.1.1.1", 1500, 2)]),
    }
    loaders.update(overrides)
    return d.Context(now=NOW, loaders=loaders)


class RunAllTests(unittest.TestCase):
    def test_runs_all_default_checks_in_order(self):
        report = d.run_all(fake_context())
        self.assertEqual([r.id for r in report.results], [*range(1, 14), 15])   # 14 runs only on demand
        self.assertEqual(len({r.key for r in report.results}), 14)
        self.assertFalse([r for r in report.results if r.error])

    def test_a_broken_check_does_not_hide_the_others(self):
        def boom():
            raise OSError("netsh died")
        report = d.run_all(fake_context(scan=boom))
        by_key = {r.key: r for r in report.results}
        for key in ("interference", "wifi7_mlo", "modem_wifi"):
            self.assertEqual(by_key[key].status, d.INFO, key)
            self.assertIn("netsh died", by_key[key].error)
        self.assertIsNone(by_key["signal"].error)
        self.assertEqual(len(report.results), 14)

    def test_only_filter(self):
        report = d.run_all(fake_context(), only={2, 13})
        self.assertEqual([r.id for r in report.results], [2, 13])

    def test_worst_reflects_results(self):
        ev = {"disconnects": [], "limited_connectivity": [], "ihv_stops": [int(NOW - H)], "port_exhaustion": [],
              "time_wait": 1, "errors": []}
        self.assertEqual(d.run_all(fake_context(events=lambda: ev)).worst, d.BAD)

    def test_events_error_marks_only_the_affected_checks_as_info(self):
        ev = {"disconnects": [], "limited_connectivity": [], "ihv_stops": [], "port_exhaustion": [],
              "time_wait": 1, "errors": ["disconnects: Access is denied"]}
        by_key = {r.key: r for r in d.run_all(fake_context(events=lambda: ev)).results}
        self.assertEqual(by_key["drops"].status, d.INFO)
        self.assertEqual(by_key["driver"].status, d.OK)
        self.assertEqual(by_key["tcp_ports"].status, d.OK)

    def test_results_are_json_serialisable(self):
        report = d.run_all(fake_context())
        json.dumps([r.to_dict() for r in report.results], ensure_ascii=False)


class CompareAndPersistTests(unittest.TestCase):
    def run_of(self, **statuses):
        return [{"key": k, "title": k.title(), "status": s} for k, s in statuses.items()]

    def test_changes(self):
        old = self.run_of(a=d.BAD, b=d.OK, c=d.WARN, e=d.OK, gone=d.OK)
        new = self.run_of(a=d.OK, b=d.WARN, c=d.WARN, e=d.INFO, fresh=d.BAD)
        by_key = {c["key"]: c["change"] for c in d.compare_runs(old, new)}
        self.assertEqual(by_key, {"a": "better", "b": "worse", "e": "changed", "fresh": "new", "gone": "removed"})

    def test_identical_runs_have_no_changes(self):
        run = self.run_of(a=d.OK, b=d.BAD)
        self.assertEqual(d.compare_runs(run, run), [])

    def test_save_and_reload_roundtrip(self):
        report = d.run_all(fake_context())
        with Storage() as db:
            run_id = d.save_report(db, report)
            saved = db.get_diagnostic_run(run_id)
        self.assertEqual(saved["ts"], int(NOW))
        self.assertEqual(saved["worst"], report.worst)
        self.assertEqual([r["key"] for r in saved["results"]], [r.key for r in report.results])
        self.assertEqual(d.compare_runs(saved["results"], [r.to_dict() for r in report.results]), [])

    def test_format_report_contains_every_check(self):
        for lang in ("en", "vi"):
            text = d.format_report(d.run_all(fake_context()), lang)
            for _, key, _ in d.CHECKS:
                self.assertIn(i18n.t(f"diag.{key}.title", lang), text)


class LocalizationTests(unittest.TestCase):
    """Results are stored as messages and rendered in whatever language is chosen when read."""

    def rendered_runs(self, **overrides):
        report = d.run_all(fake_context(**overrides))
        stored = json.loads(json.dumps([r.to_dict() for r in report.results], ensure_ascii=False))
        return {lang: i18n.localize(stored, lang) for lang in ("en", "vi")}

    def assert_fully_rendered(self, results):
        for r in results:
            for text in [r["title"], r["summary"], r["advice"], *r["details"]]:
                self.assertIsInstance(text, str, r["key"])
                self.assertNotRegex(text, r"\{[a-z_]+(:[^}]*)?\}", r["key"])   # no placeholder left
                self.assertNotIn("diag.", text, r["key"])                    # no missing key

    def test_every_check_renders_in_every_language(self):
        ev_bad = {"disconnects": [{"ts": int(NOW - H), "reason": "The network is disconnected by the driver."}] * 7
                  + [{"ts": int(NOW - 2 * H), "reason": ""}],
                  "limited_connectivity": [int(NOW)], "ihv_stops": [int(NOW - H)], "port_exhaustion": [int(NOW - H)],
                  "time_wait": None, "errors": []}
        scenarios = [{}, {"events": lambda: ev_bad, "wifi": lambda: wifi(rssi=-80)},
                     {"wifi": lambda: None, "adapters": lambda: []}]
        for overrides in scenarios:
            for lang, results in self.rendered_runs(**overrides).items():
                with self.subTest(lang=lang, scenario=list(overrides)):
                    self.assert_fully_rendered(results)

    def test_same_stored_run_reads_in_either_language(self):
        runs = self.rendered_runs()
        by_lang = {lang: {r["key"]: r for r in results} for lang, results in runs.items()}
        self.assertEqual(by_lang["en"]["signal"]["title"], "Wi‑Fi signal")
        self.assertEqual(by_lang["vi"]["signal"]["title"], "Tín hiệu Wi‑Fi")
        self.assertEqual(by_lang["en"]["signal"]["summary"], "Good signal (-55 dBm)")

    def test_english_wording(self):
        en = DiagnosticsIn("en")
        r = en.evaluate_drops([{"ts": int(NOW - 3 * H), "reason": "x by the driver"}] * 6, [], now=NOW)
        self.assertEqual(r.summary, "6 disconnects by the driver in 7 days, last 3 hours ago")
        self.assertIn("6 times: x by the driver", r.details)
        r = en.evaluate_ping([prow("router", 1000, 0)], [prow("router", 100, 50)])
        self.assertIn("last 5 minutes: 50.00% lost", r.details[0])
        self.assertEqual(en.evaluate_tweaks({"a": {"risk": "low"}}).summary, "1 low-risk optimization is not enabled")

    def test_old_runs_with_plain_text_show_verbatim(self):
        legacy = [{"id": 2, "key": "signal", "title": "Tín hiệu Wi‑Fi", "status": "ok", "summary": "Tín hiệu tốt",
                   "details": ["RSSI -55 dBm"], "advice": "", "tweak": None, "error": None}]
        self.assertEqual(i18n.localize(legacy, "en"), legacy)

    def test_a_failed_check_is_rendered_too(self):
        def boom():
            raise OSError("netsh died")
        results = i18n.localize([r.to_dict() for r in d.run_all(fake_context(scan=boom)).results], "en")
        failed = next(r for r in results if r["key"] == "interference")
        self.assertEqual((failed["title"], failed["summary"]), ("Channel interference", "This check could not run"))


class CliTests(unittest.TestCase):
    def test_json_output_for_selected_checks_without_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["STABLEINTERNET_DATA"] = tmp
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = d.main(["--only", "2,13", "--json", "--no-save"])
                payload = json.loads(out.getvalue())
                with Storage(os.path.join(tmp, "metrics.db")) as db:
                    saved = db.list_diagnostic_runs()
            finally:
                os.environ.pop("STABLEINTERNET_DATA", None)
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in payload["results"]], [2, 13])
        self.assertIsNone(payload["run_id"])
        self.assertEqual(saved, [])  # --no-save really saves nothing


if __name__ == "__main__":
    unittest.main()
