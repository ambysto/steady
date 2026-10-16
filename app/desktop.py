"""Desktop shell (ADR-0002): a pywebview window around the local UI and a tray icon.

The monitor (and its server) keeps running as its own scheduled task; this process is only a
viewer. Closing the window hides it to the tray; "Quit" ends this process, never the monitor.

    pythonw -m app.desktop               open the window (and the tray icon)
    pythonw -m app.desktop --minimized   start in the tray only
    python  -m app.desktop --selftest    open, wait for the UI to load, print what it shows, quit

The tray menu also toggles the floating monitor (web/mini.html): a frameless window that stays on
top. By default it is a one-line bar (Optimize on the left, download/upload/ping); expanded it is
the full table with the sweep chart, the apps holding connections and the connection's facts.
Whether it is open, where it sits and which of the two it shows are remembered in mini.json; a new
version opens it once, so an install or upgrade shows it.

Needs `pip install -r requirements.txt` (pywebview, pystray, Pillow). Nothing else in app/
imports this module, so the monitor stays standard-library only (ADR-0001).
"""
from __future__ import annotations

import argparse
import ctypes
import functools
import json
import logging
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import __version__, config

log = logging.getLogger("stableinternet.desktop")

INSTANCE_NAME = "StableInternet.Desktop"
SHOW_EVENT = "Local\\StableInternet.Desktop.Show"
POLL_S = 5
_TOKEN_RE = re.compile(r'<meta name="si-token" content="([A-Za-z0-9_\-]+)"')


# --- talking to the monitor's server (standard library) ------------------------------------------

