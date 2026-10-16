"""HTTPS verified by the system (ADR-0025): app/tls.py and the connections that use it."""
import importlib.util
import ssl
import sys
import unittest
from unittest import mock

from app import bufferbloat as bb
from app import tls

HAS_TRUSTSTORE = importlib.util.find_spec("truststore") is not None


class ContextTests(unittest.TestCase):
    def setUp(self):
        tls.context.cache_clear()
        self.addCleanup(tls.context.cache_clear)

    def test_always_checks_the_hostname_and_the_certificate(self):
        ctx = tls.context()
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    @unittest.skipUnless(HAS_TRUSTSTORE, "truststore is not installed (pip install -r requirements.txt)")
    def test_uses_truststore_when_installed(self):
        import truststore
        self.assertIsInstance(tls.context(), truststore.SSLContext)

    def test_falls_back_to_the_standard_library(self):
        with mock.patch.dict(sys.modules, {"truststore": None}):    # import truststore -> ImportError
            ctx = tls.context()
        self.assertIsInstance(ctx, ssl.SSLContext)
        self.assertEqual(type(ctx).__module__, "ssl")
        self.assertTrue(ctx.check_hostname)

    def test_one_context_for_every_request(self):
        self.assertIs(tls.context(), tls.context())


class ConnectTests(unittest.TestCase):
    def test_https_uses_the_context(self):
        conn, path = bb._connect("https://speed.cloudflare.com/__down?bytes=10", 5)
        self.assertIs(conn._context, tls.context())
        self.assertEqual(path, "/__down?bytes=10")

    def test_http_has_no_tls(self):
        conn, path = bb._connect("http://127.0.0.1:8080/up", 5)
        self.assertNotIn("HTTPS", type(conn).__name__)
        self.assertEqual(path, "/up")


class UntrustedTests(unittest.TestCase):
    def test_a_certificate_failure_reads_as_untrusted(self):
        self.assertTrue(tls.untrusted("OSError: SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] "
                                      "certificate verify failed: unable to get local issuer certificate (_ssl.c:1006)"))
        self.assertTrue(tls.untrusted("OSError: SSLCertVerificationError: (-2146762487, 'A certificate chain "
                                      "processed, but terminated in a root certificate which is not trusted.')"))

    def test_other_failures_do_not(self):
        for error in ("", "OSError: TimeoutError: timed out", "ServerRefused: HTTP 429",
                      "OSError: SSLError: [SSL: WRONG_VERSION_NUMBER] wrong version number"):
            self.assertFalse(tls.untrusted(error), error)


if __name__ == "__main__":
    unittest.main()
