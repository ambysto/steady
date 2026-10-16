---
status: building   # draft → approved → planned → building → verified → shipped (PR #n)
---
# Speed test: ask on every metered connection, and a smooth live chart

Follows [2026-10-15-windows-speed-test.md](2026-10-15-windows-speed-test.md) (shipped in 1.0.1) and
amends [ADR-0024](../docs/adr/0024-windows-speed-test.md). Windows only.

**Problem**

1. **The question is missed on some metered connections.** Since 1.0.1 the speed test asks first
   only when the Internet route leaves by a mobile broadband adapter (interface type WWAN). A phone's
   hotspot over Wi‑Fi, a USB tether (Ethernet to Windows), a full-tunnel VPN over mobile data (the
   route leaves by the VPN) and an IPv6-only mobile network (no IPv4 route to read) all run at once,
   with up to 2 GB of traffic and no 250 MB cap. ADR-0024 lists this as a known gap; reading Windows'
   connection cost (`INetworkCostManager`) was left for later.
2. **The upload chart jumps while it runs.** The download line is calm, the upload line saw-tooths
   and the large upload figure jumps with it, so the live view looks less trustworthy than the
   result it ends on. The final figures are fine (they agree with the interface byte counters).

**Outcome**

1. The speed test asks first (and caps each direction at 250 MB, as now) whenever Windows says the
   connection it would use costs money, as well as on a mobile broadband adapter:
   - the connection cost is *fixed* or *variable*, or roaming, over or near its data limit. This is
     what "Set as metered connection" in Windows Settings sets for any Wi‑Fi or Ethernet network,
     what Windows sets for mobile broadband, and what it sets for a phone hotspot that tells Windows
     it is metered;
   - the connection is the one the Internet route leaves by, over IPv4 **or IPv6** (an IPv6-only
     mobile network is read like any other);
   - when that route leaves by a VPN or other tunnel, the networks underneath it count: if any
     connected network that reaches the Internet is metered, it asks (erring towards one extra
     click rather than 2 GB of someone's data plan). This case is a guess, so it asks **once per
     network underneath**: after a yes, a run over the same VPN and the same metered network does
     not ask again, also after a restart (the 250 MB cap stays). Every other metered case asks every
     time, as ADR-0024 decided for mobile data;
   - a phone's USB tether reads as mobile data from the adapter it shows up as (Android's "Remote
     NDIS" tethering device, the iPhone's "Apple Mobile Device Ethernet"), whatever its cost says,
     since Windows usually reports it as an ordinary unmetered Ethernet.
2. Off those cases nothing changes: Wi‑Fi and Ethernet run without a question. When the cost
   cannot be read (the call fails), it reads as not metered, as an unknown route does today.
3. The live upload and download lines are smooth and the large live figure stops jumping, while it
   still follows a real change in speed. The result's figures do not change: download, upload,
   "at least", latency, jitter, loss and check #14's verdict come out the same for the same run.
   The saved chart shows the same smoothed line the user watched.
4. Success is measured by:
   - `python -m unittest discover -s tests -t .` green, with new tests for the metered decision
     (every case above, from fake cost readings), the IPv6 route, the tunnel case, the USB-tether
     adapters, and the live rate.
   - A read-only reading on the dev PC: this Wi‑Fi reads as unmetered, `ask` is false, and the
     reading takes under 50 ms (it runs on every `GET /api/speedtest`).
   - With the owner, by hand (setting a network as metered is a change to the machine, so only the
     owner does it): Wi‑Fi set as metered in Settings → the card and the floating bar ask; set back
     → they do not. A phone hotspot, a USB tether and a VPN over the phone where the owner can set
     them up, recorded as checked or not checked.
   - The live rate replayed over a recorded upload (meter readings every 0.25 s from one real run,
     taken with the owner's go-ahead): after the ramp, the mean step between consecutive live points
     drops to 5 % of the direction's figure or less (the "before" value is recorded too), and after a
     step change in a synthetic trace the live reading reaches 90 % of the new rate within 2.5 s
     (was 1.5 s in the draft: the trace showed no window that short removes the saw-tooth, see
     Findings).
   - The same replay gives bit-identical `mbps`, `steady_mbps`, `capped` and result figures before
     and after the change, and the shared diagnosis vectors still pass.

**Constraints**

- Python standard library only (ADR-0001): the cost is read through COM with `ctypes`
  (`INetworkListManager` / `INetworkConnectionCost`), unelevated, read-only. No PowerShell on this
  path (it runs on every page load of the card and the bar).
- The figures are ADR-0023's on both platforms; only the live rate and the chart drawing change.
  The engine's byte counting, `steady_mbps`, `Phase.mbps`, `evaluate_bufferbloat` and
  `spec/diagnosis` are not touched.
- No new UI text: the existing question already says "mobile data or a personal hotspot" and is
  shared with the Apple app, so no catalog key changes and no xcstrings regeneration.
- No new network endpoint, nothing new leaves the machine. `PRIVACY.md` already says the speed test
  asks first on mobile data; it does not change.
- Each real speed test needs the owner's go-ahead first (about 1 GB per direction, and the test
  server's per-address limit).
- Nothing is written to the machine by the agent; setting a network as metered is the owner's
  action.

**Out of scope**

- Counting upload bytes at the wire instead of at the socket (it would change the figures).
- The Apple app: it reads `NWPath.isExpensive` already; whether its upload chart has the same
  saw-tooth is a question for a Mac session (a follow-up ticket if the recorded trace says so).
- A measured tweak's upload (ADR-0016): it runs without the speed test's question today, and stays so.
- New text for a network the user set as metered themselves (the current text names mobile data and
  hotspots); a Windows-only key can follow if the owner wants it.
- The floating bar's ring and the Diagnostics card's layout.

**Decisions** (owner, 2026-10-16)

1. USB tether is recognised by its adapter's description (Remote NDIS, Apple Mobile Device
   Ethernet), even though a development board using RNDIS then asks too.
2. Behind a VPN, it asks when any other connected network with Internet access is metered, but only
   once per such network (Outcome 1).
3. The owner checks a phone hotspot by hand; tether and VPN over the phone are recorded as checked
   or not.
4. One real run to record the upload trace: agreed, run on 2026-10-16.
5. The hand check on the owner's iPhone hotspot (2026-10-16) found Windows reports it unrestricted
   (cost source default, no metered hint in the DHCP lease), so the cost alone misses it. The owner
   agreed to recognise an iPhone's Personal Hotspot by its fixed network (gateway 172.20.10.1, the
   PC's address in 172.20.10.0/28). Android hotspots pick a random network; they stay caught only
   when they tell Windows.

**Findings** (the recorded run, 2026-10-16, Wi‑Fi; download 295, upload 177 Mbit/s)

- The upload line on the wire is steady: the route interface's byte counter, read over 1 s, moves
  by 1.6 % of the figure between 0.25 s readings (185 to 198 Mbit/s after the ramp). The meter,
  which counts upload bytes as they are handed to the socket, swings from 118 to 261 Mbit/s with a
  period of about 4.5 s, the time one of the 4 connections takes to send its 25 MB request before
  it reconnects: mean step 10.4 %, swing 78 % of the figure. So the saw-tooth is in the counting,
  not in the line, and the figures (which add up the same bytes over 8 s) are unaffected.
- Replayed at the 0.25 s beat, mean step / swing / time to 90 % of a step, upload: 1 s ends (now)
  10.4 % / 78 % / 1.0 s; 2 s ends 4.2 % / 30 % / 2.0 s; least-squares slope over 2 s
  4.0 % / 35 % / 1.75 s; 3 s 2.2 % / 21 % / 2.5 s; 4 s 1.2 % / 17 % / 3.5 s; an exponential average
  over 1 s 3.7 % / 28 % / 2.75 s. Download (calm already): 7.3 % / 36 % now, 1.3 % / 19 % with the
  3 s slope. Chosen: the least-squares slope over 3 s.

**Real run after the change** (2026-10-16, owner's go-ahead, home Wi‑Fi read as unmetered, through
an isolated dev server driven from the page)

- Download 292.0, upload 178.4 Mbit/s (the 3 s slope's live line moved by 1.8 % and 2.8 % of the
  figure between readings; the recorded run before the change: 6.4 % and 10.1 %).
- Against the route interface's byte counters over the same 2-10 s: download -5.4 % (308.7),
  upload -7.2 % (192.3). The run recorded before the change, read the same way: -5.2 % and -7.7 %.
  The meter's own bytes over that window give the figure to within 0.5 %, so the gap is what the
  counters add (TCP/IP headers, about 3 %, and retransmissions on the upload, which were not read
  this time) and the change does not move the figures. Proof 6's 5 % is not met on the raw
  counters, as in the 1.0.1 runs (-5.1 % and -11.9 %).
- The page's screenshots of this run failed (the card re-rendered under the capture); the chart is
  shown by the replay screenshots (proof 3).