class ServerLink:
    """Finds the running server (server.json) and calls its API with the page token."""

    def __init__(self, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self._open = opener
        self.port: int | None = None
        self._token: str | None = None

    @property
    def base(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    def discover(self) -> bool:
        try:
            info = json.loads((config.user_dir() / "server.json").read_text(encoding="utf-8"))
            port = int(info["port"])
        except (OSError, ValueError, KeyError, TypeError):
            self.port = None
            return False
        if port != self.port:
            self.port, self._token = port, None
        return True

    def _fetch_token(self) -> str:
        with self._open(f"{self.base}/", timeout=5) as resp:
            m = _TOKEN_RE.search(resp.read().decode("utf-8", "replace"))
        if not m:
            raise ValueError("no token in the page")
        return m.group(1)

    def call(self, method: str, path: str, body: Any = None) -> Any:
        """JSON API call. A 401 means the server restarted with a new token: fetch it and retry once."""
        if not self.base and not self.discover():
            raise ConnectionError("monitor not running")
        for attempt in (0, 1):
            if self._token is None:
                self._token = self._fetch_token()
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(f"{self.base}{path}", data=data, method=method,
                                         headers={"X-Token": self._token, "Content-Type": "application/json"})
            try:
                with self._open(req, timeout=10) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self._token = None
                    self.discover()
                    continue
                raise
        raise ConnectionError("unauthorized")


def tray_state(live: dict | None) -> str:
    """'ok' / 'bad' (an outage is open) / 'unknown' (no monitor)."""
    if not live:
        return "unknown"
    outages = live.get("outages") or {}
    return "bad" if outages.get("router") or outages.get("internet") else "ok"


def state_key(live: dict | None) -> str:
    if not live:
        return "ui.error.offline"
    outages = live.get("outages") or {}
    return ("ui.state.router_down" if outages.get("router") else "ui.state.internet_down" if outages.get("internet")
            else "ui.state.online")


# The tray icon is the app's logo (app.ico, v3: a dark tile with Wi-Fi arcs and a dot in Steady Sky,
# scripts/make_app_icon.py) with the arcs and the dot in the state's colour: Sky when all is well,
# so the icon is then exactly the logo; red during an outage; grey while the monitor is unreachable.
TILE_COLOR = (0x0B, 0x0B, 0x0C)          # the logo's dark tile
TONE_COLOR = (0x66, 0xCC, 0xFF)          # Steady Sky (#62C4F5) as the 48 px frame has it after shrinking
COLORS = {"ok": TONE_COLOR, "bad": (0xFF, 0x45, 0x3A), "unknown": (0x8E, 0x8E, 0x93)}
TRAY_SIZE = 48      # the largest app.ico frame without the caption; Windows takes the smaller sizes from it


def _drawn_icon(state: str, size: int) -> Any:
    """Without app.ico: the logo's tile and arcs, drawn (Pillow)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((1, 1, size - 2, size - 2), radius=size // 5, fill=TILE_COLOR)
    colour = COLORS.get(state, COLORS["unknown"])
    cx, cy, w = size / 2, size * 0.7, max(2, size // 12)
    for r in (size * 0.36, size * 0.24, size * 0.12):
        d.arc((cx - r, cy - r, cx + r, cy + r), start=225, end=315, fill=colour, width=w)
    d.ellipse((cx - w, cy - w, cx + w, cy + w), fill=colour)
    return img


@functools.lru_cache(maxsize=8)
def draw_icon(state: str, ico: Path | None = None, size: int = TRAY_SIZE) -> Any:
    """Tray icon: the logo with its arcs in the state's colour. A pixel's share of Sky over the tile
    (its anti-aliasing included) is kept and only the colour changes; the grey edge stays."""
    from PIL import Image
    colour = COLORS.get(state, COLORS["unknown"])
    try:
        with Image.open(ico or APP_ICON) as source:
            source.size = (size, size)
            img = source.convert("RGBA")
    except (OSError, ValueError, KeyError):
        return _drawn_icon(state, size)
    if colour == TONE_COLOR:
        return img
    span = sum((t - k) ** 2 for t, k in zip(TONE_COLOR, TILE_COLOR))
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            share = sum((c - k) * (t - k) for c, k, t in zip((r, g, b), TILE_COLOR, TONE_COLOR)) / span
            if share > 0.08 and max(r, g, b) - min(r, g, b) > 6:       # Sky, not the grey edge (spread 2)
                share = min(share, 1.0)
                px[x, y] = tuple(round(k + share * (c - k)) for k, c in zip(TILE_COLOR, colour)) + (a,)
    return img


# --- single instance: a second launch brings the first window forward ----------------------------

def _k32() -> Any:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateEventW.restype = k.OpenEventW.restype = ctypes.c_void_p
    k.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    k.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    k.SetEvent.argtypes = k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return k


def signal_running_instance(name: str = SHOW_EVENT) -> bool:
    """Ask the instance already running to show its window. True if there was one."""
    k = _k32()
    handle = k.OpenEventW(0x0002, False, name)   # EVENT_MODIFY_STATE
    if not handle:
        return False
    k.SetEvent(handle)
    k.CloseHandle(handle)
    return True


def watch_show_requests(on_show: Callable[[], None], stop: threading.Event, name: str = SHOW_EVENT) -> None:
    k = _k32()
    handle = k.CreateEventW(None, False, False, name)   # auto-reset
    try:
        while not stop.is_set():
            if k.WaitForSingleObject(handle, 500) == 0:
                on_show()
    finally:
        k.CloseHandle(handle)


# --- the shell -------------------------------------------------------------------------------

# Windows' own frame (not a frameless window with a drawn title bar): resizing from every edge,
# Snap Layouts, Win+arrows and the maximize button behave like in any other app. Only its colour
# is set, to the page background, so the title bar and the page read as one surface.
CAPTION_COLORS = {"light": 0xF7F5F5, "dark": 0x1F1E1E}     # COLORREF (0x00BBGGRR) of --window
DWMWA_CAPTION_COLOR = 35


def system_theme() -> str:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def paint_caption(hwnd: int, theme: str) -> bool:
    """Title bar colour (Windows 11; older versions ignore it and keep their default)."""
    colour = ctypes.c_uint32(CAPTION_COLORS[theme])
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CAPTION_COLOR, ctypes.byref(colour),
                                                      ctypes.sizeof(colour)) == 0


APP_ICON = Path(__file__).resolve().parent / "assets" / "app.ico"    # made by scripts/make_app_icon.py


def set_window_icon(hwnd: int, ico: Path = APP_ICON) -> bool:
    """Title bar and taskbar icon of the window (the tray icon keeps its status colors)."""
    if not ico.is_file():
        return False
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
    ok = False
    for which, metric in ((0, 49), (1, 11)):          # ICON_SMALL / SM_CXSMICON, ICON_BIG / SM_CXICON
        size = user32.GetSystemMetrics(metric)
        handle = user32.LoadImageW(None, str(ico), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(ctypes.c_void_p(hwnd), WM_SETICON, which, ctypes.c_void_p(handle))
            ok = True
    return ok


MIN_SIZE = (420, 560)
SELFTEST_SIZE = (600, 760)

# --- the floating monitor ----------------------------------------------------------------------

MINI_SIZE = (340, 500)          # expanded: the full table
BAR_SIZE = (330, 32)            # collapsed: one line, as long as its numbers and the speed test need
# The bar (ADR-0026) is one rectangle, BAR_SIZE: no window region. Windows (DWM) rounds its corners
# smoothly, and the Optimize button is a rounded square inside it, drawn by web/mini.css.
SIZES = {"full": MINI_SIZE, "bar": BAR_SIZE}
MINI_MARGIN = (16, 64)          # from the right and bottom of the screen (clear of the taskbar)
MINI_VISIBLE = 48               # a saved position must keep this much of the window on a screen
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND, DWMWCP_ROUNDSMALL = 33, 2, 3
DWMWA_NCRENDERING_POLICY, DWMNCRP_ENABLED = 2, 2
CORNER_PREFERENCES = {"full": DWMWCP_ROUND, "bar": DWMWCP_ROUNDSMALL}
BACKGROUNDS = {"light": "#F5F5F7", "dark": "#1E1E1F"}     # --window of tokens.css


def mini_file() -> Path:
    return config.user_dir() / "mini.json"


MINI_KEYS = ("open", "x", "y", "seen_version", "mode")


def load_mini_state() -> dict[str, Any]:
    """{"open", "x", "y", "seen_version", "mode"}; defaults when the file is missing or bad. The
    transparency an earlier version saved is dropped (ADR-0026)."""
    state: dict[str, Any] = {"open": False, "x": None, "y": None, "seen_version": None, "mode": "bar"}
    try:
        saved = json.loads(mini_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if isinstance(saved, dict):
        state["open"] = saved.get("open") is True
        for key in ("x", "y"):
            if isinstance(saved.get(key), int) and not isinstance(saved.get(key), bool):
                state[key] = saved[key]
        if isinstance(saved.get("seen_version"), str):
            state["seen_version"] = saved["seen_version"]
        if saved.get("mode") in SIZES:
            state["mode"] = saved["mode"]
    return state


def open_for_new_version(state: dict[str, Any], version: str = __version__) -> bool:
    """The first start of a version (an install or an upgrade) opens the floating window once."""
    if state.get("seen_version") == version:
        return False
    state.update(seen_version=version, open=True)
    return True


def save_mini_state(state: dict[str, Any]) -> None:
    try:
        mini_file().write_text(json.dumps({k: state.get(k) for k in MINI_KEYS}), encoding="utf-8")
    except OSError as exc:
        log.warning("could not save the floating window state: %r", exc)


def mini_position(state: dict[str, Any], screens: list[Any], size: tuple[int, int] = MINI_SIZE) -> tuple[int, int]:
    """Where the window opens: the saved spot if it is still on a screen (a monitor may have been
    unplugged), else the bottom-right corner of the first screen."""
    x, y = state.get("x"), state.get("y")
    if x is not None and y is not None:
        for scr in screens:
            if (scr.x - size[0] + MINI_VISIBLE <= x <= scr.x + scr.width - MINI_VISIBLE
                    and scr.y <= y <= scr.y + scr.height - MINI_VISIBLE):
                return x, y
    if not screens:
        return 100, 100
    scr = screens[0]
    return (scr.x + scr.width - size[0] - MINI_MARGIN[0], scr.y + scr.height - size[1] - MINI_MARGIN[1])


def screen_of(x: int, y: int, size: tuple[int, int], screens: list[Any]) -> Any:
    """The screen holding the window's centre (the first one when none does)."""
    cx, cy = x + size[0] / 2, y + size[1] / 2
    for scr in screens:
        if scr.x <= cx < scr.x + scr.width and scr.y <= cy < scr.y + scr.height:
            return scr
    return screens[0] if screens else None


def anchor_of(x: int, y: int, size: tuple[int, int], screens: list[Any]) -> tuple[bool, bool]:
    """(right, lower): the screen edges the window is nearer to, which it keeps when it changes size."""
    scr = screen_of(x, y, size, screens)
    if scr is None:
        return False, False
    return x + size[0] / 2 > scr.x + scr.width / 2, y + size[1] / 2 > scr.y + scr.height / 2


def resized_position(x: int, y: int, old: tuple[int, int], new: tuple[int, int], screens: list[Any],
                     anchor: tuple[bool, bool] | None = None) -> tuple[int, int]:
    """Where the window goes when it changes size: it grows away from the screen edge it is near
    (a bar at the bottom expands upward, one on the right expands leftward) and stays on screen.
    Collapsing passes the anchor the expansion used, so the bar comes back to the same spot."""
    scr = screen_of(x, y, old, screens)
    if scr is None:
        return x, y
    right, lower = anchor if anchor is not None else anchor_of(x, y, old, screens)
    nx = x + old[0] - new[0] if right else x
    ny = y + old[1] - new[1] if lower else y
    nx = min(max(nx, scr.x), scr.x + scr.width - new[0])
    ny = min(max(ny, scr.y), scr.y + scr.height - new[1])
    return int(nx), int(ny)


# --- the corners (ADR-0026) --------------------------------------------------------------------

def _dwm_int(hwnd: int, attribute: int, value: int) -> bool:
    data = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attribute, ctypes.byref(data),
                                                      ctypes.sizeof(data)) == 0


def round_corners(hwnd: int, preference: int) -> bool:
    """Let DWM round the window's corners (Windows 11; older versions ignore it and keep square ones).
    Its non-client rendering stays on, which is where the frame's shadow comes from. DWM anti-aliases
    the corners, which a window region cannot."""
    _dwm_int(hwnd, DWMWA_NCRENDERING_POLICY, DWMNCRP_ENABLED)
    return _dwm_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, preference)


