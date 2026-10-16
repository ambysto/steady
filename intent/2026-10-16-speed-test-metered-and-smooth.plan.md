# Plan: speed test asks on every metered connection, and a smooth live chart

Intent: [2026-10-16-speed-test-metered-and-smooth.md](2026-10-16-speed-test-metered-and-smooth.md)
(approved 2026-10-16, plan accepted the same day). Branch `claude/speed-test-metered-smooth`, one PR, two logical commits (metered, smooth)
plus docs. Windows session: no Swift, no catalog keys, no `app/__init__.py` bump.

## Design in one paragraph

A new module `app/netcost.py` decides whether the speed test's connection is metered. Its pure part
takes plain readings (the IPv4 and IPv6 routes' interface indexes, each interface's kind and
description, and each connected network connection's adapter, Internet access and cost flags) and
returns a `Verdict` by the intent's rules (metered or not, and, for the VPN case, which networks
underneath made it a guess); its Windows part gets those readings unelevated through COM
with `ctypes`: `INetworkListManager.GetNetworkConnections`, and for each connection
`GetAdapterId`, `get_IsConnectedToInternet` and `INetworkConnectionCost.GetCost`, the adapter GUID
mapped to an interface index with `ConvertInterfaceGuidToLuid` + `ConvertInterfaceLuidToIndex`. The
IPv6 route comes from `GetBestInterfaceEx` for `2606:4700:4700::1111`. The server's `on_cellular`
becomes `on_metered` and calls it; the question, the 409, the 250 MB cap and the recheck once the
run has the line are unchanged, except that a guessed (VPN) verdict asks only while one of its
networks has not been confirmed: a confirmed run adds them to `speedtest.json`. For the chart, `LiveRate` reports the
least-squares slope of the running total over the last 3 s of readings instead of the difference
between the ends of the last second (settled on the trace recorded 2026-10-16, see the intent's
Findings), and
`web/js/speed.js` draws the line as a monotone curve on a scale that only grows, in the floating
monitor's steps (`niceScale`). `steady_mbps`, `Phase.mbps` and the figures never read `LiveRate`.

## Steps and files, in order

### A. Metered connection

1. `app/winutil.py`: `internet_route6_native(destination="2606:4700:4700::1111")` →
   `{"interface_index"}` or None (`GetBestInterfaceEx` with a `sockaddr_in6`); `interface_index_of_guid(guid)`
   (`ConvertInterfaceGuidToLuid`, `ConvertInterfaceLuidToIndex`). Read-only iphlpapi calls.
2. `app/netcost.py` (new):
   - `NLM_CONNECTION_COST_*` flags; `costs_money(flags)`: FIXED | VARIABLE | ROAMING | OVERDATALIMIT
     | APPROACHINGDATALIMIT; UNKNOWN (0) and UNRESTRICTED alone are no.
   - `TETHER_DESCRIPTIONS` ("Remote NDIS", "Apple Mobile Device Ethernet"; case-insensitive
     substring) and `is_tether(description)`.
   - `@dataclass Connection(interface_index, internet, cost)`, `@dataclass Interface(index, kind, description)`.
   - `Connection(interface_index, internet, cost, network_id)`, `Verdict(metered, guessed_from)`
     (`guessed_from`: the network ids behind a tunnel that made it metered; empty when certain).
   - `verdict(route_indexes, interfaces, connections) -> Verdict`, pure: for each route interface
     (IPv4, IPv6): kind `cellular` or a tether description → metered; its connection's cost costs
     money → metered. Only when none is: if a route interface's kind is `tunnel`, `ppp` or `other`
     (no physical medium), every other connection with Internet access that costs money (or is
     cellular / a tether) makes it metered, guessed from that connection's network id. No route → no.
   - `read() -> Verdict`: gathers the readings (COM initialised per call, MTA, released in
     `finally`; any `OSError`/COM failure → the readings it has, never an exception), calls
     `verdict`. Network ids come from `INetworkConnection.GetNetwork` → `INetwork.GetNetworkId`.
