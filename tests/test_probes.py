import socket
import struct
import sys
import threading
import unittest

from app import dnsprobe


def make_reply(query: bytes, rcode: int = 0, answers: int = 1, qid: int | None = None) -> bytes:
    """Fake server reply: echo the question, set QR/RA and the given rcode/ancount."""
    rid = struct.unpack("!H", query[:2])[0] if qid is None else qid
    flags = 0x8180 | rcode
    return struct.pack("!HHHHHH", rid, flags, 1, answers, 0, 0) + query[12:]


class FakeDnsServer:
    """UDP server on loopback. `behaviour(query) -> list[bytes]` gives the datagrams to send."""

    def __init__(self, behaviour):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.behaviour = behaviour
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            for out in self.behaviour(data):
                self.sock.sendto(out, addr)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(1)
        self.sock.close()


class BuildQueryTests(unittest.TestCase):
    def test_known_packet(self):
        qid, pkt = dnsprobe.build_query("a.bc", qid=0x1234)
        self.assertEqual(qid, 0x1234)
        self.assertEqual(
            pkt,
            bytes.fromhex("1234" "0100" "0001" "0000" "0000" "0000")
            + b"\x01a\x02bc\x00" + bytes.fromhex("0001" "0001"),
        )

    def test_trailing_dot_and_idna(self):
        _, a = dnsprobe.build_query("example.com.", qid=1)
        _, b = dnsprobe.build_query("example.com", qid=1)
        self.assertEqual(a, b)
        _, pkt = dnsprobe.build_query("bücher.de", qid=1)
        self.assertIn(b"xn--bcher-kva", pkt)

    def test_bad_names(self):
        for bad in ("", ".", "a" * 64 + ".com"):
            with self.assertRaises(dnsprobe.DnsError, msg=bad):
                dnsprobe.build_query(bad)

    def test_random_ids_differ(self):
        ids = {dnsprobe.build_query("x.com")[0] for _ in range(20)}
        self.assertGreater(len(ids), 1)


class ParseResponseTests(unittest.TestCase):
    def setUp(self):
        self.qid, self.query = dnsprobe.build_query("example.com", qid=7)

    def test_ok(self):
        self.assertEqual(dnsprobe.parse_response(make_reply(self.query, answers=2), 7), (0, 2))

    def test_nxdomain(self):
        self.assertEqual(dnsprobe.parse_response(make_reply(self.query, rcode=3, answers=0), 7), (3, 0))

    def test_wrong_id(self):
        with self.assertRaises(dnsprobe.DnsError):
            dnsprobe.parse_response(make_reply(self.query, qid=8), 7)

    def test_query_is_not_a_response(self):
        with self.assertRaises(dnsprobe.DnsError):
            dnsprobe.parse_response(self.query, 7)

    def test_short(self):
        with self.assertRaises(dnsprobe.DnsError):
            dnsprobe.parse_response(b"\x00\x07", 7)


class ServerAddressTests(unittest.TestCase):
    def test_usable(self):
        for addr in ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"):
            self.assertTrue(dnsprobe.is_usable_server(addr), addr)

    def test_unusable(self):
        for addr in ("fe80::1", "fe80::1%12", "not-an-ip", ""):
            self.assertFalse(dnsprobe.is_usable_server(addr), addr)

    def test_probe_rejects_link_local_without_network(self):
        res = dnsprobe.probe("fe80::1")
        self.assertFalse(res.ok)
        self.assertIn("unusable", res.error)


class ProbeTests(unittest.TestCase):
    def test_ok_reply(self):
        with FakeDnsServer(lambda q: [make_reply(q)]) as srv:
            res = dnsprobe.probe("127.0.0.1", "example.com", timeout=1, port=srv.port)
        self.assertTrue(res.ok)
        self.assertEqual((res.rcode, res.answers), (0, 1))
        self.assertGreaterEqual(res.rtt_ms, 0)

    def test_nxdomain_has_rtt_but_is_not_ok(self):
        with FakeDnsServer(lambda q: [make_reply(q, rcode=3, answers=0)]) as srv:
            res = dnsprobe.probe("127.0.0.1", "nope.example", timeout=1, port=srv.port)
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "NXDOMAIN")
        self.assertIsNotNone(res.rtt_ms)

    def test_timeout(self):
        with FakeDnsServer(lambda q: []) as srv:
            res = dnsprobe.probe("127.0.0.1", timeout=0.3, port=srv.port)
        self.assertEqual((res.ok, res.rtt_ms, res.error), (False, None, "timeout"))

    def test_stray_packet_is_ignored_until_the_real_reply(self):
        stray = struct.pack("!HHHHHH", 0xFFFF, 0x8180, 0, 0, 0, 0)
        with FakeDnsServer(lambda q: [stray, make_reply(q)]) as srv:
            res = dnsprobe.probe("127.0.0.1", timeout=1, port=srv.port)
        self.assertTrue(res.ok)

    def test_closed_port_does_not_raise(self):
        # Nothing listens: Windows reports ICMP port unreachable as ConnectionReset on recv.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        res = dnsprobe.probe("127.0.0.1", timeout=0.5, port=port)
        self.assertFalse(res.ok)
        self.assertTrue(res.error)

    def test_bad_name_returns_result(self):
        for bad in ("", "a" * 64 + ".com"):
            res = dnsprobe.probe("127.0.0.1", name=bad, timeout=0.1)
            self.assertFalse(res.ok, bad)
            self.assertTrue(res.error, bad)


