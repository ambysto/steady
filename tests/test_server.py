import http.client
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from app import config, elevated, elevation, i18n
from app.server import Api, Jobs, Server
from app.storage import Storage


class FakeMonitor:
    def snapshot(self, window_s=300):
        return {"window": window_s, "wifi": {"interface": "Wi-Fi", "state": "connected", "profile": "Home"},
                "last_profile": "Home", "outages": {}}


class FakeManager:
    def __init__(self):
        self.calls = []

    def states(self):
        return []   # tests that need states replace this with real TweakState objects

    def get(self, tweak_id):
        if tweak_id != "wifi_power_saving":
            raise KeyError(tweak_id)

    def enable(self, tweak_id):
        self.calls.append(("enable", tweak_id))
        return SimpleNamespace(ok=True, changed=True, message="Đã bật")

    def disable(self, tweak_id):
        self.calls.append(("disable", tweak_id))
        return SimpleNamespace(ok=True, changed=True, message="Đã khôi phục")


class FakeActions:
    def __init__(self):
        self.calls = []

    def reconnect(self, interface, profile, force=False):
        self.calls.append(("reconnect", interface, profile, force))
        return SimpleNamespace(ok=True, message="ok")

    def restart_adapter(self, interface):
        self.calls.append(("restart", interface))
        return SimpleNamespace(ok=True, message="ok")

    def flush_dns(self):
        self.calls.append(("flush",))
        return SimpleNamespace(ok=True, message="ok")

    def renew_dhcp(self, interface):
        self.calls.append(("renew", interface))
        return SimpleNamespace(ok=True, message="ok")


