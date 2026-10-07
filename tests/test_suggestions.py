import json
import unittest
from pathlib import Path

from app import diagnostics, i18n, suggestions
from app.i18n import msg
from app.storage import Storage
from tests.test_diagnostics import NOW, fake_context, wifi

H = 3600
META = {"power_pcie_aspm_off": {"name": msg("tweak.power_pcie_aspm_off.name"), "note": "", "risk": "low"},
        "wifi_power_saving": {"name": msg("tweak.wifi_power_saving.name"), "note": "", "risk": "low"},
        "wifi_mode_ac": {"name": msg("tweak.wifi_mode_ac.name"), "note": "", "risk": "experimental"}}


def result(key, status, summary_key="x", **extra):
    return {"id": 0, "key": key, "title": msg(f"diag.{key}.title"), "status": status,
            "summary": msg(summary_key), "details": [], "advice": "", "tweak": None, "error": None, **extra}


def ids(items):
    return [(i["kind"], i["id"]) for i in items]


class SuggestTests(unittest.TestCase):
    def test_manual_steps_follow_the_checks(self):
        items = suggestions.suggest([result("physical_link", "warn"), result("signal", "bad"),
                                     result("interference", "ok"), result("modem_wifi", "info")], None, {}, META)
        self.assertEqual(ids(items), [("manual", "move_closer"), ("manual", "antenna"), ("manual", "modem_wifi_off")])
        self.assertEqual(items[0]["reason"]["status"], "bad")

    def test_improved_link_is_not_a_reason_to_move_the_antenna(self):
        improved = result("physical_link", "info", "diag.physical_link.improved")
        self.assertEqual(suggestions.suggest([improved], None, {}, META), [])
        occasional = result("physical_link", "info", "diag.physical_link.info")
        self.assertEqual(ids(suggestions.suggest([occasional], None, {}, META)), [("manual", "antenna")])

    def test_tweaks_from_findings_and_from_check_10(self):
        results = [result("drops", "bad", tweak="power_pcie_aspm_off"),
                   result("tweaks", "info", details=["wifi_power_saving", "wifi_mode_ac", "not_in_catalog"])]
        items = suggestions.suggest(results, None, {}, META)
        self.assertEqual(ids(items), [("tweak", "power_pcie_aspm_off"), ("tweak", "wifi_power_saving")])  # no experimental
        self.assertEqual(items[0]["severity"], 3)

    def test_tweaks_already_on_or_unsupported_are_not_suggested(self):
        states = {"power_pcie_aspm_off": {"enabled": True, "supported": True},
                  "wifi_power_saving": {"enabled": False, "supported": False}}
        results = [result("drops", "bad", tweak="power_pcie_aspm_off"),
                   result("tweaks", "info", details=["wifi_power_saving"])]
        self.assertEqual(suggestions.suggest(results, states, {}, META), [])

    def test_done_steps_sort_after_open_ones_of_the_same_level(self):
        items = suggestions.suggest([result("signal", "warn"), result("interference", "warn")], None,
                                    {"move_closer": int(NOW)}, META)
        self.assertEqual(ids(items), [("manual", "router_channel"), ("manual", "move_closer")])
        self.assertEqual(items[1]["done_at"], int(NOW))

    def test_every_step_has_a_real_check_and_text(self):
        checks = {key for _, key, _ in diagnostics.CHECKS + diagnostics.ON_DEMAND_CHECKS}
        english = json.loads(Path(i18n.LOCALES_DIR, "en.json").read_text(encoding="utf-8"))
        for step in suggestions.MANUAL_STEPS:
            self.assertIn(step.check, checks, step.id)
            self.assertIn(f"manual.{step.id}.title", english)
            self.assertIn(f"manual.{step.id}.body", english)

    def test_real_diagnostics_results_render_in_every_language(self):
        report = diagnostics.run_all(fake_context(wifi=lambda: wifi(rssi=-78)))
        stored = json.loads(json.dumps([r.to_dict() for r in report.results]))
        items = suggestions.suggest(stored, None, {}, META)
        self.assertIn(("manual", "move_closer"), ids(items))
        for code in [entry["code"] for entry in i18n.available()]:
            text = json.dumps(i18n.localize(items, code), ensure_ascii=False)
            self.assertNotRegex(text, r'"(manual|diag)\.[a-z_.]+"', code)   # every key resolved


def route_result(**ms):
    rtts = {host: ms.get(host.split(".")[0]) for host in diagnostics.ROUTE_HOSTS}
    return json.loads(json.dumps(diagnostics.evaluate_route(rtts).to_dict()))


