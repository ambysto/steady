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


if __name__ == "__main__":
    unittest.main()
