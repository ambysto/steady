# ADR-0021: The floating monitor's glass look comes from a blurred capture of what is behind it

- **Status:** Superseded on Windows by [ADR-0026](0026-solid-floating-monitor-on-windows.md) (2026-10-16)
- **Date:** 2026-10-08

## Context

The floating monitor (`web/mini.html`, opened by `app/desktop.py`) should look like frosted glass: what is behind the window shows through, blurred, while its text stays solid. The window is a pywebview window, that is a WinForms form hosting WebView2. On Windows 11 each of the usual ways was tried and none reaches the page:

1. **DWM system backdrop** (`DWMWA_SYSTEMBACKDROP_TYPE`, acrylic or Mica) with `DwmExtendFrameIntoClientArea`. The backdrop turns solid when the window is not active, and a floating monitor is almost never the active window. Behind WebView2 it never showed at all.
2. **`SetWindowCompositionAttribute`** with `ACCENT_ENABLE_ACRYLICBLURBEHIND`, with the form painted black or not painted (`ControlStyles.Opaque`) and WebView2 transparent (`WEBVIEW2_DEFAULT_BACKGROUND_COLOR=0`). The transparent page showed the form's black, not the desktop.
3. **A colour key** (`Form.TransparencyKey`, a layered window). It keys the form's own pixels, not WebView2's, which draws on its own child window through DirectComposition.

`Form.Opacity` does work, but it makes the text as see-through as the background and blurs nothing. It stays as the "Low" and "High" levels.

## Decision

1. **"Glass" is a transparency level, and the default one.** With it the window stays solid (`Form.Opacity` 1), and the page gets a picture of what is behind the window as its background, blurred and tinted with the theme's window colour.
2. **The window leaves screen captures while Glass is on.** `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` makes a capture of the window's area show what is behind it. Where Windows does not support it (before Windows 10 2004) the call fails and Glass falls back to the plain window colour, since the capture would show the window itself.
3. **Capture, blur, hand over.** A thread in the desktop shell captures the window's rectangle with GDI `BitBlt` (a few milliseconds; Pillow's `ImageGrab` copies every screen first, about 100 ms), shrinks it 4×, blurs and saturates it slightly (like Windows' acrylic), encodes it as a small JPEG and passes it to the page as a `data:` URL through `evaluate_js`. The page accepts only a `data:image/jpeg;base64,…` URL. An unchanged picture is not sent again.
4. **Only while it is seen.** The thread works while the window is open and Glass is on: once a second, and at most about 8 times a second while the window is being dragged or resized. Hiding the window or choosing another level stops it.
5. **The pixels stay in the shell.** Nothing is written to disk, logged or sent anywhere. The picture lives in the page's memory until the next one replaces it.

## Consequences

- While Glass is on, the floating monitor does not appear in screenshots, screen recordings or screen sharing. Choosing Off, Low or High brings it back. The Glass button's tooltip says so, and so does `PRIVACY.md`.
- The glass shows the screen as it was up to a second ago: video playing behind the window shows through as a slowly changing blur, which suits a frosted look. While the window is dragged the picture can lag a few frames.
- The cost is one small capture and blur per second (about 15 to 30 ms of one core for the bar or the table), only while the window is open with Glass.
- `PRIVACY.md` states that the app reads the pixels behind this window, only to draw it, and keeps them in memory.
- If pywebview ever hosts WebView2 in a way DWM backdrops reach (visual hosting), the Windows acrylic can replace this without changing the page: it only needs to stop receiving the backdrop.

## Amendment (2026-10-15): the bar's rim in the solid levels

The one-line bar is cut to its thermometer outline with a window region, which has no soft edge.
With Glass the shapes are drawn 1.5 px inside the cut and the sliver between shows the unblurred
capture, so the edge blends into the screen. In Off, Low and High the shapes filled the cut, and the
region's stair-stepped outline showed against the desktop (the owner saw it on the bulb's ring and
the tube's ends).

1. **The solid levels get the same rim.** `grab_behind` leaves the window out of captures until
   that is composed (`SetWindowDisplayAffinity`, then `DwmFlush` twice, since a frame may already
   have started), copies its area and puts it back in captures at once. The page gets that picture unblurred (`setRim`), shows it in the sliver and
   draws the shapes inside it, as with Glass. Without a picture (before Windows 10 2004, or before the
   first one arrives) the shapes fill the cut as before.
2. **Rarely.** Only for the bar: when it is shown, moved (several times a second while it is
   dragged), resized or switched to a solid level, and every 10 s otherwise; an unchanged picture is
   not sent again, except after it was shown again or its look changed. One lock serialises a look
   change and a capture, so a rim capture cannot put the window back in captures after Glass took
   it out, and no picture reaches the page after its look changed.
3. **Consequences.** On its own screen the window never disappears; a screen recording or screen
   sharing can miss the bar for a frame or two each time. What scrolls behind the bar shows in its 1.5 px rim
   up to 10 s late. `PRIVACY.md` says that the solid levels also read the pixels behind the bar.
