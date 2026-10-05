"""Project rule: identifiers are English. No Vietnamese names - with or without diacritics -
for functions, variables, classes, parameters, settings keys or translation keys.
User-visible text belongs in app/locales/*.json (ADR-0006), never in names."""
import io
import json
import re
import tokenize
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Unaccented Vietnamese syllables that are not also ordinary English / technical words.
VIETNAMESE = set("""
tieu noi dung mang ket loi chan doan kiem thong bao canh cai dat nguoi lieu gio ngay tuan thang
hien trang thai uu giam khoi phuc luu ghi nhat duong dan nhanh cham manh yeu xau moi cuoi tien
truoc trong ngoai tren duoi gui nhan chay bieu tuong giao dien chinh phu khac nhau giong phan mem
cung quyen quan tri khach muc thoi gian luong toc tre goi thiet xoa sua them bot chon huy xac
nghiem tham gia mac dinh hinh tep thu
""".split())


def _words(name: str) -> list[str]:
    return [w.lower() for w in re.split(r"[_\-.]|(?<=[a-z])(?=[A-Z])", name) if w]


def _has_diacritic(name: str) -> bool:
    return "đ" in name.lower() or any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", name))


def looks_vietnamese(name: str) -> bool:
    if _has_diacritic(name):
        return True
    words = _words(name)
    hits = [w for w in words if w in VIETNAMESE]
    return bool(hits) and len(hits) * 2 >= len(words)   # at least half the words are Vietnamese


def python_names(path: Path):
    for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if tok.type == tokenize.NAME:
            yield tok.start[0], tok.string


def script_names(path: Path):
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        for var, func in re.findall(r"\$([A-Za-z_]\w*)|function\s+([\w-]+)", line, re.I):
            yield i, var or func


class DetectorTests(unittest.TestCase):
    """The scanner must catch what the rule forbids, or a clean result means nothing."""

    def test_catches_vietnamese_names(self):
        for bad in ("kiem_tra_mang", "ketNoi", "tốc_độ", "tocDo", "trang_thai", "luu_ket_qua", "Get-TrangThai"):
            self.assertTrue(looks_vietnamese(bad), bad)

    def test_accepts_english_names(self):
        for ok in ("check_network", "run_tick", "router_down", "probe_round", "tcp_timedwait", "do_GET",
                   "send_toast", "MinuteAggregator", "is_admin", "dry_run", "max_per_hour", "notify.back.title"):
            self.assertFalse(looks_vietnamese(ok), ok)


class NamingRuleTests(unittest.TestCase):
    def _report(self, hits):
        self.assertEqual(hits, [], "Vietnamese identifiers (rename to English):\n" +
                         "\n".join(f"  {p}:{line}: {name}" for p, line, name in hits))

    def test_python_identifiers_are_english(self):
        hits = []
        for path in sorted(list((ROOT / "app").rglob("*.py")) + list((ROOT / "tests").rglob("*.py"))
                           + list((ROOT / "scripts").rglob("*.py*"))):
            if path.name == "test_naming.py":
                continue  # this file lists Vietnamese words on purpose
            hits += [(str(path.relative_to(ROOT)), line, name) for line, name in python_names(path)
                     if looks_vietnamese(name)]
        self._report(hits)

    def test_powershell_and_vbs_identifiers_are_english(self):
        hits = []
        for path in sorted(list((ROOT / "scripts").rglob("*.ps1")) + list((ROOT / "scripts").rglob("*.vbs"))):
            hits += [(str(path.relative_to(ROOT)), line, name) for line, name in script_names(path)
                     if looks_vietnamese(name)]
        self._report(hits)

    def test_translation_and_settings_keys_are_english(self):
        from app import config
        hits = []
        for path in sorted((ROOT / "app" / "locales").glob("*.json")):
            hits += [(str(path.relative_to(ROOT)), 0, k) for k in json.loads(path.read_text(encoding="utf-8"))
                     if looks_vietnamese(k)]

        def walk(d, prefix=""):
            for k, v in d.items():
                if looks_vietnamese(k):
                    hits.append(("app/config.py DEFAULT_SETTINGS", 0, prefix + k))
                if isinstance(v, dict):
                    walk(v, prefix + k + ".")
        walk(config.DEFAULT_SETTINGS)
        self._report(hits)


if __name__ == "__main__":
    unittest.main()


# Letters that only Vietnamese uses (á, é... are left out: French, Spanish and others share them).
VIETNAMESE_LETTERS = set("ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")


class UserTextTests(unittest.TestCase):
    """User-visible text lives in app/locales/*.json (ADR-0006), not in Python string literals."""

    def test_no_vietnamese_text_in_app_code(self):
        import ast
        offenders = []
        for path in sorted((ROOT / "app").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if VIETNAMESE_LETTERS & set(node.value.lower()):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value[:50]!r}")
        self.assertEqual(offenders, [])

    def test_detector_catches_vietnamese(self):
        self.assertTrue(VIETNAMESE_LETTERS & set("không tìm thấy card"))
        self.assertFalse(VIETNAMESE_LETTERS & set("Café — résumé · naïve"))
