---
status: building   # draft → approved → planned → building → verified → shipped (PR #n)
---
# A solid floating monitor on Windows that screenshots can see

Supersedes [ADR-0021](../docs/adr/0021-floating-monitor-glass.md) on Windows (new ADR-0026). Windows
only: the Apple app keeps its Glass look (ADR-0022), and the shared catalogs do not change.

**Problem**

1. **The floating monitor is missing from screenshots.** Its default look, Glass, marks the window
   `WDA_EXCLUDEFROMCAPTURE`, so Lightshot, the Snipping Tool, screen recordings and screen sharing
   show what is behind it instead. The owner met it taking a screenshot to report a problem, which is
   exactly when a user wants the bar in the picture. Measured 2026-10-16 with the source build: Glass
   gives the window affinity `0x11` and a GDI capture of its rectangle shows the terminal behind it;
   Off gives `0x0` and the capture shows the bar.
2. **The looks cost more than they give.** Glass, the Low and High opacities and the bar's smooth
   rim (ADR-0021 and its 2026-10-15 amendment) take about 215 of the 1003 lines of
   `app/desktop.py`, about 70 lines of the page and 7 tests. They run a background thread that
   reads the screen behind the window and holds a lock while it calls `evaluate_js` across threads,
   the same family of code as the freeze fixed on `fix/sic-131-desktop-freeze`. `PRIVACY.md` has to
   explain that the app reads screen pixels.

**Outcome**

1. On Windows the floating monitor is always solid. There is no transparency choice; the appearance
   options keep the theme only. A `mini.json` saved by an earlier version, with a `transparency`
   field, loads without error and the field is dropped.
2. The one-line bar has the shape the owner chose: the Optimize button is a square, 32 × 32, with
   slightly rounded corners (about 4 px); the bar with the numbers is a rectangle 24 px high,
   centred on the button's height, starting at its right edge, with the same small rounding. The
   window is cut to exactly those two shapes (a window region), scaled with the screen's DPI. Their
   corners may show a 1 to 2 px step. If that step is plainly visible at the PC's scale, the bar
   becomes one rounded rectangle instead, with the button inside it, and Windows rounds its corners
   smoothly (DWM, no region) — the owner's fallback, 2026-10-16.
3. The full table is unchanged and keeps Windows' own rounded corners.
4. The app no longer reads screen pixels and never calls `SetWindowDisplayAffinity`. Nothing new
   leaves the PC, nothing is stored.
5. Success is measured by:
   - `python -m unittest discover -s tests -t .` green; the look tests are removed and new ones cover
     the bar's two shapes (sizes, centring, rounding, DPI) and an old `mini.json` with
     `transparency`.
   - The same probe as the Problem, on the new build: both windows report affinity `0x0` and a GDI
     capture of the bar's rectangle shows the bar, in the light and the dark theme.
   - A stack dump of the running shell has no `desktop-glass` thread.
   - By eye, with the owner: the bar's shape, its corners at the PC's scale, dragging it, switching to
     the table and back.
   - `PRIVACY.md` no longer says the Windows app reads screen pixels; ADR-0021 is marked superseded
     on Windows by ADR-0026; an independent hard review passes (a `PRIVACY.md` change is HARD).

**Not in scope**

- The Apple app's Glass and its catalog keys (`ui.mini.transparency*`, still used by
  `FloatingTable.swift`).
- The full table's layout, the bar's contents and behaviour, and the freeze fixes (their own PR).
