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

The looks also cost: about 215 of the 1003 lines of `app/desktop.py`, about 70 lines of the page
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
   its rectangle and Windows' own rounded corners.
3. **The Mac is unchanged.** Its Glass (ADR-0022) has no such cost there, and the shared catalog
   keys it uses (`ui.mini.transparency*`) stay.

## Consequences

- The floating monitor shows in screenshots, recordings and screen sharing, in every state.
- A window region has no soft edge: each rounded corner steps by a pixel or two. Seen at 100 %
  scale on the owner's PC this reads as a small rounded corner; a 4 px radius keeps the steps
  short, where the old round bulb showed them along its whole curve. If the steps prove plainly
  visible, the fallback the owner accepted is one rounded rectangle with the button inside, which
  Windows rounds smoothly without a region.
- Nothing reads the screen any more, so `PRIVACY.md` no longer describes it for Windows, and the
  shell no longer spends 15 to 30 ms of a core each second while the window is open.
- The Windows and Mac floating monitors no longer look alike in this respect.
