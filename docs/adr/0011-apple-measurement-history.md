# ADR-0011: The Apple app keeps its measurement history in SQLite, in the Windows schema

- **Status:** Accepted
- **Date:** 2026-10-05
- **Related:** [ADR-0010](0010-apple-app-structure.md) (Apple app structure), [ADR-0007](0007-measured-impact.md) (before/after comparison), `app/storage.py`

## Context

The Apple app measures only while it is open (iOS gives no continuous background time). Its per-minute rows lived in memory, so closing the app threw them away and check #5, which needs an hour of data, started from nothing every time. Comparing before and after a change (ADR-0007) also needs history that outlives the process.

Options considered: SwiftData/Core Data (a second data model to keep in step with Windows), JSON files (rewritten whole on every minute), SQLite through the system library (what Windows already uses, no dependency).

## Decision

1. **SQLite via the system `SQLite3` module**, one database `metrics.sqlite` in its own `History` folder in the app's Application Support folder (inside the sandbox container on macOS); the folder is excluded from iCloud/device backups, which covers SQLite's `-wal` and `-shm` files too: it is measurement data that can be measured again.
2. **Same table as Windows:** `minute_stats (ts, target, ip, sent, lost, avg, max, jitter)`, keyed by `(ts, target)`, timestamps in Unix seconds at the start of the minute, the same target names (`router`, `cloudflare`, `tcp_google`…). Rules read the same rows on every platform, and exported data compares directly.
3. **Partial minutes are kept:** when measuring stops (screen closed, app suspended) the current minute is written as it is. Because the app may reopen within the same minute, a row that already exists is **merged** (sent and lost added, the newer jitter kept), where Windows replaces it.
4. **Retention 30 days**, like `retention_days` on Windows, purged when measuring starts.
5. Nothing leaves the device.
6. **Route changes** (added with SIC-61) go to the Windows `events` table, kind `route_change`, so check #5 leaves out the minutes around them after a relaunch too; purged and deleted with the minutes.

## Consequences

- ✅ Check #5 uses the last hour across app launches.
- ✅ One schema for Windows and Apple; later features (charts, before/after) can share queries.
- ⚠️ Gaps while the app is closed are real gaps, not losses; rules already judge only the samples that exist.
- ⚠️ `ip`, `avg` and `max` stay empty for now: no rule on Apple platforms reads them yet.
