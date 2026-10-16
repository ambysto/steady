# ADR-0025: The app's HTTPS is verified by Windows, through truststore

- **Status:** Accepted
- **Date:** 2026-10-16
- **Related:** [ADR-0001](0001-python-stdlib-web-ui.md) (amended: one library outside the desktop shell), [ADR-0024](0024-windows-speed-test.md) (the speed test), check #14 in [DIAGNOSTICS.md](../DIAGNOSTICS.md)

## Context

The speed test's loads are the app's only HTTPS requests to the Internet (`app/bufferbloat.py`; the
HTTP 204 probe is plain HTTP unless a user sets an `https://` URL). They used the standard library's
`ssl`, which checks a server's certificate against the root certificates already in Windows' store.
Windows ships few roots and fetches a missing one from Windows Update when its own certificate code
(CryptoAPI) meets it; OpenSSL, inside Python, never asks it to. On a fresh Windows virtual machine the
speed test therefore failed in both directions with `CERTIFICATE_VERIFY_FAILED: unable to get local
issuer certificate` while the ICMP pings worked, and the card only said "Could not measure. Check the
connection". Opening speed.cloudflare.com in Edge on that machine did not fix it.
A one-off `Invoke-WebRequest` to the same address (PowerShell 5.1 uses CryptoAPI) returned 200 and
fixed the machine, which shows that Windows' own check is the one that works.

## Decision

1. **`app/tls.py` gives every HTTPS connection the app opens its own client context**: the speed test's
   loads (`bufferbloat._connect`) and an `https://` HTTP probe (`probe.http_204`). With
   [truststore](https://pypi.org/project/truststore/) installed it is `truststore.SSLContext`, which
   hands the certificate check to the operating system (on Windows `CertGetCertificateChain`, so a
   missing root is fetched as for Edge's downloads or PowerShell, and a root a company or an
   antivirus installed is honoured). Hostname and certificate are always checked; nothing is ever
   accepted unverified. **Each connection gets a new context, never a shared one**: truststore turns
   its inner context's checks off around the handshake and back on afterwards, and in 0.10.4 the
   turning back on is not locked, so two handshakes at once on one context (the speed test opens 4
   connections together) can leave it checking nothing for every later connection (found in review;
   0.10.5 fixes it upstream). A context per connection costs little next to a 25 MB request and does
   not depend on the library's locking.
2. **truststore is optional at run time.** Without it the context is `ssl.create_default_context()`,
   exactly as before, so the monitor, server and tests still run on the standard library alone, as
   ADR-0001 asks. `requirements.txt` pins it and the packaged build carries it (and its licence in
   `THIRD-PARTY-NOTICES.txt`). It is pure Python, MIT-licensed, maintained by the people behind
   urllib3 and vendored by pip itself; pinned to 0.10.4 (August 2025) rather than 0.10.5, four days
   old; moving to 0.10.5 later is a pin change, and decision 1 stays either way.
3. **The card says why when Windows could not verify the certificate.** `speedtest.result` adds
   `untrusted`: a direction without a figure whose error is a certificate failure. The card then shows
   `ui.speed.untrusted` (check the date and time, and that Windows Update can be reached) instead of
   `ui.speed.failed`. That is what is left when Windows itself cannot fetch the root: automatic root
   updates turned off by policy, or Windows Update out of reach.

## Consequences

- ✅ The speed test works on a fresh Windows and on networks whose root only Windows knows, with no
  step for the user.
- ✅ A certificate failure is named instead of looking like a broken connection.
- ⚠️ One more third-party library in the packaged app, outside the desktop shell. Updating it is a
  deliberate pin change.
- ⚠️ Verifying a certificate may make Windows contact Microsoft's certificate service (Windows
  Update) for a missing root, or the certificate authority for a missing intermediate certificate, as
  it does for any Windows program (the app does not ask for revocation checks); `PRIVACY.md`
  says so. The app sends nothing there itself.
- The Apple app is unchanged: URLSession already verifies with the system.
