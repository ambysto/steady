# Translating and reviewing the UI catalogs

Every text the user sees lives in `app/locales/<language>.json` ([ADR-0006](adr/0006-i18n.md)); English (`en.json`) is the source. The Windows app reads these files directly. The Apple app gets String Catalogs generated from them (`scripts/locales_to_xcstrings.py`, ADR-0010). Keys starting with `apple.` are the iPhone, iPad and Mac wording of the key that follows the prefix.

## Adding a key

1. Add it to `en.json` and to every other catalog. A machine translation is fine as a first draft.
2. Run `python scripts/locales_to_xcstrings.py` if the Apple app uses the key.
3. The tests check that every catalog has the same keys and the same `{placeholders}` as English.

## Having translations reviewed

`scripts/translation_review.py` turns the catalogs into one spreadsheet per language and back:

```bash
python scripts/translation_review.py export --since <git ref> --out build/translation-review
```

`--since` limits the sheets to the keys added since that commit; `--keys ui.report. apple.` limits them by prefix; without either, every key is listed.

Each `<language>.csv` (UTF-8, opens in Excel, Numbers or Google Sheets) has one row per text, with one row per plural form ("one", "other"). The columns:

- **english**: the source text.
- **translation**: the current text.
- **placeholders**: what must stay in the text unchanged, such as `{count}`.
- **context**: where the text is shown.
- **comment**: notes for the reviewer.

The reviewer writes a corrected text in **suggestion**, and leaves it empty when the translation is right.

Spreadsheet tips:
- **Excel in a language that separates CSV columns with ";"** (German, French…) may put everything in column A. Use Data > From Text/CSV to open the sheet instead.
- **Save as "CSV UTF-8".** Sheets saved with ";" or tabs are read as well.
- Leading and trailing spaces in a suggestion do not matter: the English text's are kept, which matters for texts joined to others, such as " · last 5 minutes".

Then:

```bash
python scripts/translation_review.py apply build/translation-review/de.csv build/translation-review/fr.csv
python scripts/locales_to_xcstrings.py
```

`apply` refuses a sheet with an unknown key or a suggestion whose placeholders differ from English, and changes nothing in that case. Otherwise it rewrites only the lines of the changed keys, so the catalogs keep their order and line endings.

## Review status

- **Apple app keys (SIC-65):** the 63 keys added since the Apple app started (`--since 00bbcdc^`) were machine-translated. Then they were checked against the reviewed Windows wording: the terms for packet loss, the counters and "report" were made consistent. A native speaker of each language still has to review them.
