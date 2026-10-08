# Architecture

> Preliminary design — will be updated according to the decisions in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).

## Overview

```
┌──────────────────────────┐        HTTP + token        ┌────────────────────────────────┐
│  UI (web/)                │ ─────────────────────────▶ │  Backend Python (app/)          │
│  Overview · Optimize ·    │ ◀───────── JSON ────────── │  unelevated, 127.0.0.1          │
│  Diagnostics · Log        │                            │                                │
└──────────────────────────┘                            │  server ─┬─ monitor ── watchdog │
                                                        │          ├─ tweaks              │
                                                        │          ├─ diagnostics         │
                                                        │          └─ storage (SQLite)    │
                                                        └───────────────┬────────────────┘
                                                                        │ ICMP (ctypes), DNS (UDP),
                                                                        │ PowerShell, netsh, powercfg,
                                                                        ▼ registry (winreg)
                                                                     Windows
```

## Backend modules (`app/`)

| Module | Responsibility |
|---|---|
| `config.py` | Paths, `settings.json`, the backup lock; `load_backup` / `save_backup` go to `backupstore` |
| `backupstore.py` | The backups (original values before a tweak, failover metrics) in `HKLM\SOFTWARE\Ambysto\Steady`, readable by everyone, writable only elevated; imports the old `backup.json` once ([ADR-0018](adr/0018-backups-in-hklm.md)) |
| `winutil.py` | Helpers for calling PowerShell (`-EncodedCommand`, returns JSON), `netsh`, Admin check, detection of the Wi‑Fi card and the main network path (uplink) |
| `icmp.py` | Ping via `IcmpSendEcho` (iphlpapi, ctypes) — no Admin needed, no process spawned; optional payload size and "do not fragment" flag |
| `pmtu.py` | Path MTU: binary search with "do not fragment" pings, pure over an injected send function (diagnostic #16) |
| `dnsprobe.py` | UDP DNS queries with hand-built packets to benchmark each DNS server |
| `storage.py` | SQLite: per-minute statistics, Wi‑Fi signal, events; cleanup of old data |
| `monitor.py` | Ping thread per target, reads Wi‑Fi state, detects incidents, aggregates per-minute statistics |
| `watchdog.py` | Automatic recovery when an incident persists — see [WATCHDOG.md](WATCHDOG.md) |
| `tweaks.py` | Toggle catalog and the `read / capture / apply / restore` framework — see [TWEAKS.md](TWEAKS.md) |
| `calibration.py` | The measurement behind a measured tweak's value: validation, network id, staleness, the before/after verdict ([ADR-0016](adr/0016-measured-tweaks.md)) |
| `diagnostics.py` | Diagnostic checks — see [DIAGNOSTICS.md](DIAGNOSTICS.md) |
| `actions.py` | One-off actions: reconnect Wi‑Fi, restart the card, flush DNS, renew DHCP |
| `probe.py` | TCP:443 / HTTP 204 probes, independent of ICMP — confirm "Internet is up" when ping is restricted |
| `notify.py` | Windows toasts on connection loss (after 30s) / when the watchdog intervenes, disables itself, or hits an error; rate-limited; runs in the background |
| `singleton.py` | Prevents two monitors running on the same data directory (named mutex) |
| `autostart.py` | Task Scheduler task that starts the monitor at logon (created from XML) |
| `winsys.py` | The only layer allowed to write to the machine (card properties, HKLM, powercfg, binding, the tool's QoS policy) |
| `elevated.py`, `elevation.py` | Child process running as Admin via UAC for write operations — ADR-0005 |
| `server.py` | HTTP server, API routing, serves `web/` |
| `desktop.py` | Desktop shell (ADR-0002): a pywebview window using the native Windows frame (Snap Layouts, resizing, title bar painted the same color as the page background) around the UI + tray icon (pystray). It is only a viewer: closing the window = hiding to the tray, "Quit" only closes the shell, the monitor keeps running. The tray menu also toggles the floating monitor (`web/mini.html`): a small frameless window that stays on top; whether it is open and where it sits is kept in `%LOCALAPPDATA%\StableInternet\mini.json`, and its page may only call `hide_mini` back. Only this module needs `requirements.txt` |
| `traffic.py` | Read-only data for the floating monitor, no Admin: throughput of the interface Internet traffic takes (`GetIfEntry2` counters, sampled every second by a thread that runs only while `/api/traffic` is being asked for and stops 30 s after), apps with open connections (`GetExtendedTcpTable`/`UdpTable` → process, named from the executable's version resource; a count, since speed per app needs Admin), local IPv4 and DNS servers |
| `failover.py` | Switching to a backup network path ([ADR-0008](adr/0008-failover.md)): discovers paths (physical cards with a default route), measures each path with TCP bound to its IP, a pure policy with safety limits, changes InterfaceMetric with a backup in the backup store (`backupstore.py`). Off by default |
| `runtime.py` | How each part (monitor, desktop, elevated) is launched, from source or from the packaged build — the task, UAC and shortcuts all ask here |
| `installer.py`, `entry.py` | Packaged build `Ambysto Steady.exe` (PyInstaller, bundles Python): subcommands `monitor`/`desktop`/`elevated`/`install`/`uninstall`/`diagnostics`; install for all users under `Program Files` ([ADR-0019](adr/0019-per-machine-install.md): one UAC prompt for the all-users part, the monitor task and tray shortcut stay per user, an earlier per-user copy in `%LOCALAPPDATA%\Programs` is removed), uninstall first restores every tweak + metric. A packaged copy outside `Program Files` never runs elevated. Data of the packaged build lives in `%LOCALAPPDATA%\StableInternet\data` |
| `impact.py` | Measures the effect of a change (tweak, manual step) using monitor data before/after — [ADR-0007](adr/0007-measured-impact.md) |
| `suggestions.py` | Suggestions from the latest diagnostic run: tweaks + manual steps (rotate the antenna, turn off the modem's Wi‑Fi…), "I did this" |
| `dnswatch.py` | Detects DNS being changed on the same network (read-only), warns via an event + toast; a change the app made itself (`dns_fastest`, ADR-0015) is only recorded |
| `i18n.py`, `locales/*.json` | Translations (ADR-0006): `t(key, **params)`, `msg()` to store messages for later translation, `format_duration()`; English is the reference and fallback |

## Monitored targets

| Name | Address | Meaning |
|---|---|---|
| `router` | Gateway of the current uplink (auto-detected, refreshed every 30s) | Failure here ⇒ Wi‑Fi / card / router problem |
| `cloudflare` | 1.1.1.1 | Internet |
| `google` | 8.8.8.8 | Internet (control) |
| `tcp_cloudflare`, `tcp_google` | 1.1.1.1:443, 8.8.8.8:443 | TCP probe (no ICMP), every 10s |
| `http_cloudflare` | `http://cp.cloudflare.com/generate_204` | HTTP probe, must return exactly 204 (200/redirect ⇒ suspected captive portal), every 10s |

Probes are configured in `settings.json` → `probes` (disable with `"enabled": false`).

The DNS of the main network path is read every 60 seconds with `GetAdaptersAddresses` (ctypes, no process spawned). "Network" = (interface, gateway, SSID): on a new network, `dns_observed` is recorded; on the same network with different DNS (seen 2 times in a row), `dns_changed` (warn) is recorded and a toast is shown. Disable with `settings.json` → `dns_watch.enabled`. Probe data lives in the same `minute_stats` table, `target` is the probe name, `ip` is the address/URL.

Incident classification:
- Router not responding ≥ 3 times in a row ⇒ **local connection lost** (PC ↔ router).
- Router OK but **all** ping targets **and** the latest probe round all fail ≥ 3 times in a row ⇒ **Internet lost** (router ↔ ISP). Ping failing alone while probes still get through is **not** an incident (ICMP is the first thing to get restricted); a probe round older than 2.5 cycles is not counted (falls back to ping only).
- **Unstable episodes**: Internet drops that start within 60 s of the previous one's recovery are one episode ([ADR-0014](adr/0014-merge-internet-drops.md)): a single `internet_down` event spans them (last recovery, duration from the first drop) and says how many drops there were. It is written up to 60 s after the last recovery; the live state (tray, toasts, watchdog) is not delayed.

## Storage (`data/`)

| File | Contents |
|---|---|
| `settings.json` | User configuration (watchdog, ping interval, targets, `ui.language`…) |
| `backup.json` | Before ADR-0018: original value of each tweak before it is applied. Now read once per machine by the first elevated process into `HKLM\SOFTWARE\Ambysto\Steady` (values `Backup`, the same JSON, and `Quarantine` for entries not imported yet; `LegacyImported` marks it done and stays after uninstalling); the file is left in place and no longer read. The tests still use it as the file backend (`STABLEINTERNET_BACKUP=file`) |
| `metrics.db` | SQLite (WAL, `PRAGMA user_version` = schema version): `minute_stats(ts, target, ip, sent, lost, avg, max, jitter)`, `wifi_stats(ts, state, ssid, bssid, channel, signal, rssi, rx_mbps, tx_mbps)`, `events(id, ts, kind, level, message, duration, message_key, message_params)` (v3: events with text store a translation key + JSON parameters, the `message` column keeps the English version for reading the DB directly; the API returns text in the currently selected language). `ts` is Unix seconds UTC; statistics tables are keyed by the start of the minute. `*_down` events are written when the incident ends, so `ts` is the recovery time and `duration` tells when it started. When the monitor loses data for > 30 s (machine asleep/hung), the open incident is cut at the last tick before the gap (message contains "cut short by a monitoring gap") and a `monitor_gap` event records the length of the gap — time that could not be measured is never counted as an outage. `rx_mbps`/`tx_mbps`/`bssid` are needed for MLO and roaming diagnostics; `ip` because the router IP can change |

Raw ping samples are kept only in RAM (last 15 minutes); the DB stores per-minute statistics, kept for 30 days.

## UI (`web/`)

Plain HTML/CSS/JS, no libraries, no build step (ES modules). The server serves `web/` on the same origin; the page only loads `type="module"` scripts from itself, so a strict CSP can be kept (`script-src 'self'`, no inline scripts, no `innerHTML` — checked by tests).

| File | Role |
|---|---|
| `index.html` | Shell: sidebar/tab bar, containers for the 5 screens, confirmation sheet, SVG icons; the X-Token token is embedded in `<meta name="si-token">` |
| `tokens.css`, `app.css` | Design tokens (SIC-27) and components; < 760px the sidebar becomes a tab bar |
| `js/app.js` | `#/screen` navigation, shared state, polling (Overview 2s, others 10–30s, paused when the tab is hidden), theme, language switching |
| `js/api.js` | Calls the API with the token, waits for jobs (`/api/jobs/{id}`), reloads the page automatically when the server restarts (new token) |
| `js/i18n.js` | Catalog from `/api/i18n`, same rules as `app/i18n.py` (plurals, decimal comma) |
| `js/chart.js` | SVG charts: router/Internet, packet loss, incident regions, change markers (tweak, manual step) |
| `js/widgets.js` | "Impact" block, suggestion rows, tweak toggles (sheet for experimental tweaks, UAC when not Admin) |
| `js/screens/*.js` | Overview, Optimize, Diagnostics, Log, Settings |
| `mini.html`, `mini.css`, `js/mini.js` | The floating monitor: download / upload / ping tiles, the sweep chart, the Apps and Network tabs; polls every second, paused by the shell while hidden |
| `js/ecg.js` | Sweep chart on a canvas in the style of a heart monitor: the trace wraps every 60 s, a gap runs ahead of the pen, older trace fades |

Text generated by the backend (diagnostics, events, tweaks) comes already translated from the API; JS only translates the UI's own labels (`ui.*`).

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/state` | Admin rights, version, ping targets, editable settings (`watchdog`, `notify`, `ui`) |
| GET | `/api/i18n[?lang=vi]` | Selected language (`setting`), language actually used, list of languages with a catalog, all translations (with English fallback merged in); `?lang=` to preview |
| GET | `/api/suggestions` | Suggestions (tweaks + manual steps) from the latest diagnostic run, with before/after results for steps already done |
| POST | `/api/manual/{id}/done` | The user has done a manual step → `manual_step_done` event, measurement starts |
| GET | `/api/failover` | Network paths, the path in use, whether it has switched, settings |
| POST | `/api/failover/prefer/{ifIndex}` · `/api/failover/restore` | Switch to / back (job; via UAC when not Admin) |
| GET | `/api/impact` | Changes in the last 14 days (tweaks enabled, steps done) with before/after data |
| GET | `/api/autostart` | Whether the monitor starts with Windows (read-only, cached 60s) |
| GET | `/api/live?window=300` | Latest ping samples (≤ 900s), Wi‑Fi state, ongoing incident |
| GET | `/api/traffic` | Throughput of the Internet path (last 120 s, bits/s), the interface, apps with open connections, local IPv4 and DNS (read-only; starts the sampler) |
| GET | `/api/history?hours=24&bucket=1` | Per-minute statistics + Wi‑Fi + events (≤ 30 days); `bucket=N` aggregates N minutes (7-day chart) |
| GET | `/api/events?limit=200` | Log |
| GET | `/api/tweaks` | Tweak state from a 60s cache; re-reading runs in the background (~11s); `impact` for enabled tweaks |
| POST | `/api/tweaks/{id}` | `{ "enable": true/false }` → job; via UAC when not Admin |
| POST | `/api/diagnostics` | Run all diagnostics → job (one run at a time) |
| GET | `/api/diagnostics/runs[/{id}]` | Saved diagnostic runs |
| GET | `/api/jobs/{id}` | Job status/result |
| POST | `/api/actions/{name}` | `reconnect`, `restart_adapter` (UAC), `flush_dns`, `renew_dhcp` → job |
| POST | `/api/settings` | Only keys in `SETTINGS_SCHEMA` (`watchdog.*`, `notify.enabled`, `ui.language`), type-checked, value ranges / allowed lists |

The server runs inside the monitor process, **unelevated**, at `http://127.0.0.1:47613/` (change in `settings.json` → `server.port`); the actual location is written to `%LOCALAPPDATA%\StableInternet\server.json`. Every request must have the correct `Host`; every `/api/*` requires the `X-Token` header; write requests are rejected if cross-origin — see [SECURITY.md](SECURITY.md) and [ADR-0005](adr/0005-unelevated-server-uac-writes.md).

## Enabling a tweak: flow

```
UI turns toggle on → POST /api/tweaks/{id} {enable:true}
  → check supported + Admin rights
  → if there is no backup yet: capture() original value → HKLM backup store (ADR-0018)
  → apply()
  → write event "Optimization on: …" to the log
  → read() again and return the new state to the UI
```

Disable: `check_original(backup)` (the entry must lie in the tweak's own domain, [ADR-0017](adr/0017-validate-backup-before-restore.md)) → `restore(backup)` → delete backup → write event. The backup comes from the HKLM store, which a process without Admin cannot write ([ADR-0018](adr/0018-backups-in-hklm.md)). If there is no backup (the value had been changed before the tool was used), restore to the driver/Windows default.
