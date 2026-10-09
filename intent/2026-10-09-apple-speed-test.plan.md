# Plan: speed test on macOS, iOS and iPadOS

Intent: [2026-10-09-apple-speed-test.md](2026-10-09-apple-speed-test.md) (approved). Branch `claude/apple-speed-test`.

## Design in one paragraph

Check #14's run (`BufferbloatTest.run`) becomes the speed test. It gains a progress callback that
reports the stage and a live Mbit/s figure 4 times a second, read from `LoadGenerator`'s byte
counter. It also records the bytes moved at the end of the 2 s ramp, so the speed figure is the
steady rate after the ramp, as Speedtest-style apps report it. `Bufferbloat.Phase.mbps` and
`Bufferbloat.evaluate` stay exactly as they are, so the spec vectors and Windows still agree. A new
`SpeedTest.Result` (Codable, persisted) holds the four figures. `AppModel` has one runner that sets
both `speedResult` and `bufferbloat`. Three places start it: the Overview card, the Diagnostics card
and the floating bar's new button.

## Change 2026-10-09: speed.cloudflare.com-like layout (intent decision 4)

- **Engine.** The progress callback becomes `BufferbloatTest.Event`, with three kinds:
  - `stage`
  - `rate(stage, seconds, mbps)`, every 0.25 s
  - `ping(stage, seconds, ms?)`, each round's Internet reply

  `sample` reports each round. `meter` also returns the rate series. `LoadWorker` reads the `colo`
  response header into the generator. `Run` gains the two series and `server`.
- **`SpeedTest.Live`** (new, pure). It applies events and gives the live figures: the current rate,
  each phase's latency median, jitter and loss, and the elapsed fraction. `SpeedTest.Result` gains:
  - each direction's series (downsampled to ≤ 48 points)
  - latency and jitter for idle, ↓ and ↑
  - `lossPercent`
  - `server`

  New fields are optional, so a result saved before still decodes. `loadedPingMs` goes, replaced by
  ↓/↑. Shared statistics (`median`, `jitter`, `loss`) live in one place and are tested.
- **Views.** `SpeedTestCard` is rewritten with:
  - `SpeedChart` (Swift Charts area + line, Catmull-Rom, no axes)
  - a latency column
  - `SpeedProgressBar` (24 one-second segments coloured by phase)

  It is three columns where they fit (`ViewThatFits`, at least 560 pt, so a Mac or an iPad). Narrow (iPhone) it is Download | Upload with
  their charts, then Latency, Jitter and Loss in a row. `AppModel.speedProgress` becomes
  `speedLive: SpeedTest.Live?`. The Diagnostics card reads its stage.
- **Strings.** Add `ui.speed.latency`, `ui.speed.jitter`, `ui.speed.loss`, `ui.speed.server`
  (`{code}`), `ui.speed.unit.mbps`, `ui.speed.unit.ms`. Drop `ui.speed.ping_idle` and
  `ui.speed.ping_loaded`.
- **Proof 5** compares with the interface counters (a scratch harness, not committed), not with a
  browser.

## Change 2026-10-09: independent review

- The meter stops when the load ends (byte limit or refusal). Before, it went on reading a frozen
  total, and the live figure and the chart fell to zero.
- A direction under 1 Mbit/s has no figure, as in check #14. The card notes a missing direction.
- The floating bar's question is watched on the Overview's Form, not on the lazily loaded card.
  `takeSpeedConfirmRequest()` hands it to one window only.
- The meter reads on a fixed 0.25 s beat.
- The Diagnostics card redraws every 15 s and says why it is disabled after a refusal from an
  earlier launch.
- The floating tooltip shows the countdown.

## Files, in order

### 1. Engine (`apple/SteadyKit`)

