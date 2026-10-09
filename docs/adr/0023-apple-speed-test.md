# ADR-0023: On Apple, the speed test is check #14's run

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** [ADR-0020](0020-check-fix-result-flow.md) (Overview flow; the Apple app changes nothing), [ADR-0022](0022-mac-floating-monitor.md) (the Mac's floating bar), check #14 in [DIAGNOSTICS.md](../DIAGNOSTICS.md), intent `intent/2026-10-09-apple-speed-test.md`

## Context

Users want the figures a speed test app shows: download, upload and ping, with a live number
while it runs. The Apple app already loads the line for check #14 (Bufferbloat): 4 connections to
speed.cloudflare.com, 4 s idle, then 10 s each way, at most 1 GB per direction (250 MB on a metered
path). The Mbit/s it measured only appeared inside a detail line of a Diagnostics card. A second,
separate speed test would load the line again, use the data twice, and reach the test server's
per-address limit (HTTP 429/403) sooner.

## Decision

1. **One run, two results.** `BufferbloatTest.run` is the speed test. It returns a `Run`: the
   unchanged `Bufferbloat.Measurement`, which `Bufferbloat.evaluate` turns into check #14, plus each
   direction's steady rate. `SpeedTest.result(from:at:)` turns a run into the four figures. The Overview's
   Speed test card, the Diagnostics Bufferbloat card and the Mac's floating bar all start the same
   `AppModel.runSpeedTest()`, and a second start while one runs does nothing.
2. **Live view.** The run reports `BufferbloatTest.Event`s: each phase's start; the rate over about the
   last second (`LiveRate`), read from the load's byte total on a fixed 0.25 s beat until the load
   ends; each ping round's Internet reply; and, when a direction ends, its figure as the result will
   show it (`SpeedTest.figure`), so the figure does not change when the run ends. `SpeedTest.Live`
   turns them into what the card shows while it runs, laid out like speed.cloudflare.com's top section: download and upload as large
   figures over their rate charts, latency, jitter and packet loss, and a segmented progress bar.
   The test server's location is read from the replies (`colo`, else the end of `CF-RAY`).
3. **The speed figure is the rate after the ramp.** It is the bytes moved between the first
   reading after the 2 s ramp and the end of the load, over that time, as speed test apps leave out
   TCP's ramp-up. `Phase.mbps` keeps the whole phase's average, so check #14, its 28 shared vectors
   and Windows are unchanged. When the load ends before the ramp does (a very fast line reaching the
   byte limit), the figure falls back to the phase average. A direction that reached the byte limit
   reads "at least N". A refused direction has no figure, nor does one under 1 Mbit/s (check #14
   calls it a load too weak to fill the line). The result keeps until when the server asked to
   wait. Latency is the median Internet ping while idle, and during each direction after the ramp.
   Jitter is the mean absolute difference between consecutive replies, as speed.cloudflare.com
   reports it. Packet loss is the share of Internet pings lost while idle and while loading, after
   the ramp.
4. **Confirmation.** On Wi‑Fi or Ethernet the app asks the first time and remembers the answer
   (`speedtest.confirmed`). On a metered path it asks every time. Low Data Mode still disables the
   run. The floating bar is a non-activating panel and cannot take the keyboard, so when a question
   is due it opens the main window's Overview, which asks.
5. **The latest result persists** as JSON in `UserDefaults` (`speedtest.last`). One that no longer
   decodes is ignored. While the server's wait lasts, the buttons are disabled and the card counts
   down. The result also keeps the run's check #14 measurement, and at launch `Bufferbloat.evaluate`
   turns it back into the Diagnostics result, which then says when it was measured: the two results
   of one run come back together. A result saved before that has no measurement and brings back
   only the speed test.
6. **On the floating bar** the button sits in the tube right after the state dot, next to the Scan
   bulb.
   - Idle, it shows a gauge.
   - While running, a ring fills over the run's length; the tube's download and upload already show
     the live rate from the interface counters.
   - When done, it shows the download figure until the result goes stale (`CheckFlow.isStale`), and
     a click opens the Overview.
   - The bar grows from 300 to 330 pt wide to fit it.

7. **Not the browser's figures.** speed.cloudflare.com in a browser reports the 90th percentile of
   single requests on one connection, which reads lower on a fast line. On 2026-10-09, on the same
   line and the same server (HKG), it read 595/356 Mbit/s. This test read 716/690 Mbit/s and the
   interface's own byte counters 743/711 over the same phases. The counters also count TCP/IP
   headers. The interface counters are the yardstick for this test.

## Consequences

- ✅ The line is loaded once for both results. Check #14 and Windows parity are untouched.
- ✅ The live rate, the steady rate and the result are pure functions tested with `swift test`.
- ⚠️ The speed test moves the same data as check #14: about 250 MB per 100 Mbit/s, at most 2 GB (500 MB on a metered path).
- ⚠️ After the first confirmation the button runs straight away, so repeated runs reach the server's
  limit sooner. The card then says when to try again.
- ⚠️ Windows has no speed test yet; it is a later piece of work.
