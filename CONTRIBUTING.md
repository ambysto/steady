# Contributing & conventions

## Core principles

1. **Never make a system change that cannot be restored.** Every tweak must save the original value to the backup store (`HKLM\SOFTWARE\Ambysto\Steady`, [ADR-0018](docs/adr/0018-backups-in-hklm.md)) *before* applying it, and must have a restore path for when there is no backup (back to the Windows/driver default).
2. **Read first, write later.** Every tweak must have an independent state-reading function with no side effects.
3. **Transparency.** Every system change and every watchdog action is recorded in the log.
4. **Works in read-only mode.** Without Admin rights the tool can still monitor and diagnose; only write operations are locked.

## Adding a new tweak

1. Add an entry to [docs/TWEAKS.md](docs/TWEAKS.md) first: purpose, target value, default value, risk, whether it interrupts the network / requires a restart, references.
2. Implement the class in `app/tweaks.py` with all 4 operations: `read`, `capture`, `apply`, `restore`.
3. A tweak that cannot be applied to the current hardware must return `supported: false` with a reason; it must not raise an error.
4. Risk classification:
   - `low` — widely recommended, practically no side effects.
   - `medium` — has trade-offs (performance, battery) or requires a restart.
   - `experimental` — should only be enabled to try it out, while tracking before/after metrics.

## Code conventions

- Python 3.11+, prefer the standard library (see [ADR-0001](docs/adr/0001-python-stdlib-web-ui.md)).
- Call PowerShell through the shared helper (`-EncodedCommand`, uniform error handling); do not build command strings yourself.
- Names of variables, functions, classes, parameters, config keys and translation keys, and comments in code, are **in English** — no Vietnamese, not even without diacritics. `tests/test_naming.py` checks this automatically.
- Text displayed to users (UI, diagnostics, toasts, log) goes through the translation catalogs `app/locales/<code>.json`, English by default ([ADR-0006](docs/adr/0006-i18n.md)). All documentation (README, CHANGELOG, `docs/`, ADRs, CLAUDE.md) is written in English; only the user-facing UI catalogs in `app/locales` are translated.

## Line endings

The repository stores **LF**, on Windows and on a Mac alike, and `.gitattributes` enforces it (only `.bat` and `.cmd` files are CRLF). Do not set `core.autocrlf` on any machine. If an editor or a script rewrites a whole file and `git diff` shows every line changed, restore the line endings with `git add --renormalize .` and check that `git diff --cached --ignore-space-at-eol` shows only the real change. When merging a branch that was started before this rule, use `git merge -X renormalize main`.

## Commits

Follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

Update the `[Unreleased]` section of `CHANGELOG.md` in the same commit as the change.

## Tests

Run `python -m unittest discover -s tests -t .` before opening a pull request. The same command runs on every pull request and every push to `main` (workflow `tests`, check name **Python tests (Windows)**), and a pull request is merged only when it is green. Run it once without `PYTHONDONTWRITEBYTECODE` set: with it set, no `.pyc` files are written and a test that trips over them passes on your machine and fails on CI.

## License and CLA

The source code is released under [GPL-3.0](LICENSE). Contributors sign the [CLA](CLA.md) once via CLA Assistant on their first pull request: you keep your copyright, and you allow Ambysto to manage the project's licensing in the future. Third-party libraries and their licenses are listed in `THIRD-PARTY-NOTICES.txt`, generated at build time.
