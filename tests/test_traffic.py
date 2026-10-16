"""app/traffic.py: throughput from interface counters, apps from the socket tables, the
connection's facts. Pure parts with synthetic tables; the Windows calls read-only on this machine."""
import socket
import struct
import sys
import unittest

from app import traffic


def tcp4(*rows):
    """rows: (state, remote, pid) -> MIB_TCPTABLE_OWNER_PID bytes."""
    out = struct.pack("<I", len(rows))
    for state, remote, pid in rows:
        addr = int.from_bytes(socket.inet_aton(remote), "little")
        out += struct.pack("<6I", state, 0, 0, addr, 443, pid)
    return out


def tcp6(*rows):
    out = struct.pack("<I", len(rows))
    for state, remote, pid in rows:
        out += (bytes(16) + struct.pack("<2I", 0, 0) + socket.inet_pton(socket.AF_INET6, remote)
                + struct.pack("<4I", 0, 443, state, pid))
    return out


def udp4(*rows):
    out = struct.pack("<I", len(rows))
    for local, pid in rows:
        out += struct.pack("<3I", int.from_bytes(socket.inet_aton(local), "little"), 53, pid)
    return out


def udp6(*rows):
    out = struct.pack("<I", len(rows))
    for local, pid in rows:
        out += socket.inet_pton(socket.AF_INET6, local) + struct.pack("<3I", 0, 53, pid)
    return out


EST, LISTEN, TIME_WAIT = 5, 2, 11


class TableTests(unittest.TestCase):
    def test_ipv4_and_ipv6_tables_parse(self):
        self.assertEqual(traffic.parse_tcp_table(tcp4((EST, "203.0.113.10", 42)), 2), [(42, EST, "203.0.113.10")])
        self.assertEqual(traffic.parse_tcp_table(tcp6((EST, "2001:db8::1", 7)), 23), [(7, EST, "2001:db8::1")])
        self.assertEqual(traffic.parse_udp_table(udp4(("0.0.0.0", 9)), 2), [(9, "0.0.0.0")])
        self.assertEqual(traffic.parse_udp_table(udp6(("::1", 9)), 23), [(9, "::1")])

    def test_only_established_connections_to_other_machines_count(self):
        tcp = (traffic.parse_tcp_table(tcp4((EST, "203.0.113.10", 10), (EST, "127.0.0.1", 10), (LISTEN, "0.0.0.0", 10),
                                            (TIME_WAIT, "203.0.113.11", 0), (EST, "127.0.0.1", 11)), 2)
               + traffic.parse_tcp_table(tcp6((EST, "::ffff:127.0.0.1", 12), (EST, "2001:db8::2", 10)), 23))
        udp = (traffic.parse_udp_table(udp4(("0.0.0.0", 10), ("127.0.0.1", 10), ("0.0.0.0", 13)), 2)
               + traffic.parse_udp_table(udp6(("::", 10)), 23))
        counts = traffic.count_by_process(tcp, udp)
        # 11 and 12 only talk to loopback; 13 only has a UDP socket (mostly listeners: left out)
        self.assertEqual(counts, {10: {"tcp": 2, "udp": 2}})


