# ADR-0026: On Windows the floating monitor is solid, and screenshots see it

- **Status:** Accepted
- **Date:** 2026-10-16
- **Related:** supersedes [ADR-0021](0021-floating-monitor-glass.md) (and its 2026-10-15 amendment) on Windows; [ADR-0022](0022-mac-floating-monitor.md) (the Mac keeps its Glass look); intent [2026-10-16-solid-floating-monitor](../../intent/2026-10-16-solid-floating-monitor.md)

## Context

ADR-0021 gave the floating monitor a frosted "Glass" look, its default, made from a blurred
capture of the screen behind it. To capture what is behind it and not itself, the window is marked
`WDA_EXCLUDEFROMCAPTURE`, so every capture leaves it out: Lightshot, the Snipping Tool, screen
recordings, screen sharing. Its amendment gave the one-line bar, cut to a thermometer outline, a
smooth rim in the other looks by capturing what is behind it now and then, leaving captures for a
frame each time.

The owner met the first consequence while taking a screenshot to report a problem, which is when a
user most wants the bar in the picture. Measured on 2026-10-16: with Glass the window's display
affinity is `0x11` and a GDI capture of its rectangle shows the window behind it; with Off it is
`0x0` and the capture shows the bar.

The looks also cost: about 250 of the 1029 lines of `app/desktop.py`, about 70 lines of the page
and 7 tests, a background thread that reads the screen and holds a lock while it calls the page
across threads (the same kind of code as a freeze found the same day), and a paragraph in
`PRIVACY.md` on reading screen pixels.

## Decision

1. **On Windows the floating monitor is solid.** Glass, the Low and High opacities and the bar's
   rim are removed, with the thread that captured the screen and every call to
   `SetWindowDisplayAffinity`. The app does not read screen pixels. There is no transparency
   choice; a `mini.json` written by an earlier version loses its `transparency` field when loaded.
2. **The bar is a square button and a rectangle.** Optimize is a 32 × 32 square and the strip with
   the numbers is 24 px high, centred on it and starting at its right edge; both have corners
   rounded by 4 px. The window is cut to those two rounded rectangles (`CreateRoundRectRgn`,
   combined), scaled with the window's DPI, and the page fills them edge to edge. The table keeps
   its rectangle and Windows' own rounded corners. *(Replaced by the amendment below.)*
3. **The Mac is unchanged.** Its Glass (ADR-0022) has no such cost there, and the shared catalog
   keys it uses (`ui.mini.transparency*`) stay.

## Consequences

- The floating monitor shows in screenshots, recordings and screen sharing, in every state.
- A window region has no soft edge: each rounded corner steps by a pixel or two. Seen at 100 %
  scale on the owner's PC this reads as a small rounded corner; a 4 px radius keeps the steps
  short, where the old round bulb showed them along its whole curve. If the steps prove plainly
  visible, the fallback the owner accepted is one rounded rectangle with the button inside, which
  Windows rounds smoothly without a region. *The amendment below replaces this bullet: the steps
  proved visible, and the fallback is what ships.*
- Nothing reads the screen any more, so `PRIVACY.md` no longer describes it for Windows, and the
  shell no longer spends 15 to 30 ms of a core each second while the window is open.
- The Windows and Mac floating monitors no longer look alike in this respect.

## Amendment (2026-10-16): one rectangle

On the owner's screen the region's 4 px corners looked jagged in 1.0.4: a window region has no
anti-aliasing, and a small radius does not hide the steps. The owner chose the fallback written
into the Consequences above.

- **The bar is one rectangle with no window region.** It keeps its size (330 × 32). Windows (DWM)
  rounds its corners with `DWMWA_WINDOW_CORNER_PREFERENCE` set to `DWMWCP_ROUNDSMALL`, which is
  anti-aliased; the table keeps `DWMWCP_ROUND`. DWM's non-client rendering stays on in both modes,
  which is where the frame's shadow comes from. `SetWindowRgn` and `CreateRoundRectRgn` are no
  longer used.
- **The Optimize button is a rounded square inside the bar.** A 26 × 26 square with 6 px corners
  and a slightly lighter fill, 3 px from the bar's left and top edges; the numbers, the speed
  button and the arrow run to its right over the bar's full height. The page paints the bar's one
  dark colour edge to edge, in either theme.
- **A simpler look, at the owner's request ("simple and professional").** The expand arrow is a
  rounded square like Optimize, 3 px from the right end; Optimize shows only its icon until a check
  runs, when its progress ring appears; with something to fix it becomes a filled blue button
  (white count), so it differs from "issues" (amber count) by more than its colour; the download,
  upload and ping icons share one dim colour, leaving colour to the status dot and Optimize's state.
- The rest of the decision is unchanged: the monitor is solid, nothing reads the screen, and the
  Mac keeps its Glass look.
