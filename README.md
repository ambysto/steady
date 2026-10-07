# Ambysto Steady

*Stable Internet Connection* — a product of Ambysto ([ambysto.com](https://ambysto.com)). The internal name of the repo, data folder and package is still `StableInternet`.

A Windows tool that helps **keep your Internet connection stable**: it continuously monitors link quality, automatically recovers when the connection drops, and provides system optimizations as **on/off toggles with restore** for safe experimentation.

> Status: **Windows version nearing completion** (monitor, diagnostics, optimizations with restore, watchdog, failover, desktop UI, packaged build). See [docs/ROADMAP.md](docs/ROADMAP.md), [docs/OPEN-QUESTIONS.md](docs/OPEN-QUESTIONS.md) and [CHANGELOG.md](CHANGELOG.md).

## Why this tool?

Software cannot make the Wi‑Fi signal stronger, but it does 3 things well:

1. **Measure and record** — know exactly whether the fault lies with the PC, the router or the Internet service provider (ISP), with data to compare before/after each change.
2. **Self-recovery** — detect a hung driver / lost connection and handle it within seconds instead of waiting for Windows or acting manually.
3. **Controlled optimization** — each tweak is a toggle, the original value is saved before applying, and turning it off puts everything back as it was.

## Planned features

| Group | Description |
|---|---|
| Overview | Realtime ping / packet loss / jitter to the router and the Internet, Wi‑Fi signal, 24h history |
| Optimize | Toggles: adapter power saving, PCIe ASPM, wake-on-LAN, TCP, IPv6… — see [docs/TWEAKS.md](docs/TWEAKS.md) |
| Diagnostics | Drivers & versions available for rollback, channel interference, connection drop history, DNS benchmark… — see [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md) |
| Watchdog | Automatically reconnects / restarts the network adapter when a problem is detected — see [docs/WATCHDOG.md](docs/WATCHDOG.md) |
| Log | Every incident, optimization change and watchdog action |

## Requirements

- Windows 10/11
- Python 3.11+
- Administrator rights to apply optimizations (read-only mode can still monitor)

## Directory structure

```
StableInternet/
├── README.md             Project overview (this file)
├── CHANGELOG.md          Change history by version
├── CONTRIBUTING.md       Code and commit conventions, adding new tweaks
├── CLAUDE.md             Guidance for AI agents working in the repo
├── docs/
│   ├── ARCHITECTURE.md   Architecture, modules, API, data flow
│   ├── UI-DESIGN.md      UI direction (draft)
│   ├── TWEAKS.md         Toggle catalog: values, risks, how to restore
│   ├── DIAGNOSTICS.md    Diagnostic check catalog and evaluation thresholds
│   ├── WATCHDOG.md       Self-recovery logic and safety limits
│   ├── SECURITY.md       Security model (server running with Admin rights)
│   ├── BASELINE.md       Measurements of the PC's current state before optimizing
│   ├── EXPERIMENT-LOG.md Log of configuration changes and observed results
│   ├── ROADMAP.md        Phased plan
│   ├── OPEN-QUESTIONS.md Points to discuss / decide
│   └── adr/              Architecture Decision Records
├── app/                  Python source code (backend)
├── web/                  UI (HTML/CSS/JS)
├── apple/                iPhone, iPad and Mac app (SwiftUI), see apple/README.md
├── spec/diagnosis/       Diagnosis test vectors shared by every platform
├── scripts/              Utility scripts (launcher, installation)
├── tests/                Tests
└── data/                 Runtime data (not committed): settings, metrics
```

## How to run

Only the monitoring part exists so far (read-only, no Admin needed). Run the monitor in the foreground and stop it with Ctrl+C:

```powershell
python -m app.monitor
```

**Packaged build (no Python installation needed):** `pip install -r requirements.txt pyinstaller==6.22.3` then `python scripts/build.py [--smoke]` → `dist/Ambysto Steady/` and `dist/StableInternetConnection-<version>-win64.zip`. Users unzip it and double-click `Ambysto Steady.exe` → the app offers to install for all users of the PC (one Administrator approval): it copies itself to `Program Files`, the monitor runs with Windows for that user, a Start menu shortcut + tray icon at sign-in, and it is listed in Apps & features. An earlier per-user copy (`%LOCALAPPDATA%\Programs`) is replaced; measurements, settings and backups carry over. Uninstall from Apps & features: restores every optimization to its original value, then removes the task, shortcuts and folder (one UAC prompt); asks whether to delete the data. Started from the unzipped folder, it always offers to install; `"Ambysto Steady.exe" desktop` runs it there without installing, to monitor and diagnose, but it does not change Windows settings from there ([ADR-0019](docs/adr/0019-per-machine-install.md)). Not yet code-signed, so SmartScreen will show a warning.

**App window + tray icon:** `pip install -r requirements.txt` (pywebview, pystray, Pillow) then double-click `scripts\desktop\run_desktop.pyw` (or `pythonw -m app.desktop`, add `--minimized` to show it only in the tray). Closing the window only hides it to the tray; the monitor runs separately and is not stopped.

Data is written to `data/metrics.db`. The monitor also serves the **UI** at `http://127.0.0.1:47613/` (accessible only from this machine; see `docs/SECURITY.md`): Overview, Optimize, Diagnostics, Log, Settings — 7 languages, light/dark, responsive down to phone size. There is no protection yet against running two instances at once (see SIC-12), so do not run it in parallel with `scripts/monitor/ping-logger.ps1` for long, as that would double the pings.

Run tests: `python -m unittest discover -s tests -t .`

## Documentation

Start from [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). All architectural decisions are recorded in [docs/adr/](docs/adr/).

## Code signing and privacy

Releases are **not code-signed** (this is a free project, and SignPath Foundation declined our first application for lack of public visibility), so Windows SmartScreen may warn on the first launch: choose **More info** → **Run anyway**.

- [Code signing policy](https://steady.ambysto.com/codesigningpolicy) (source: [CODE_SIGNING.md](CODE_SIGNING.md))
- [Privacy policy](https://steady.ambysto.com/privacy) (source: [PRIVACY.md](PRIVACY.md)) — no data collection; lists every connection the app makes.

## License

Copyright (C) 2026 Ambysto. Released under the **GNU General Public License v3.0**, see [LICENSE](LICENSE). Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md) and [CLA.md](CLA.md).
