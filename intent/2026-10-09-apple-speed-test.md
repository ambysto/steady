---
status: building   # draft → approved → planned → building → verified → shipped (PR #n)
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
2. While it runs: the phase (ping → download → upload) and a **live Mbit/s figure** that updates at
   least 4 times a second.
3. When it ends: four figures — **Download**, **Upload** (Mbit/s), **Ping idle** and **Ping under load**
   (ms) — plus when it was measured.
4. The same run also sets the **Bufferbloat result** (check #14) on the Diagnostics tab; starting the
   test from either place runs the same single measurement, never two.
5. Every failure mode the engine already knows reads as a clear state, not a wrong number: server
   refused (HTTP 429/403, "try again in about N min" with the button disabled until then), no
   connection, load too weak, hit the byte limit (shown as "at least N Mbit/s").
6. Success is measured by: `swift test` green with new tests for the live rate and the speed figures;
   `xcodebuild` green for macOS and iOS (iPhone and iPad); a real run on the Mac shows figures within ±15 % of
   speed.cloudflare.com in a browser on the same line, and the floating bar button works.

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
- Choosing a server, multi-server tests, jitter / packet-loss figures beyond what check #14 measures.
- A needle gauge animation; a large live number is enough for this round.
- Changing the test's duration or connection count.

**Decisions** (open questions settled 2026-10-09)

1. Confirmation: on Wi‑Fi/Ethernet only the first time (remembered); on a metered path every time.
2. Floating bar: while the test runs, the tube's download/upload show the live rate (the interface
   counters already do) and the new button's ring follows the phase. When it ends, the button shows
   the download figure; a click opens the main window's Overview at the result.
3. The latest result persists across launches (`UserDefaults`), shown with when it was measured.

**Open questions** — none.
