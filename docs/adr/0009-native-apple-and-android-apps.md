# ADR-0009: Native apps for Apple platforms (SwiftUI) and Android (Kotlin), sharing data rather than code

- **Status:** Accepted
- **Date:** 2026-10-04
- **Related:** [ADR-0006](0006-i18n.md) (message catalogs), [docs/MOBILE.md](../MOBILE.md) (platform analysis), SIC-48

## Context

After the Windows version (v0.1.0), Ambysto Steady is coming to macOS, iPadOS, iOS and Android. Its value lies in reading deep network state from the operating system: which Wi‑Fi band and signal the device uses, whether a VPN is active, which apps use bandwidth, why the connection is slow right now. On iOS and Android these APIs exist only natively (Network framework, NEHotspotNetwork, CoreWLAN on macOS; ConnectivityManager, WifiManager, NetworkStatsManager on Android), and background work is tightly limited by each system.

Options considered:

| | Native per platform | Cross-platform UI (Flutter, React Native) | Web UI in a native shell (Capacitor) |
|---|---|---|---|
| Network/OS APIs | Direct | Through hand-written Swift/Kotlin plugins | Through hand-written Swift/Kotlin plugins |
| Codebases in practice | 2 (Apple, Android) | 1 UI + 2 plugin sets | 1 UI + 2 plugin sets |
| System integration (widgets, Control Center, Shortcuts, Quick Settings tile, background tasks) | Full | Extra native work | Extra native work |
| Battery and background behaviour | Best | Extra runtime | Extra runtime |
| UI work | Twice | Once | Once (reuses `web/`) |

## Decision

1. **Apple platforms: one SwiftUI multiplatform app** for iPhone, iPad and macOS. The Mac version shares most of its code with iOS instead of porting the Python backend.
2. **Android: Kotlin with Jetpack Compose.** Android allows measurements iOS forbids (per-app data usage, more Wi‑Fi detail), so it gets its own UI.
3. **Share data, not code:**
   - The UI catalogs in `app/locales/*.json` stay the single source of user-facing text; a script converts them to each platform's format (String Catalogs on Apple, `strings.xml` on Android).
   - The diagnosis rules (thresholds for loss, jitter, "only ICMP is rate-limited", signal quality…) are written as a specification with shared JSON test vectors, so the Windows, Apple and Android versions reach the same verdict from the same measurements.
4. **Order:** the Apple app first (one codebase covers three device types, and the Windows version is there to compare against), then Android.
5. Store accounts are enrolled as an **organization (Ambysto)**, never as an individual, so stores show the company as the seller.

## Consequences

- ✅ Full access to each platform's network APIs, best battery life and background behaviour, native system integration.
- ✅ Same texts and same verdicts across platforms thanks to shared catalogs and test vectors.
- ⚠️ Two UIs to build and maintain (Apple, Android).
- ⚠️ Building and signing for Apple platforms needs Xcode on a Mac and an Apple Developer Program membership for the organization (D-U-N-S number required); Google Play needs an organization developer account.
- ⚠️ Rules ported to Swift and Kotlin must be kept in step with the Python implementation; the shared test vectors are the guard.
