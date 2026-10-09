---
status: shipped (PR #70)   # draft → approved → planned → building → verified → shipped (PR #n)
---
# Speed test on macOS, iOS and iPadOS

**Problem** — People want to know "how fast is my line right now", the way Speedtest-style apps show it:
one button, a live number while it runs, then download, upload and ping. The Apple app already moves
that traffic in check #14 (Bufferbloat: 4 connections to speed.cloudflare.com, 10 s each way), but the
Mbps it measures is buried in a detail line of a Diagnostics card, and nothing shows the speed while
the test runs.

**Outcome** — one measurement, two results:

1. A **Speed test** button on the **Overview** (macOS, iOS and iPadOS) and on the Mac's **floating bar**,
   right next to the Scan bulb.
2. While it runs, a layout like speed.cloudflare.com's top section (decision 4):
   - **Download** and **Upload**, each a large figure over a live area chart of the rate. The figure
     updates at least 4 times a second.
   - **Latency** (idle, ↓ during download, ↑ during upload), **Jitter** (same three) and
     **Packet loss**, which fill in as the pings come back.
   - A segmented progress bar for ping → download → upload, with the phase named.
3. When it ends, the same layout with the final figures, the charts, the test server's location code
   (e.g. HKG) and when it was measured.
4. The same run also sets the **Bufferbloat result** (check #14) on the Diagnostics tab; starting the
   test from either place runs the same single measurement, never two.
5. Every failure mode the engine already knows reads as a clear state, not a wrong number: server
   refused (HTTP 429/403, "try again in about N min" with the button disabled until then), no
   connection, load too weak, hit the byte limit (shown as "at least N Mbit/s").
6. Success is measured by:
   - `swift test` green, with new tests for the live rate and the speed figures.
   - `xcodebuild` green for macOS and iOS (iPhone and iPad).
   - A real run on the Mac whose figures are within 5 % of the interface's own byte counters over the
     same phases.
   - The floating bar button works.

   Speed.cloudflare.com in a browser is not the yardstick. It reports the 90th percentile of single
   requests on one connection, which reads lower on fast lines. On 2026-10-09 it read 595/356 while
   the interface counters read 743/711 and this test 716/690, all to the same HKG server.

**Constraints**

- Reuse `BufferbloatTest` / `LoadGenerator`; no new dependency, no new test server.
- The Bufferbloat evaluation (`Bufferbloat.evaluate`) and its shared test vectors
  (`spec/diagnosis/bufferbloat.json`) do not change: Windows and Apple must still agree.
- Same data rules as check #14: confirmation before running, 500 MB limit on a metered path,
  unavailable in Low Data Mode.
- Every user-visible string goes through `app/locales/*.json` (all 7 catalogs), regenerated with
  `scripts/locales_to_xcstrings.py`. Code and docs in English.
- The Apple app still changes nothing on the device (ADR-0020 point 11).
- The floating bar stays 300×36 unless the plan shows the button cannot fit.
- Update `CHANGELOG.md`, `docs/DIAGNOSTICS.md`, and add an ADR for "speed test = check #14's run".

**Out of scope**

- Windows (`app/`, `web/`) — a later session.
- History of past speed tests, charts of them, sharing a result image.
- Choosing a server, multi-server tests.
- speed.cloudflare.com's Network Quality Score, its per-size request box plots and its server map:
  the run is a sustained 4-connection load, not a ladder of request sizes.
- Changing the test's duration or connection count.

**Decisions** (open questions settled 2026-10-09)

1. Confirmation: on Wi‑Fi/Ethernet only the first time (remembered); on a metered path every time.
2. Floating bar: while the test runs, the tube's download/upload show the live rate (the interface
   counters already do) and the new button's ring follows the phase. When it ends, the button shows
   the download figure; a click opens the main window's Overview at the result.
3. The latest result persists across launches (`UserDefaults`), shown with when it was measured.
4. (2026-10-09, after the first real run) The card looks like speed.cloudflare.com's top section,
   with the app's own colours: download blue and upload purple, as on the floating monitor. Jitter is
   the mean absolute difference between consecutive replies. Packet loss is the share of Internet
   pings lost over idle and the loaded phases after the ramp.

**Open questions** — none.

**Verification** (2026-10-09)

- `swift test`: 131 pass.
- `locales_to_xcstrings.py --check`: up to date.
- `xcodebuild`: macOS and iOS succeed.
- Real run on the Mac to HKG: 716/690 Mbit/s against the interface counters' 743/711 over the same
  phases. That is within 5 % once the counters' TCP/IP headers are allowed for.
- A second run from the Overview read 742/684 Mbit/s, latency 23.5 ms (↓ 58.1, ↑ 38.4), and the
  floating bar showed 742.
- Screenshots of the result on the Mac, iPhone 17 and iPad Pro 11" (iOS 27 simulator).
- An independent review found no blockers. Its findings are fixed; see the plan's review section.
- Not checked: the live view during a run on iPhone/iPad, because the simulator cannot be clicked
  from the terminal.

