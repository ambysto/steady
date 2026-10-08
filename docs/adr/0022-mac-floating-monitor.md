# ADR-0022: The Mac's floating monitor is a non-activating panel that reads the measurements the app already makes

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** [ADR-0009](0009-native-apple-and-android-apps.md) (native Apple app), [ADR-0010](0010-apple-app-structure.md) (structure, shared catalogs), [ADR-0011](0011-apple-measurement-history.md) (measurements), [ADR-0020](0020-check-fix-result-flow.md) point 11 (Apple: Check and Result only), [ADR-0021](0021-floating-monitor-glass.md) (the Windows glass look), SIC-117

## Context

Windows 0.8.0 added a floating monitor: a small window on top of the others, a one-line bar (Scan, download, upload, ping) that opens into a table (three metric tiles, a heart-monitor chart, the apps holding connections, the connection's facts). The Mac app should have the same window.

The Mac differs from Windows in ways that change the design:

- The Mac app is native SwiftUI (ADR-0009), not a web page in a pywebview shell. Its measurements already exist in `SteadyKit`: ping and TCP to the router and the Internet every second, Wi‑Fi state every 5 s, the network path.
- A sandboxed Mac app cannot see which other processes hold connections. Windows gets that from `GetExtendedTcpTable`, which needs no administrator rights but reads every process's sockets. The Mac has no equivalent for a sandboxed app.
- AppKit can blur what is behind a window itself (`NSVisualEffectView` with behind-window blending). Windows could not, which is why ADR-0021 captures the screen.
- The Apple app changes nothing on the device (ADR-0020, point 11), so there is no Fix.

## Decision

1. **A non-activating `NSPanel`** (`FloatingMonitorController`), floating above other windows and visible in every Space and over full-screen apps. It does not take the keyboard from the app the user is working in. It opens at the top-right of the main screen the first time and then where it was left, if that is still on a screen.
2. **It lives while it is shown.** Its SwiftUI tree calls `AppModel.run()`, the same counting the windows use, so measuring goes on while the monitor is open. Hiding it releases that tree, which ends the measuring. Nothing is measured while it is closed and no window is open.
3. **Two forms of one view.** The bar is 300×36, a thermometer: the Scan bulb on the left, the tube with the state dot, download, upload and ping, and an arrow. The table is 340×470 and grows away from the nearest screen edge. The table has three tiles that pick what the chart shows, the sweep chart, the connection's facts (the Windows Network tab) and the options (appearance, transparency).
4. **Looks.** Glass (the default) is `NSVisualEffectView` with behind-window blending, tinted as on Windows. Glass also sets `sharingType = .none`, so the panel is left out of screenshots and screen sharing, as Windows does. Off is solid. Low and High fade the whole panel to 60 % or 35 % while the pointer is elsewhere, and make it solid again on hover.
5. **The chart and the numbers are in `SteadyKit`, not in the views.** `Sweep` is a port of `web/js/ecg.js` (`sweepLayout`, `niceScale`), `SweepSeries` turns the timed samples into the chart's series (the Internet ping is the quickest reply of each round, nil where every target lost it), and `Throughput` turns the interface byte counters into rates. All of it is tested with `swift test`, next to the Windows cases. One deliberate difference: the Mac scales the chart over the samples it still shows (`Sweep.shown`, the last 57 s), while Windows scales over its whole two-minute history, so there a spike that has scrolled off can keep the trace flat for up to another minute.
6. **Throughput** comes from the byte counters of the path's interface (`getifaddrs`, `if_data`), which a sandboxed app may read. The counters are 32-bit, so the arithmetic wraps. A gap longer than 2.5 s gives no rate, and a rate over 10 Gbit/s is dropped as a counter that went back. The address and DNS servers are read every 10 s while the monitor is open.
7. **No Apps tab on the Mac.** macOS does not let a sandboxed app see which other apps hold connections, and no permission the user can grant (not even an administrator password) changes that. The table leaves the list out instead of showing a tab that only says it is unavailable, so there is no Apps / Network switch either: the connection's facts sit directly under the chart. Showing the list would need a build outside the App Store sandbox, which would be a separate decision.
8. **Scan on the bulb** runs the same check as the main window's Scan (`AppModel.runCheck`), so the Mac's check list applies. The bulb's ring fills with the checks finished, shows the count of problems in amber or a green tick when there are none, and a click on a result opens the main window's Overview. There is no Fix, as ADR-0020 point 11 requires.
9. **Texts are the Windows ones** (`ui.mini.*`, `ui.tray.mini`), with four Mac wordings as `apple.` variants (`apple.ui.mini.apps.unavailable`, `apple.ui.mini.optimize_hint`, `apple.ui.mini.fix_hint`, `apple.ui.mini.close`), added to all seven catalogs in `app/locales` and regenerated into the String Catalog.
10. **Where it is opened:** the Window menu (Floating monitor, ⌥⌘M) and a row on the Overview. Its state persists: the look and the theme are kept in `UserDefaults`, the position too. It starts as the bar every time.

## Consequences

- ✅ The Mac and the Windows monitor show the same numbers and the same chart (except the chart's scale, see 5), and the same texts in every language.
- ✅ The sweep, the series and the throughput rules are tested with `swift test`, so they do not depend on a Mac running the window.
- ✅ Glass uses the system's own blur, so nothing is read from the screen and no capture runs.
- ⚠️ The Mac has no Apps list. This is the largest gap with Windows, and it is a platform limit, not a missing feature.
- ⚠️ The window has no Fix: a result says what the check found and where it can be looked at, and the main window says what to do.
- ⚠️ The panel uses AppKit APIs (`NSPanel`, `sharingType`, behind-window blending) that the Xcode build and a Mac must confirm: the layout and the Glass look are not checked by the unit tests.
- ⚠️ Glass hides the panel from screen sharing. Turning it off shows it again, and the glass hint says so.
