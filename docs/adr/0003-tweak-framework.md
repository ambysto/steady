# ADR-0003: Tweak framework — back up first, verify afterwards, roll back on failure

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

Tweaks write to driver properties, the HKLM registry, powercfg and network adapter bindings. Mistakes here leave behind configuration that users find hard to fix themselves. The development machine has previously had tweaks applied by hand (EXP-001), with backups stored outside the tool (`data/manual/backup-*.json`). Requirements: enabling and then disabling must return exactly the original values, backups must survive a restart, and a failure midway must roll back automatically ([TWEAKS.md](../TWEAKS.md), [CONTRIBUTING.md](../../CONTRIBUTING.md)).

## Decision

1. **Tweaks are declarative data over 4 primitive types**: adapter advanced properties (`Set-NetAdapterAdvancedProperty`), HKLM registry DWORDs, powercfg AC/DC indexes of the current power plan, adapter bindings (`Enable/Disable-NetAdapterBinding`). All system operations go through **a single** `System` class (`app/winsys.py`): the real implementation is `WindowsSystem`, tests use a fake. Only `winsys.py` is allowed to write to the machine.
2. **Enable**: read the state → check support and Admin rights → if there is no backup yet, capture the original values and **write `backup.json` to disk before applying** → apply → **read back to verify**. If applying fails or has no effect ⇒ roll back to the values from just before applying; if the rollback also fails ⇒ keep the backup and record a `bad` event so the user can disable it later.
3. **Never overwrite an existing backup**: keep the oldest original values. A corrupt `backup.json` ⇒ refuse all write operations (rather than treating it as having no backup and then mistakenly capturing values that have already been changed).
4. **Disable with a backup**: restore the original values, capture again and compare against the originals; delete the backup only when they match.
5. **Disable without a backup** (values changed before the tool existed): use only **known-for-certain** defaults — `Reset-NetAdapterAdvancedProperty` (driver default), delete the registry value when the Windows default is "not set", re-enable the binding. **powercfg has no known default** per index ⇒ refuse and leave it unchanged, never guess.
6. **Already enabled** (not by the tool) ⇒ enabling is a no-op and creates no backup. Backups from external sources (e.g. the manual EXP-001 copy) are **adopted** via `adopt_backup()`, changing nothing on the machine and never overwriting an existing backup.
7. One lock for all write operations. Every change records an event (`tweak_enabled`, `tweak_disabled`, `tweak_failed`) and the time of the most recent change, so that the watchdog does not treat an adapter restart caused by a tweak as an outage.
8. Reading state (`list_states()`) has no side effects and shares a cache within a single read. The CLI prints only the plan by default; `--apply` is required to write. Tests never run real write operations.

## Consequences

- ✅ Adding a new tweak = adding one declaration; the backup/rollback/verification logic is written only once.
- ✅ "Disable" can answer "cannot be restored safely" instead of guessing wrong.
- ⚠️ A powercfg tweak that was enabled before the tool existed and has no backup cannot be disabled through the tool — an external backup has to be adopted or the user has to adjust it themselves.
- ⚠️ Verification only reads back the configured value (registry/driver); it does not prove that the driver has applied it to the hardware (some changes require an adapter or machine restart).
