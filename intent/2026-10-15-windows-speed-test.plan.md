# Plan: speed test on Windows

Intent: [2026-10-15-windows-speed-test.md](2026-10-15-windows-speed-test.md) (approved 2026-10-15). Branch `claude/windows-speed-test`.

## Design in one paragraph

As on Apple (ADR-0023), check #14's run is the speed test. `bufferbloat.measure` gains an optional
`events` callback and a shared `Meter` that the loads count their bytes into and note the test
server's location in (`colo`, else the end of `CF-RAY`). A meter thread reads it on a fixed 0.25 s
beat during each loaded phase and reports the rate over the last second, and records the reading
taken once the 2 s ramp is over, which gives the steady figure. `Phase` gains `steady_mbps` and
`series` with defaults, so `evaluate_bufferbloat` and the vectors are unchanged. A new pure module
`app/speedtest.py` ports `SpeedTest`, `SpeedTest.Live` and `LiveRate`. The server has one job,
`speedtest`, that both the Overview and Diagnostics start (and the bar, through the same endpoint);
its progress is the live view, its result is saved to the data folder with the run's measurement, and
check #14's result is rebuilt from it at start.

## Files, in order

1. `app/bufferbloat.py`: `Meter` (thread-safe bytes and server), loads take an optional meter and
   read `colo` / `CF-RAY`; `measure(..., events=None, meter_interval=0.25)` emits
   `("stage", name)`, `("rate", name, seconds, mbps)`, `("ping", name, seconds, ms)` and
   `("figure", name, mbps)`; the meter stops when the load ends (byte limit or refusal);
   `Phase.steady_mbps`, `Phase.series`, `Measurement.server`.
2. `app/speedtest.py` (new, pure): `LiveRate`, `steady_mbps`, `figure`, `median`, `jitter`, `loss`,
   `thinned` (≤ 48 points), `result(measurement, at)`, `Live` (applies events, `snapshot()` for the
   job's progress), `to_dict` / `from_dict` for the result and the measurement, `needs_confirmation`.
3. `app/server.py`: `GET /api/speedtest` (last result, `confirmed`, `retry_until`);
   `POST /api/speedtest` (body `{confirmed: true}` records the confirmation; a job with progress,
   single with the old bufferbloat job, under `_line_lock`); `POST /api/diagnostics/bufferbloat`
   runs the same job. The last result is `data/speedtest.json` (`storage.data_dir`), loaded at start,
   so check #14 comes back too; one that does not decode is ignored.
4. `web/js/speed.js` (new): the card (big figures, area charts drawn as inline SVG, latency column,
   24-segment progress bar, server and time, refusal countdown) and `runSpeedTest(ctx)` with the
   confirmation sheet. `web/app.css`: its styles with the floating monitor's download blue and upload
   purple tokens.
5. `web/js/screens/overview.js`: a speed slot under the check card. `web/js/screens/diagnostics.js`:
   the Bufferbloat card starts `runSpeedTest`, shows the phase while it runs and the saved result
   with its time.
6. `web/mini.html`, `web/mini.css`, `web/js/mini.js`: the gauge button in the tube; ring while
   running (poll `/api/jobs/<id>`), download figure after; a click opens the main window's Overview
   (as Fix N does). `app/desktop.py`: `BAR_SIZE` 290 → 320 wide.
7. Locales: reuse `ui.speed.*`; add only what Windows needs (none expected). Regenerate the Apple
   catalogs if a key changes.
8. Tests: `tests/test_speedtest.py` (ported Swift cases plus round trip of the saved result),
   `tests/test_bufferbloat.py` additions (events, meter, steady figure, server header with fake
   loads and clock), `tests/test_server.py` additions (job, confirmation, persistence, check #14
   rebuilt).
9. Docs: ADR-0024 (Windows: same run, where the result is kept, no metered detection yet) and the ADR
   index, `CHANGELOG.md` `[Unreleased]`, `docs/DIAGNOSTICS.md` check #14, `docs/ARCHITECTURE.md`
   (new module and endpoints), CLAUDE.md "Open".

## Verification

- `python -m unittest discover -s tests -t .`
- `python scripts/locales_to_xcstrings.py --check`
- The isolated dev server with a scripted run (no traffic): Overview, Diagnostics and the floating
  bar, light and dark, refusal state.
- With the owner's go-ahead only: one real run, compared with the interface byte counters.
- An independent review (the speed-test engine is HARD in CLAUDE.md).

## Change 2026-10-15: independent review

- A page that saw a run going elsewhere joins it with `{"join": true}`, which never starts one: a
  plain POST arriving just after that run ended would have started a second, unrequested test.
  A page that gave up waiting on a run (timeout) does not rejoin it.
- Progress is sent on phase changes and at most 5 times a second for rates and pings alike, so a
  refused or failed load (no rates) still moves the bar and the latency.
- The Diagnostics page redraws only when what its on-demand card shows changes, keeps each check's
  open state across redraws, and reloads the speed test on its 30 s poll (the wait counts down).
- The Overview's 30 s redraw ignores the speed card's disabled button.
- A result that cannot be written to `speedtest.json` still ends the run; the store has a lock.
- The steady figure's time never exceeds the load's 10 s (a worker blocked past the deadline).
- A run in which check #14 could not measure says so; Diagnostics shows when check #14 itself was
  measured (`bufferbloat_ts`).
- The bar shows an amber alert for a recent run without a download figure.
- A kept check #14 adds its suggestions for 7 days only.