3. `app/traffic.py`: no change expected (`interface_row` already gives `kind` and `description`).
4. `app/server.py`: `on_cellular` → `on_metered` (constructor argument, `_default_on_metered` calls
   `netcost.read()`); docstrings say "metered connection". `app/speedtest.py`:
   `needs_confirmation(verdict, confirmed_networks)`: certain → always; guessed → while any of its
   networks is not in `confirmed_networks`. `Store.confirmed_networks()` / `confirm_networks(ids)`
   keep them in `speedtest.json` (a list of GUID strings, local only). The server adds a guessed
   verdict's networks after a run the user confirmed (`{"confirmed": true}`), before the run starts,
   so a run refused by the test server still counts as asked.
5. Tests: `tests/test_netcost.py` (new): the decision table (Wi‑Fi unrestricted, Wi‑Fi fixed,
   Ethernet variable, roaming flag alone, unknown cost, WWAN with unknown cost, IPv6-only metered,
   IPv4 unmetered + IPv6 metered, VPN over metered Wi‑Fi, VPN over unmetered Ethernet with a metered
   network that has no Internet, RNDIS and Apple tether adapters, no route); `read()` with fake COM
   readers returning partial results and raising. `tests/test_speedtest.py`: a guessed verdict asks
   once, then not again for the same networks, again for a new one, after a restart (new `Api`
   on the same store) still not; a certain verdict asks every time. `tests/test_speedtest.py`: rename the injected
   `on_cellular`, replace `test_a_mobile_broadband_route_reads_as_mobile_data` with a test that
   `_default_on_metered` uses `netcost.read`. `tests/test_server.py`, `tests/test_bufferbloat.py`:
   the renamed argument. `tests/test_winutil.py`: the IPv6 route and GUID mapping return None on a
   failing call (patched `WinDLL`).

### B. Smooth live chart

6. **Record a trace (done 2026-10-16 with the owner's go-ahead; result in the intent's Findings).** A scratch script (not committed)
   runs `bufferbloat.measure(..., events=...)` once and, at each 0.25 s beat, logs the meter's total
   and the route interface's `in_octets` / `out_octets` (`traffic.interface_row`). Committed:
   `tests/fixtures/speed_trace.json` with `[seconds, meter_bytes, interface_bytes]` per direction,
   the two figures and a one-line description (no server code, address or adapter name). It also says which hypothesis holds: steps in the
   meter total while the interface counter is steady (bytes counted at the socket in bursts, and each
   connection reconnecting every 25 MB) versus a line that really varies.
