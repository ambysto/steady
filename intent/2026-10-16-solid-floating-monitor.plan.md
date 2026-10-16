# Plan: a solid floating monitor on Windows that screenshots can see

Intent: [2026-10-16-solid-floating-monitor.md](2026-10-16-solid-floating-monitor.md) (scope chosen
by the owner 2026-10-16, plan accepted the same day). The cut is tried first and judged on a zoomed
capture of the real bar; if its corners step visibly, step 1's region and step 3's shapes become
one rounded rectangle with the button inside (the table's path: no region, DWM corners). Branch `claude/solid-floating-monitor`, cut from
`fix/sic-131-desktop-freeze` (both change `app/desktop.py`); its PR targets `main` once that one is
merged. HARD (it edits `PRIVACY.md`): the main session builds it, an independent hard review checks
it. Windows session: no Swift, no catalog keys, no `app/__init__.py` bump.

## Design in one paragraph

Everything that reads the screen or changes the floating window's opacity goes: the Glass backdrop,
the Low and High levels, the bar's rim, the `desktop-glass` thread, `_look_lock` and
`SetWindowDisplayAffinity`. The bar keeps its window-region cut, now the union of two rounded
rectangles made with `CreateRoundRectRgn` (a square button and a lower bar), drawn by the page edge
to edge so no sliver of the screen is needed. The table keeps `SetWindowRgn(None)` and DWM's rounded
corners, as now.

## Steps and files, in order

1. `app/desktop.py`
   - Constants: drop `TRANSPARENCY`, `DEFAULT_TRANSPARENCY`, `GLASS_EVERY_S`, `GLASS_MOVE_S`,
     `RIM_EVERY_S`, `GLASS_SCALE`, `WDA_*`, `BULB`, `TUBE`. Add `BUTTON = 32` (the square's side, the
     bar's full height), `STRIP = 24` (the bar's height), `CORNER = 4` (corner radius, logical px).
     `BAR_SIZE` stays (330, 32).
   - Functions: drop `exclude_from_capture`, `grab_behind`, `window_rect`, `_BitmapInfoHeader`,
     `grab_screen`, `jpeg`, `frosted`, `set_form_opacity`. `thermometer_parts` becomes
     `bar_parts(size, scale) -> [(left, top, right, bottom, radius)]` for the button and the strip
     (exclusive right/bottom, as `CreateRoundRectRgn` takes them; the ellipse size is twice the
     radius). `shape_window` builds both with `CreateRoundRectRgn`, combines them (`RGN_OR`) and
     keeps the DWM switches as now.
   - `load_mini_state`/`save_mini_state`: no `transparency` key; an old file's key is ignored.
   - `MiniBridge`: drop `get_transparency`, `set_transparency`, `hover`.
   - `Desktop`: drop `_glass_wake`, `_glass_digest`, `_glass_ok`, `_rim_digest`, `_look_lock`,
     `_rim_at`, `apply_mini_opacity`, `set_mini_transparency`, `glass_on`, `apply_glass`,
     `update_backdrop`, `rim_on`, `update_rim`, `_glass_loop`, `mini_hover`, and `_run_js` unless
     something else still calls it. `_mini_hwnd` stays (`_fit_mini` needs the handle for
     `shape_window`). `show_mini`, `set_mini_mode`, `_on_mini_moved`, `_on_mini_loaded` and
     `create_mini`'s `shown` handlers lose their look calls; `run()` no longer starts the thread.
   - Unused imports (`base64`, `hashlib`, `io`) go. Module docstring: no "how see-through it is".
2. `web/mini.html`: drop the transparency row. `web/js/mini.js`: drop `markTransparency`, the
   transparency setup, the hover listeners, `setBackdrop`, `setRim`, `JPEG_URL` if unused;
   `window.steadyMini = { setActive }`.
3. `web/mini.css`: drop the glass and rim rules. `.optimize` in the bar: 32 × 32 at the left edge,
   `border-radius: 4px`; `.tube`: from 32 px to the right edge, 24 px high, centred,
   `border-radius: 4px` (the left corners sit against the button). Shapes fill the cut exactly.
   The ring inside the square stays, slightly inset so it clears the corners.
4. `tests/test_desktop.py`: remove the 7 look tests; `test_thermometer_outline` becomes a test of
   `bar_parts` (sizes, centring, radius, DPI 1.5); add: a `mini.json` with `"transparency": "glass"`
   loads and saves without it.
5. Docs: new `docs/adr/0026-solid-floating-monitor-on-windows.md` (context: the screenshot problem
   and the measured affinity; decision; consequences: slight steps at the corners, no screen
   reading, the Mac keeps Glass); ADR-0021 status "Superseded on Windows by ADR-0026"; index in
   `docs/adr/README.md`; `docs/ARCHITECTURE.md`'s `desktop.py` row; `PRIVACY.md` drops the paragraph
   on the floating monitor reading pixels (Windows part only); `CHANGELOG.md` `[Unreleased]`
   (Changed: the floating monitor is solid and shows in screenshots; Removed: the transparency
   levels on Windows).
6. Verify (the intent's list): full suite; the affinity and capture probe and a stack dump with the
   scratch harness (isolated user folder, the installed app untouched); a look at the bar in both
   themes; then the owner by eye.
7. Hard review of the diff against the intent; fix at most two rounds; PR.