- `Sources/SteadyKit/Measurement/BufferbloatTest.swift`
  - `LoadGenerator.bytes` (read the running total under the lock).
  - `Progress { stage, liveMbps: Double? }`; `run(router:maxBytes:progress:)` replaces the `stage`
    closure. `loaded` runs a poller task beside the pings: every 0.25 s it reads `load.bytes`, feeds a
    `LiveRate`, reports the rate, and remembers the bytes at `rampSeconds`. It returns the phase plus
    `steadyMbps = (bytesEnd − bytesAtRamp)·8 / (elapsed − ramp) / 1e6` (nil when the phase ended before
    the ramp or moved nothing). `run` returns `BufferbloatTest.Run { measurement, downloadSteadyMbps,
    uploadSteadyMbps }`.
- `Sources/SteadyKit/Measurement/LiveRate.swift` (new): sliding-window rate from (seconds, bytes)
  points, 1 s window; pure, no clock.
- `Sources/SteadyKit/Measurement/SpeedTest.swift` (new): `SpeedTest.Result` (Codable, Equatable):
  `measuredAt`, `downloadMbps`, `uploadMbps`, `downloadCapped`, `uploadCapped`, `idlePingMs`,
  `loadedPingMs`, `refusal (status, retryUntil)?`, `failure: Message?`.
  `SpeedTest.result(from: Run, at: Date) -> Result`. Ping idle = median of the "internet" idle
  samples. Ping under load = the worse of the download and upload medians of "internet" samples
  after the ramp (matches check #14's "worse direction"). A direction whose load failed, was refused
  or was too weak (< 1 Mbps) has a nil figure. `retryUntil` = at + Retry-After.
  Also `SpeedTest.needsConfirmation(metered:confirmedBefore:)`.
- `Tests/SteadyKitTests/LiveRateTests.swift`, `SpeedTestTests.swift` (new); update
  `BufferbloatTestTests.swift` for the new signature. The stub-URLProtocol load test also checks that
  `progress` sees a non-nil live rate.

### 2. Strings (`app/locales/*.json`, all 7, then `python3 scripts/locales_to_xcstrings.py`)

Keys under `ui.speed.*`, 23 in all:
- `title`, `start`, `again`, `idle_body`
- `running.idle|download|upload`
- `download`, `upload`, `latency`, `jitter`, `loss`
- `unit.mbps`, `unit.ms`
- `at_least` (`{value}`), `measured` (`{time}`), `server` (`{code}`)
- `refused` (`{minutes}`), `refused_later`, `failed`
- `mini.hint`, `mini.result`, `mini.open`

The confirmation reuses `ui.diag.bufferbloat_confirm[_metered]` and `ui.diag.bufferbloat_run[_metered]`.
Low Data Mode reuses `ui.diag.bufferbloat_constrained`. The floating bar's rate reuses `ui.mini.unit.*`.

### 3. App (`apple/Steady`)

- `AppModel.swift`: `runBufferbloat` → `runSpeedTest()`. New `speedStage: BufferbloatTest.Stage?`,
  `speedLiveMbps`, `speedResult` (loaded from and saved to `UserDefaults` key `speedtest.last` as
  JSON), `speedBlockedUntil` (from the result's refusal), `speedConfirmed` (`speedtest.confirmed`),
  `needsSpeedConfirmation`, `canRunSpeedTest`, `speedConfirmRequested` (set by the floating button so
  the main window shows the confirmation). `bufferbloatStage` → `speedStage` everywhere.
- `Steady/Components/SpeedTestConfirmation.swift` (new): a view modifier holding the
  `confirmationDialog`, used by the Overview card and Diagnostics; on confirm it sets
  `speedConfirmed` (unmetered only) and runs.
- `Steady/Overview/SpeedTestCard.swift` (new): a `Section` with three states. Idle shows the title,
  the Start button and the latest result if any. Running shows the phase and a large live Mbit/s
  number. Done shows a 2×2 grid (Download, Upload, Ping idle, Ping under load), "measured N ago" and
  a re-run button. Refused shows the countdown and disables the button. Low Data Mode shows the note
  and disables the button.
- `Steady/Overview/OverviewView.swift`: the card after `CheckCard()`.
- `Steady/Diagnostics/DiagnosticsView.swift`: the Bufferbloat card uses `runSpeedTest`, the shared
  confirmation and `speedStage`.
- `Steady/FloatingMonitor/FloatingSpeedButton.swift` (new, macOS):
  - Placement: a capsule in the tube right after the state dot, next to the Scan bulb.
  - Idle: a speedometer glyph.
  - Running: a ring that follows the phase (ping → download → upload by elapsed time).
  - Done: the download figure (e.g. "245").
  - Click when idle: runs it, or, when a confirmation is needed, opens the main window at the
    Overview and asks there.
  - Click when done: opens the Overview.
  - Refused: dimmed, with the countdown in the tooltip.
- `Steady/FloatingMonitor/FloatingMonitorView.swift`: add the button to the tube. Stat width
  66 → 60. Grow the bar if needed.
- `Steady/FloatingMonitor/FloatingMonitorController.swift`: `barSize` 300×36 → 330×36 if the button
  does not fit in 300.

### 4. Docs

- `docs/adr/0023-apple-speed-test.md` (new): the speed test is check #14's run; the steady rate after
  the ramp vs `Phase.mbps`; the confirmation rule; persistence.
- `docs/adr/0022-mac-floating-monitor.md`: point 3, the bar's new button and width.
- `docs/DIAGNOSTICS.md` check 14: one note that on Apple the run is also the speed test.
- `CHANGELOG.md` (Unreleased).
- `apple/README.md` if it lists features.

## Risks and how they are caught

| Risk | Caught by |
|---|---|
| The live poller keeps running or leaks the load after a cancel or leaving the screen | It is a child task of `loaded`, cancelled with it. A unit test cancels mid-run with the stub protocol and checks that `progress` stops and the load ends. |
| The steady rate is wrong (ramp bytes taken at the wrong time, division by ~0) | `SpeedTestTests` with fixed points. Nil when elapsed ≤ ramp. |
| Bufferbloat results change | `DiagnosisVectorTests` (spec vectors) still pass unchanged. `Phase.mbps` code is not touched. |
| Two runs at once (Overview + floating + Diagnostics) | One `speedTask` guard in `AppModel`. Every button reads `speedStage`. |
| The non-activating panel cannot show a confirmation | Never shown there: the floating button opens the main window instead. Checked by hand. |
| The bar overflows at 300 pt or in a long language (de) | Screenshot of the bar with `floating.look off`, in en and de. |
| Cloudflare refuses after a few runs during testing | Expected. The refused state itself is verified that way. Keep the manual runs to 2–3. |
| A stale persisted result is decoded from an older format | Decoding failure means no result (`try?`). Tested. |
| Catalog drift between the 7 languages | `tests/test_catalog.py`, `tests/test_apple_catalog.py`. |

## Proof (done = all green)

1. `cd apple/SteadyKit && swift test`: all pass (113 before plus the new tests).
2. `python3.11 -m unittest tests.test_catalog tests.test_apple_catalog tests.test_naming`: pass.
3. `xcodebuild -project apple/Steady.xcodeproj -scheme Steady -destination 'platform=macOS'
   -derivedDataPath build/mac CODE_SIGNING_ALLOWED=NO build`: succeeds.
4. The same for `-destination 'generic/platform=iOS Simulator'`: succeeds. The card is checked on an
   iPhone and an iPad simulator (screenshots of the idle and done states; the iPad's wider Form must
   not stretch the 2×2 grid oddly).
5. Manual run on the Mac (sandboxed ad-hoc build, see memory notes). The user presses the buttons; the
   terminal cannot click.
   - From the Overview: the live charts and figures while it runs, then the final layout. The
     figures are within 5 % of the interface counters over the same phases (scratch harness).
   - The Diagnostics Bufferbloat card shows a result from the same run.
   - Relaunch: the result is still there.
   - Floating bar: the button is next to Scan, runs it, shows the download figure, and a click opens
     the Overview. Screenshot with Glass off.
6. Independent review (a separate agent) with this plan and the intent as the contract.
