"""scripts/translation_review.py: review sheets out, reviewed text back into app/locales."""
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("translation_review", ROOT / "scripts" / "translation_review.py")
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)

ENGLISH = {
    "ui.nav.history": "History",
    "ui.live.rtt": "{value} ms",
    "ui.settings.history_minutes": {"one": "{count} minute stored", "other": "{count} minutes stored"},
    "apple.diag.ping.no_data": "Not enough measurements yet (needs ≥ {count} samples)",
    "diag.ping.recent": " · last 5 minutes: {loss:.2f}% lost",
}
GERMAN = {
    "ui.nav.history": "Verlauf",
    "ui.live.rtt": "{value} ms",
    "ui.settings.history_minutes": {"one": "{count} Minute gespeichert", "other": "{count} Minuten gespeichert"},
    "apple.diag.ping.no_data": "Noch zu wenige Messungen (mindestens {count})",
    "diag.ping.recent": " · letzte 5 Minuten: {loss:.2f}% verloren",
}


def catalog_text(entries: dict) -> str:
    """Like app/locales: one key per line, CRLF line endings."""
    lines = [f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}" for k, v in entries.items()]
    return "{\r\n" + ",\r\n".join(lines) + "\r\n}\r\n"


class TranslationReviewTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.locales = Path(self.dir.name) / "locales"
        self.locales.mkdir()
        (self.locales / "en.json").write_bytes(catalog_text(ENGLISH).encode())
        (self.locales / "de.json").write_bytes(catalog_text(GERMAN).encode())

    def tearDown(self):
        self.dir.cleanup()

    def sheet(self, keys):
        path = review.export(["de"], keys, Path(self.dir.name) / "out", self.locales)[0]
        with path.open(encoding="utf-8-sig", newline="") as f:
            return path, list(csv.DictReader(f))

    def write(self, path, rows):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=review.COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def test_sheet_has_one_row_per_plural_form_with_context_and_placeholders(self):
        _, rows = self.sheet(list(ENGLISH))
        self.assertEqual([(r["key"], r["form"]) for r in rows],
                         [("ui.nav.history", ""), ("ui.live.rtt", ""), ("ui.settings.history_minutes", "one"),
                          ("ui.settings.history_minutes", "other"), ("apple.diag.ping.no_data", ""),
                          ("diag.ping.recent", "")])
        plural = rows[3]
        self.assertEqual((plural["english"], plural["translation"], plural["placeholders"]),
                         ("{count} minutes stored", "{count} Minuten gespeichert", "{count}"))
        self.assertIn("short", rows[0]["context"])
        self.assertIn("wording of diag.ping.no_data", rows[4]["context"])

    def test_suggestions_go_back_into_the_catalog_keeping_its_lines(self):
        path, rows = self.sheet(list(ENGLISH))
        rows[0]["suggestion"] = "Verlauf "                       # the same text: no change
        rows[3]["suggestion"] = "{count} Minuten sind gespeichert"
        rows[4]["suggestion"] = "Noch nicht genug Messungen (mindestens {count} nötig)"
        self.write(path, rows)
        found = review.read_suggestions(path)
        self.assertEqual(review.check("de", found, self.locales), [])
        self.assertEqual(review.apply("de", found, self.locales), 2)
        raw = (self.locales / "de.json").read_bytes().decode()
        self.assertNotIn("\n", raw.replace("\r\n", ""))           # still CRLF only
        catalog = json.loads(raw)
        self.assertEqual(catalog["ui.settings.history_minutes"],
                         {"one": "{count} Minute gespeichert", "other": "{count} Minuten sind gespeichert"})
        self.assertEqual(catalog["apple.diag.ping.no_data"], "Noch nicht genug Messungen (mindestens {count} nötig)")
        self.assertEqual(list(catalog), list(GERMAN))

    def test_a_suggestion_must_keep_the_english_placeholders(self):
        path, rows = self.sheet(["ui.live.rtt", "ui.settings.history_minutes"])
        rows[0]["suggestion"] = "{wert} ms"
        rows[1]["suggestion"] = "eine Minute gespeichert"
        self.write(path, rows)
        problems = review.check("de", review.read_suggestions(path), self.locales)
        self.assertEqual(len(problems), 2)
        self.assertIn("ui.live.rtt", problems[0])
        self.assertIn("should be ['count']", problems[1])

    def test_a_text_joined_to_another_keeps_its_leading_space(self):
        path, rows = self.sheet(["diag.ping.recent"])
        rows[0]["suggestion"] = "· in den letzten 5 Minuten: {loss:.2f}% verloren  "   # the spreadsheet trimmed it
        self.write(path, rows)
        review.apply("de", review.read_suggestions(path), self.locales)
        catalog = json.loads((self.locales / "de.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["diag.ping.recent"], " · in den letzten 5 Minuten: {loss:.2f}% verloren")

    def test_sheets_saved_with_semicolons_are_read(self):
        path, rows = self.sheet(["ui.nav.history"])
        rows[0]["suggestion"] = "Verlauf; Messungen"
        with path.open("w", encoding="utf-8-sig", newline="") as f:   # as Excel saves it in German
            writer = csv.DictWriter(f, fieldnames=review.COLUMNS, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        self.assertEqual(review.read_suggestions(path), {"ui.nav.history": {"": "Verlauf; Messungen"}})

    def test_a_sheet_that_cannot_be_read_stops_with_a_message(self):
        path = Path(self.dir.name) / "de.csv"
        path.write_bytes("key,form,suggestion\nui.nav.history,,Übersicht\n".encode("latin-1"))
        with self.assertRaises(SystemExit) as stop:
            review.read_suggestions(path)
        self.assertIn("CSV UTF-8", str(stop.exception))
        path.write_text("Schlüssel;Vorschlag\nui.nav.history;Verlauf\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as stop:
            review.read_suggestions(path)
        self.assertIn("columns", str(stop.exception))

    def test_unknown_keys_and_forms_are_refused(self):
        problems = review.check("de", {"ui.no.such.key": {"": "x"}, "ui.nav.history": {"one": "x"}}, self.locales)
        self.assertEqual(len(problems), 2)


if __name__ == "__main__":
    unittest.main()
