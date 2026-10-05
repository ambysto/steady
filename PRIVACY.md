# Privacy policy

Ambysto Steady runs on your Windows PC, and on iPhone, iPad and Mac. **It has no account, no
telemetry, no analytics and no advertising, and it never sends your measurements, settings or any
personal data anywhere.**

## What stays on your device

**Windows.** Measurements (latency, packet loss, Wi‑Fi signal, outages), diagnostics results,
settings and logs are stored only in `%LOCALAPPDATA%\StableInternet`. The app's window talks to its
own local server at `127.0.0.1`, which is not reachable from other computers and requires a
per-session token. Uninstalling offers to delete this data.

**iPhone, iPad and Mac.** Per-minute measurements (latency, packet loss and jitter to each target
below) are stored only in the app's own storage on the device, kept for 30 days and excluded from
iCloud and device backups. Diagnostics results and the Mac's Wi‑Fi signal readings are shown but
not stored. Deleting the app deletes this data.

## Network connections the app makes

The app's purpose is to check your connection, so it contacts a few well-known public services.
These connections carry no personal data; like any connection, the service sees your public IP
address.

**Windows**

| When | What | To |
|---|---|---|
| Always, while monitoring | ICMP ping, about once a second | Your router, `1.1.1.1` (Cloudflare), `8.8.8.8` (Google) |
| Always, while monitoring | TCP connection without data, every 10 s | `1.1.1.1:443`, `8.8.8.8:443` |
| Always, while monitoring | HTTP request expecting an empty reply (status 204), every 10 s, identified as `AmbystoSteady/<version>` | `http://cp.cloudflare.com/generate_204` |
| Backup connection switching, only if you turn it on | TCP connection without data from each network adapter | `1.1.1.1:443`, `8.8.8.8:443` |
| When you run diagnostics | DNS lookups of `google.com`, `cloudflare.com`, `microsoft.com`, `youtube.com`, `facebook.com` | The DNS servers your PC uses, your router, `1.1.1.1`, `8.8.8.8`, `9.9.9.9` (Quad9) |
| When you start the load test and confirm it | Download and upload of test data (about 25 MB per request) | `speed.cloudflare.com` |

You can change the monitored targets in the settings file.

**iPhone, iPad and Mac.** The app measures only while it is open; nothing runs in the background.

| When | What | To |
|---|---|---|
| While the app is open | ICMP ping, about once a second | Your router, `1.1.1.1` (Cloudflare), `8.8.8.8` (Google) |
| While the app is open | TCP connection without data, every 10 s | `1.1.1.1:443`, `8.8.8.8:443` |
| When the app opens or connects to a network, when the router changes, and when you tap refresh | DNS lookups of `google.com`, `cloudflare.com`, `microsoft.com`, `youtube.com`, `facebook.com` | The DNS servers your device uses, your router, `1.1.1.1`, `8.8.8.8`, `9.9.9.9` (Quad9) |
| When you start the load test and confirm it | Download and upload of test data (about 25 MB per request), identified as `AmbystoSteady/<version>` | `speed.cloudflare.com` |

The privacy policies of these services apply to the connections they receive:
[Cloudflare](https://www.cloudflare.com/privacypolicy/),
[Google Public DNS](https://developers.google.com/speed/public-dns/privacy),
[Quad9](https://quad9.net/privacy/policy/).

## Permissions on iPhone, iPad and Mac

- **Local network:** the system asks once whether the app may reach devices on your network. It
  is used only to ping your router and to ask it for DNS answers, which tells Wi‑Fi problems
  apart from Internet problems. If you decline, the router is simply not measured.
- **Location is not requested.** The app therefore cannot read the name (SSID) or hardware address
  (BSSID) of your Wi‑Fi network, and does not.

## Reporting a problem (iPhone, iPad and Mac)

Settings > Report a problem shows a text report in full before anything can leave your device, and
the app never sends it: you choose to share it, email it to us or copy it. The report holds the app
and system versions, the device model (not its name), the app language, the connection state, the
checks' verdicts, the last hour of per-minute measurement counts, the app's own recent log and short
summaries of crashes the system reported to the app. It contains no network names, and IP addresses
are replaced with `<address>` (except the public resolvers listed above). Crash summaries are kept
on the device only for this purpose.

## Components from Microsoft and Apple

**Windows.** The app's window uses Microsoft Edge WebView2 and Windows notifications, which are part
of Windows and follow the [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement)
and your Windows privacy settings.

**iPhone, iPad and Mac.** The app uses only Apple's own system frameworks and no third-party code.
Crash reports and usage data that Apple collects follow your device's analytics settings
(including whether to share them with app developers) and the
[Apple Privacy Policy](https://www.apple.com/legal/privacy/).

## Changes and contact

Changes to this policy are published with the app's source at
[github.com/ambysto/steady](https://github.com/ambysto/steady). Questions: contact@ambysto.com.
