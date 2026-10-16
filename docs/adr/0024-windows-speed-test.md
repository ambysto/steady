# ADR-0024: On Windows too, the speed test is check #14's run

- **Status:** Accepted (amended 2026-10-16: the live rate, and metered connections beyond mobile broadband)
- **Date:** 2026-10-15
- **Related:** [ADR-0023](0023-apple-speed-test.md) (the same decision on Apple), [ADR-0020](0020-check-fix-result-flow.md) (the Overview; the floating bar hands results to the main window), [ADR-0021](0021-floating-monitor-glass.md) (the floating bar), check #14 in [DIAGNOSTICS.md](../DIAGNOSTICS.md), intent `intent/2026-10-15-windows-speed-test.md`, amended by `intent/2026-10-16-speed-test-metered-and-smooth.md`

## Context

The Apple app has had a speed test since 0.8.1 (ADR-0023): one button on the Overview and on the
Mac's floating bar, a live view while it runs, then download, upload, latency, jitter and packet
loss. Windows moved the same traffic in check #14 (`app/bufferbloat.py`), but its Mbit/s were only a
detail line of the Diagnostics card, nothing moved while it ran, and the result was gone when the app
restarted. A separate speed test would load the line twice and reach speed.cloudflare.com's
per-address limit sooner.

## Decision

1. **One run, two results, as on Apple.** `bufferbloat.measure(..., events=...)` is the speed test.
   With `events` each load also gets a `Meter` it counts its bytes into (and notes the test server's
   location in, from `colo` or the end of `CF-RAY`). A meter thread reads it on a fixed 0.25 s beat
   and reports the live rate (`LiveRate`), until the load ends (byte limit or refusal), so a frozen
   total never reads as a rate falling to zero. The live rate is the least-squares slope of the
   readings of the last 3 s (amended 2026-10-16; it was the difference between the ends of the last
   second): the upload is counted as bytes are handed to the socket, which takes them in bursts and
   reconnects every 25 MB, so on a recorded run the 1 s rate saw-toothed from 118 to 261 Mbit/s while
   the interface byte counter stayed between 185 and 198. The 3 s slope moves by 2 % of the figure
   between readings (10 % before) and reaches 90 % of a real change in 2.5 s. Only the live view and
   the stored chart read it; the chart draws a monotone curve on a stepped scale (`web/js/curve.js`). The reading taken once the 2 s
   ramp is over gives `Phase.steady_mbps`. `Phase.mbps`, `evaluate_bufferbloat` and the shared
   vectors are unchanged. Without `events`, the loads are called exactly as before (a measured tweak,
   the CLI).
2. **The figures are ADR-0023's**, in `app/speedtest.py` (a port of `SpeedTest` and
   `SpeedTest.Live`, with the same test cases): the rate after the ramp, else the phase average; no
   figure for a refused direction or one under 1 Mbit/s; "at least" at the byte limit; latency the
   median Internet ping, jitter the mean absolute difference between consecutive replies, loss over
   idle and the loaded phases after the ramp.
3. **One job.** `POST /api/speedtest` runs it under the line lock, single like every job; the
   Overview card, the Diagnostics Bufferbloat card (`POST /api/diagnostics/bufferbloat` now starts the
   same job) and the floating bar all start it. A page that sees a run going elsewhere joins it with
   `{"join": true}`, which never starts one, so a run that ended in the meantime is not repeated. Its
   progress is `Live.snapshot()`, sent on each phase change and at most 5 times a second, and polled
   4 times a second by the page.
4. **A question only on a metered connection** (intent decision 3, which replaces decision 2;
   amended 2026-10-16). On unmetered Wi-Fi or Ethernet the test runs at once. `app/netcost.py`
   decides from the interfaces the IPv4 and IPv6 Internet routes leave by (so an IPv6-only mobile
   network counts) and from Windows' cost of each connected network (`INetworkListManager` and
   `INetworkConnectionCost` through COM with ctypes, unelevated, read-only): metered when a route
   interface is mobile broadband (WWAN), a phone's USB tether (an adapter named "Remote NDIS" or
   "Apple Mobile Device Ethernet", which Windows reports as unmetered Ethernet), or a connection
   whose cost is fixed or variable, roaming, or over or near its data limit (what "Set as metered
   connection" sets, and what a phone's hotspot can tell Windows). Then every run asks, with the
   mobile-data text, and each direction stops at 250 MB (500 MB in all, as on Apple, so a line above
   200 Mbit/s reads as incomplete). When the route leaves by a VPN or other tunnel and only a
   connected network underneath that reaches the Internet is metered, the verdict is a guess: it
   asks once per such network (kept in `speedtest.json`), and the cap stays. A cost that cannot be
   read counts as unmetered. The server enforces it: on a metered connection a run without
   `{"confirmed": true}` is refused (409), so no page can start traffic the user has not agreed to. While the test server's `Retry-After` from the last run
   lasts, it answers 409 too. Whether the connection is metered is read when the run is asked for
   and again once it has the line (it may wait behind a measured tweak): a run the user did not
   confirm does not start on a route that has become metered. The speed test's texts do not name
   the test server; `PRIVACY.md` does, and so does check #14's advice when the server cannot be
   reached (a firewall or proxy may need it).
5. **The latest result persists** in `speedtest.json` in the data folder: the figures, the run's
   measurement, and check #14's result as stored messages (ADR-0006). The server reads it at start,
   so the Diagnostics Bufferbloat card and its suggestions come back with the speed test's, with
   when they were measured. A file that does not decode is ignored.
6. **On the floating bar** a gauge sits in the tube right after the state dot. Running, a ring fills
   with the run's progress. Done, it shows the download figure until it goes stale (3 h, like the
   check); a click opens the result on the Overview. On a metered connection a click opens the Overview,
   which asks: the bar has no room to say how much data the test uses. The bar is 330 px wide (was
   290).

## Consequences

- ✅ Windows and Apple read one run the same way, and the line is loaded once for both results.
- ✅ The live rate, the steady figure, the live view and the saved result are tested without a
  network (`tests/test_speedtest.py`).
- ✅ The metered decision is tested from fake readings (`tests/test_netcost.py`), and the live rate
  against a recorded run (`tests/fixtures/speed_trace.json`), which also checks that the figures do
  not depend on it.
- ⚠️ Off a metered connection the buttons run straight away, so repeated runs reach the server's
  limit sooner; the card and the bar then say when to try again.
- ⚠️ A phone's hotspot over Wi-Fi is caught only when the phone tells Windows it is metered or the
  user set the network as metered; one that does neither reads as ordinary Wi-Fi and runs without
  asking (up to 2 GB). A USB tether is caught by its adapter's name, so an unknown tethering adapter
  is missed, and a development board using RNDIS asks needlessly. Behind a VPN a metered network
  that is connected but not what the VPN runs over (Ethernet and a mobile modem both up) asks once.
- ⚠️ The live line trails a real change in speed by up to 2.5 s; at the end of each direction the
  card shows the result's figure, as before.
