# Phone & tablet version (iOS / Android) — preparatory analysis

> Status: **research** (2026-10-04). Points marked ⚠️ must be verified with a prototype on real devices before committing.
>
> **Superseded in part:** the technology choice is settled by [ADR-0009](adr/0009-native-apple-and-android-apps.md) (native SwiftUI and Kotlin, not Capacitor/TypeScript) and the Apple app's structure by [ADR-0010](adr/0010-apple-app-structure.md). Sections 4 and 6 below describe the earlier Capacitor plan; the platform analysis in sections 1–3 and 5 still applies.

## 1. The user's problem

On a phone/tablet, the network is slow or flaky and the user does not know why, for example:

- On 5 GHz Wi‑Fi, the user walks to another room and the signal gets weak, but the device **does not switch to 2.4 GHz** (or to a closer mesh node).
- A **VPN** (or "Private DNS", a proxy) is on and slows things down or blocks traffic.
- **Background apps** (photo backup, updates, cloud sync) are eating bandwidth.
- The router is fine but the ISP has a fault, DNS is slow, the network is restricted (Low Data Mode / Data Saver), captive portal, bufferbloat…

Goal: **one tap to find out what is going on, one tap to fix it.**

## 2. Platform reality: what the operating system allows

The biggest difference from the Windows version: on mobile **an app is almost never allowed to change network configuration by itself**. "1 click to optimize" on mobile really means: *1-tap diagnosis* → a list of causes with evidence → each cause has a *button that goes straight to where it can be fixed* (or the app does it itself if the OS allows). Promising to "speed up your network" without being able to deliver is a reason for rejection by the App Store / Google Play.

