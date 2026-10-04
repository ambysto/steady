import io
import json
import os
import sys
import tempfile
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

    def test_icon_is_drawn_in_the_state_color(self):
        try:
            img = desktop.draw_icon("bad")
        except ImportError:
            self.skipTest("Pillow not installed")
        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.getpixel((8, 32))[:3], desktop.COLORS["bad"])
        self.assertEqual(img.getpixel((0, 0))[3], 0)      # transparent corner


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
