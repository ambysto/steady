---
status: verified   # draft → approved → planned → building → verified → shipped (PR #n)
---
# Speed test on Windows

**Problem** — The Apple app has had a speed test since 0.8.1 ([ADR-0023](../docs/adr/0023-apple-speed-test.md)):
one button on the Overview and on the Mac's floating bar, a live figure while it runs, then download,
upload, latency, jitter and packet loss. Windows already moves the same traffic in check #14
(Bufferbloat, `app/bufferbloat.py`), but its Mbit/s only appear in a detail line of a Diagnostics card,
nothing moves while it runs, and the result is gone when the app restarts. Windows is the platform
with the feature gap.

**Outcome** — the Apple behaviour, on Windows. One measurement, two results:

1. A **Speed test** card on the **Overview** and a button on the **floating bar**, right after the
   state dot.
2. While it runs, the speed.cloudflare.com-like layout of the Apple card: download and upload as
   large figures over live rate charts (updated at least 4 times a second), latency and jitter
   (idle, ↓, ↑) and packet loss as the pings come back, and a segmented progress bar for
   ping → download → upload with the phase named.
3. When it ends, the same layout with the final figures, the charts, the test server's location
   code (e.g. HKG) and when it was measured. The result survives a restart of the app.
4. The same run sets the **Bufferbloat result** (check #14) on Diagnostics, which also survives a
   restart and says when it was measured. Starting from either place runs one measurement, never two.
5. Every failure mode reads as a state, not a wrong number: server refused (HTTP 429/403, "try again
   in about N min", buttons disabled until then), no connection, load too weak (no figure), hit the
   byte limit ("at least N").
6. On the floating bar: a gauge while idle, a ring that fills while it runs, the download figure
   once done (until it goes stale after 3 h); a click opens the result in the main window, which
   also asks the question when one is due.
7. Success is measured by:
   - `python -m unittest discover -s tests -t .` green, with new tests for the live rate, the steady
     figure, the live view and the result, ported from `SpeedTestTests.swift`.
   - The Overview card, the Diagnostics card and the floating bar checked by hand on the dev server
     with a scripted (fake) run, light and dark.
   - One real run on the dev PC, with the owner's go-ahead, whose figures are within 5 % of the
     interface byte counters over the same phases (not speed.cloudflare.com in a browser).

**Constraints**

- Reuse `app/bufferbloat.py`'s loads and measurement; no new dependency, no new endpoint on the
  network (speed.cloudflare.com is already in `PRIVACY.md`).
- `evaluate_bufferbloat`, `Phase.mbps` and `spec/diagnosis/bufferbloat.json` do not change.
- Same figures and rules as ADR-0023: the rate after the 2 s ramp over 4 connections; a refused
  direction or one under 1 Mbit/s has no figure; latency is the median Internet ping; jitter the mean
  absolute difference between consecutive replies; loss over idle and the loaded phases after the ramp.
- Confirmation: the first time only, then remembered (decision 2). Nothing is written to the machine
  and no Administrator approval is needed.
- Reuse the existing `ui.speed.*` keys; any new key goes into all 7 catalogs and the Apple catalogs
  are regenerated.
- Windows code only (`app/`, `web/`), plus docs: ADR-0024, `CHANGELOG.md` `[Unreleased]`,
  `docs/DIAGNOSTICS.md`, CLAUDE.md's "Open" line. No version bump, no tag.

**Out of scope**

- The Apple app.
- A history of past speed tests, sharing a result image, choosing a server.
- Changing the test's length or connection count.

**Decisions** (open questions settled 2026-10-15)

1. The floating bar button ships in the same PR as the Overview and Diagnostics cards.
2. No metered detection on Windows for now: the connection is treated as unmetered, so the app asks
   only the first time and each direction keeps check #14's 1 GB limit.

**Open questions** — none.

**Verification** (2026-10-15)

- `python -m unittest discover -s tests -t .`: 1188 pass (1 skipped), including 28 new tests in
  `tests/test_speedtest.py` ported from `SpeedTestTests.swift`.
- `locales_to_xcstrings.py --check`: up to date.
- On the isolated dev server with a scripted run (no traffic), light and dark: the Overview card idle,
  the first-run question, the live view (figures, charts, latency/jitter/loss, 24 segments under the
  phase label), the result with "Measured … · Server HKG", the narrow layout, the Diagnostics
  Bufferbloat card from the same run with when it was measured, the floating bar's download figure
  and its tooltip, and a refused upload (warning with "try again in about 10 min", buttons disabled).
- An independent review found no blockers; its findings are fixed (see the plan).
- Two real runs on the dev PC (2026-10-15, owner's go-ahead), through the server's own runner, to
  HKG over 2.4 GHz Wi-Fi (transmit rate 86 Mbit/s), with the interface byte counters and Windows' TCP
  statistics read over the same 2-10 s of each direction:
  - Run 2: download 58.9 Mbit/s against the counters' 62.1 (-5.1 %, no TCP retransmissions: the
    counters also count TCP/IP headers); upload 58.6 against 66.6 (-11.9 %), with 8.8 % of the TCP
    segments sent being retransmissions, which the counters count and the test does not. Allowing for
    both, the upload agrees within about 1 %.
  - Run 1, on a worse moment (25 % of idle pings lost): download 49.0 against 52.4 (-6.4 %), upload
    35.6 against 44.9 (-20.8 %); retransmissions were not read then.
  - Both runs: check #14 bad (latency under upload 94-237 ms), server HKG, 40-47 live points per
    direction.
- Seen in the runs: the live upload rate is uneven second by second (e.g. 113, 64, 126, 26 Mbit/s
  while the counters read 39-91), because upload bytes are counted as they are handed to the socket
  and Windows fills large send buffers. The steady figure is not affected; the live chart is. As on
  Apple; a smoother live upload rate is a possible follow-up.