7. `app/bufferbloat.py` `LiveRate`: the slope over the window (3 s) by least squares over the
   readings in it, None until two readings are apart in time, never negative (chosen from the
   replayed candidates in the intent's Findings). Docstrings and ADR-0024 decision 1
   ("the rate over the last second") follow.
8. `web/js/speed.js` `chart`: the top of the scale is `niceScale` over the points (imported from
   `ecg.js`, already tested) with finer steps than the floating monitor's (`CHART_STEPS`: 1, 1.5, 2,
   3, 5, 7.5 per decade; with 1-2-5 an upload of 180 drew under 40 % of the chart's height). The
   series is cumulative while a run goes, so the top only grows without kept state. The path is a
   monotone cubic through the points (no overshoot below zero or above the peak), in a new pure
   module `web/js/curve.js` so node can test it (`speed.js` needs the DOM). Nothing else on the
   card changes.
9. Tests: `tests/test_bufferbloat.py`: `LiveRate` on a steady total read in bursts (the trace
   pattern) stays within 5 %, on a step it reaches 90 % within 2.5 s, None before two readings,
   never negative; replaying `speed_trace.json` through `run_meter` (where the live rate is read)
   with a fake clock gives the same ramp reading and steady figure with the new live rate and with
   1.0.1's (kept in the test as `EndsRate`), and the mean step stays at 5 % or less.
   `tests/js/speed.test.mjs` (new): the scale only grows, the curve stays between its points.
   (The fixture lives in `tests/fixtures/`: `.gitignore` ignores every `data/` folder.)

### C. Docs

10. `docs/adr/0024-windows-speed-test.md`: status "Accepted (amended 2026-10-16)"; decision 1 (the
    live rate's window and method), decision 4 (metered by the connection cost, IPv6, tunnels, USB
    tether; a mobile broadband adapter still counts); the ⚠️ consequence replaced by what is still
    missed (a hotspot that does not tell Windows it is metered, a tether with another adapter name)
    and which cases the owner checked by hand. `docs/adr/README.md` status column.
11. `CLAUDE.md` "Open": the bullet on mobile data detection is replaced by what is left (the hand
    checks not done, if any). `docs/ARCHITECTURE.md`: `ask` reads "on a metered connection";
    `app/netcost.py` in the module list. `docs/DIAGNOSTICS.md` check #14: when it asks.
    `CHANGELOG.md` `[Unreleased]`: two Fixed entries (asks on metered connections; the live chart
    no longer saw-tooths, which the owner reported as a bug). `PRIVACY.md` unchanged (it already says "on mobile data, after you confirm it", still
    true; no new data leaves).
12. Intent status lines as the work moves; Plane items moved and commented with commits.

## Risks, and what catches them

| Risk | Caught by |
|---|---|
| A wrong COM vtable offset crashes the server process (an access violation is not an exception) | Offsets taken from `netlistmgr.h` and written next to each call; the read-only probe on the dev PC before wiring it into the server; `read()` runs once at server start in a smoke test on the dev server |
| COM initialised in a thread that already has another apartment (`RPC_E_CHANGED_MODE`) | `read()` treats that result as initialised and does not uninitialise; test with a fake that returns it |
| Windows marks the VPN interface's own connection unrestricted and the route reads it first | The tunnel rule reads the other connections too (test case "VPN over metered Wi‑Fi") |
| Slow reading on every `GET /api/speedtest` and every bar refresh | Measured on the dev PC (target under 50 ms); if slower, cached for 5 s in the server |
| A metered network that is connected but not used (Ethernet + idle WWAN) makes Wi‑Fi runs ask | Only route interfaces count, except behind a tunnel (test case) |
| Smoothing lags so the live figure trails the final figure at the end of a direction | 90 % within 2.5 s target; at "ended" the card already switches to the result's figure |
| A remembered VPN confirmation hides a new metered network | Kept per network id; a network not in the list asks (test case) |
| The smoothing changes a figure through a shared path | `steady_mbps` and `mbps` never use `LiveRate` (read in review); the replay test compares figures bit for bit |
| The trace fixture leaks an identifier | It holds numbers only; reviewed in the diff; `tests/test_naming.py` and the identifier rules |
| Apple and Windows read one run differently | Figures unchanged on both; only the live line differs; ADR says so and a Mac follow-up is filed if the trace shows the same cause there |

## Proof (what "done" means)

1. `python -m unittest discover -s tests -t .` green (count before/after reported), including
   `tests/js` through `test_web.py` (node is installed).
2. `python -c "import time; from app import netcost; t=time.perf_counter(); print(netcost.read(), round((time.perf_counter()-t)*1000), 'ms')"`
   on the dev PC: `False`, under 50 ms. Readings printed without names or addresses.
3. Dev server (isolated, per the repo's dev-server notes) with a scripted run that replays the
   trace: screenshots of the Overview card mid-upload and after, light and dark, before and after.
4. The replay numbers for the old and new `LiveRate` (mean step as % of the figure, time to 90 %),
   in the PR description.
5. Owner's hand checks (Settings → metered on and off; hotspot, tether, VPN where available), each
   recorded as checked or not in the PR and the ADR.
6. One real run after the change (owner's go-ahead): the chart watched live, the figures within 5 %
   of the interface byte counters over the same phases, as for 1.0.1.
7. Independent review (reviewer agent) against intent and plan; the COM code and the decision rules
   are read as HARD (they decide whether real traffic goes over someone's data plan).
