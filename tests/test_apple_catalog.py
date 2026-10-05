"""The Apple String Catalogs are generated from app/locales (ADR-0010) and must not go stale."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "locales_to_xcstrings.py"
CATALOG = ROOT / "apple" / "Steady" / "Resources" / "Localizable.xcstrings"


class AppleCatalogTests(unittest.TestCase):
    def test_generated_files_are_up_to_date(self):
        run = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_plural_entries_select_on_count(self):
        entry = json.loads(CATALOG.read_text(encoding="utf-8"))["strings"]["time.seconds"]["localizations"]
        self.assertEqual(entry["en"]["stringUnit"]["value"], "%#@count@")
        forms = entry["en"]["substitutions"]["count"]["variations"]["plural"]
        self.assertEqual(forms["one"]["stringUnit"]["value"], "%arg second")
        self.assertEqual(set(entry["vi"]["substitutions"]["count"]["variations"]["plural"]), {"other"})

    def test_placeholders_become_positional_and_percent_is_escaped(self):
        strings = json.loads(CATALOG.read_text(encoding="utf-8"))["strings"]
        self.assertEqual(strings["diag.common.network_signal"]["localizations"]["en"]["stringUnit"]["value"],
                         "  %1$@ — %2$@%%")


class AppleVariantTests(unittest.TestCase):
    """apple.<key> replaces <key> in the Apple app; it must keep the key's parameters (ADR-0010)."""

    def test_every_variant_replaces_an_existing_key_with_the_same_parameters(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("locales_to_xcstrings", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        catalogs = module.load_catalogs()
        module.check_variants(catalogs)   # raises SystemExit on a mismatch
        broken = {"en": {**catalogs["en"], "apple.diag.signal.good": "Good ({dbm} dBm)"}}
        with self.assertRaises(SystemExit):
            module.check_variants(broken)

    def test_variants_never_mention_a_pc(self):
        for path in sorted((ROOT / "app" / "locales").glob("*.json")):
            catalog = json.loads(path.read_text(encoding="utf-8"))
            for key, value in catalog.items():
                if key.startswith("apple."):
                    self.assertNotRegex(str(value), r"\bPC\b|电脑|monitor\b", f"{path.name}: {key}")


if __name__ == "__main__":
    unittest.main()
