# CLAUDE.md

Guidance for AI agents working in this repo.

## Project

**Ambysto Steady** (display name, key `app.name`; company Ambysto) — a Windows tool that monitors, automatically recovers and optimizes the network connection. `StableInternet` is now only the internal name (repo, package, data folder, mutex). Python backend, web UI at `127.0.0.1`. Read [README.md](README.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing code.

Since ADR-0009 there is also a native Apple app (iPhone, iPad, macOS; SwiftUI, `apple/`), with its own README, release notes and ADRs (0010 catalog rules, 0011, 0013 release, 0020, 0022 floating monitor, 0023 speed test). The two share data only: the UI catalogs in `app/locales` and the diagnosis test vectors in `spec/diagnosis`. Steady is one app of the Ambysto Pro Suite; it does not use Ambysto Kit yet. This repo is public.

## Mandatory rules

- **Do not apply tweaks to the real machine on your own** during development/testing. Only run read operations; write operations (Set-NetAdapterAdvancedProperty, powercfg /set…, registry writes, Restart-NetAdapter) run only when the user explicitly agrees.
- Every new tweak must be described in [docs/TWEAKS.md](docs/TWEAKS.md) before writing code.
- New architectural decisions → add an ADR in `docs/adr/`.
- Update `CHANGELOG.md` when changing behavior.
- **Names in code are always in English**: functions, variables, classes, parameters, config keys, translation keys, PowerShell variables. Do not use Vietnamese, not even Vietnamese without diacritics (`kiem_tra_mang`, `ketNoi` are both wrong). `tests/test_naming.py` blocks this automatically.
- User-visible text is not hard-coded in code: it goes through `app/locales/*.json` (English by default) — see [ADR-0006](docs/adr/0006-i18n.md). The Apple catalogs under `apple/` are generated from them by `scripts/locales_to_xcstrings.py` and are never edited by hand (ADR-0010).
- All documentation (README, CHANGELOG, `docs/`, ADRs, CLAUDE.md) is written in English; only the user-facing UI catalogs in `app/locales` are translated. `tests/test_docs_language.py` fails on Vietnamese letters in any `.md`, this file and the untracked `CLAUDE.local.md` included.

## Git and publishing

- Commits and annotated tags are authored `Ambysto <283515636+ambysto@users.noreply.github.com>` in UTC. Set this per clone (`git config user.name`/`user.email`) and use `TZ=UTC git commit` / `TZ=UTC git tag -a` (aliases `git ci`, `git tag-utc`; `apple/scripts/release.sh` expects `git tag-utc`, so define the aliases per clone where they are missing). Never commit with a machine's global identity.
- Push only as the `ambysto` GitHub account over HTTPS: remote `https://ambysto@github.com/ambysto/steady.git`. Do not change the machine's default GitHub CLI account; for `gh` commands on this repo use `GH_TOKEN=$(gh auth token --user ambysto) gh …`.
- Tracked files never contain personal names, personal email addresses, local user paths, or real network identifiers (Wi‑Fi SSIDs, BSSIDs/MAC addresses, public IP addresses). Use placeholders such as `HomeNet`, `02:5e:00:9a:40:24`, `203.0.113.10`. No team id and no App Store Connect key is ever tracked (`apple/RELEASE.md`); the app id `com.ambysto.steady` is never registered under a personal team.
- Commit messages, release notes, issues and pull requests are in English. Conventional Commits (`feat(apple):`, `fix:`, `docs:`…), the Co-Authored-By trailer, `[Unreleased]` in `CHANGELOG.md` in the same commit; branches `claude/<slug>`, `fix/sic-NN-<slug>`, `release/x.y.z`; PRs merge by merge commit; LF line endings everywhere (CONTRIBUTING.md). Do not merge without being asked.
- A version bump is `CHANGELOG.md` plus `app/__init__.py` (Windows) or `apple/Config/Base.xcconfig` (Apple). Tags: `v<version>` fires the Windows release workflow; the Apple tag is `apple-v<version>-<build>` (ADR-0013) and is never a `v*`. Tags are the owner's call. The next ADR number comes from `docs/adr/README.md` (update the index) after checking the remote branches, which may already use one.
- Intent work: `intent/<date>-<slug>.md` plus `.plan.md`, status `draft → approved → planned → building → verified → shipped (PR #n)`; building starts after the owner approves the intent and accepts the plan.
- Tickets live in the owner's Plane project **SIC** (ticket names in Vietnamese). This repo is public, so the tracker's host, workspace and project id are in the gitignored `CLAUDE.local.md` on the owner's machines, never here. Cross-link GitHub and Plane both ways.

## Platforms

- Windows: this Python app (`app/`, `web/`), released by `.github/workflows/release.yml` on `v*` tags.
- Apple (iPhone, iPad, macOS) and Android: native apps per [ADR-0009](docs/adr/0009-native-apple-and-android-apps.md) — SwiftUI multiplatform first, then Kotlin + Jetpack Compose. They share the UI catalogs (`app/locales`) and the diagnosis rules' test vectors as data, not code. Read [docs/MOBILE.md](docs/MOBILE.md) before starting.

## Sessions by platform

The owner runs separate sessions per platform. A macOS session works on `apple/`, the shared catalogs (`app/locales`, `spec/diagnosis`) and docs; it never changes Windows code, never bumps `app/__init__.py` and never pushes a `v*` tag. A Windows session works on `app/`, `web/`, the catalogs and docs; when it changes a `ui.` or `diag.` key it also regenerates the Apple catalogs with `scripts/locales_to_xcstrings.py`, or CI's `test_apple_catalog` fails. A diagnosis-rule change crosses both platforms (the Python rule, the `spec/diagnosis` vectors and the Swift port change together, ADR-0010) and is done in one session with the owner's go-ahead. Ask which platform a session is for when it is unclear.

## Verify

- Windows app: `python -m unittest discover -s tests -t .` (CI runs it on Windows, Python 3.11). On a Mac about a third of the test modules need `winreg`; the catalog, naming, language and vector checks run anywhere: `python3 -m unittest tests.test_naming tests.test_docs_language tests.test_apple_catalog tests.test_diagnosis_vectors` (Python 3.10 or later; see `CLAUDE.local.md` for the interpreter on the owner's Mac).
- Apple app: `swift test --package-path apple/SteadyKit` (133 tests). There is no Xcode test target, so SwiftUI and AppKit changes are checked by building and by hand. Build: `xcodebuild -project apple/Steady.xcodeproj -scheme Steady -destination 'generic/platform=macOS' CODE_SIGNING_ALLOWED=NO build` (or `generic/platform=iOS`); CI never builds the Apple app. Xcode 27 or later (`apple/README.md`).
- Catalogs: `scripts/locales_to_xcstrings.py --check` is read-only; writing needs Python 3.10 or later and rewrites four generated files.
- Scripts with side effects: `apple/scripts/release.sh` (archives, provisions, and with `--upload` sends both platforms to App Store Connect; needs the team id, and the App Store Connect key for the upload; the owner runs it), `apple/AppStore/screenshots.sh` (boots simulators), `scripts/translation_review.py apply` (rewrites the locale files), `scripts/manual/*.ps1` (changes Windows adapters; never on a Mac).
- A real speed test moves up to about 1 GB per direction and Cloudflare answers 429/403 after a few runs per address (`docs/DIAGNOSTICS.md`): ask before each run. Accuracy is judged against the interface byte counters, not the browser's speed.cloudflare.com, which reads lower on fast lines.
- UI checks on a Mac: keep the display awake (`caffeinate -d` blocks on its own: wrap the command, or start it in the background and end that one process by its saved pid, never by a name pattern). The floating bar's Glass look sets `sharingType = .none`, so set `floating.look off` in the app's defaults before capturing it (a sandboxed build keeps its preferences in its container).

## Task rating (for the builder / reviewer / transcriber agents in `~/.claude/agents/`)

HARD, the main session does it directly and an independent reviewer checks it:
- Release, upload and tagging (`apple/RELEASE.md`, `apple/scripts/release.sh`, `.github/workflows/release.yml`, ADR-0013): credentials, irreversible.
- The speed-test engine (`apple/SteadyKit`, ADR-0023): real traffic, per-address limits, the byte-counter yardstick.
- Diagnosis rules: the Python rule, the `spec/diagnosis` vectors and the Swift port change together (ADR-0010).
- Anything that writes to a machine or elevates: the Apple app changes nothing by design (ADR-0020); Windows tweaks follow ADR-0003 (back up first, verify afterwards, roll back on failure; backups in HKLM, ADR-0018) and run only with the user's explicit agreement; the elevated helper and the server's token checks (ADR-0005, `SECURITY.md`); `scripts/manual/*.ps1`.
- A new network endpoint: `PRIVACY.md` and the App Store review notes (`apple/AppStore/metadata/review_information/notes.txt`) change with it; `PRIVACY.md` and `SECURITY.md` edits are HARD on their own.

MEDIUM, builder (Sonnet) builds and reviewer (Opus) reviews:
- SwiftUI and AppKit views (floating bar, dashboard, charts: not unit-tested), catalog keys with their xcstrings regeneration, code that comes with an ADR or a CHANGELOG entry, the non-release scripts and the test workflow.

EASY, transcriber (Haiku) or builder, no review:
- Docs other than `PRIVACY.md`, `SECURITY.md` and the release docs; the ADR index; intent status lines; CHANGELOG wording; `--check` runs (builder: the transcriber has no shell); screenshots once produced.

## Development machine environment (Windows)

- Windows 11 Pro, PowerShell 5.1 (no `pwsh`), Python 3.11, Node 26.
- PowerShell 5.1: no `&&`, no `Test-Connection -TargetName`; use `System.Net.NetworkInformation.Ping` or ICMP via ctypes.
- `netsh` output is in English, OEM-encoded.

## Development machine environment (Mac)

- Unsigned build: `xcodebuild … -derivedDataPath build/mac CODE_SIGNING_ALLOWED=NO build`. Sandboxed build without an Apple ID: `CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM=`. `build/` is gitignored.
- What is true of one particular Mac (interpreter paths, installed runtimes, missing tools or permissions, the GitHub token's scopes, the Plane setup) lives in the gitignored `CLAUDE.local.md`, not here.

## Useful commands (read-only)

```powershell
netsh wlan show interfaces
Get-NetAdapterAdvancedProperty -Name 'Wi-Fi'
powercfg /q SCHEME_CURRENT 501a4d13-42af-4429-9fd1-a8218c268e20 ee12f906-d277-404b-b6da-e5fa1a576df5
pnputil /enum-drivers /class Net
```

## Open

- TestFlight: waits for the organization's Apple Developer Program enrollment; the owner then runs `apple/scripts/release.sh --upload` (`apple/RELEASE.md`) and the `apple-v0.8.1-<build>` tag follows. Before that upload, `apple/Steady/PrivacyInfo.xcprivacy` must declare the required-reason API the app uses (UserDefaults in `AppModel.swift`), or App Store Connect refuses the build (ITMS-91053).
- Windows has no speed test yet (ADR-0023).
- `docs/DIAGNOSTICS.md` and `docs/TRANSLATING.md` carry their own open tickets; iPad Split View and the floating-bar handover are unverified (the speed-test intent).
