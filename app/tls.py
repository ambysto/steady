"""TLS for the app's own HTTPS requests (ADR-0025): the speed test's loads and an https probe.

Python's `ssl` checks a certificate against the root certificates already in Windows' store, and
never makes Windows fetch a missing one, as Windows does for its own clients. A fresh Windows (a new
virtual machine, a new install) carries few roots, so speed.cloudflare.com failed there with
CERTIFICATE_VERIFY_FAILED while a browser worked. `truststore` hands the check to Windows itself
(CertGetCertificateChain), which fetches a missing root and honours one a company or an antivirus
installed. Without `truststore` installed the standard library's check is used, as before.

Every connection gets its own context. truststore turns its inner context's checks off around the
handshake and back on afterwards; in 0.10.4 the turning back on is not locked, so two handshakes at
once on one context (the speed test opens 4 connections together) can leave it checking nothing for
every later connection. A context of its own per connection is never shared, whatever the version.
"""
from __future__ import annotations

import ssl


def context() -> ssl.SSLContext:
    """A new client context for one HTTPS connection, never shared between connections: hostname and
    certificate are always checked."""
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def untrusted(error: str) -> bool:
    """Whether a load's error (`Phase.error`) is a certificate the system could not verify."""
    return "CERTIFICATE_VERIFY_FAILED" in error or "SSLCertVerificationError" in error