class ServerTestCase(unittest.TestCase):
    admin = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["STABLEINTERNET_USERDIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_USERDIR", None)
        self.settings = json.loads(json.dumps(config.DEFAULT_SETTINGS))
        self.storage = Storage()
        self.addCleanup(self.storage.close)
        self.manager, self.actions, self.elevations = FakeManager(), FakeActions(), []
        self.route = {"interface_index": 6, "gateway": "192.168.3.1"}
        self.backup = {}

        def elevate(op, value):
            self.elevations.append((op, value))
            return SimpleNamespace(ok=False, message="Đã hủy", cancelled=True)
        self.api = Api(monitor=FakeMonitor(), storage=self.storage, load_settings=lambda: json.loads(json.dumps(self.settings)),
                       save_settings=self._save, is_admin=lambda: self.admin, tweak_manager=lambda: self.manager,
                       elevate=elevate, actions=self.actions,
                       run_diagnostics=lambda: {"worst": "ok", "results": []}, sync_jobs=True,
                       load_backup=lambda: self.backup, autostart_status=self._autostart,
                       route=lambda: self.route)
        self.server = Server(self.api, 0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.host = f"127.0.0.1:{self.server.port}"

    def _autostart(self):
        self.autostart_calls = getattr(self, "autostart_calls", 0) + 1
        return SimpleNamespace(installed=True, problems=[], legacy_shortcut=False)

    def _save(self, s):
        self.settings = json.loads(json.dumps(s))

    def req(self, method, path, body=None, headers=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        h = {"Host": host or self.host}
        if token:
            h["X-Token"] = self.server.token
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw.decode("utf-8", "replace")
        return resp.status, payload, resp


class GateTests(ServerTestCase):
    def test_index_has_token_and_security_headers(self):
        status, html, resp = self.req("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, html)
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))

    def test_dns_rebinding_host_is_refused_even_for_the_page(self):
        for host in ("evil.example:%d" % self.server.port, "127.0.0.1", "127.0.0.1:1", "localhost.evil.com",
                     "LOCALHOST:%d" % self.server.port):
            status, payload, _ = self.req("GET", "/", token=False, host=host)
            self.assertEqual(status, 403, host)
            self.assertNotIn(self.server.token, json.dumps(payload))
            status, _, _ = self.req("GET", "/api/state", host=host)
            self.assertEqual(status, 403, host)

    def test_localhost_name_is_accepted(self):
        self.assertEqual(self.req("GET", "/api/state", host=f"localhost:{self.server.port}")[0], 200)

    def test_api_requires_token(self):
        self.assertEqual(self.req("GET", "/api/state", token=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/state", token=False, headers={"X-Token": "wrong"})[0], 401)
        self.assertEqual(self.req("GET", "/api/state", token=False, headers={"X-Token": ""})[0], 401)
        self.assertEqual(self.req("GET", "/api/state")[0], 200)

    def test_token_is_random_per_start(self):
        other = Server(self.api, 0)
        self.addCleanup(other.httpd.server_close)
        self.assertNotEqual(other.token, self.server.token)
        self.assertGreaterEqual(len(self.server.token), 40)

    def test_preflight_is_refused_without_cors_headers(self):
        status, _, resp = self.req("OPTIONS", "/api/settings", token=False,
                                   headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "x-token"})
        self.assertEqual(status, 403)
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))
        self.assertIsNone(resp.getheader("Access-Control-Allow-Headers"))

    def test_cross_origin_writes_refused_even_with_token(self):
        body = {"watchdog": {"enabled": True}}
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": f"http://127.0.0.1:{self.server.port + 1}"}):
            self.assertEqual(self.req("POST", "/api/settings", body, headers=headers)[0], 403, headers)
        self.assertFalse(self.settings["watchdog"]["enabled"])

    def test_same_origin_write_accepted(self):
        status, payload, _ = self.req("POST", "/api/settings", {"watchdog": {"dry_run": True}},
                                      headers={"Origin": f"http://127.0.0.1:{self.server.port}",
                                               "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200, payload)
        self.assertTrue(self.settings["watchdog"]["dry_run"])

    def test_body_limits(self):
        self.assertEqual(self.req("POST", "/api/settings", b"x" * (64 * 1024 + 1))[0], 413)
        self.assertEqual(self.req("POST", "/api/settings", b"{not json")[0], 400)
        status, _, _ = self.req("POST", "/api/settings", b'{"watchdog":{"dry_run":true}}',
                                headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_refusals_never_reset_the_connection(self):
        # Regression: replying before reading the body made Windows reset the connection ~1 time in 10
        # (client saw "connection aborted" instead of 413/401/403). Hammer each refusal path.
        body = b"x" * (64 * 1024 + 1)
        bad_origin = {"Origin": "https://evil.example"}
        for _ in range(60):
            self.assertEqual(self.req("POST", "/api/settings", body)[0], 413)
            self.assertEqual(self.req("POST", "/api/settings", body, token=False)[0], 401)
            self.assertEqual(self.req("POST", "/api/settings", body, headers=bad_origin)[0], 403)
            self.assertEqual(self.req("POST", "/api/settings", body, host="evil.example")[0], 403)

    def test_a_huge_declared_body_is_refused_without_reading_it_all(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        conn.putrequest("POST", "/api/settings", skip_host=True)
        for k, v in {"Host": self.host, "X-Token": self.server.token, "Content-Type": "application/json",
                     "Content-Length": str(5 * 1024 ** 3)}.items():
            conn.putheader(k, v)
        conn.endheaders()
        conn.send(b"x" * 1000)          # send almost nothing of the 5 GiB it promised
        started = time.monotonic()
        resp = conn.getresponse()
        self.assertEqual(resp.status, 413)
        self.assertLess(time.monotonic() - started, 6)   # bounded drain, not a 5 GiB wait
        conn.close()

    def test_static_traversal_and_unknown_types(self):
        for path in ("/../app/config.py", "/%2e%2e/app/config.py", "/..%5capp%5cconfig.py", "/index.html%00.css",
                     "/../data/settings.json", "/.gitkeep"):
            self.assertEqual(self.req("GET", path, token=False)[0], 404, path)

    def test_unknown_route_and_method(self):
        self.assertEqual(self.req("GET", "/api/nope")[0], 404)
        self.assertEqual(self.req("POST", "/api/state", {})[0], 405)
        self.assertEqual(self.req("DELETE", "/api/state")[0], 405)

    def test_internal_errors_do_not_leak_details(self):
        self.api.monitor = None  # live() will crash
        status, payload, _ = self.req("GET", "/api/live")
        self.assertEqual((status, payload), (500, {"error": "internal error"}))

    def test_runtime_file_written_and_removed(self):
        path = os.path.join(self.tmp.name, "server.json")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["port"], self.server.port)
        self.server.stop()
        self.assertFalse(os.path.exists(path))
        self.server.stop = lambda: None  # already stopped for the cleanup

    def test_only_loopback(self):
        with self.assertRaises(ValueError):
            Server(self.api, 0, host="0.0.0.0")


class ReadApiTests(ServerTestCase):
    def test_state_and_live(self):
        state = self.req("GET", "/api/state")[1]
        self.assertFalse(state["is_admin"])
        self.assertIn("watchdog", state)
        self.assertEqual(self.req("GET", "/api/live?window=99999")[1]["window"], 900)  # clamped
        self.assertEqual(self.req("GET", "/api/live?window=abc")[0], 400)

    def test_i18n(self):
        payload = self.req("GET", "/api/i18n")[1]
        self.assertEqual((payload["setting"], payload["language"]), ("en", "en"))
        self.assertEqual(payload["available"][0]["code"], "en")
        self.assertIn("vi", [entry["code"] for entry in payload["available"]])
        self.assertEqual(payload["messages"]["notify.back.title"], "Connection restored")
        self.assertNotIn("_meta", payload["messages"])
        self.settings["ui"]["language"] = "vi"
        payload = self.req("GET", "/api/i18n")[1]
        self.assertEqual((payload["setting"], payload["language"]), ("vi", "vi"))
        self.assertEqual(payload["messages"]["notify.back.title"], "Đã có mạng trở lại")
        preview = self.req("GET", "/api/i18n?lang=en-GB")[1]       # preview without saving
        self.assertEqual((preview["setting"], preview["language"]), ("vi", "en"))
        self.assertEqual(self.req("GET", "/api/i18n", token=False)[0], 401)

    def test_saved_diagnostic_run_reads_in_the_chosen_language(self):
        from app import diagnostics
        result = diagnostics.evaluate_signal(None).to_dict()
        run_id = self.storage.save_diagnostic_run(int(time.time()), "info", [result])
        en = self.req("GET", f"/api/diagnostics/runs/{run_id}")[1]["results"][0]
        self.assertEqual((en["title"], en["summary"]), ("Wi‑Fi signal", "No Wi‑Fi card"))
        vi = self.req("GET", f"/api/diagnostics/runs/{run_id}?lang=vi")[1]["results"][0]
        self.assertEqual((vi["title"], vi["summary"]), ("Tín hiệu Wi‑Fi", "Không có card Wi‑Fi"))
        self.settings["ui"]["language"] = "vi"
        self.assertEqual(self.req("GET", f"/api/diagnostics/runs/{run_id}")[1]["results"][0]["summary"],
                         "Không có card Wi‑Fi")

    def test_events_read_in_the_chosen_language(self):
        self.storage.add_event(int(time.time()), "watchdog_recovered", i18n.msg("watchdog.event.recovered", duration=40))
        self.storage.add_event(int(time.time()) - 1, "legacy", "Mạng trở lại (sự cố kéo dài 9s)")
        en = [e["message"] for e in self.req("GET", "/api/events")[1]["events"]]
        vi = [e["message"] for e in self.req("GET", "/api/events?lang=vi")[1]["events"]]
        self.assertEqual(en, ["Network back (incident lasted 40s)", "Mạng trở lại (sự cố kéo dài 9s)"])
        self.assertEqual(vi, ["Mạng trở lại (sự cố kéo dài 40s)", "Mạng trở lại (sự cố kéo dài 9s)"])

    def test_tweak_states_are_localized(self):
        from app import tweaks
        self.manager.states = lambda: [tweaks.TweakState(
            "wifi_roaming", i18n.msg("tweak.wifi_roaming.name"), "low", i18n.msg("tweak.group.wifi_card"), False,
            False, None, i18n.msg("tweak.reason.no_property"), False, True, True, False, True)]
        self.req("GET", "/api/tweaks")                       # starts the (sync) refresh
        state = self.req("GET", "/api/tweaks?lang=vi")[1]["states"][0]
        self.assertEqual((state["name"], state["group"], state["reason"]),
                         ("Giảm roaming", "Card Wi‑Fi", "card không có thuộc tính này"))

    def test_impact_of_a_tweak(self):
        now = int(time.time()) // 60 * 60
        t = now - 26 * 3600
        for ts in range(t - 26 * 3600, now, 300):
            self.storage.add_minute_stat(ts, "router", 60, 3 if ts < t else 0)
        name = i18n.msg("tweak.power_pcie_aspm_off.name")
        self.storage.add_event(t, "tweak_enabled", i18n.msg("tweak.event.enabled", name=name, tweak_id="power_pcie_aspm_off"))
        change = self.req("GET", "/api/impact")[1]["changes"][0]
        self.assertEqual((change["id"], change["title"]), ("power_pcie_aspm_off", "Turn off PCIe ASPM"))
        self.assertEqual(change["impact"]["status"], "better")
        self.assertEqual(change["impact"]["summary"], "Better since the change")
        self.assertIn("Packet loss to the router: 5.00% → 0.00%", change["impact"]["details"])
        vi = self.req("GET", "/api/impact?lang=vi")[1]["changes"][0]
        self.assertEqual(vi["title"], "Tắt PCIe ASPM")
        self.req("GET", "/api/tweaks")
        self.assertEqual(self.req("GET", "/api/tweaks")[1]["impact"]["power_pcie_aspm_off"]["status"], "better")

    def test_state_carries_the_editable_settings(self):
        state = self.req("GET", "/api/state")[1]
        self.assertEqual(set(state["settings"]), {"watchdog", "notify", "ui", "failover"})
        self.assertEqual(state["settings"]["ui"]["language"], "en")
        self.assertEqual(state["notify_after_s"], 30)

    def test_autostart_status_is_cached(self):
        for _ in range(3):
            self.assertEqual(self.req("GET", "/api/autostart")[1]["installed"], True)
        self.assertEqual(self.autostart_calls, 1)

    def test_history_buckets(self):
        now = int(time.time()) // 3600 * 3600
        for i in range(30):
            self.storage.add_minute_stat(now - 3600 + 60 * i, "router", 60, 1, avg=2.0)
        minute = self.req("GET", "/api/history?hours=2")[1]
        bucketed = self.req("GET", "/api/history?hours=2&bucket=15")[1]
        self.assertEqual((minute["bucket_s"], len(minute["minute_stats"])), (60, 30))
        self.assertEqual((bucketed["bucket_s"], len(bucketed["minute_stats"])), (900, 2))
        self.assertEqual(sum(r["sent"] for r in bucketed["minute_stats"]), 1800)

    def test_ui_modules_are_served(self):
        status, body, resp = self.req("GET", "/js/app.js", token=False)
        self.assertEqual(status, 200)
        self.assertTrue(resp.getheader("Content-Type").startswith("text/javascript"))
        self.assertEqual(self.req("GET", "/js/screens/overview.js", token=False)[0], 200)
        status, html, _ = self.req("GET", "/", token=False)
        self.assertIn('src="js/app.js"', html)

    def test_history_and_events(self):
        self.storage.add_event(int(time.time()), "router_down", "x", level="bad", duration=5)
        hist = self.req("GET", "/api/history?hours=1")[1]
        self.assertEqual(hist["events"][0]["kind"], "router_down")
        self.assertEqual(len(self.req("GET", "/api/events?limit=5")[1]["events"]), 1)

    def test_diagnostics_job_and_runs(self):
        job = self.req("POST", "/api/diagnostics", {})[1]
        self.assertEqual(job["status"], "done")
        self.assertEqual(self.req("GET", f"/api/jobs/{job['id']}")[1]["result"]["worst"], "ok")
        self.assertEqual(self.req("GET", "/api/jobs/" + "0" * 32)[0], 404)
        self.assertEqual(self.req("GET", "/api/diagnostics/runs")[1], {"runs": []})
        self.assertEqual(self.req("GET", "/api/diagnostics/runs/5")[0], 404)


class WriteApiTests(ServerTestCase):
    def test_tweak_without_admin_goes_through_uac(self):
        job = self.req("POST", "/api/tweaks/wifi_power_saving", {"enable": True})[1]
        self.assertEqual(self.elevations, [("tweak-enable", "wifi_power_saving")])
        self.assertEqual(self.manager.calls, [])
        self.assertTrue(job["result"]["cancelled"])

    def test_tweak_with_admin_runs_directly(self):
        self.admin = True
        job = self.req("POST", "/api/tweaks/wifi_power_saving", {"enable": False})[1]
        self.assertEqual(self.manager.calls, [("disable", "wifi_power_saving")])
        self.assertEqual(self.elevations, [])
        self.assertTrue(job["result"]["ok"])

    def test_suggestions_and_manual_done(self):
        self.assertEqual(self.req("GET", "/api/suggestions")[1]["hint"], "Run diagnostics to get suggestions for this network")
        bad_link = {"id": 13, "key": "physical_link", "title": i18n.msg("diag.physical_link.title"), "status": "warn",
                    "summary": i18n.msg("diag.physical_link.warn"), "details": [], "advice": "", "tweak": None, "error": None}
        self.storage.save_diagnostic_run(int(time.time()), "warn", [bad_link])
        item = self.req("GET", "/api/suggestions?lang=vi")[1]["items"][0]
        self.assertEqual((item["id"], item["title"]), ("antenna", "Đổi vị trí ăng-ten card Wi‑Fi"))
        self.assertEqual(item["reason"]["summary"], "Đường truyền Wi‑Fi thường xuyên sụp tốc độ kèm mất gói")
        status, done, _ = self.req("POST", "/api/manual/antenna/done", {})
        self.assertEqual((status, done["id"]), (200, "antenna"))
        self.assertEqual(self.req("GET", "/api/suggestions")[1]["items"][0]["done_at"], done["ts"])
        self.assertEqual(self.req("POST", "/api/manual/format_c/done", {})[0], 404)
        self.assertEqual(self.req("POST", "/api/manual/antenna/done", {}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(len(self.storage.query_events(kinds=["manual_step_done"])), 1)

    def test_failover_without_admin_goes_through_uac(self):
        self.api.failover = SimpleNamespace(snapshot=lambda: {"paths": [{"index": 21, "name": "USB 4G"}], "preferred": None,
                                                              "primary": 6, "home": None, "tripped": False})
        state = self.req("GET", "/api/failover")[1]
        self.assertEqual((state["paths"][0]["index"], state["is_admin"], state["settings"]["enabled"]), (21, False, False))
        job = self.req("POST", "/api/failover/prefer/21", {})[1]
        self.assertEqual(self.elevations[-1], ("path-prefer", "21"))
        self.assertTrue(job["result"]["elevated"])
        self.req("POST", "/api/failover/restore", {})
        self.assertEqual(self.elevations[-1], ("path-restore", "all"))
        self.assertEqual(self.req("POST", "/api/failover/prefer/abc", {})[0], 404)
        self.assertEqual(self.req("POST", "/api/failover/restore", {}, headers={"Origin": "https://evil.example"})[0], 403)

    def test_switching_to_the_path_in_use_is_refused_before_uac(self):
        self.api.failover = SimpleNamespace(paths=[SimpleNamespace(index=6, name="Wi-Fi")], snapshot=lambda: {})
        job = self.req("POST", "/api/failover/prefer/6?lang=en", {})[1]
        self.assertFalse(job["result"]["ok"])
        self.assertEqual(job["result"]["message"], "Wi-Fi is already the connection in use; nothing to switch")
        self.assertEqual(self.elevations, [])                       # no UAC prompt for nothing

    def test_admin_switches_are_logged(self):
        from app.failover import SwitchResult
        self.admin = True
        switch = SimpleNamespace(
            prefer=lambda backup, home, source: SwitchResult(True, i18n.msg("failover.result.switched", path=backup.name)),
            restore_all=lambda: [SwitchResult(True, i18n.msg("failover.result.restored", path="USB 4G"))])
        self.api.failover = SimpleNamespace(paths=[], switch=switch, snapshot=lambda: {})
        rows = [{"interface_index": 21, "name": "USB 4G", "gateway": "g", "ipv4": "192.168.8.100", "route_metric": 0,
                 "interface_metric": 35, "automatic_metric": True, "virtual": False, "media": "", "status": "Up"}]
        with mock.patch("app.winutil.get_paths", return_value=rows):
            self.assertTrue(self.req("POST", "/api/failover/prefer/21", {})[1]["result"]["ok"])
        self.req("POST", "/api/failover/restore", {})
        kinds = [e["kind"] for e in self.storage.query_events(kinds=["failover_switched", "failover_restored"])]
        self.assertEqual(sorted(kinds), ["failover_restored", "failover_switched"])

    def test_failover_settings(self):
        self.settings["failover"]["tripped_at"] = 123
        status, payload, _ = self.req("POST", "/api/settings", {"failover": {"enabled": True, "dry_run": True}})
        self.assertEqual(status, 200)
        self.assertEqual((self.settings["failover"]["enabled"], self.settings["failover"]["tripped_at"]), (True, None))
        self.assertEqual(self.req("POST", "/api/settings", {"failover": {"threshold_s": 1}})[0], 400)

    def test_tweak_input_validation(self):
        self.assertEqual(self.req("POST", "/api/tweaks/BAD-ID", {"enable": True})[0], 400)
        self.assertEqual(self.req("POST", "/api/tweaks/x%20y", {"enable": True})[0], 400)
        self.assertEqual(self.req("POST", "/api/tweaks/wifi_power_saving", {"enable": "yes"})[0], 400)
        self.assertEqual(self.req("POST", "/api/tweaks/wifi_power_saving", [1])[0], 400)
        self.assertEqual(self.elevations, [])

    def test_unknown_tweak_with_admin_is_a_job_error(self):
        self.admin = True
        job = self.req("POST", "/api/tweaks/nope", {"enable": True})[1]
        self.assertEqual(job["status"], "error")
        self.assertEqual(self.manager.calls, [])

    def test_actions(self):
        self.req("POST", "/api/actions/reconnect", {})
        self.assertEqual(self.actions.calls[-1], ("reconnect", "Wi-Fi", "Home", True))
        self.req("POST", "/api/actions/restart_adapter", {})
        self.assertEqual(self.elevations[-1], ("restart-adapter", "Wi-Fi"))  # not admin: UAC
        self.assertEqual(self.req("POST", "/api/actions/format_c", {})[0], 404)
        self.assertEqual(self.storage.query_events(kinds=["user_action"])[0]["kind"], "user_action")

    def test_settings_validation(self):
        bad = [{"watchdog": {"threshold_s": True}}, {"watchdog": {"threshold_s": 1}},
               {"watchdog": {"max_per_hour": 1000}}, {"watchdog": {"enabled": 1}},
               {"watchdog": {"tripped_at": None}}, {"server": {"port": 80}}, {"targets": {"x": "1.2.3.4"}},
               {"watchdog": "on"}, {}]
        for body in bad:
            self.assertEqual(self.req("POST", "/api/settings", body)[0], 400, body)
        self.assertEqual(self.settings, json.loads(json.dumps(config.DEFAULT_SETTINGS)))

    def test_language_setting(self):
        for value in ("klingon", "", 5, None, "../en"):
            self.assertEqual(self.req("POST", "/api/settings", {"ui": {"language": value}})[0], 400, value)
        self.assertEqual(self.settings["ui"]["language"], "en")
        for value in ("vi", "auto", "en"):
            status, payload, _ = self.req("POST", "/api/settings", {"ui": {"language": value}})
            self.assertEqual(status, 200, value)
            self.assertEqual(payload["settings"]["ui"]["language"], value)

    def test_language_change_invalidates_the_cached_language(self):
        with mock.patch.object(i18n, "invalidate") as invalidate:
            self.req("POST", "/api/settings", {"watchdog": {"dry_run": True}})
            invalidate.assert_not_called()
            self.req("POST", "/api/settings", {"ui": {"language": "vi"}})
            invalidate.assert_called_once()

    def test_reenabling_watchdog_clears_trip(self):
        self.settings["watchdog"]["tripped_at"] = 123
        status, payload, _ = self.req("POST", "/api/settings", {"watchdog": {"enabled": True, "threshold_s": 45}})
        self.assertEqual(status, 200)
        self.assertEqual((self.settings["watchdog"]["enabled"], self.settings["watchdog"]["threshold_s"],
                          self.settings["watchdog"]["tripped_at"]), (True, 45, None))
        self.assertTrue(self.storage.query_events(kinds=["settings_changed"]))


class JobsTests(unittest.TestCase):
    def test_single_flight(self):
        import threading
        gate = threading.Event()
        jobs = Jobs()
        a = jobs.submit("diag", lambda: gate.wait(5), single="diag")
        b = jobs.submit("diag", lambda: None, single="diag")
        self.assertEqual(a["id"], b["id"])
        gate.set()
        for _ in range(50):
            if jobs.get(a["id"])["status"] == "done":
                break
            time.sleep(0.05)
        c = jobs.submit("diag", lambda: None, single="diag")
        self.assertNotEqual(c["id"], a["id"])


class ElevatedChildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["STABLEINTERNET_USERDIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_USERDIR", None)

    def test_result_path_must_be_ours(self):
        good = config.results_dir() / ("a" * 32 + ".json")
        self.assertEqual(elevated.check_result_path(str(good)), good.resolve())
        for bad in (os.path.join(self.tmp.name, "a" * 32 + ".json"),            # wrong dir
                    str(config.results_dir() / "x.json"), str(config.results_dir() / ".." / ("a" * 32 + ".json")),
                    r"C:\Windows\System32\drivers\etc\hosts"):
            with self.assertRaises(ValueError, msg=bad):
                elevated.check_result_path(bad)

    def test_not_elevated_reports_instead_of_acting(self):
        if __import__("app.winutil", fromlist=["is_admin"]).is_admin():
            self.skipTest("running as admin")
        self.assertIn("Administrator", i18n.render(elevated.run_op("tweak-enable", "wifi_power_saving")["message"], "en"))
        self.assertFalse(elevated.run_op("format-disk", "C")["ok"])

    def test_path_ops_refuse_the_path_in_use_and_log_real_switches(self):
        os.environ["STABLEINTERNET_DATA"] = self.tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_DATA", None)
        rows = [{"interface_index": i, "name": n, "gateway": "g", "ipv4": ip, "route_metric": 0, "interface_metric": m,
                 "automatic_metric": True, "virtual": False, "media": "", "status": "Up"}
                for i, n, ip, m in ((6, "Wi-Fi", "192.168.3.4", 30), (44, "Ethernet 2", "172.20.10.9", 25))]

        class System:
            metrics = {6: {"automatic": True, "metric": 30}, 44: {"automatic": True, "metric": 25}}

            def interface_metric_get(self, i):
                return dict(self.metrics[i])

            def interface_metric_set(self, i, metric):
                self.metrics[i] = {"automatic": True, "metric": 25} if metric is None else {"automatic": False, "metric": metric}

        with mock.patch("app.winutil.get_paths", return_value=rows), \
                mock.patch("app.winutil.default_route_native", return_value={"interface_index": 6}), \
                mock.patch("app.winsys.WindowsSystem", System):
            refused = elevated._path_op("path-prefer", "6")
            self.assertEqual(i18n.render(refused["message"], "en"), "Wi-Fi is already the connection in use; nothing to switch")
            self.assertTrue(elevated._path_op("path-prefer", "44")["ok"])
            self.assertTrue(elevated._path_op("path-restore", "all")["ok"])
        from app.storage import Storage
        with Storage(config.db_path()) as storage:
            kinds = [e["kind"] for e in reversed(storage.query_events(limit=10))]
        self.assertEqual(kinds, ["failover_switched", "failover_restored"])
        self.assertEqual(System.metrics[44], {"automatic": True, "metric": 25})

    def test_main_rejects_foreign_result_path_without_writing(self):
        target = os.path.join(self.tmp.name, "evil.txt")
        self.assertEqual(elevated.main(["tweak-enable", "x", "--result-file", target]), 2)
        self.assertFalse(os.path.exists(target))


class ElevationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["STABLEINTERNET_USERDIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "STABLEINTERNET_USERDIR", None)

    def test_cancelled_uac(self):
        res = elevation.run_elevated("tweak-enable", "x", launcher=lambda *a: elevation.Launch(False, error=1223))
        self.assertTrue(res.cancelled)
        self.assertFalse(res.ok)

    def test_unknown_op_never_launches(self):
        with self.assertRaises(ValueError):
            elevation.run_elevated("rm-rf", "x", launcher=lambda *a: self.fail("launched"))

    def test_parameters_are_quoted_argv(self):
        seen = {}

        def launcher(file, params, directory, verb, timeout):
            seen.update(file=file, params=params, verb=verb)
            return elevation.Launch(True, exit_code=1)
        res = elevation.run_elevated("restart-adapter", "Wi-Fi 2", launcher=launcher)
        self.assertEqual(seen["verb"], "runas")
        self.assertIn('"Wi-Fi 2"', seen["params"])
        self.assertIn("--result-file", seen["params"])
        self.assertFalse(res.ok)                      # no result file written
        self.assertEqual(os.listdir(config.results_dir()), [])

    @unittest.skipUnless(sys.platform == "win32", "ShellExecuteEx")
    def test_real_shell_execute_round_trip_without_elevation(self):
        # verb "open" runs the same child unelevated: it must report "no admin" through the result file.
        # Run as Administrator (CI runners are), the child would really apply the tweak: never do that.
        if __import__("app.winutil", fromlist=["is_admin"]).is_admin():
            self.skipTest("running as admin: the child would change the machine")
        res = elevation.run_elevated("tweak-enable", "wifi_power_saving", verb="open", timeout_s=60)
        self.assertFalse(res.ok)
        self.assertIsNotNone(res.result, res.message)
        self.assertIn("Administrator", i18n.render(res.message, "en"))
        self.assertEqual(os.listdir(config.results_dir()), [])  # cleaned up


if __name__ == "__main__":
    unittest.main()