| Issue | Android | iOS / iPadOS |
|---|---|---|
| Signal (RSSI), band (2.4/5/6 GHz), link speed, BSSID | ✅ `WifiInfo` via `NetworkCapabilities` (requires location permission) | ❌ no RSSI/band. Only SSID/BSSID (`NEHotspotNetwork.fetchCurrent`, requires the "Access Wi‑Fi Information" entitlement + precise location permission) |
| "Stuck" on weak 5 GHz while 2.4 GHz/another mesh node is stronger | ✅ detectable: current RSSI + network scan (limited to ~4 times/2 minutes) | ⚠️ only inferred indirectly: latency/packet loss to the router rises, download speed drops. Band not visible |
| Force a band switch / move to another AP | ❌ cannot be forced (the device decides, especially when both bands share one SSID). ⚠️ If the router has a separate SSID for 2.4 GHz: suggest connecting via `WifiNetworkSuggestion`/`WifiNetworkSpecifier` (user confirms) | ❌. ⚠️ `NEHotspotConfiguration` offers to join a known SSID (user confirms) |
| VPN is on | ✅ `NetworkCapabilities.TRANSPORT_VPN`; open the VPN settings screen directly | ✅ detectable (`utun/ipsec` interfaces in the system proxy configuration) ⚠️; can only give instructions to turn it off |
| Background apps eating bandwidth | ✅ per-app data via `NetworkStatsManager` (the user must grant the "Usage data access" permission in Settings) | ❌ no per-app figures. Only the device's total traffic is known ⚠️ |
| Data Saver / Low Data Mode, metered network | ✅ `getRestrictBackgroundStatus`, `NOT_METERED` | ✅ `NWPath.isConstrained`, `isExpensive` |
| Router or ISP fault (ping router vs Internet), slow DNS, packet loss, bufferbloat, captive portal | ✅ measured directly (ICMP, TCP, HTTP, DNS) | ✅ measured directly (ICMP over a datagram socket as in Apple's SimplePing sample, TCP, HTTP, DNS) |
| Channel interference (how many networks share the channel) | ✅ from scan results | ❌ |
| Turn Wi‑Fi on/off, forget network, change system DNS | ❌ since Android 10 cannot toggle by itself; ✅ open the "Settings Panel" for the user to tap. Private DNS: instructions only | ❌ no. Deep links into a specific Settings page are private API ⇒ rejected in review |
| 24/7 monitoring like the Windows version | ⚠️ requires a foreground service (with a persistent notification), restricted by battery saving | ❌ no continuous background running; measures only while the app is open (+ short BGTask, not guaranteed) |

Consequence: **Android can do nearly the full** diagnostic set; **iOS can only do the network measurement part** (router/Internet/DNS/bufferbloat/VPN/Low Data Mode), with no signal, band or background apps.

### Stronger option (for later, not proposed for the first version)
A "local VPN" (`VpnService` on Android, `NetworkExtension` on iOS) makes per-app traffic visible on Android and allows changing DNS automatically on both — the way apps like 1.1.1.1/NetGuard do it. In exchange: it conflicts with any VPN the user is already using, costs battery, and iOS requires the Network Extension entitlement and a stricter review. Only consider it once the first version has proven its value.

## 3. Proposed experience: one-tap "Network check"

1. Tap **Check** (~15–30 seconds): measure router, Internet, DNS, a short speed test, (Android) signal/band/scan, VPN, Data Saver, background apps.
2. Results as cards, like the existing Diagnostics screen: *"You are on 5 GHz with a weak signal (−78 dBm); the same network on 2.4 GHz is stronger"* → a **Turn Wi‑Fi off/on** button (opens the Wi‑Fi panel so the device picks an AP again) or the instruction *"Split the 2.4/5 GHz SSIDs on the router"*.
3. **One-tap fix** = do *whatever the OS allows*, in order; for the rest, open the right settings screen with a one-line instruction.
4. Save the history of checks to compare before/after (like ADR-0007); no data is sent anywhere.

## 4. Reuse from the Windows version

| Component | How it is reused |
|---|---|
| UI (`web/`, already scales down to phone size), `tokens.css` | Wrapped with **Capacitor** (WebView + native plugins) — keep HTML/CSS/JS as is, add bottom-tab navigation for mobile |
| Translations `app/locales/*.json` (7 languages) | Used as is |
| Diagnostic rules (`app/diagnostics.py`), suggestions, effectiveness measurement (ADR-0007) | Rewritten in TypeScript running on the device (mobile has no Python server). Keep a **shared set of test cases** as JSON (input → expected result) so the two versions do not drift apart |
| Monitor, Windows tweaks, watchdog | Not usable (different operating system) |

Native parts to write: a Kotlin plugin (Wi‑Fi info, scan, per-app usage, Data Saver, settings panels, foreground service) and Swift (Wi‑Fi SSID/BSSID, NWPathMonitor, ICMP).

Other options considered: Flutter or pure native (Kotlin + Swift) feel more native but require rewriting the whole UI ⇒ choose one of them instead if Capacitor does not meet performance/UX needs (final decision recorded in an ADR after the prototype).

## 5. What to prepare

**Accounts & legal**
- Apple Developer Program (99 USD/year), Google Play Console (25 USD, one-time). Organization accounts if publishing under the company name (requires D‑U‑N‑S).
- Privacy policy (mandatory because location permission is used to read the SSID), Google Play Data safety, Apple Privacy Nutrition Label. Principle: collect nothing, all data stays on the device.
- Check the name "Ambysto Steady" on both stores and as a trademark.

**Devices & tools**
- **A Mac** (Xcode is required to build/sign iOS) or a CI service with macOS.
- Real devices: ≥ 1 iPhone, 1 iPad, 2–3 Android devices from different makers (Samsung, Pixel, Xiaomi — each maker does battery saving differently), a dual-band / mesh router to reproduce the "stuck on 5 GHz" case.
- Android Studio, Node (already available), Capacitor CLI.

**Design**
- Mobile UI variant: bottom tabs, touch targets ≥ 44 pt, safe area; 2-column tablet layout (the current mockup is already close).
- App icon, store screenshots for each size, store descriptions in 7 languages (catalog already exists).
- Explain permissions before requesting them (why location is needed, why "usage data access" is needed).

## 6. Proposed roadmap

| Phase | Content | Estimate |
|---|---|---|
| 0. Spike | Minimal Android app (Capacitor + Kotlin plugin) reading RSSI/band/scan/VPN/per-app usage on 2–3 real devices; ICMP measurement on iOS. Verify the ⚠️ cells in the table above | 1–2 weeks |
| 1. Shared core | Port the diagnostic rules + suggestions to TypeScript, JSON test-case set shared with the Windows version | 2 weeks |
| 2. Android MVP | "One-tap check", result cards, guided fixes, history | 2–3 weeks |
| 3. iOS MVP | Network measurement + VPN + Low Data Mode (no detailed Wi‑Fi) | 2 weeks |
| 4. Tablet + release | 2-column layout, store listing, review | 1–2 weeks |

Decisions to settle before starting: (1) whether to do iOS right away when iOS can only do ~50% of the diagnostics; (2) Capacitor or native (after the spike); (3) whether to use a "local VPN" in a later version.