class AppGroupingTests(unittest.TestCase):
    def test_processes_of_one_program_are_one_row_busiest_first(self):
        counts = {1: {"tcp": 4, "udp": 1}, 2: {"tcp": 3, "udp": 0}, 3: {"tcp": 2, "udp": 0}, 4: {"tcp": 9, "udp": 0}}
        exes = {1: "browser.exe", 2: "browser.exe", 3: "tool.exe", 4: "svchost.exe"}
        paths = {1: r"C:\Apps\browser.exe", 2: r"C:\Apps\browser.exe", 3: None, 4: r"C:\Windows\svchost.exe"}
        describe = {r"C:\Apps\browser.exe": "Web Browser", r"C:\Windows\svchost.exe": None}.get
        rows = traffic.group_apps(counts, exes, paths, describe)
        self.assertEqual([(r["name"], r["processes"], r["tcp"], r["udp"]) for r in rows],
                         [("svchost", 1, 9, 0), ("Web Browser", 2, 7, 1), ("tool", 1, 2, 0)])

    def test_unknown_pid_and_limit(self):
        rows = traffic.group_apps({i: {"tcp": i, "udp": 0} for i in range(1, 30)}, {}, {}, lambda p: None, limit=5)
        self.assertEqual([r["name"] for r in rows], ["PID 29", "PID 28", "PID 27", "PID 26", "PID 25"])

    def test_app_list_caches_and_describes_each_path_once(self):
        now = [100.0]
        described, owner_calls = [], []
        tcp = traffic.parse_tcp_table(tcp4((EST, "203.0.113.10", 10), (EST, "203.0.113.11", 0)), 2)

        def owners():
            owner_calls.append(1)
            return tcp, []
        apps = traffic.AppList(owners=owners, exes=lambda: {10: "app.exe"}, path_of=lambda pid: r"C:\app.exe",
                               describe=lambda p: described.append(p) or "App", clock=lambda: now[0])
        self.assertEqual(apps.apps(), [{"name": "App", "exe": "app.exe", "processes": 1, "tcp": 1, "udp": 0}])
        apps.apps()
        self.assertEqual(len(owner_calls), 1)             # cached
        now[0] += traffic.APPS_CACHE_S
        apps.apps()
        self.assertEqual((len(owner_calls), described), (2, [r"C:\app.exe"]))

    def test_no_table_means_unknown_not_empty(self):
        self.assertIsNone(traffic.AppList(owners=lambda: None).apps())


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.index = 7
        self.counters = [0, 0]

    def meter(self):
        def read_row(index):
            if index is None:
                return None
            return {"index": index, "alias": "Wi-Fi", "description": "Card", "kind": "wifi", "rx_link_bps": 10**9,
                    "tx_link_bps": 10**9, "in_octets": self.counters[0], "out_octets": self.counters[1]}
        return traffic.Meter(route=lambda: {"interface_index": self.index} if self.index else None, read_row=read_row,
                             clock=lambda: self.now, start_thread=False)

    def step(self, meter, down_bytes, up_bytes, seconds=1.0):
        self.now += seconds
        self.counters[0] += down_bytes
        self.counters[1] += up_bytes
        meter.sample()

    def test_rates_in_bits_per_second(self):
        self.assertIsNone(traffic.rates(None, (1, 0, 0)))
        self.assertEqual(traffic.rates((1, 0, 0), (3, 1000, 250)), (4000, 1000))
        self.assertIsNone(traffic.rates((1, 1000, 0), (2, 10, 0)))      # counters reset
        self.assertIsNone(traffic.rates((1, 0, 0), (1, 10, 0)))         # no time passed

    def test_series_of_rates_and_interface(self):
        m = self.meter()
        m.sample()
        self.step(m, 125_000, 12_500)
        snap = m.snapshot()
        self.assertEqual(snap["series"], [[1001.0, 1_000_000, 100_000]])
        self.assertEqual(snap["interface"]["kind"], "wifi")
        self.assertNotIn("in_octets", snap["interface"])

    def test_a_new_interface_starts_a_new_series_without_a_spike(self):
        m = self.meter()
        m.sample()
        self.step(m, 1000, 0)
        self.index = 9
        self.counters = [10**12, 10**12]            # another card: unrelated counters
        self.step(m, 0, 0)
        self.assertEqual(len(m.snapshot()["series"]), 1)
        self.step(m, 1000, 0)
        self.assertEqual(m.snapshot()["series"][-1][1], 8000)

    def test_no_route_means_no_interface(self):
        m = self.meter()
        m.sample()
        self.index = None
        self.step(m, 0, 0)
        self.assertIsNone(m.snapshot()["interface"])

    def test_old_samples_are_dropped(self):
        m = self.meter()
        m.sample()
        for _ in range(traffic.HISTORY_S + 30):
            self.step(m, 10, 10)
        series = m.snapshot()["series"]
        self.assertLessEqual(len(series), traffic.HISTORY_S + 1)
        self.assertGreaterEqual(series[0][0], self.now - traffic.HISTORY_S)

    def test_the_thread_starts_on_demand_and_stops_when_nobody_asks(self):
        now = [0.0]
        m = traffic.Meter(route=lambda: None, read_row=lambda i: None, clock=lambda: now[0])
        m.snapshot()
        thread = m._thread
        assert thread is not None
        now[0] += traffic.IDLE_STOP_S + 1
        thread.join(traffic.SAMPLE_S * 3)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(m._thread)


class SnapshotTests(unittest.TestCase):
    def test_connection_facts_are_cached_per_interface(self):
        now = [0.0]
        dns_calls = []
        meter = traffic.Meter(route=lambda: None, read_row=lambda i: None, clock=lambda: now[0], start_thread=False)
        t = traffic.Traffic(meter=meter, app_list=traffic.AppList(owners=lambda: None), local_address=lambda: "192.0.2.5",
                            dns=lambda: dns_calls.append(1) or {7: ["192.0.2.1"]}, clock=lambda: now[0])
        self.assertEqual(t.connection({"index": 7}), {"local_ipv4": "192.0.2.5", "dns": ["192.0.2.1"]})
        t.connection({"index": 7})
        self.assertEqual(len(dns_calls), 1)
        self.assertEqual(t.connection(None)["dns"], [])       # no interface: no DNS lookup by index
        snap = t.snapshot()
        self.assertEqual(sorted(snap), ["apps", "connection", "interface", "series", "ts"])
        self.assertIsNone(snap["apps"])


@unittest.skipUnless(sys.platform == "win32", "Windows API")
class WindowsReadTests(unittest.TestCase):
    """Read-only calls on this machine: layouts must match what Windows fills in."""

    def test_struct_layouts(self):
        self.assertEqual(traffic.ctypes.sizeof(traffic._MibIfRow2), 1352)
        self.assertEqual((traffic._MibIfRow2.InOctets.offset, traffic._MibIfRow2.OutOctets.offset), (1208, 1280))

    def test_loopback_interface_reads(self):
        row = traffic.interface_row(1)              # the loopback pseudo-interface is always index 1
        assert row is not None
        self.assertEqual(row["index"], 1)
        self.assertGreaterEqual(row["in_octets"], 0)

    def test_socket_tables_and_process_names(self):
        owners = traffic.socket_owners()
        self.assertIsNotNone(owners)
        import os
        exes = traffic.process_exes()
        self.assertIn(os.getpid(), exes)
        self.assertTrue(exes[os.getpid()].lower().endswith(".exe"))
        path = traffic.process_path(os.getpid())
        self.assertTrue(path and path.lower().endswith(".exe"))


if __name__ == "__main__":
    unittest.main()
