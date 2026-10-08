# Ambysto Steady for iPhone, iPad and Mac

One SwiftUI multiplatform app (ADR-0009). Layout, versions, signing and how texts and rules are shared with the Windows version: [ADR-0010](../docs/adr/0010-apple-app-structure.md). What iOS allows compared with Android and Windows: [docs/MOBILE.md](../docs/MOBILE.md).

| Path | Content |
|---|---|
| `Steady.xcodeproj` | One app target `Steady` (product "Ambysto Steady.app", Swift module `Steady`): iPhone, iPad, native Mac. Minimum iOS/iPadOS/macOS 26.0 |
| `Steady/` | SwiftUI views: four tabs (Overview, Diagnostics, History, Settings) over one `AppModel` that measures for the whole app. `Resources/*.xcstrings` are generated, do not edit them in Xcode. `AppIcon.icon` is the app icon (Icon Composer): the Windows icon's Wi‑Fi arcs (`app/desktop.py` `draw_icon`) and dot with a small "A" and "S", in three layers (`arcs`, `dot`, `letters`) so the system gives them depth and glass. Light mode: graphite (#2B2B2E) on an off-white tile; dark mode: white on graphite; tinted and clear: white, tinted by the system. `ictool` inside Icon Composer.app renders every appearance for a check. Shapes in the SVGs are filled outlines, not strokes (the renderer lights open strokes as closed shapes and shows stray lines) |
| `SteadyKit/` | Swift package with everything testable: localization runtime, network path model, diagnosis rules |
| `Config/Base.xcconfig` | Shared build settings; `Local.xcconfig` (not tracked) adds your team ID. `ExportOptions.plist`: App Store Connect export, team filled in at release time |
| `Steady/PrivacyInfo.xcprivacy` | Privacy manifest: no tracking, no collected data, no APIs that need a reason |
| `scripts/release.sh`, `RELEASE.md` | Archive and upload to TestFlight from a Mac ([ADR-0013](../docs/adr/0013-apple-release-from-a-mac.md)); the organization's one-time setup |

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

## On an iPhone or iPad from the command line

After the first-time setup above (team in `Local.xcconfig`, Apple account signed in to Xcode, the device paired and in Developer Mode, a development certificate created in Xcode > Settings > Accounts > Manage Certificates), no Xcode window is needed:

```bash
xcrun devicectl list devices
xcodebuild -project apple/Steady.xcodeproj -scheme Steady -destination 'id=<UDID>' -derivedDataPath build/device -allowProvisioningUpdates build
xcrun devicectl device install app --device <UDID> build/device/Build/Products/Debug-iphoneos/Steady.app
xcrun devicectl device process launch --device <UDID> --terminate-existing com.ambysto.steady.dev
```

- The first `codesign` asks for the Mac's login password to use the signing key; "Always Allow" stops it asking again.
- The first launch fails until the device trusts the developer: Settings > General > VPN & Device Management > Apple Development > Trust. The app then asks for local network access; allow it so the router is measured.
- With a free personal team the app stops opening after 7 days; build and install again.
- To read the app's log on the Mac (one line per minute and target, no addresses), add `--console --environment-variables '{"OS_ACTIVITY_DT_MODE":"enable"}'` to the launch command. On a Mac build, use `/usr/bin/log show --info --predicate 'subsystem == "com.ambysto.steady"'` (in zsh a bare `log` is a shell builtin).

## Texts

Every user-visible string comes from `app/locales/*.json`. To add one: add the key to `en.json` and the other 8 catalogs, run `python scripts/locales_to_xcstrings.py`, then render it with `Localizer` (`text("ui.path.connected")`, or a `Message` with parameters). Pass strings to SwiftUI as values (`Text(text(...))`), never as literals, so Xcode does not extract or look up keys on its own.

Views read the shared `Localizer` from the environment (`@Environment(\.localizer) private var text`); `ContentView` builds it from the language chosen in Settings (`AppLanguage`, stored under the `language` key: `auto` or a catalog language) and sets `\.locale` to match. Do not create `Localizer()` in a view, or it ignores that choice. Problem reports render with an English `Localizer` on purpose.

## Measurements

