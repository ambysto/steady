"""Review sheets for the UI catalogs (app/locales, ADR-0006).

`export` writes one CSV per language: each key's English text, the current translation, where the
text is shown and which placeholders it must keep. A reviewer fills in the "suggestion" column
(and "comment" if useful) in any spreadsheet. `apply` writes the suggestions back into
app/locales/<lang>.json, line by line so the files keep their order and line endings, after
checking every suggestion keeps exactly the English placeholders.

    python scripts/translation_review.py export --since 00bbcdc^ --out review/
    python scripts/translation_review.py apply review/de.csv review/fr.csv
    python scripts/locales_to_xcstrings.py      # then regenerate the Apple catalogs

Plural keys get one row per form ("one", "other"). Keys whose English text is a product name or a
unit only ("Wi-Fi", "{value} ms") are still listed: a reviewer may confirm them as they are.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "app" / "locales"
DEFAULT_LANGUAGE = "en"
COLUMNS = ("key", "form", "english", "translation", "suggestion", "comment", "placeholders", "context")
PLACEHOLDER = re.compile(r"\{(\w+)(?::[^{}]*)?\}")

# Where a key's text appears, by prefix (the first match wins). Helps a reviewer pick the tone
# and length: a tab name must stay short, a permission prompt is read once.
CONTEXT = (
    ("apple.", "iPhone, iPad and Mac wording of {base}: shown there instead of it"),
    ("ui.nav.", "Tab or sidebar name: keep it short"),
    ("ui.path.via.", "Overview, connection card: how the device is connected"),
    ("ui.path.reason.", "Overview, connection card: why there is no connection"),
    ("ui.path.", "Overview (iPhone, iPad, Mac): the connection card and its details"),
    ("ui.live.", "Overview (iPhone, iPad, Mac): live latency rows and their footnote"),
    ("ui.permission.", "System prompt on iPhone, iPad and Mac asking for local network access"),
    ("ui.overview.", "Overview (iPhone, iPad, Mac)"),
    ("ui.diag.count.", "Overview summary line, such as \"1 to note · 3 OK\""),
    ("ui.history.", "History tab (iPhone, iPad, Mac): charts of latency and loss"),
    ("ui.settings.", "Settings tab (iPhone, iPad, Mac)"),
    ("ui.report.", "Settings > Report a problem (iPhone, iPad, Mac)"),
    ("diag.", "Diagnostics: a check's result (Windows and Apple)"),
    ("ui.", "App window"),
    ("app.", "Product name"),
)


def load(lang: str, locales: Path = LOCALES) -> dict:
    return json.loads((locales / f"{lang}.json").read_text(encoding="utf-8"))


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def context_of(key: str) -> str:
    for prefix, text in CONTEXT:
        if key.startswith(prefix):
            return text.replace("{base}", key[len("apple."):]) if prefix == "apple." else text
    return ""


def keys_since(ref: str, locales: Path = LOCALES) -> list[str]:
    """Keys of the English catalog that were not in it at `ref`, in catalog order."""
    path = (locales / f"{DEFAULT_LANGUAGE}.json").resolve().relative_to(ROOT).as_posix()
    old = json.loads(subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{path}"],
                                    capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    return [k for k in load(DEFAULT_LANGUAGE, locales) if k not in old]


def rows(lang: str, keys: list[str], locales: Path = LOCALES) -> list[dict[str, str]]:
    english, catalog = load(DEFAULT_LANGUAGE, locales), load(lang, locales)
    out = []
    for key in keys:
        source, current = english[key], catalog.get(key, "")
        forms = list(source) if isinstance(source, dict) else [""]
        for form in forms:
            text = source[form] if form else source
            have = (current.get(form, "") if isinstance(current, dict) else "") if form else current
            out.append({"key": key, "form": form, "english": text, "translation": have if isinstance(have, str) else "",
                        "suggestion": "", "comment": "",
                        "placeholders": " ".join("{%s}" % p for p in sorted(placeholders(text))),
                        "context": context_of(key)})
    return out


def export(langs: list[str], keys: list[str], out: Path, locales: Path = LOCALES) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for lang in langs:
        path = out / f"{lang}.csv"
        # UTF-8 with a BOM, so spreadsheet apps read the accents and CJK text correctly.
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows(lang, keys, locales))
        written.append(path)
    return written


def read_suggestions(path: Path) -> dict[str, dict[str, str]]:
    """key -> {form: suggestion}; form is "" for a plain key."""
    found: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            text = (row.get("suggestion") or "").strip()
            if text:
                found.setdefault(row["key"], {})[row.get("form") or ""] = text
    return found


def check(lang: str, suggestions: dict[str, dict[str, str]], locales: Path = LOCALES) -> list[str]:
    """Problems that stop `apply`: unknown keys or forms, placeholders that differ from English."""
    english, problems = load(DEFAULT_LANGUAGE, locales), []
    for key, forms in suggestions.items():
        if key not in english:
            problems.append(f"{lang}: {key}: not in {DEFAULT_LANGUAGE}.json")
            continue
        source = english[key]
        for form, text in forms.items():
            if isinstance(source, dict) != bool(form) or (form and form not in source):
                problems.append(f"{lang}: {key}: form {form or '(none)'} does not match the English entry")
                continue
            wanted = placeholders(source[form] if form else source)
            if placeholders(text) != wanted:
                problems.append(f"{lang}: {key} {form}: placeholders {sorted(placeholders(text))} "
                                f"should be {sorted(wanted)}")
    return problems


def apply(lang: str, suggestions: dict[str, dict[str, str]], locales: Path = LOCALES) -> int:
    """Rewrites the lines of the suggested keys; returns how many keys changed."""
    path = locales / f"{lang}.json"
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)
    catalog = json.loads(raw)
    changed = 0
    for key, forms in suggestions.items():
        value = catalog.get(key)
        if isinstance(value, dict) or any(forms):
            value = dict(value) if isinstance(value, dict) else {}
            value.update(forms)
        else:
            value = forms[""]
        if value == catalog.get(key):
            continue
        prefix = f"  {json.dumps(key, ensure_ascii=False)}: "
        index = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
        if index is None:
            raise SystemExit(f"{lang}: {key} is missing from {path.name}; add it there first")
        comma = "," if lines[index].rstrip().endswith(",") else ""
        lines[index] = prefix + json.dumps(value, ensure_ascii=False) + comma
        changed += 1
    text = newline.join(lines)
    json.loads(text)   # still valid JSON
    path.write_bytes(text.encode("utf-8"))
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="write review sheets")
    exp.add_argument("--since", help="only keys added to en.json since this git ref")
    exp.add_argument("--keys", nargs="*", default=[], help="key prefixes to include")
    exp.add_argument("--lang", nargs="*", help="languages (default: every translation)")
    exp.add_argument("--out", type=Path, default=ROOT / "build" / "translation-review")
    app = sub.add_parser("apply", help="write the suggestions of review sheets named <lang>.csv")
    app.add_argument("sheets", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.command == "export":
        keys = keys_since(args.since) if args.since else list(load(DEFAULT_LANGUAGE))
        if args.keys:
            keys = [k for k in keys if k.startswith(tuple(args.keys))]
        langs = args.lang or sorted(p.stem for p in LOCALES.glob("*.json") if p.stem != DEFAULT_LANGUAGE)
        for path in export(langs, keys, args.out):
            print(f"wrote {path} ({len(keys)} keys)")
        return 0

    sheets = {sheet.stem: read_suggestions(sheet) for sheet in args.sheets}
    problems = [p for lang, found in sheets.items() for p in check(lang, found)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    for lang, found in sheets.items():
        print(f"{lang}: {apply(lang, found)} keys changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
