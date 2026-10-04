# ADR-0002: macOS-style UI in a pywebview window

- **Status:** Accepted
- **Date:** 2026-10-03
- **Amends:** [ADR-0001](0001-python-stdlib-web-ui.md) (relaxes the "standard library only" policy)

## Context

The user wants a UI that feels like a native macOS application. The tool still runs on Windows and keeps the Python + PowerShell backend (see [UI-DESIGN.md](../UI-DESIGN.md)).

## Decision

1. **Direction A**: macOS design language (System Settings / HIG) for a Windows application. No actual macOS app is built.
2. **Application shell: pywebview** (WebView2), frameless window, custom-drawn title bar.
3. **Mac-style window controls**: 3 "traffic light" buttons (close / minimize / zoom) in the **left corner** of the title bar.
4. **Small libraries with a clear purpose** may be added (pywebview; candidates: pystray, Pillow for the tray icon). The core backend still prefers the standard library.
5. **Inter** font (OFL) and **Lucide/Phosphor** icons (MIT) bundled locally; SF Pro / SF Symbols are not used because of licensing restrictions.
6. The UI is still HTML/CSS/JS served from the local backend ⇒ it can also be opened in a browser when debugging is needed.

## Consequences

- ✅ Feels like a real application: no address bar, its own window, Mica/Acrylic backdrop effects on Windows 11.
- ⚠️ Requires the WebView2 Runtime (included with Windows 11) and `pip install pywebview`.
- ⚠️ Traffic-light buttons on the left differ from Windows habits; window dragging, double-click to maximize, and Snap Layouts have to be handled manually.
- 📌 A `requirements.txt` must be added when coding starts.

## Implementation notes (SIC-29, 2026-10-04)

- pywebview 6.2.1 + WebView2. The script pywebview injects into the page is not blocked by the server's CSP; `window.pywebview.api` is available to the custom-drawn title bar.
- pywebview's frameless window on Windows (`FormBorderStyle = None`) **has no resizable border** and no Snap Layouts ⇒ a bottom-right drag corner that calls `window.resize` was added; maximize/restore is done via the green button or by double-clicking the title bar.
- Mica/Acrylic is not enabled yet: WebView2 content is not transparent, so the effect would not be visible; a solid background per `tokens.css` is kept.
- Windows 11 puts new tray icons in the hidden "^" overflow group by default; users can drag it onto the taskbar if they want it always visible.
- The monitor (Task Scheduler) and the desktop shell are two processes: the shell only reads `server.json` + the token in the page, and calls the API like a browser does.
