"""All documentation is written in English (README, CHANGELOG, docs/, ADRs, CLAUDE.md...).
Only the UI catalogs in app/locales are translated."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"data", "dist", "build", ".claude", ".git", "node_modules", "__pycache__"}
# Letters that only Vietnamese uses among the languages around here: ă đ ơ ư and the precomposed
# letters with tone marks (Latin Extended Additional U+1EA0-U+1EF9).
VIETNAMESE = re.compile("[ĂăĐđƠơƯưẠ-ỹ]")


def documents() -> list[Path]:
    return [p for p in ROOT.rglob("*.md") if not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


class DocsLanguageTests(unittest.TestCase):
    def test_documentation_is_in_english(self):
        offenders = []
        for path in documents():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if VIETNAMESE.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:80]}")
                    break
        self.assertEqual(offenders, [], "documentation must be in English")

    def test_the_check_recognises_vietnamese(self):
        self.assertTrue(VIETNAMESE.search("kết nối"))          # "connection"
        self.assertTrue(VIETNAMESE.search("đường"))       # "path"
        self.assertIsNone(VIETNAMESE.search("Français, Português, café, naïve"))


if __name__ == "__main__":
    unittest.main()
