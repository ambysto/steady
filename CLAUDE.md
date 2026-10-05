# CLAUDE.md

Guidance for AI agents working in this repo.

## Project

**Ambysto Steady** (display name, key `app.name`; company Ambysto) — a Windows tool that monitors, automatically recovers and optimizes the network connection. `StableInternet` is now only the internal name (repo, package, data folder, mutex). Python backend, web UI at `127.0.0.1`. Read [README.md](README.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing code.

## Mandatory rules

- **Do not apply tweaks to the real machine on your own** during development/testing. Only run read operations; write operations (Set-NetAdapterAdvancedProperty, powercfg /set…, registry writes, Restart-NetAdapter) run only when the user explicitly agrees.
- Every new tweak must be described in [docs/TWEAKS.md](docs/TWEAKS.md) before writing code.
- New architectural decisions → add an ADR in `docs/adr/`.
- Update `CHANGELOG.md` when changing behavior.
- **Names in code are always in English**: functions, variables, classes, parameters, config keys, translation keys, PowerShell variables. Do not use Vietnamese, not even Vietnamese without diacritics (`kiem_tra_mang`, `ketNoi` are both wrong). `tests/test_naming.py` blocks this automatically.
- User-visible text is not hard-coded in code: it goes through `app/locales/*.json` (English by default) — see [ADR-0006](docs/adr/0006-i18n.md).
- All documentation (README, CHANGELOG, `docs/`, ADRs, CLAUDE.md) is written in English; only the user-facing UI catalogs in `app/locales` are translated.

## Git and publishing

- Commits and annotated tags are authored `Ambysto <283515636+ambysto@users.noreply.github.com>` in UTC. Set this per clone (`git config user.name`/`user.email`) and use `TZ=UTC git commit` / `TZ=UTC git tag -a` (aliases `git ci`, `git tag-utc`). Never commit with a machine's global identity.
- Push only as the `ambysto` GitHub account over HTTPS: remote `https://ambysto@github.com/ambysto/steady.git`. Do not change the machine's default GitHub CLI account; for `gh` commands on this repo use `GH_TOKEN=$(gh auth token --user ambysto) gh …`.
- Tracked files never contain personal names, personal email addresses, local user paths, or real network identifiers (Wi‑Fi SSIDs, BSSIDs/MAC addresses, public IP addresses). Use placeholders such as `HomeNet`, `02:5e:00:9a:40:24`, `203.0.113.10`.
- Commit messages, release notes, issues and pull requests are in English.

## Platforms

- Windows: this Python app (`app/`, `web/`), released by `.github/workflows/release.yml` on `v*` tags.
- Apple (iPhone, iPad, macOS) and Android: native apps per [ADR-0009](docs/adr/0009-native-apple-and-android-apps.md) — SwiftUI multiplatform first, then Kotlin + Jetpack Compose. They share the UI catalogs (`app/locales`) and the diagnosis rules' test vectors as data, not code. Read [docs/MOBILE.md](docs/MOBILE.md) before starting.

## Development machine environment (Windows)

- Windows 11 Pro, PowerShell 5.1 (no `pwsh`), Python 3.11, Node 26.
- PowerShell 5.1: no `&&`, no `Test-Connection -TargetName`; use `System.Net.NetworkInformation.Ping` or ICMP via ctypes.
- `netsh` output is in English, OEM-encoded.

## Useful commands (read-only)

```powershell
netsh wlan show interfaces
Get-NetAdapterAdvancedProperty -Name 'Wi-Fi'
powercfg /q SCHEME_CURRENT 501a4d13-42af-4429-9fd1-a8218c268e20 ee12f906-d277-404b-b6da-e5fa1a576df5
pnputil /enum-drivers /class Net
```
