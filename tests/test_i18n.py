import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, i18n

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]*)?\}")


class FakeCatalogs:
    """Points i18n at a temporary locales directory for one test."""

    def __init__(self, test: unittest.TestCase, catalogs: dict):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        for code, content in catalogs.items():
            Path(tmp.name, f"{code}.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        patcher = mock.patch.object(i18n, "LOCALES_DIR", Path(tmp.name))
        patcher.start()
        test.addCleanup(patcher.stop)
        test.addCleanup(i18n.reload)
        i18n.reload()


CATALOGS = {
    "en": {"_meta": {"name": "English", "english_name": "English"},
           "greet": "Hello {name}", "only.en": "English only",
           "items": {"one": "{count} item", "other": "{count} items"},
           "ratio": "{value:.1f} ms", "nested": "Because: {reason}"},
    "vi": {"_meta": {"name": "Tiếng Việt", "english_name": "Vietnamese"},
           "greet": "Xin chào {name}", "items": "{count} mục", "ratio": "{value:.1f} ms"},
    "pt-BR": {"_meta": {"name": "Português (Brasil)", "english_name": "Portuguese (Brazil)"}, "greet": "Olá {name}"},
}


class TranslateTests(unittest.TestCase):
    def setUp(self):
        FakeCatalogs(self, CATALOGS)

    def test_lookup_and_fallback_to_english(self):
        self.assertEqual(i18n.t("greet", "vi", name="An"), "Xin chào An")
        self.assertEqual(i18n.t("only.en", "vi"), "English only")

    def test_missing_key_renders_the_key_and_warns_once(self):
        with self.assertLogs("stableinternet.i18n", "WARNING") as cm:
            self.assertEqual(i18n.t("no.such.key", "en"), "no.such.key")
            self.assertEqual(i18n.t("no.such.key", "vi"), "no.such.key")
        self.assertEqual(len(cm.output), 1)

    def test_plurals(self):
        self.assertEqual(i18n.t("items", "en", count=1), "1 item")
        self.assertEqual(i18n.t("items", "en", count=0), "0 items")
        self.assertEqual(i18n.t("items", "en", count=2), "2 items")
        self.assertEqual(i18n.t("items", "vi", count=1), "1 mục")   # no plural forms in Vietnamese

    def test_plural_rules(self):
        self.assertEqual(i18n.plural_category("fr", 0), "one")
        self.assertEqual(i18n.plural_category("fr", 1.5), "one")
        self.assertEqual(i18n.plural_category("fr", 2), "other")
        self.assertEqual(i18n.plural_category("ja", 1), "other")
        self.assertEqual(i18n.plural_category("en", "not a number"), "other")

    def test_unknown_placeholder_stays_literal(self):
        self.assertEqual(i18n.t("greet", "en"), "Hello {name}")

    def test_format_spec_and_decimal_comma(self):
        self.assertEqual(i18n.t("ratio", "en", value=12.345), "12.3 ms")
        self.assertEqual(i18n.t("ratio", "vi", value=12.345), "12,3 ms")

    def test_integers_are_not_touched_by_decimal_comma(self):
        self.assertEqual(i18n.t("items", "vi", count=1500), "1500 mục")

    def test_template_cannot_reach_into_objects(self):
        # str.format would happily evaluate attribute and index access; ours must not.
        for template in ("{name.__class__}", "{name[0]}", "{name!r}", "{name:{other}}"):
            out = i18n._fill(template, {"name": "x", "other": "y"}, "en")
            self.assertNotIn("class", out.replace("__class__", ""))
            self.assertNotIn("str", out)

    def test_unsafe_or_huge_spec_is_ignored(self):
        self.assertEqual(i18n._fill("{v:99999999}", {"v": 1}, "en"), "1")
        self.assertEqual(i18n._fill("{v:.1f}", {"v": "text"}, "en"), "text")   # wrong type -> plain str

    def test_msg_and_render(self):
        stored = json.loads(json.dumps(i18n.msg("greet", name="Bình")))   # survives a JSON round trip
        self.assertEqual(i18n.render(stored, "vi"), "Xin chào Bình")
        self.assertEqual(i18n.render(stored, "en"), "Hello Bình")
        self.assertEqual(i18n.render("legacy plain text", "en"), "legacy plain text")
        self.assertEqual(i18n.render(None, "en"), "")

    def test_nested_message_params_render_in_the_same_language(self):
        message = i18n.msg("nested", reason=i18n.msg("greet", name="An"))
        self.assertEqual(i18n.render(message, "vi"), "Because: Xin chào An")

    def test_messages_fill_english_for_missing_keys(self):
        merged = i18n.messages("vi")
        self.assertEqual(merged["greet"], "Xin chào {name}")
        self.assertEqual(merged["only.en"], "English only")
        self.assertNotIn("_meta", merged)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        FakeCatalogs(self, CATALOGS)

    def test_exact_base_and_fallback(self):
        self.assertEqual(i18n.resolve("vi"), "vi")
        self.assertEqual(i18n.resolve("VI-vn"), "vi")
        self.assertEqual(i18n.resolve("pt-br"), "pt-BR")
        self.assertEqual(i18n.resolve("pt-PT"), "pt-BR")    # same base language beats English
        self.assertEqual(i18n.resolve("xx"), "en")

    def test_auto_follows_windows(self):
        with mock.patch.object(i18n, "windows_ui_language", return_value="vi-VN"):
            self.assertEqual(i18n.resolve("auto"), "vi")
        with mock.patch.object(i18n, "windows_ui_language", return_value=None):
            self.assertEqual(i18n.resolve("auto"), "en")

    def test_available_lists_english_first(self):
        codes = [entry["code"] for entry in i18n.available()]
        self.assertEqual(codes[0], "en")
        self.assertEqual(set(codes), {"en", "vi", "pt-BR"})

    def test_broken_catalog_is_skipped_not_fatal(self):
        Path(i18n.LOCALES_DIR, "de.json").write_text("{ not json", encoding="utf-8")
        i18n.reload()
        with self.assertLogs("stableinternet.i18n", "ERROR"):
            codes = [entry["code"] for entry in i18n.available()]
        self.assertNotIn("de", codes)
        self.assertEqual(i18n.t("greet", "de", name="X"), "Hello X")


class CurrentLanguageTests(unittest.TestCase):
    def setUp(self):
        FakeCatalogs(self, CATALOGS)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"STABLEINTERNET_DATA": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(i18n.invalidate)

    def set_language(self, code):
        settings = config.load_settings()
        settings["ui"]["language"] = code
        config.save_settings(settings)

    def test_default_is_english(self):
        i18n.invalidate()
        self.assertEqual(i18n.current_language(), "en")
        self.assertEqual(i18n.t("greet", name="A"), "Hello A")

    def test_setting_change_takes_effect_after_invalidate(self):
        i18n.invalidate()
        self.assertEqual(i18n.current_language(), "en")
        self.set_language("vi")
        self.assertEqual(i18n.current_language(), "en")   # cached
        i18n.invalidate()
        self.assertEqual(i18n.current_language(), "vi")
        self.assertEqual(i18n.t("greet", name="A"), "Xin chào A")

    def test_unknown_setting_falls_back_to_english(self):
        self.set_language("klingon")
        i18n.invalidate()
        self.assertEqual(i18n.current_language(), "en")


class DurationTests(unittest.TestCase):
    def setUp(self):
        i18n.reload()
        self.addCleanup(i18n.reload)

    def test_english(self):
        cases = {0: "0 seconds", 1: "1 second", 5: "5 seconds", 60: "1 minute", 61: "1 minute 1 second",
                 125: "2 minutes 5 seconds", 3600: "1 hour", 3720: "1 hour 2 minutes", 7260: "2 hours 1 minute",
                 -3: "0 seconds"}
        for seconds, text in cases.items():
            self.assertEqual(i18n.format_duration(seconds, "en"), text, seconds)

    def test_vietnamese(self):
        self.assertEqual(i18n.format_duration(5, "vi"), "5 giây")
        self.assertEqual(i18n.format_duration(60, "vi"), "1 phút")
        self.assertEqual(i18n.format_duration(3720, "vi"), "1 giờ 2 phút")


class CatalogHygieneTests(unittest.TestCase):
    """The real catalogs in app/locales."""

    def setUp(self):
        i18n.reload()
        self.addCleanup(i18n.reload)
        self.catalogs = {path.stem: json.loads(path.read_text(encoding="utf-8"))
                         for path in sorted(i18n.LOCALES_DIR.glob("*.json"))}

    @staticmethod
    def placeholders(value):
        texts = value.values() if isinstance(value, dict) else [value]
        return {name for text in texts for name in PLACEHOLDER.findall(text)}

    def test_english_exists_and_every_catalog_has_meta(self):
        self.assertIn("en", self.catalogs)
        for code, catalog in self.catalogs.items():
            meta = catalog.get("_meta", {})
            self.assertTrue(meta.get("name") and meta.get("english_name"), code)

    def test_no_catalog_has_keys_english_lacks(self):
        english = set(self.catalogs["en"])
        for code, catalog in self.catalogs.items():
            self.assertEqual(set(catalog) - english, set(), code)

    def test_translations_use_the_same_placeholders(self):
        english = self.catalogs["en"]
        for code, catalog in self.catalogs.items():
            for key, value in catalog.items():
                if key.startswith("_"):
                    continue
                self.assertEqual(self.placeholders(value), self.placeholders(english[key]), f"{code}:{key}")

    def test_plural_dicts_have_other(self):
        for code, catalog in self.catalogs.items():
            for key, value in catalog.items():
                if isinstance(value, dict) and not key.startswith("_"):
                    self.assertIn("other", value, f"{code}:{key}")
                    self.assertLessEqual(set(value), {"zero", "one", "two", "few", "many", "other"}, f"{code}:{key}")

    def test_every_literal_key_used_in_code_exists_in_english(self):
        english = set(self.catalogs["en"])
        call = re.compile(r"""\b(?:t|msg)\(\s*["']([a-z0-9_.]+)["']""")
        mapping_value = re.compile(r""":\s*["']((?:notify|diag|tweak|watchdog|ui|time|server)\.[a-z0-9_.]+)["']""")
        used = set()
        for path in (ROOT / "app").rglob("*.py"):
            if path.name == "i18n.py":   # its docstring shows example keys
                continue
            text = path.read_text(encoding="utf-8")
            used |= set(call.findall(text)) | set(mapping_value.findall(text))
        self.assertTrue(used)
        self.assertEqual(used - english, set())

    def test_keys_built_at_runtime_exist(self):
        from app import notify
        english = set(self.catalogs["en"])
        for kind in notify.KNOWN_OUTAGES:
            for part in ("title", "body"):
                self.assertIn(f"notify.outage.{kind}.{part}", english)
        for key in notify.WATCHDOG_TOASTS.values():
            self.assertIn(key, english)


if __name__ == "__main__":
    unittest.main()