def fit_form(form: Any, size: tuple[int, int]) -> bool:
    """Exact window size in logical pixels. pywebview's resize() cannot go below the 100 px a
    Windows form keeps by default, which a one-line bar needs, so the form's minimum goes first."""
    try:
        from System import Action      # pythonnet, loaded by pywebview
        from System.Drawing import Size

        def work() -> None:
            scale = getattr(form, "DeviceDpi", 96) / 96
            form.MinimumSize = Size(1, 1)
            form.Size = Size(round(size[0] * scale), round(size[1] * scale))
        form.Invoke(Action(work))
        return True
    except Exception as exc:
        log.debug("could not size the floating window: %r", exc)
        return False


class MiniBridge:
    """What mini.html may call through window.pywebview.api: hide itself, switch bar and table, ask
    the main window to show a result, nothing else. pywebview exposes every public attribute, so the shell stays private."""

    def __init__(self, shell: "Desktop") -> None:
        self._shell = shell

    def hide_mini(self) -> None:
        self._shell.hide_mini()

    def get_mode(self) -> str:
        return self._shell.mini_state["mode"]

    def set_mode(self, mode: str) -> str:
        return self._shell.set_mini_mode(mode)

    def open_result(self) -> None:
        self._shell.open_check_result()

    def open_speed(self, ask: bool) -> None:
        self._shell.open_speed_result(bool(ask))


