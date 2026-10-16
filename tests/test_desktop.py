import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import urllib.error

from app import desktop

PAGE = '<html><head><meta name="si-token" content="{}"></head></html>'


class FakeServer:
    """Stands in for urllib.request.urlopen: serves the page (with a token) and the API."""

    def __init__(self, token="tok1"):
        self.token, self.calls = token, []

    def __call__(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        self.calls.append(url)
        if url.endswith("/"):
            return io.BytesIO(PAGE.format(self.token).encode())
        if req.headers.get("X-token") != self.token:
            raise urllib.error.HTTPError(url, 401, "bad token", {}, None)
        return io.BytesIO(json.dumps({"ok": True, "path": url.split("47613")[1]}).encode())


class ServerLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["STABLEINTERNET_USERDIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_USERDIR", None)

    def write_runtime(self, port=47613):
        with open(os.path.join(self.tmp.name, "server.json"), "w", encoding="utf-8") as fh:
            json.dump({"port": port}, fh)

    def test_no_server_json_means_not_running(self):
        link = desktop.ServerLink(FakeServer())
        self.assertFalse(link.discover())
        with self.assertRaises(ConnectionError):
            link.call("GET", "/api/live")

    def test_token_from_the_page_then_api(self):
        self.write_runtime()
        server = FakeServer()
        link = desktop.ServerLink(server)
        self.assertEqual(link.call("GET", "/api/live?window=1")["path"], "/api/live?window=1")
        self.assertEqual(server.calls, ["http://127.0.0.1:47613/", "http://127.0.0.1:47613/api/live?window=1"])
        link.call("GET", "/api/state")
        self.assertEqual(len(server.calls), 3)           # token reused

    def test_server_restart_with_a_new_token_is_handled_once(self):
        self.write_runtime()
        server = FakeServer()
        link = desktop.ServerLink(server)
        link.call("GET", "/api/state")
        server.token = "tok2"                             # monitor restarted
        self.assertTrue(link.call("GET", "/api/state")["ok"])
        self.assertEqual(server.calls.count("http://127.0.0.1:47613/"), 2)   # fetched the page again, once

    def test_port_change_drops_the_token(self):
        self.write_runtime(47613)
        link = desktop.ServerLink(FakeServer())
        link.call("GET", "/api/state")
        self.write_runtime(47614)
        link.discover()
        self.assertEqual((link.port, link._token), (47614, None))


class StateTests(unittest.TestCase):
    def test_tray_state(self):
        self.assertEqual(desktop.tray_state(None), "unknown")
        self.assertEqual(desktop.tray_state({"outages": {}}), "ok")
        self.assertEqual(desktop.tray_state({"outages": {"internet": 1.0}}), "bad")

    def test_state_key_exists_in_the_catalog(self):
        from app import i18n
        english = i18n.messages("en")
        for live in (None, {"outages": {}}, {"outages": {"router": 1}}, {"outages": {"internet": 1}}):
            self.assertIn(desktop.state_key(live), english)
        for key in ("app.name", "ui.tray.open", "ui.tray.quit", "ui.action.reconnect"):
            self.assertIn(key, english)

    def test_tray_icon_is_the_logo_with_arcs_in_the_state_colour(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        with Image.open(desktop.APP_ICON) as ico:
            ico.size = (desktop.TRAY_SIZE, desktop.TRAY_SIZE)
            logo = ico.convert("RGBA")
        ok, bad, unknown = (desktop.draw_icon(s) for s in ("ok", "bad", "unknown"))
        self.assertEqual(ok.size, (desktop.TRAY_SIZE, desktop.TRAY_SIZE))
        self.assertEqual(list(ok.getdata()), list(logo.getdata()))         # all well: exactly the logo
        self.assertEqual(ok.getpixel((0, 0))[3], 0)                         # transparent corner
        arc = max(((x, y) for x in range(logo.width) for y in range(logo.height)),
                  key=lambda p: logo.getpixel(p)[2] - logo.getpixel(p)[0])   # the most Sky pixel
        self.assertEqual(logo.getpixel(arc)[:3], desktop.TONE_COLOR)
        self.assertEqual(bad.getpixel(arc)[:3], desktop.COLORS["bad"])
        self.assertEqual(unknown.getpixel(arc)[:3], desktop.COLORS["unknown"])
        tile = (logo.width // 4, logo.height // 4)                          # the dark tile stays dark
        self.assertEqual(bad.getpixel(tile), logo.getpixel(tile))

    def test_tray_icon_is_drawn_without_the_icon_file(self):
        try:
            img = desktop.draw_icon("bad", desktop.APP_ICON.with_name("missing.ico"))
        except ImportError:
            self.skipTest("Pillow not installed")
        self.assertEqual(img.size, (desktop.TRAY_SIZE, desktop.TRAY_SIZE))
        self.assertIn(desktop.COLORS["bad"], {c[:3] for c in img.getdata()})
        self.assertEqual(img.getpixel((0, 0))[3], 0)

    def test_app_icon_has_every_size_the_shell_asks_for(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        self.assertTrue(desktop.APP_ICON.is_file(), "run scripts/make_app_icon.py")
        with Image.open(desktop.APP_ICON) as ico:
            self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"]))

    def test_window_icon_is_skipped_when_the_file_is_missing(self):
        self.assertFalse(desktop.set_window_icon(0, desktop.APP_ICON.with_name("missing.ico")))


class FakeWindow:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append(name)


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.shell = desktop.Desktop(desktop.ServerLink(FakeServer()))
        self.shell.window = FakeWindow()

    def test_closing_the_window_hides_it_and_keeps_running(self):
        self.assertFalse(self.shell._on_closing())
        self.assertEqual(self.shell.window.calls, ["hide"])
        self.assertFalse(self.shell.stop.is_set())

    def test_quit_really_closes(self):
        self.shell.quit()
        self.assertTrue(self.shell._on_closing())
        self.assertTrue(self.shell.stop.is_set())
        self.assertIn("destroy", self.shell.window.calls)

    def test_title_bar_follows_the_system_theme(self):
        painted = []
        self.shell.window.native = type("Form", (), {"Handle": type("H", (), {"ToInt64": lambda self: 4242})()})()
        theme = ["light"]
        with mock.patch.object(desktop, "system_theme", lambda: theme[0]), \
                mock.patch.object(desktop, "paint_caption", lambda hwnd, t: painted.append((hwnd, t)) or True):
            self.shell.paint_title_bar()
            self.shell.paint_title_bar()                    # unchanged: not painted again
            theme[0] = "dark"
            self.shell.paint_title_bar()
        self.assertEqual(painted, [(4242, "light"), (4242, "dark")])

    def test_title_bar_waits_until_the_window_exists(self):
        with mock.patch.object(desktop, "paint_caption") as paint:
            self.shell.window = type("Hidden", (), {})()    # no .native yet
            self.shell.paint_title_bar()
        paint.assert_not_called()

    def test_caption_colours_match_the_page_background(self):
        tokens = (Path(__file__).resolve().parents[1] / "web" / "tokens.css").read_text(encoding="utf-8")
        for theme, colour in desktop.CAPTION_COLORS.items():
            r, g, b = colour & 0xFF, (colour >> 8) & 0xFF, (colour >> 16) & 0xFF
            self.assertIn(f"--window: #{r:02x}{g:02x}{b:02x};", tokens, theme)

    def test_menu_text_falls_back_before_the_catalog_arrives(self):
        self.assertEqual(self.shell.text("ui.tray.open"), "open")
        self.shell.messages = {"ui.tray.open": "Mở Ambysto Steady", "app.name": "Ambysto Steady",
                               "ui.state.online": "Đã kết nối"}
        self.shell.live = {"outages": {}}
        self.assertEqual(self.shell.title(), "Ambysto Steady · Đã kết nối")


class PollingTests(unittest.TestCase):
    """What refresh() asks of the windows when the monitor goes away and comes back."""

    BASE = "http://127.0.0.1:47613"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["STABLEINTERNET_USERDIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_USERDIR", None)
        self.port = 47613
        self.up = True
        self.shell = desktop.Desktop(desktop.ServerLink(self.serve))
        self.shell.window, self.shell.mini = FakeWindow(), FakeWindow()
        self.shell.messages = {"app.name": "Ambysto Steady"}       # the catalog is fetched once, not the subject here

    def serve(self, req, timeout=None):
        if not self.up:
            raise urllib.error.URLError("refused")
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/"):
            return io.BytesIO(PAGE.format("tok").encode())
        return io.BytesIO(json.dumps({"outages": {}}).encode())

    def write_runtime(self):
        with open(os.path.join(self.tmp.name, "server.json"), "w", encoding="utf-8") as fh:
            json.dump({"port": self.port}, fh)

    def loads(self):
        return [name for name in self.shell.window.calls + self.shell.mini.calls if name == "load_url"]

    def test_same_port_after_a_restart_loads_nothing(self):
        self.write_runtime()
        self.shell._window_base = self.shell._mini_base = self.BASE      # both pages came from this monitor
        self.shell.refresh()
        self.up = False
        self.shell.refresh()
        self.assertFalse(self.shell.reachable)
        self.up = True
        self.shell.refresh()
        self.assertTrue(self.shell.reachable)
        self.assertEqual(self.shell.window.calls + self.shell.mini.calls, [])     # no load_url, no get_current_url

    def test_the_offline_page_is_replaced_once_the_monitor_answers(self):
        self.write_runtime()
        self.up = False
        self.shell.refresh()
        self.assertEqual(self.loads(), [])
        self.up = True
        self.shell.refresh()
        self.shell.refresh()
        self.assertEqual(self.shell.window.calls, ["load_url"])
        self.assertEqual(self.shell.mini.calls, ["load_url"])
        self.assertEqual((self.shell._window_base, self.shell._mini_base), (self.BASE, self.BASE))

    def test_a_monitor_on_another_port_is_loaded(self):
        self.write_runtime()
        self.shell._window_base = self.shell._mini_base = self.BASE
        self.shell.refresh()
        self.port = 47614
        self.write_runtime()
        self.shell.refresh()
        self.assertEqual(self.loads(), ["load_url", "load_url"])
        self.assertEqual(self.shell._window_base, "http://127.0.0.1:47614")

    def test_a_failed_load_is_tried_again_and_does_not_stop_the_other_window(self):
        self.write_runtime()
        failed = []

        def broken(*args):
            failed.append(args)
            raise RuntimeError("Main window failed to start")
        self.shell.window.load_url = broken
        with self.assertLogs("stableinternet.desktop", "WARNING"):
            self.shell.refresh()                              # does not raise
        self.assertEqual(self.shell.mini.calls, ["load_url"])
        self.assertIsNone(self.shell._window_base)
        with self.assertLogs("stableinternet.desktop", "WARNING"):
            self.shell.refresh()
        self.assertEqual(len(failed), 2)

    def test_polling_goes_on_after_a_failed_poll(self):
        rounds = []

        def refresh():
            rounds.append(1)
            if len(rounds) == 1:
                raise RuntimeError("boom")

        def wait(seconds):                  # stop after the second round
            if len(rounds) == 2:
                self.shell.stop.set()
        with mock.patch.object(self.shell, "refresh", refresh), mock.patch.object(self.shell.stop, "wait", wait), \
                self.assertLogs("stableinternet.desktop", "ERROR"):
            self.shell._poll()
        self.assertEqual(len(rounds), 2)


class Screen:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


class MiniWindowTests(unittest.TestCase):
    """The floating monitor: remembered state, where it opens, what its page may call."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["STABLEINTERNET_USERDIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_USERDIR", None)
        self.shell = desktop.Desktop(desktop.ServerLink(FakeServer()))
        self.shell.mini = FakeWindow()

    def saved(self):
        return json.loads(Path(self.tmp.name, "mini.json").read_text(encoding="utf-8"))

    DEFAULTS = {"open": False, "x": None, "y": None, "seen_version": None, "mode": "bar"}

    def test_state_defaults_and_bad_files(self):
        self.assertEqual(desktop.load_mini_state(), self.DEFAULTS)
        for text in ("not json", "[1]",
                     '{"open": "yes", "x": true, "y": 1.5, "transparency": "frosted", "seen_version": 7, "mode": "huge"}'):
            Path(self.tmp.name, "mini.json").write_text(text, encoding="utf-8")
            self.assertEqual(desktop.load_mini_state(), self.DEFAULTS, text)
        saved = {"open": True, "x": -300, "y": 40, "seen_version": "0.8.0", "mode": "full"}
        desktop.save_mini_state(saved)
        self.assertEqual(desktop.load_mini_state(), saved)

    def test_an_earlier_versions_transparency_is_dropped(self):
        Path(self.tmp.name, "mini.json").write_text(
            '{"open": true, "x": 10, "y": 20, "transparency": "glass", "seen_version": "1.0.3", "mode": "bar"}',
            encoding="utf-8")
        state = desktop.load_mini_state()
        self.assertEqual(state, {"open": True, "x": 10, "y": 20, "seen_version": "1.0.3", "mode": "bar"})
        desktop.save_mini_state(state)
        self.assertNotIn("transparency", self.saved())

    def test_a_new_version_opens_it_once(self):
        state = desktop.load_mini_state()                  # first install: no mini.json yet
        self.assertTrue(desktop.open_for_new_version(state, "0.8.0"))
        self.assertEqual((state["open"], state["seen_version"]), (True, "0.8.0"))
        state["open"] = False                              # the user closed it
        self.assertFalse(desktop.open_for_new_version(state, "0.8.0"))
        self.assertFalse(state["open"])
        self.assertTrue(desktop.open_for_new_version(state, "0.8.1"))    # upgrade
        self.assertTrue(state["open"])

    def test_opens_bottom_right_or_where_it_was_left(self):
        screens = [Screen(0, 0, 1920, 1080), Screen(-1280, 0, 1280, 1024)]
        w, h = desktop.MINI_SIZE
        default = (1920 - w - desktop.MINI_MARGIN[0], 1080 - h - desktop.MINI_MARGIN[1])
        self.assertEqual(desktop.mini_position({"x": None, "y": None}, screens), default)
        self.assertEqual(desktop.mini_position({"x": -900, "y": 300}, screens), (-900, 300))   # second monitor
        self.assertEqual(desktop.mini_position({"x": 5000, "y": 300}, screens), default)       # monitor unplugged
        self.assertEqual(desktop.mini_position({"x": 100, "y": 1070}, screens), default)       # only a sliver visible
        self.assertEqual(desktop.mini_position({"x": 1, "y": 1}, []), (100, 100))

    def test_toggle_shows_and_hides_and_remembers(self):
        self.shell.toggle_mini()
        self.assertEqual(self.shell.mini.calls[:1], ["show"])
        self.assertTrue(self.saved()["open"])
        self.shell._on_mini_moved(12, 34)
        self.shell.toggle_mini()
        self.assertIn("hide", self.shell.mini.calls)
        self.assertEqual({k: self.saved()[k] for k in ("open", "x", "y")}, {"open": False, "x": 12, "y": 34})

    def test_hiding_pauses_the_page(self):
        scripts = []
        self.shell.mini.evaluate_js = scripts.append
        self.shell.show_mini()
        self.shell.hide_mini()
        self.assertEqual(scripts, ["window.steadyMini && window.steadyMini.setActive(true)",
                                   "window.steadyMini && window.steadyMini.setActive(false)"])

    def test_closing_hides_unless_quitting(self):
        self.shell.mini_state["open"] = True
        with mock.patch.object(self.shell, "_off_ui_thread", lambda fn: fn()):     # run inline
            self.assertFalse(self.shell._on_mini_closing())
        self.assertFalse(self.saved()["open"])
        self.shell.mini_state["open"] = True
        self.shell.quit()
        self.assertTrue(self.shell._on_mini_closing())
        self.assertTrue(self.saved()["open"])           # quitting keeps it open for the next start
        self.assertIn("destroy", self.shell.mini.calls)

    def test_closing_does_not_wait_on_the_page_from_the_ui_thread(self):
        # pywebview runs closing handlers on the UI thread; evaluate_js there never returns.
        scripts, deferred = [], []
        self.shell.mini.evaluate_js = scripts.append
        self.shell.mini_state["open"] = True
        with mock.patch.object(self.shell, "_off_ui_thread", deferred.append):
            self.assertFalse(self.shell._on_mini_closing())
        self.assertEqual(scripts, [])
        self.assertEqual(deferred, [self.shell.hide_mini])
        self.assertFalse(self.shell.mini_state["open"])     # read as closed at once (the tray's toggle)
        self.assertNotIn("hide", self.shell.mini.calls)     # the window itself is hidden by the other thread

    def test_off_ui_thread_runs_the_function_on_another_thread(self):
        ran = []
        done = threading.Event()
        self.shell._off_ui_thread(lambda: (ran.append(threading.current_thread()), done.set()))
        self.assertTrue(done.wait(5))
        self.assertIsNot(ran[0], threading.current_thread())
        self.assertTrue(ran[0].daemon)

    def test_the_bar_is_one_rectangle_that_windows_rounds(self):
        set_attributes = []
        self.shell.mini.native = mock.Mock()
        self.shell.mini.native.Handle.ToInt64.return_value = 4242
        with mock.patch.object(desktop, "fit_form", lambda form, size: True), \
                mock.patch.object(desktop, "_dwm_int", lambda hwnd, attribute, value:
                                  set_attributes.append((hwnd, attribute, value)) or True):
            self.shell.mini_state["mode"] = "bar"
            self.shell._fit_mini()
            self.shell.mini_state["mode"] = "full"
            self.shell._fit_mini()
        frame = (desktop.DWMWA_NCRENDERING_POLICY, desktop.DWMNCRP_ENABLED)       # the shadow stays
        self.assertEqual(set_attributes, [
            (4242, *frame), (4242, desktop.DWMWA_WINDOW_CORNER_PREFERENCE, desktop.DWMWCP_ROUNDSMALL),
            (4242, *frame), (4242, desktop.DWMWA_WINDOW_CORNER_PREFERENCE, desktop.DWMWCP_ROUND)])
        self.assertEqual((desktop.DWMWCP_ROUND, desktop.DWMWCP_ROUNDSMALL), (2, 3))
        for name in ("bar_parts", "shape_window", "BUTTON", "STRIP", "CORNER"):
            self.assertFalse(hasattr(desktop, name), name)          # no window region any more
        self.assertNotIn("SetWindowRgn", Path(desktop.__file__).read_text(encoding="utf-8"))

    def test_bar_grows_away_from_the_screen_edge_it_is_near(self):
        screens = [Screen(0, 0, 1920, 1080)]
        bar, full = desktop.BAR_SIZE, desktop.MINI_SIZE
        # bottom-right bar: the table opens up and to the left, its corner where the bar's was
        x, y = 1920 - bar[0] - 16, 1080 - bar[1] - 64
        self.assertEqual(desktop.resized_position(x, y, bar, full, screens), (x + bar[0] - full[0], y + bar[1] - full[1]))
        # top-left bar: it opens down and to the right
        self.assertEqual(desktop.resized_position(20, 20, bar, full, screens), (20, 20))
        # and back: the bar returns to the corner the table came from
        fx, fy = desktop.resized_position(x, y, bar, full, screens)
        self.assertEqual(desktop.resized_position(fx, fy, full, bar, screens), (x, y))
        # a bar in the upper half opens downward; collapsing brings it back up, not to the table's bottom
        top = desktop.anchor_of(1500, 400, bar, screens)
        tx, ty = desktop.resized_position(1500, 400, bar, full, screens, top)
        self.assertEqual((tx, ty), (1500 + bar[0] - full[0], 400))      # wider: grows to the left (right half)
        self.assertEqual(desktop.resized_position(tx, ty, full, bar, screens, top), (1500, 400))
        # never off screen
        self.assertEqual(desktop.resized_position(0, 1050, bar, full, screens)[1], 1080 - full[1])
        self.assertEqual(desktop.resized_position(5, 5, bar, full, []), (5, 5))

    def test_switching_between_bar_and_table(self):
        moves = []
        self.shell.mini.x, self.shell.mini.y = 1300, 970
        self.shell.mini.move = lambda x, y: moves.append((x, y))
        screens = [Screen(0, 0, 1920, 1080)]
        with mock.patch.object(desktop, "fit_form", lambda form, size: True):
            self.assertEqual(self.shell.set_mini_mode("full", screens), "full")
            self.assertEqual(self.shell.set_mini_mode("full", screens), "full")      # already: nothing moves
            self.assertEqual(self.shell.set_mini_mode("wide", screens), "full")      # unknown: ignored
        self.assertEqual(len(moves), 1)
        self.assertEqual(self.saved()["mode"], "full")
        self.assertEqual((self.saved()["x"], self.saved()["y"]), moves[0])

    def test_fix_on_the_bar_opens_the_result_in_the_main_window(self):
        self.shell.window = FakeWindow()
        scripts = []
        self.shell.window.evaluate_js = scripts.append
        desktop.MiniBridge(self.shell).open_result()
        self.assertEqual(scripts, ["window.steadyApp && window.steadyApp.openResult()"])
        self.assertIn("show", self.shell.window.calls)

    def test_the_speed_button_on_the_bar_opens_the_overview_and_asks_only_when_told(self):
        self.shell.window = FakeWindow()
        scripts = []
        self.shell.window.evaluate_js = scripts.append
        desktop.MiniBridge(self.shell).open_speed(True)
        desktop.MiniBridge(self.shell).open_speed(0)
        self.assertEqual(scripts, ["window.steadyApp && window.steadyApp.openSpeed(true)",
                                   "window.steadyApp && window.steadyApp.openSpeed(false)"])
        self.assertIn("show", self.shell.window.calls)

    def test_what_the_pages_may_call(self):
        bridge = desktop.MiniBridge(self.shell)
        self.assertEqual([n for n in dir(bridge) if not n.startswith("_")],
                         ["get_mode", "hide_mini", "open_result", "open_speed", "set_mode"])
        self.assertEqual(bridge.get_mode(), "bar")
        bridge.hide_mini()
        self.assertIn("hide", self.shell.mini.calls)
        main = desktop.MainBridge(self.shell)
        self.assertEqual([n for n in dir(main) if not n.startswith("_")], ["open_mini"])
        main.open_mini()
        self.assertIn("show", self.shell.mini.calls)
        self.assertTrue(self.saved()["open"])

    def test_mini_page_follows_the_monitor(self):
        self.assertIsNone(self.shell.mini_url())
        self.shell.link.port = 47613
        self.assertEqual(self.shell.mini_url(), "http://127.0.0.1:47613/mini.html")


@unittest.skipUnless(sys.platform == "win32", "Windows named events")
class SingleInstanceTests(unittest.TestCase):
    """A private event name: the real desktop app may be running on this machine."""

    def setUp(self):
        import uuid
        self.name = f"Local\StableInternet.Test.{uuid.uuid4().hex}"

    def test_no_running_instance_to_signal(self):
        self.assertFalse(desktop.signal_running_instance(self.name))

    def test_a_second_launch_wakes_the_first(self):
        import threading
        shown, stop = threading.Event(), threading.Event()
        t = threading.Thread(target=desktop.watch_show_requests, args=(shown.set, stop, self.name), daemon=True)
        t.start()
        for _ in range(50):          # wait until the event exists
            if desktop.signal_running_instance(self.name):
                break
            stop.wait(0.05)
        self.assertTrue(shown.wait(3))
        stop.set()
        t.join(3)


if __name__ == "__main__":
    unittest.main()
