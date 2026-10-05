# Ambysto Steady for iPhone, iPad and Mac

One SwiftUI multiplatform app (ADR-0009). Layout, versions, signing and how texts and rules are shared with the Windows version: [ADR-0010](../docs/adr/0010-apple-app-structure.md). What iOS allows compared with Android and Windows: [docs/MOBILE.md](../docs/MOBILE.md).

| Path | Content |
|---|---|
| `Steady.xcodeproj` | One app target `Steady`: iPhone, iPad, native Mac. Minimum iOS/iPadOS/macOS 26.0 |
| `Steady/` | SwiftUI views. `Resources/*.xcstrings` are generated, do not edit them in Xcode |
| `SteadyKit/` | Swift package with everything testable: localization runtime, network path model, diagnosis rules |
| `Config/Base.xcconfig` | Shared build settings; `Local.xcconfig` (not tracked) adds your team ID |

Requirements: Xcode 27 or later on a Mac.

## First-time setup

1. Copy `Config/Local.xcconfig.example` to `Config/Local.xcconfig` and set `DEVELOPMENT_TEAM`. With a free personal team keep `STEADY_BUNDLE_ID_SUFFIX = .dev`, so the app is `com.ambysto.steady.dev` and the production ID stays free for the Ambysto team.
2. Xcode > Settings > Accounts: sign in with the Apple ID of that team.
3. Open `Steady.xcodeproj`, choose the `Steady` scheme and a destination (My Mac, a connected iPad...).

On an iPad or iPhone, the first run also needs Developer Mode (Settings > Privacy & Security) and, with a personal team, trusting the developer profile (Settings > General > VPN & Device Management). Apps signed by a personal team expire after 7 days; run them again from Xcode.

## Everyday commands

```bash
swift test --package-path apple/SteadyKit                 # rules and localization, no simulator needed
python scripts/locales_to_xcstrings.py                     # after any change in app/locales/*.json
xcodebuild -project apple/Steady.xcodeproj -scheme Steady -destination 'generic/platform=macOS' CODE_SIGNING_ALLOWED=NO build
xcodebuild -project apple/Steady.xcodeproj -scheme Steady -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

## Texts

Every user-visible string comes from `app/locales/*.json`. To add one: add the key to `en.json` and the other 8 catalogs, run `python scripts/locales_to_xcstrings.py`, then render it with `Localizer` (`text("ui.path.connected")`, or a `Message` with parameters). Pass strings to SwiftUI as values (`Text(text(...))`), never as literals, so Xcode does not extract or look up keys on its own.

## Measurements

While the Overview screen is open, `LiveMonitor` measures with the Windows monitor's defaults (`app/config.py`): ICMP echo every second (900 ms timeout) to the router, `1.1.1.1` and `8.8.8.8`, and a TCP handshake to port 443 of both every 10 seconds (3 s timeout). Samples are grouped per minute exactly like `app/monitor.py` (sent, lost, jitter = mean absolute difference between consecutive replies), and check #5 runs over the completed minutes of the last hour.

- iOS gives apps no continuous background time, so nothing is measured while the app is in the background, and the history is kept in memory only (for now).
- ICMP uses an unprivileged datagram socket **connected** to the target: the macOS App Sandbox refuses to read replies on an unconnected ICMP socket (`EPERM`) unless the app also asks for `network.server`.
- Pinging the router is local network access: iOS and macOS ask the user once, with the `NSLocalNetworkUsageDescription` text generated from `ui.permission.local_network`.
- The iOS Simulator does not report the router address, IPv4/IPv6 or DNS support of the path, so the router row is missing there; Internet pings and TCP probes work.

## Diagnosis rules

A rule is ported from `app/diagnostics.py` together with its vectors in `spec/diagnosis/`. The Swift tests (`DiagnosisVectorTests`) and `tests/test_diagnosis_vectors.py` run the same cases; a change to a rule changes the vectors and both implementations.

| # | Check | Swift | Vectors | Inputs on Apple platforms |
|---|---|---|---|---|
| 5 | Ping quality | `PingQuality` | `ping.json` | `LiveMonitor` (ICMP + TCP while the app is open) |
| 6 | DNS benchmark | `DNSBenchmark` | `dns.json` | `DNSCheck`: system DNS servers from `res_ninit` (the `CResolver` C target, libresolv), raw UDP queries as in `app/dnsprobe.py`; runs when connected and when the router changes, or from its refresh button |
| 2 | Wi‑Fi signal | `WiFiSignal` | `signal.json` | Mac only: `WiFiReader` (CoreWLAN, works in the sandbox) every 5 s; RSSI, channel, PHY mode and transmit rate. The network name needs location permission and the receive rate is not available, so both show as unknown. iOS has no API for the signal |
| 14 | Bufferbloat | `Bufferbloat` | `bufferbloat.json` | `BufferbloatTest`, on demand after a confirmation (moves up to ~200 MB): pings the router and 1.1.1.1 every 0.2 s for 4 s idle, then 10 s while downloading and 10 s while uploading over 4 connections to speed.cloudflare.com (25 MB requests, at most 100 MB per direction), dropping the first 2 s of each loaded phase, as `app/bufferbloat.py` does |