While the app has a window open, whichever tab is shown, `LiveMonitor` measures with the Windows monitor's defaults (`app/config.py`): ICMP echo every second (900 ms timeout) to the router, `1.1.1.1` and `8.8.8.8`, and a TCP handshake to port 443 of both every 10 seconds (3 s timeout). Samples are grouped per minute exactly like `app/monitor.py` (sent, lost, jitter = mean absolute difference between consecutive replies), and check #5 runs over the completed minutes of the last hour.

- iOS gives apps no continuous background time, so nothing is measured while the app is in the background.
- Minutes are stored in SQLite (`Application Support/History/metrics.sqlite`, the folder excluded from backups, the `minute_stats` table of the Windows app, 30 days, excluded from backups; [ADR-0011](../docs/adr/0011-apple-measurement-history.md)), so check #5 keeps the last hour across launches. A partial minute is saved when measuring stops and merged if the app reopens within the same minute.
- ICMP uses an unprivileged datagram socket **connected** to the target: the macOS App Sandbox refuses to read replies on an unconnected ICMP socket (`EPERM`) unless the app also asks for `network.server`.
- Pinging the router is local network access: iOS and macOS ask the user once, with the `NSLocalNetworkUsageDescription` text generated from `ui.permission.local_network`. When it is declined the system refuses the send (`EHOSTUNREACH`/`EPERM`/`EACCES`): the router is left out of the live numbers and of the DNS check, not counted as lost, and the Overview says local network access is off. Seen on a real iPhone (iOS 26.6.2): with the permission declined, iOS still let pings to the router through, so there is nothing to refuse; with a VPN up, the router was refused instead, so then the note names the VPN (`ui.path.reason.vpn_blocks_router`) rather than the permission.
- **Mobile data has no router**: the path's gateway there is the carrier's, which never answers pings, so `NetworkPath.routers` is empty on cellular.
- **iOS pauses the app** in the background or when locked. A ping or probe round that takes longer than its timeout plus 0.5 s ran across such a pause and is dropped, and so are rounds started in the first 5 s after the app comes back, while the radio wakes up (the first TCP probes failed at once there).
- When the route changes (connected or not, the preferred interface such as `en0` → `utun5` when a VPN starts, the router), the live numbers start afresh so they do not mix two routes. The history keeps those minutes (they are real measurements), but check #5 leaves out the minute of the change and the next one, as on Windows, when traffic moves from one working route to another (losing the connection and getting the same route back is an outage and still counts: there is no check #4 here); the times are stored as `route_change` rows in the Windows `events` table, so this holds across launches.
- The iOS Simulator does not report the router address, IPv4/IPv6 or DNS support of the path, so the router row is missing there; Internet pings and TCP probes work.

## Floating monitor (Mac)

Window menu > Floating monitor (⌥⌘M), or the Overview: a non-activating panel on top of the other windows ([ADR-0022](../docs/adr/0022-mac-floating-monitor.md)). It is `Steady/FloatingMonitor/`: `FloatingMonitorController` (the `NSPanel`: position, size, the glass level, hiding it), `FloatingMonitorView` (the bar, the figures), `FloatingTable`, `FloatingScanButton` and `FloatingChart`. The logic it draws from is in `SteadyKit`: `Sweep` (the port of `web/js/ecg.js`), `SweepSeries` (the series) and `Throughput` (the interface byte counters). Its measuring is `AppModel.run()`, so it stops when the panel is hidden. There is no Apps list on purpose: a sandboxed app cannot see other apps' connections.

## Check my connection

The Overview's main button ([ADR-0020](../docs/adr/0020-check-fix-result-flow.md) point 11): `AppModel.runCheck()` goes through `AppModel.checkSteps` one by one and keeps the run in `CheckRun`; `CheckFlow` (SteadyKit) decides what counts (warn and bad only), worst first, and whether the user can fix it. Nothing is changed on the device, so there is no Fix step. `CheckCard` draws it like the Windows card (`web/app.css`, "connection check"): the 180 pt dial, the six-segment ring, the percent ring while checking, and the stale state after `CheckFlow.staleAfter`.

## History and problem reports