class ExternalCauseTests(unittest.TestCase):
    """Bottlenecks the PC cannot fix: the router's queue and the ISP's route abroad (SIC-89)."""

    DETOUR = dict(cloudflare=48, google=161, microsoft=52, wikipedia=55, apple=60)
    FINE = dict(cloudflare=31, google=35, microsoft=38, wikipedia=42, apple=47)

    def test_a_detour_suggests_a_tunnel(self):
        items = suggestions.suggest([route_result(**self.DETOUR)], None, {}, META)
        self.assertEqual(ids(items), [("manual", "tunnel_route")])
        self.assertEqual(items[0]["reason"]["check"], "route")

    def test_no_tunnel_suggestion_when_routes_are_fine_unknown_or_already_tunnelled(self):
        for name, r in {"fine": route_result(**self.FINE), "too few answers": route_result(cloudflare=20),
                        "tunnel up": json.loads(json.dumps(diagnostics.evaluate_route({}, tunnel_up=True).to_dict()))}.items():
            self.assertEqual(suggestions.suggest([r], None, {}, META), [], name)

    def test_bufferbloat_suggests_sqm_on_the_router(self):
        for status in ("warn", "bad"):
            items = suggestions.suggest([result("bufferbloat", status)], None, {}, META)
            self.assertEqual(ids(items), [("manual", "router_sqm")], status)
        self.assertEqual(suggestions.suggest([result("bufferbloat", "ok")], None, {}, META), [])

    def test_the_texts_say_what_to_do_without_naming_a_product(self):
        sqm, tunnel = suggestions.STEPS["router_sqm"], suggestions.STEPS["tunnel_route"]
        body = i18n.render(sqm.body, "en")
        for word in ("CAKE", "fq_codel", "OpenWrt", "download"):
            self.assertIn(word, body)
        text = i18n.render(tunnel.title, "en") + i18n.render(tunnel.body, "en")
        self.assertIn("tunnel", text)
        for brand in ("WARP", "Cloudflare", "NordVPN", "ExpressVPN", "Tailscale", "WireGuard"):
            self.assertNotIn(brand, text)

    def test_both_steps_are_translated_everywhere(self):
        for code in [entry["code"] for entry in i18n.available()]:
            for step in ("router_sqm", "tunnel_route"):
                for part in ("title", "body"):
                    key = f"manual.{step}.{part}"
                    self.assertNotEqual(i18n.t(key, code), key, (code, key))
        # translated, not left in English (the catalog falls back to English for a missing key)
        english = {k: i18n.t(k, "en") for k in ("manual.router_sqm.body", "manual.tunnel_route.body", "diag.route.advice")}
        for code in [entry["code"] for entry in i18n.available() if entry["code"] != "en"]:
            for key, en_text in english.items():
                self.assertNotEqual(i18n.t(key, code), en_text, (code, key))

    def test_the_detour_goes_through_build_and_can_be_marked_done(self):
        with Storage() as db:
            db.save_diagnostic_run(int(NOW), "info", [route_result(**self.DETOUR)])
            out = suggestions.build(db, NOW, None, META)
            self.assertEqual(ids(out["items"]), [("manual", "tunnel_route")])
            suggestions.mark_done(db, "tunnel_route", NOW - H)
            item = suggestions.build(db, NOW, None, META)["items"][0]
            self.assertEqual((item["id"], item["done_at"]), ("tunnel_route", int(NOW - H)))
            self.assertEqual(item["impact"]["status"], "collecting")      # measured before and after, like any step


class DnsTweakSuggestionTests(unittest.TestCase):
    def test_a_broken_dns_suggests_the_tweak_unless_it_is_on_or_not_possible(self):
        meta = {**META, "dns_fastest": {"name": msg("tweak.dns_fastest.name"), "note": "", "risk": "medium"}}
        broken = result("dns", "warn", tweak="dns_fastest")
        self.assertIn(("tweak", "dns_fastest"), ids(suggestions.suggest([broken], None, {}, meta)))
        for state in ({"enabled": True, "supported": True}, {"enabled": False, "supported": False}):   # on, or a VPN/captive network
            items = suggestions.suggest([broken], {"dns_fastest": state}, {}, meta)
            self.assertNotIn(("tweak", "dns_fastest"), ids(items), state)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage()
        self.addCleanup(self.db.close)

    def test_without_a_run_there_is_a_hint(self):
        out = suggestions.build(self.db, NOW, None, META)
        self.assertEqual((out["run"], out["items"]), (None, []))
        self.assertEqual(i18n.render(out["hint"], "en"), "Run diagnostics to get suggestions for this network")

    def test_marking_done_records_an_event_and_starts_measuring(self):
        self.db.save_diagnostic_run(int(NOW) - H, "warn", [result("physical_link", "warn")])
        done = suggestions.mark_done(self.db, "antenna", NOW - 3 * H)
        self.assertEqual(done["id"], "antenna")
        event = self.db.query_events(kinds=["manual_step_done"])[0]
        self.assertEqual(i18n.render(event["message"], "en"), "Marked as done: Reposition the Wi‑Fi card's antennas")
        out = suggestions.build(self.db, NOW, None, META)
        item = out["items"][0]
        self.assertEqual((item["id"], item["done_at"]), ("antenna", int(NOW - 3 * H)))
        self.assertEqual(item["impact"]["status"], "collecting")   # no monitor data in this test

    def test_done_step_stays_listed_after_the_problem_is_gone(self):
        self.db.save_diagnostic_run(int(NOW), "ok", [result("physical_link", "ok")])
        suggestions.mark_done(self.db, "antenna", NOW - H)
        items = suggestions.build(self.db, NOW, None, META)["items"]
        self.assertEqual(ids(items), [("manual", "antenna")])
        self.assertIsNone(items[0]["reason"])

    def test_old_done_marks_expire(self):
        suggestions.mark_done(self.db, "antenna", NOW - 20 * 86400)
        self.assertEqual(suggestions.done_steps(self.db, NOW), {})

    def test_unknown_step_is_refused(self):
        with self.assertRaises(KeyError):
            suggestions.mark_done(self.db, "format_c", NOW)


if __name__ == "__main__":
    unittest.main()
