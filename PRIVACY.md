# Privacy policy

Ambysto Steady runs on your PC. **It has no account, no telemetry, no analytics and no
advertising, and it never sends your measurements, settings or any personal data anywhere.**

## What stays on your PC

Measurements (latency, packet loss, Wi‑Fi signal, outages), diagnostics results, settings and logs
are stored only in `%LOCALAPPDATA%\StableInternet`. The app's window talks to its own local
server at `127.0.0.1`, which is not reachable from other computers and requires a per-session
token. Uninstalling offers to delete this data.

## Network connections the app makes

The app's purpose is to check your connection, so it contacts a few well-known public services.
These connections carry no personal data; like any connection, the service sees your public IP
address.

| When | What | To |
|---|---|---|
| Always, while monitoring | ICMP ping, about once a second | Your router, `1.1.1.1` (Cloudflare), `8.8.8.8` (Google) |
| Always, while monitoring | TCP connection without data, every 10 s | `1.1.1.1:443`, `8.8.8.8:443` |
| Always, while monitoring | HTTP request expecting an empty reply (status 204), every 10 s, identified as `AmbystoSteady/<version>` | `http://cp.cloudflare.com/generate_204` |
| Backup connection switching, only if you turn it on | TCP connection without data from each network adapter | `1.1.1.1:443`, `8.8.8.8:443` |
| When you run diagnostics | DNS lookups of `google.com`, `cloudflare.com`, `microsoft.com`, `youtube.com`, `facebook.com` | The DNS servers your PC uses, your router, `1.1.1.1`, `8.8.8.8`, `9.9.9.9` (Quad9) |
| When you start the load test and confirm it | Download and upload of test data (about 25 MB per request) | `speed.cloudflare.com` |

You can change the monitored targets in the settings file. The privacy policies of these
services apply to the connections they receive:
[Cloudflare](https://www.cloudflare.com/privacypolicy/),
[Google Public DNS](https://developers.google.com/speed/public-dns/privacy),
[Quad9](https://quad9.net/privacy/policy/).

## Components from Microsoft

The app's window uses Microsoft Edge WebView2 and Windows notifications, which are part of
Windows and follow the [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement)
and your Windows privacy settings.

## Changes and contact

Changes to this policy are published with the app's source at
[github.com/ambysto/steady](https://github.com/ambysto/steady). Questions: contact@ambysto.com.