- **Overview > Last 7 days** (`WeekCard`, SIC-108): `WeekSummary` reads the stored minutes of the week: minutes measured, median per-minute latency to the router and the Internet, loss to the Internet pings, and outages (runs of minutes in which every Internet target, pings and TCP probes, lost everything). Below `WeekSummary.enoughMinutes` (60) it shows only how long it measured.
- **History** draws latency and loss to the router and to the Internet from the stored minutes (`HistorySeries`: per minute over an hour, 10-minute buckets over a day, hours over a week; a gap starts a new line segment).
- **Settings > Report a problem** ([ADR-0012](../docs/adr/0012-user-sent-problem-reports.md)): `ProblemReport` writes the text the user reads in full and sends themselves; addresses are masked except the public resolvers. `CrashReports` keeps short MetricKit summaries on the device; `RecentLog` reads the app's own log.

## Diagnosis rules

A rule is ported from `app/diagnostics.py` together with its vectors in `spec/diagnosis/`. The Swift tests (`DiagnosisVectorTests`) and `tests/test_diagnosis_vectors.py` run the same cases; a change to a rule changes the vectors and both implementations.

| # | Check | Swift | Vectors | Inputs on Apple platforms |
|---|---|---|---|---|
| 5 | Ping quality | `PingQuality` | `ping.json` | `LiveMonitor` (ICMP + TCP while the app is open) |
| 6 | DNS benchmark | `DNSBenchmark` | `dns.json` | `DNSCheck`: system DNS servers from `res_ninit` (the `CResolver` C target, libresolv), raw UDP queries as in `app/dnsprobe.py`; runs when connected and when the router changes, or from its refresh button |
| 2 | Wi‑Fi signal | `WiFiSignal` | `signal.json` | Mac only: `WiFiReader` (CoreWLAN, works in the sandbox) every 5 s; RSSI, channel, PHY mode and transmit rate. The network name needs location permission and the receive rate is not available, so both show as unknown. iOS has no API for the signal |
| 3 | Channel interference | `Interference` | `interference.json` | Mac only: `WiFiReader.surroundings` every 5 minutes and when the channel changes, from the system's cached scan (a scan only when the cache is empty, so the radio does not leave the channel). Without location permission CoreWLAN gives no BSSIDs, so `Interference.anonymous` stands in for them: our router is the network on our channel closest to our RSSI (within 10 dB), and networks within 3 dB of it in the same scan are its other SSIDs. Measured on a Mac, the scan's RSSI differs from the connection's by 1–5 dB, hence the comparison within one scan. A neighbour at the same signal is taken for ours, so the check can miss a crowded channel but does not invent one. Names are not read; details say "Nearby network" |
| 13 | Poor physical link | `PhysicalLink` | `link.json` | Mac only: the Wi‑Fi reading at the end of each minute goes to `wifi_stats` (Windows schema, no name or BSSID) and is joined with the router's minute as `join_minutes` does. macOS reports only the transmit rate, so the Mac judges Tx where Windows judges Rx: provisional thresholds until calibrated on Mac data (SIC-70). The app measures only while open, so 30 connected minutes in 24 hours are needed |
| 8 | VPN | `VPNCheck` | `vpn.json` | `VPNReader`, on every path change: tunnel interfaces (`utun`, `ipsec`, `ppp`, `tap`, `tun`) that have scoped network settings or that the path uses. The system keeps several `utun` interfaces of its own, so their mere presence means nothing. Apps cannot see other VPN configurations, so "installed but off" never shows |
| 14 | Bufferbloat | `Bufferbloat` | `bufferbloat.json` | `BufferbloatTest`, on demand after a confirmation (about 250 MB per 100 Mbps of line speed, at most 2 GB; on a metered path at most 250 MB per direction, and not at all in Low Data Mode): pings the router and 1.1.1.1 every 0.2 s for 4 s idle, then 10 s while downloading and 10 s while uploading over 4 connections to speed.cloudflare.com (25 MB requests; a direction stops early only at 1 GB and is then marked `capped`; HTTP 429/403 marks it `refused` with its `Retry-After`), dropping the rounds that began in the first 2 s of each loaded phase, as `app/bufferbloat.py` does |
