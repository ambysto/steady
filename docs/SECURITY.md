# Security

Process model per [ADR-0005](adr/0005-unelevated-server-uac-writes.md): **the server runs unelevated** inside the monitor process; operations that need Admin run in a separate child process that requests rights via **UAC every time**. Even so, the server can still control disruptive things (reconnecting Wi‑Fi, enabling the watchdog, triggering a UAC dialog), so every caller other than the tool's own UI must be blocked.

## Threats & mitigations

| Threat | Mitigation |
|---|---|
| Another machine on the LAN calls the API | Bind only to `127.0.0.1`, never to `0.0.0.0` |
| Any web page in the browser sends requests to `127.0.0.1` (CSRF) | Random token (`secrets`, 256 bits) generated on every startup, embedded in the UI page; every `/api/*` requires the `X-Token` header, compared in constant time. A custom header forces the browser to send a CORS preflight — the server rejects `OPTIONS` and returns no CORS headers at all. A second layer for write requests: reject if `Origin` is not the server's own, or if `Sec-Fetch-Site: cross-site` |
| DNS rebinding (a foreign page points its domain at 127.0.0.1 to read the page containing the token) | `Host` must be exactly `127.0.0.1:<port>` or `localhost:<port>`, applied to **every** path, including the UI page |
| Embedding the UI in a foreign page's frame (clickjacking) | `Content-Security-Policy: frame-ancestors 'none'`, `X-Frame-Options: DENY` |
| Reading files outside the UI directory | Serve only files inside `web/`, check the path after normalization, only certain file extensions |
| Hanging the server (large body, slow connections) | Body limit 64 KB, socket timeout 10 s, long jobs (diagnostics, reading tweaks) run in the background with only one job of each kind at a time |
| Command injection into PowerShell / netsh | No string concatenation: PowerShell receives strings via base64 (`ps_literal`), netsh/ipconfig receive each argv separately; interface/profile/tweak names are format-validated |
| The unelevated process is compromised and uses the Admin child process as a springboard | The child process only runs commands from a fixed list, re-validates parameters itself, only writes results to `%LOCALAPPDATA%\StableInternet\results\<uuid>.json`; every run requires the user to click through UAC |
| A process of the user (no Admin) edits `backup.json` so the Admin child process "restores" a value it chose (confused deputy) | Before restoring, every entry is checked against the tweak's own domain: fixed setting names, the registry path and Wi‑Fi card the tweak resolves now, values the setting accepts, only the providers' own DoH templates; anything else restores nothing and keeps the backup ([ADR-0016](adr/0016-validate-backup-before-restore.md)). Since [ADR-0018](adr/0018-backups-in-hklm.md) the backups live in `HKLM\SOFTWARE\Ambysto\Steady`, which only Administrators can write, so the file is no longer read; the old `backup.json` is imported once, only entries that pass these checks for tweaks that are on |
| System changes that cannot be undone | Back up the original value before applying, verify, roll back on error — see [ADR-0003](adr/0003-tweak-framework.md) |
| The watchdog itself causing repeated connection loss | Limits in [WATCHDOG.md](WATCHDOG.md) / [ADR-0004](adr/0004-watchdog-safety.md) |

## Out of scope

- Malware already running with the user's rights on the machine (it could read the token from memory/the process, or trigger UAC itself).
- The user deliberately approving a UAC dialog they did not ask for.
