# UI direction

> **Decided (2026-10-03):** option A, pywebview window, Mac-style window controls — see [ADR-0002](adr/0002-macos-style-ui-pywebview.md).

## Question: designing in a "macOS native" direction

There are two interpretations, with very different amounts of work:

| | A. macOS style on Windows | B. Real macOS app |
|---|---|---|
| Runs on | Windows (current machine) | Mac |
| Technology | Web UI + desktop window (pywebview / Tauri) | Swift + SwiftUI, Xcode |
| Backend | Kept as is (Python + PowerShell) | Completely rewritten for macOS |
| Existing tweaks | Kept as is | Mostly do not exist on macOS (driver properties, ASPM, PnPCapabilities…) |
| Distribution | Folder/exe | Requires an Apple Developer account to sign & notarize |

**Recommendation: A** — use the macOS design language for the Windows tool.

## What option A needs

### 1. Design language
Reference **System Settings** (macOS Sonoma/Sequoia) and the Apple Human Interface Guidelines:
- **Sidebar + content** layout: a translucent (vibrancy) sidebar listing Overview · Optimize · Diagnostics · Log.
- **Grouped inset list**: settings grouped in blocks with ~10px rounded corners; each row has a label, a secondary description, and a control on the right.
- Controls: macOS-style switch, segmented control, popover, confirmation **sheet** for risky tweaks.
- Typography: 13px body, large titles; 8pt grid; few colors, accent color follows the system.
- Light/dark follows the system automatically; soft (spring) motion, no underline on hover.

### 2. Fonts & icons — licensing notes
- **SF Pro** and **SF Symbols** are licensed for use on Apple platforms only ⇒ do not bundle them into the Windows tool.
- Alternatives: the **Inter** font (OFL), close to SF; or `system-ui` (Windows will resolve it to Segoe UI Variable).
- Icons: **Lucide** / **Phosphor** (MIT) — thin strokes similar to SF Symbols.

### 3. App window (instead of a browser tab)
A browser tab cannot feel native. Options:

| Option | Pros | Cons |
|---|---|---|
| **pywebview** (WebView2) | Keeps Python, lightweight, frameless window | Adds a dependency (breaks ADR-0001 "standard library only") |
| Tauri | Very lightweight, good packaging | Adds the Rust toolchain, backend in a separate process |
| Electron | Large ecosystem | Heavy (~150MB) |
| Browser tab | Needs nothing extra | Does not feel like an app |

A frameless window needs a self-drawn title bar. Window control buttons: "traffic light" buttons on the left (like Mac, unfamiliar to Windows users) or Windows buttons on the right → **decided (2026-10-04): use the native Windows frame**, not frameless. A self-drawn title bar has no Snap Layouts, no split-screen when dragged to an edge and no Win+arrow; the native frame has all of these. The title bar is painted in the same color as the page background.

Real blurred background effect: Windows 11 has **Mica/Acrylic** via `DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)` — callable with ctypes on the window handle; combined with CSS `backdrop-filter` for the sidebar.

### 4. System tray icon (the equivalent of a macOS menu bar extra)
A very good fit for a network monitoring tool: green/yellow/red icon by status; clicking it opens a small popover (current ping, packet loss, "Reconnect" button). Requires `pystray` + `Pillow`, or calling `Shell_NotifyIcon` directly via ctypes.

### 5. Details that create a native feel
- Keyboard shortcuts (`Ctrl+,` opens settings, `Ctrl+R` runs diagnostics).
- System notifications (Windows toast) instead of in-page popups.
- No horizontal scrolling, no visible address bar, no browser right-click menu.
- Instant feedback: a toggle switches to an "applying…" state, then confirms.

### 6. Design process
1. Static mockup (HTML) for 4 screens + tray popover → review.
2. A shared set of design tokens (colors, spacing, corner radius, fonts).
3. Build the real UI on top of those tokens.

## Mockup and design tokens (SIC-27, approved 2026-10-04)

- **Display name:** "Ambysto Steady" (key `app.name`, not translated; changed on 2026-10-04 from "Stable Internet Connection", which is now the tagline). Ambysto is the company name, derived from the scientific name of the aquatic salamander (axolotl). "StableInternet" is now only the internal repo/folder/task/mutex name.
- **Narrow width (< 760px):** the sidebar turns into a horizontal tab bar at the top — navigation is never hidden.

- **To view:** `docs/mockup/dist/stableinternet-mockup.html` (a single file, open it directly in a browser). Sources: `docs/mockup/mockup.html`, `mockup.css`, `mockup.js`; run `python scripts/build_mockup.py --bundle` after editing the sources or the catalog.
- **Screens:** Overview (status, router/Internet latency chart, metrics, recent outages, quick actions, watchdog) · Optimize (grouped by `tweak.group.*`, risk/Admin/network-drop labels, switches, confirmation sheet for experimental tweaks) · Diagnostics (13 expandable checks, "What to do", on-demand bufferbloat) · Log (filter All/Outages/Watchdog/Changes, grouped by day) · Settings (**Language**, Light/Dark/Same as Windows appearance, start with Windows, notifications, watchdog) · tray popover. The top bar (mockup only) simulates a network outage and toggles the popover.
- **Check → Fix → Result (SIC-100, [ADR-0020](adr/0020-check-fix-result-flow.md)):** the Overview's first card replaces the old "Suggestions" list. Its centre is one big round button: "Check my connection" before checking, a ring that fills as each check really finishes while checking (the checks tick off below it), and "Fix N of M" when problems are found (warn/bad only, each labelled "The app can fix this" / "You can fix this" / "Nothing on this PC fixes this"; a status circle instead when the app can fix none of them), the confirmation sheet (the low-risk tweaks for the problems, then the other low-risk tweaks check #10 lists, all ticked and untickable, one note for the Wi‑Fi drop and one for the single UAC prompt), turning on (tweaks that do not drop Wi‑Fi first), checking again, and the result (measured over the coming days / better now / no change yet, the last with "Undo these changes"), followed by what is still open. "Nothing to fix" is a green circle with the numbers behind it. Below it, "What Steady did for you" lists the last 7 days, or how long the app has been watching when there is nothing to count. The review toolbar picks the scenario, the result and the value card; `?flow=found|clear|result` (with `scenario`, `result`, `value`, `lang`, `theme`) opens a state directly.
- **Text:** all taken from `app/locales/*.json` (keys `ui.*` and the backend's own `diag.*`/`event.*`/`watchdog.*` messages); changing the language in Settings takes effect immediately. Sample data taken from the development machine on 2026-10-04.
- **Design tokens:** `web/tokens.css` — font (Inter → Segoe UI Variable), font sizes 11/12/13/15/22/28, 4 pt grid, corner radius 10 (groups/windows) and 6 (controls), semantic light/dark palette (`--ok/--warn/--bad/--info`, `--surface`, `--separator`…), short motion that is disabled under `prefers-reduced-motion`. Dark mode follows the system, forced with `data-theme`. The real UI (SIC-28) reuses this file.
- **Not yet in the mockup:** bundled Inter font (system font used for now), the window's real Mica effect, the tray icon.