class BenchmarkTests(unittest.TestCase):
    def test_counts_and_stats(self):
        def behaviour(q):
            name_len = q[12]
            if q[13:13 + name_len] == b"bad":
                return [make_reply(q, rcode=2, answers=0)]  # SERVFAIL = failure
            if q[13:13 + name_len] == b"gone":
                return [make_reply(q, rcode=3, answers=0)]  # NXDOMAIN = valid timing
            return [make_reply(q)]

        with FakeDnsServer(behaviour) as srv:
            orig = dnsprobe.probe
            dnsprobe.probe = lambda s, n, t=2.0, port=53: orig(s, n, t, port=srv.port)
            try:
                (b,) = dnsprobe.benchmark(["127.0.0.1"], names=("a.com", "bad.com", "gone.com", "b.com"), timeout=1)
            finally:
                dnsprobe.probe = orig
        # label comparison above uses the first label: "a", "bad", "gone", "b"
        self.assertEqual((b.sent, b.replies), (4, 4))
        self.assertEqual(b.failures, 1)
        self.assertEqual(len(b.rtts_ms), 3)
        self.assertIsNotNone(b.avg_ms)

    def test_all_timeouts(self):
        with FakeDnsServer(lambda q: []) as srv:
            orig = dnsprobe.probe
            dnsprobe.probe = lambda s, n, t=2.0, port=53: orig(s, n, 0.2, port=srv.port)
            try:
                (b,) = dnsprobe.benchmark(["127.0.0.1"], names=("a.com", "b.com"))
            finally:
                dnsprobe.probe = orig
        self.assertEqual((b.sent, b.replies, b.failures), (2, 0, 2))
        self.assertIsNone(b.avg_ms)


@unittest.skipUnless(sys.platform == "win32", "IcmpSendEcho is Windows-only")
class IcmpTests(unittest.TestCase):
    def setUp(self):
        from app import icmp
        self.icmp = icmp

    def test_loopback_ok(self):
        with self.icmp.Pinger() as p:
            res = p.ping("127.0.0.1", 1000)
        self.assertTrue(res.ok, res)
        self.assertIsNotNone(res.rtt_ms)
        self.assertLess(res.rtt_ms, 50)
        self.assertEqual(res.status, self.icmp.IP_SUCCESS)

    def test_repeated_pings_reuse_handle(self):
        with self.icmp.Pinger() as p:
            results = [p.ping("127.0.0.1", 1000) for _ in range(5)]
        self.assertTrue(all(r.ok for r in results))

    def test_unreachable_does_not_raise(self):
        with self.icmp.Pinger() as p:
            res = p.ping("192.0.2.1", 300)  # TEST-NET-1, never answers
        self.assertFalse(res.ok)
        self.assertIsNone(res.rtt_ms)
        self.assertTrue(res.error)

    def test_invalid_address(self):
        with self.icmp.Pinger() as p:
            for bad in ("", "not-an-ip", "999.1.1.1", "::1"):
                res = p.ping(bad)
                self.assertFalse(res.ok, bad)
                self.assertEqual(res.status, -1)

    def test_closed_pinger(self):
        p = self.icmp.Pinger()
        p.close()
        p.close()  # idempotent
        self.assertFalse(p.ping("127.0.0.1").ok)

    def test_status_text(self):
        self.assertEqual(self.icmp.status_text(11010), "timed out")
        self.assertIn("1234", self.icmp.status_text(1234))


if __name__ == "__main__":
    unittest.main()
