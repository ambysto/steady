import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import probe


class _Handler(BaseHTTPRequestHandler):
    status = 204

    def do_GET(self):
        self.send_response(type(self).status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class HttpServer:
    def __init__(self, status=204):
        handler = type("H", (_Handler,), {"status": status})
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/generate_204"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TcpTests(unittest.TestCase):
    def test_connect_ok(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        r = probe.tcp_connect(f"127.0.0.1:{srv.getsockname()[1]}", 2)
        self.assertTrue(r.ok)
        self.assertGreaterEqual(r.rtt_ms, 0)

    def test_refused_is_a_failure_not_an_exception(self):
        r = probe.tcp_connect(f"127.0.0.1:{closed_port()}", 2)
        self.assertFalse(r.ok)
        self.assertIsNone(r.rtt_ms)
        self.assertTrue(r.error)

    def test_timeout(self):
        # A listening socket with a full backlog stops completing handshakes.
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(0)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        held = []
        self.addCleanup(lambda: [s.close() for s in held])
        for _ in range(6):  # fill the accept queue
            c = socket.socket()
            c.settimeout(0.3)
            try:
                c.connect(("127.0.0.1", port))
            except OSError:
                pass
            held.append(c)
        r = probe.tcp_connect(f"127.0.0.1:{port}", 0.4)
        self.assertFalse(r.ok)

    def test_bad_target(self):
        for bad in ("nohost", "host:port", "", ":"):
            r = probe.tcp_connect(bad, 1)
            self.assertFalse(r.ok, bad)

    def test_ipv6_literal(self):
        r = probe.tcp_connect(f"[::1]:{closed_port()}", 1)
        self.assertFalse(r.ok)  # nothing listens; must not crash on parsing


class HttpTests(unittest.TestCase):
    def test_204_is_ok(self):
        with HttpServer(204) as s:
            r = probe.http_204(s.url, 2)
        self.assertTrue(r.ok)
        self.assertGreaterEqual(r.rtt_ms, 0)

    def test_captive_portal_style_answers_are_not_connectivity(self):
        for status in (200, 302):
            with HttpServer(status) as s:
                r = probe.http_204(s.url, 2)
            self.assertFalse(r.ok, status)
            self.assertIn("captive", r.error)

    def test_server_error(self):
        with HttpServer(503) as s:
            r = probe.http_204(s.url, 2)
        self.assertFalse(r.ok)
        self.assertIn("503", r.error)

    def test_connection_refused(self):
        r = probe.http_204(f"http://127.0.0.1:{closed_port()}/x", 2)
        self.assertFalse(r.ok)

    def test_bad_urls_do_not_raise(self):
        for bad in ("", "ftp://x/y", "http://", "not a url", "http:///path"):
            self.assertFalse(probe.http_204(bad, 1).ok, bad)

    def test_unresolvable_host_fails(self):
        self.assertFalse(probe.http_204("http://no-such-host.invalid/generate_204", 3).ok)


class DispatchTests(unittest.TestCase):
    def test_dispatch(self):
        with HttpServer(204) as s:
            self.assertTrue(probe.run_probe("http", s.url, 2).ok)
        self.assertFalse(probe.run_probe("icmp", "1.1.1.1").ok)


if __name__ == "__main__":
    unittest.main()