class MainBridge:
    """What the main window's page may call: open the floating monitor."""

    def __init__(self, shell: "Desktop") -> None:
        self._shell = shell

    def open_mini(self) -> None:
        self._shell.show_mini()


class Desktop:
    def __init__(self, link: ServerLink | None = None, minimized: bool = False) -> None:
        self.link = link or ServerLink()
        self.minimized = minimized
        self.window: Any = None
        self.mini: Any = None
        self.mini_state = load_mini_state()
        self._anchor: tuple[bool, bool] | None = None      # edges the bar kept when it expanded
        self.icon: Any = None
        self.messages: dict[str, Any] = {}
        self.live: dict | None = None
        self.reachable = False         # last poll reached the monitor
        # Which monitor base each window's page was loaded from (None: the offline page). The shell
        # asks the windows nothing about their URL: pywebview waits 20 s for a page that never loads.
        self._window_base: str | None = None
        self._mini_base: str | None = None
        self._caption: str | None = None
        self.quitting = False
        self.stop = threading.Event()
        self.selftest_result: dict[str, Any] = {}

    # text for the tray menu comes from the same catalogs as the UI
    def text(self, key: str) -> str:
        value = self.messages.get(key)
        return value if isinstance(value, str) else key.rsplit(".", 1)[-1]

    def title(self) -> str:
        return f"{self.text('app.name')} · {self.text(state_key(self.live))}"

    # -- window ---------------------------------------------------------------------------
    def show(self) -> None:
        if self.window is not None:
            self.window.show()
            self.window.restore()

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()

    def paint_title_bar(self) -> None:
        """Match the title bar to the page; re-checked every poll so it follows a theme switch."""
        theme = system_theme()
        if self.window is None or theme == self._caption:
            return
        try:
            hwnd = int(self.window.native.Handle.ToInt64())
        except Exception:
            return              # not shown yet
        if self._caption is None:
            set_window_icon(hwnd)
        if paint_caption(hwnd, theme):
            self._caption = theme

    def _on_closing(self) -> bool:
        if self.quitting:
            return True
        self.hide()            # the window's own close (Alt+F4, taskbar) also just hides it
        return False

    def quit(self) -> None:
        self.quitting = True
        self.stop.set()
        if self.mini is not None:
            save_mini_state(self.mini_state)      # reopens where it was, if it was open
        if self.icon is not None:
            self.icon.stop()
        for window in (self.mini, self.window):
            if window is not None:
                window.destroy()

    # -- floating monitor -----------------------------------------------------------------
    def mini_url(self) -> str | None:
        return f"{self.link.base}/mini.html" if self.link.base else None

    def _mini_active(self, active: bool) -> None:
        """Pause the page's polling and drawing while hidden (it would keep running otherwise)."""
        flag = "true" if active else "false"
        try:
            self.mini.evaluate_js(f"window.steadyMini && window.steadyMini.setActive({flag})")
        except Exception as exc:
            log.debug("floating window not ready: %r", exc)

    def show_mini(self) -> None:
        if self.mini is None:
            return
        self.mini.show()
        self._fit_mini()
        self._mini_active(True)
        self.mini_state["open"] = True
        save_mini_state(self.mini_state)

    def hide_mini(self) -> None:
        if self.mini is None:
            return
        self.mini.hide()
        self._mini_active(False)
        self.mini_state["open"] = False
        save_mini_state(self.mini_state)

    def toggle_mini(self) -> None:
        if self.mini_state["open"]:
            self.hide_mini()
        else:
            self.show_mini()

    def _mini_hwnd(self) -> int | None:
        try:
            return int(self.mini.native.Handle.ToInt64())
        except Exception:
            return None                 # not created yet

    def set_mini_mode(self, mode: str, screens: list[Any] | None = None) -> str:
        """One-line bar or full table: resize the window, growing away from the nearest screen edge."""
        old = self.mini_state["mode"]
        if mode not in SIZES or mode == old:
            return old
        self.mini_state["mode"] = mode
        if self.mini is not None:
            if screens is None:
                import webview
                screens = webview.screens
            if mode == "full":
                self._anchor = anchor_of(self.mini.x, self.mini.y, SIZES[old], screens)
            x, y = resized_position(self.mini.x, self.mini.y, SIZES[old], SIZES[mode], screens, self._anchor)
            self._fit_mini()
            self.mini.move(x, y)
            self.mini_state.update(x=x, y=y)
        save_mini_state(self.mini_state)
        return mode

    def open_check_result(self) -> None:
        """"Fix N" on the bar: the main window shows what the check found and what Fix would turn on
        (ADR-0020: the user sees the list and the Wi-Fi note before any UAC prompt)."""
        self.show()
        if self.window is not None:
            try:
                self.window.evaluate_js("window.steadyApp && window.steadyApp.openResult()")
            except Exception as exc:
                log.debug("main window not ready: %r", exc)

    def open_speed_result(self, ask: bool) -> None:
        """The bar's speed test: the main window shows the result, or asks the question first (the bar
        has no room for it, and the user reads what the test uses before it moves any data)."""
        self.show()
        if self.window is not None:
            try:
                self.window.evaluate_js(f"window.steadyApp && window.steadyApp.openSpeed({'true' if ask else 'false'})")
            except Exception as exc:
                log.debug("main window not ready: %r", exc)

    def _fit_mini(self) -> None:
        mode = self.mini_state["mode"]
        try:
            fit_form(self.mini.native, SIZES[mode])
        except AttributeError:
            return              # not created yet
        hwnd = self._mini_hwnd()
        if hwnd is not None:
            round_corners(hwnd, CORNER_PREFERENCES[mode])       # small for the bar, the usual ones for the table

    def _on_mini_moved(self, x: int, y: int) -> None:
        self.mini_state.update(x=int(x), y=int(y))       # saved on hide and quit

    def _off_ui_thread(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, name="desktop-ui-off", daemon=True).start()

    def _on_mini_closing(self) -> bool:
        if self.quitting:
            return True
        # pywebview runs closing handlers on the UI thread, where evaluate_js (inside hide_mini) waits
        # for a callback that only that thread can deliver: it would freeze every window and the tray.
        self.mini_state["open"] = False           # at once, so the tray's toggle reads it closed
        self._off_ui_thread(self.hide_mini)       # Alt+F4 hides it like the close button
        return False

    def _on_mini_loaded(self) -> None:
        self._mini_active(bool(self.mini_state["open"]))

    # -- tray -----------------------------------------------------------------------------
    def _reconnect(self) -> None:
        try:
            self.link.call("POST", "/api/actions/reconnect", {})
        except Exception as exc:
            log.warning("reconnect failed: %r", exc)

    def build_tray(self) -> Any:
        import pystray
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self.title(), None, enabled=False),
            pystray.MenuItem(lambda _: self.text("ui.tray.open"), lambda: self.show(), default=True),
            pystray.MenuItem(lambda _: self.text("ui.tray.mini"), lambda: self.toggle_mini(),
                             checked=lambda _: bool(self.mini_state["open"])),
            pystray.MenuItem(lambda _: self.text("ui.action.reconnect"), lambda: self._reconnect()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: self.text("ui.tray.quit"), lambda: self.quit()),
        )
        self.icon = pystray.Icon(INSTANCE_NAME, draw_icon("unknown"), self.title(), menu)
        return self.icon

    def refresh(self) -> None:
        """One poll: catalog (once), live state -> tray icon, tooltip, window URL."""
        try:
            self.link.discover()
            if not self.messages:
                self.messages = self.link.call("GET", "/api/i18n").get("messages", {})
            self.live = self.link.call("GET", "/api/live?window=1")
        except Exception as exc:
            log.debug("monitor not reachable: %r", exc)
            self.live = None
        if self.icon is not None:
            self.icon.icon = draw_icon(tray_state(self.live))
            self.icon.title = self.title()
            self.icon.update_menu()
        self.paint_title_bar()
        self.reachable = self.live is not None
        # Load the UI only from a monitor that answered: server.json can name a port nothing listens
        # on yet (monitor just restarted), and the browser's error page would never reload by itself.
        # A window is loaded only when it shows the offline page or a monitor on another port: Edge
        # does not navigate to the URL it already has, and pywebview would then wait 20 s for a load
        # that never happens. A monitor back on the same port needs nothing: the page reloads itself
        # when its token is refused (web/js/api.js).
        if self.reachable and self.link.base:
            if self.window is not None and self._window_base != self.link.base:
                try:
                    self.window.load_url(f"{self.link.base}/")
                    self._window_base = self.link.base
                except Exception as exc:
                    log.warning("main window could not load the monitor: %r", exc)
            if self.mini is not None and self._mini_base != self.link.base:
                try:
                    self.mini.load_url(self.mini_url())
                    self._mini_base = self.link.base
                except Exception as exc:
                    log.warning("floating window could not load the monitor: %r", exc)

    def _poll(self) -> None:
        while not self.stop.is_set():
            try:
                self.refresh()
            except Exception:
                log.exception("poll failed")      # one bad poll must not stop the tray and the windows
            self.stop.wait(POLL_S)

    # -- run ------------------------------------------------------------------------------
    def run(self, selftest: bool = False, screenshot: str | None = None) -> int:
        import webview
        self.refresh()
        url = f"{self.link.base}/" if self.reachable else None
        html = None if url else _OFFLINE_HTML.replace("{text}", self.text("ui.error.offline"))
        self._window_base = self.link.base if url else None
        self.window = webview.create_window(self.text("app.name") if self.messages else "Ambysto Steady",
                                            url=url, html=html, width=1100, height=740, min_size=MIN_SIZE,
                                            hidden=self.minimized and not selftest, background_color="#F5F5F7",
                                            js_api=MainBridge(self))
        self.window.events.closing += self._on_closing
        self.window.events.shown += self.paint_title_bar
        result: dict[str, Any] = {}
        if selftest:
            return self._run_selftest(webview, result, screenshot)
        if open_for_new_version(self.mini_state):
            save_mini_state(self.mini_state)
        self.create_mini(webview)
        self.build_tray().run_detached()
        threading.Thread(target=self._poll, name="desktop-poll", daemon=True).start()
        threading.Thread(target=watch_show_requests, args=(self.show, self.stop), name="desktop-show", daemon=True).start()
        webview.start(private_mode=False, storage_path=str(config.user_dir() / "webview"))
        self.quit()
        return 0

    def create_mini(self, webview: Any) -> None:
        theme = system_theme()
        size = SIZES[self.mini_state["mode"]]
        x, y = mini_position(self.mini_state, webview.screens, size)
        url = self.mini_url() if self.reachable else None
        self._mini_base = self.link.base if url else None
        html = None if url else _OFFLINE_HTML.replace("{text}", self.text("ui.error.offline")).replace(
            "#f5f5f7", BACKGROUNDS[theme])
        self.mini = webview.create_window(
            self.text("ui.tray.mini") if self.messages else "Ambysto Steady", url=url, html=html,
            width=size[0], height=size[1], x=x, y=y, resizable=False, frameless=True, easy_drag=False,
            on_top=True, hidden=not self.mini_state["open"], background_color=BACKGROUNDS[theme],
            js_api=MiniBridge(self))
        self.mini.events.closing += self._on_mini_closing
        self.mini.events.moved += self._on_mini_moved
        self.mini.events.shown += self._fit_mini
        self.mini.events.loaded += self._on_mini_loaded

    def _run_selftest(self, webview: Any, result: dict, screenshot: str | None) -> int:
        def probe() -> None:
            try:
                for _ in range(60):     # wait for the app to render a screen
                    time.sleep(0.5)
                    ready = self.window.evaluate_js("document.querySelector('.screen.active h1')?.textContent || ''")
                    if ready:
                        break
                result.update(
                    url=self.window.get_current_url(), heading=ready,
                    nav=self.window.evaluate_js("[...document.querySelectorAll('.nav-item span[data-t]')].map(n => n.textContent).join('|')"))
                self.paint_title_bar()
                result["caption"] = self._caption
                self.window.maximize()
                time.sleep(1)
                result["maximized_width"] = self.window.width
                result["maximized_page"] = self.window.evaluate_js("window.innerWidth")
                self.window.restore()
                time.sleep(1)
                result["restored_width"] = self.window.width
                # Resizing must re-lay the page out, not just change the frame around it.
                self.window.resize(*SELFTEST_SIZE)
                time.sleep(1.5)
                result["resized"] = {"window": [self.window.width, self.window.height],
                                     "page": self.window.evaluate_js("[window.innerWidth, window.innerHeight]")}
                if screenshot:      # what the frameless window looks like on screen
                    time.sleep(1.5)
                    from PIL import ImageGrab
                    x, y, w, h = self.window.x, self.window.y, self.window.width, self.window.height
                    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(screenshot)
                    result["screenshot"] = screenshot
            except Exception as exc:
                result["error"] = repr(exc)
            finally:
                self.quitting = True
                self.window.destroy()
        webview.start(probe, private_mode=True)
        self.selftest_result = result
        print(json.dumps(result, ensure_ascii=False))
        resized = result.get("resized") or {}
        relaid = bool(resized.get("page")) and abs(resized["page"][0] - resized["window"][0]) <= 40   # the native frame takes a few px
        return 0 if result.get("heading") and result.get("caption") and relaid and not result.get("error") else 1


_OFFLINE_HTML = """<!doctype html><meta charset="utf-8"><body style="font:13px system-ui;display:grid;place-items:center;
height:100vh;margin:0;background:#f5f5f7;color:#444"><p class="pywebview-drag-region">{text}</p></body>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.desktop", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minimized", action="store_true", help="start in the tray, window hidden")
    ap.add_argument("--selftest", action="store_true", help="open the window, check the UI loads, quit")
    ap.add_argument("--screenshot", help="with --selftest: save a picture of the window here")
    ap.add_argument("--result-file", help="with --selftest: also write the result here (the packaged exe has no console)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not args.selftest:
        from .singleton import SingleInstance
        guard = SingleInstance(INSTANCE_NAME)
        if not guard.acquire():
            signal_running_instance()     # show the window that is already open
            return 0
    shell = Desktop(minimized=args.minimized)
    code = shell.run(selftest=args.selftest, screenshot=args.screenshot)
    if args.selftest and args.result_file:
        with open(args.result_file, "w", encoding="utf-8") as fh:
            json.dump(shell.selftest_result, fh, ensure_ascii=False)
    return code


if __name__ == "__main__":
    sys.exit(main())
