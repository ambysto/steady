"""The real UI in web/: text keys exist, the page works under the server's CSP, JS parses."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app import i18n
from app.server import bucket_minute_stats, bucket_wifi_stats

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
JS = sorted(WEB.rglob("*.js"))
PAGES = sorted(WEB.glob("*.html"))


class WebTextTests(unittest.TestCase):
    def setUp(self):
        self.english = json.loads(Path(i18n.LOCALES_DIR, "en.json").read_text(encoding="utf-8"))

    def test_every_literal_key_exists_in_english(self):
        used = set()
        for path in JS:
            used |= set(re.findall(r"""\bt\(\s*["']([a-z0-9_.]+)["']""", path.read_text(encoding="utf-8")))
        for page in PAGES:
            used |= set(re.findall(r'data-t="([a-z0-9_.]+)"', page.read_text(encoding="utf-8")))
        self.assertGreater(len(used), 50)
        self.assertEqual(sorted(used - set(self.english)), [])

    def test_keys_built_from_known_parts_exist(self):
        for kind in ("online", "router_down", "internet_down"):
            self.assertIn(f"ui.state.{kind}", self.english)
        for kind in ("router_down", "internet_down"):
            self.assertIn(f"ui.state.{kind}_detail", self.english)
        for name in ("reconnect", "flush_dns", "renew_dhcp", "restart_adapter"):
            self.assertIn(f"ui.action.{name}", self.english)
        for key in ("all", "outages", "watchdog", "changes"):
            self.assertIn(f"ui.log.filter.{key}", self.english)
        for key in ("live", "day", "week"):
            self.assertIn(f"ui.range.{key}", self.english)
        for key in ("ok", "warn", "bad", "info"):
            self.assertIn(f"ui.status.{key}", self.english)
        for key in ("off", "on", "dry_run", "tripped"):
            self.assertIn(f"ui.watchdog.state.{key}", self.english)
        for key in ("low", "medium", "experimental"):
            self.assertIn(f"ui.risk.{key}", self.english)
        for key in ("system", "light", "dark"):
            self.assertIn(f"ui.settings.theme.{key}", self.english)
        for unit in ("kbps", "mbps"):                                      # web/js/mini.js rateText
            self.assertIn(f"ui.mini.unit.{unit}", self.english)
        from app.traffic import _KIND_BY_IFTYPE
        for kind in {*_KIND_BY_IFTYPE.values(), "other"}:                 # the interface kinds traffic.py reports
            self.assertIn(f"ui.mini.net.kind.{kind}", self.english)


class WebSecurityTests(unittest.TestCase):
    def test_no_inline_script_so_the_csp_can_stay_strict(self):
        self.assertEqual([p.name for p in PAGES], ["index.html", "mini.html"])
        for page in PAGES:
            html = page.read_text(encoding="utf-8")
            for tag in re.findall(r"<script\b[^>]*>", html):
                self.assertIn("src=", tag)
            self.assertNotRegex(html, r"\son[a-z]+=")       # no inline event handlers either
            self.assertIn('content="{{TOKEN}}"', html)

    def test_every_page_is_served_with_its_token(self):
        from app.server import PAGES as SERVED
        self.assertEqual(sorted(set(SERVED.values())), [p.name for p in PAGES])

    def test_scripts_never_inject_markup(self):
        for path in JS:
            text = path.read_text(encoding="utf-8")
            for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
                self.assertNotIn(banned, text, f"{path.name}: {banned}")

    def test_only_same_origin_requests(self):
        for path in JS:
            for url in re.findall(r"""fetch\(\s*["'`]([^"'`]+)""", path.read_text(encoding="utf-8")):
                self.assertTrue(url.startswith("/"), url)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WebSyntaxTests(unittest.TestCase):
    def test_every_module_parses(self):
        for path in JS:
            proc = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, f"{path.name}: {proc.stderr}")

    def test_js_unit_tests_pass(self):
        files = [str(p) for p in sorted((ROOT / "tests" / "js").glob("*.test.mjs"))]
        self.assertTrue(files)
        proc = subprocess.run(["node", "--test", *files], capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-2000:])


class BucketTests(unittest.TestCase):
    def test_minute_stats_merge_per_target(self):
        rows = [{"ts": 0, "target": "router", "ip": "1", "sent": 60, "lost": 0, "avg": 2.0, "max": 5.0, "jitter": 1.0},
                {"ts": 60, "target": "router", "ip": "1", "sent": 60, "lost": 30, "avg": 8.0, "max": 20.0, "jitter": 3.0},
                {"ts": 120, "target": "router", "ip": "1", "sent": 60, "lost": 60, "avg": None, "max": None, "jitter": None},
                {"ts": 60, "target": "google", "ip": "8", "sent": 60, "lost": 0, "avg": 30.0, "max": 31.0, "jitter": None}]
        out = {(r["ts"], r["target"]): r for r in bucket_minute_stats(rows, 300)}
        router = out[(0, "router")]
        self.assertEqual((router["sent"], router["lost"], router["max"], router["jitter"]), (180, 90, 20.0, 2.0))
        self.assertEqual(router["avg"], 4.0)                 # reply-weighted: (2*60 + 8*30) / 90
        self.assertEqual(out[(0, "google")]["avg"], 30.0)

    def test_wifi_stats_keep_last_state_and_mean_numbers(self):
        rows = [{"ts": 0, "state": "connected", "ssid": "A", "rssi": -60, "signal": 80, "rx_mbps": 100, "tx_mbps": 100},
                {"ts": 60, "state": "disconnected", "ssid": "", "rssi": None, "signal": None, "rx_mbps": None, "tx_mbps": None}]
        row, = bucket_wifi_stats(rows, 300)
        self.assertEqual((row["state"], row["rssi"], row["rx_mbps"]), ("disconnected", -60.0, 100.0))


if __name__ == "__main__":
    unittest.main()
