# ADR-0018: Backups live in HKLM, where only the elevated side can write

- **Status:** Accepted
- **Date:** 2026-10-07
- **Amends:** [ADR-0017](0017-validate-backup-before-restore.md) (takes its option 1)
- **Related:** [ADR-0003](0003-tweak-framework.md) (backup/restore), [ADR-0005](0005-unelevated-server-uac-writes.md) (UAC writes), [ADR-0008](0008-failover.md) (failover metrics)

## Context

ADR-0017 made the elevated helper check every `backup.json` entry against the tweak's own domain before restoring it. It left one gap on purpose: the user's original static DNS servers can be any address, so a forged `dns_fastest` entry in the user-writable `backup.json` can still choose which unicast IPv4 DNS servers come back when the user approves turning `dns_fastest` off or uninstalls. No check of the value can tell a forged server list from a real one; only the place the value is kept can.

Every process that writes a backup already runs elevated: the helper started through UAC, the server and the monitor only when they themselves run as Administrator, and the command line with `--apply`. Unelevated processes only read backups (whether a tweak has one, which failover path is preferred, whether uninstalling needs a UAC prompt).

Two stores fit "only the elevated side can write": a ProgramData folder with an explicit Administrators-only ACL plus an owner check (a standard user can create folders in ProgramData, so the name can be squatted before the app creates it), or a key under `HKLM\SOFTWARE`, where `BUILTIN\Users` only has `ReadKey` and cannot create subkeys at all.

## Decision

1. **The store is `HKLM\SOFTWARE\Ambysto\Steady`, value `Backup` (REG_SZ)**: the whole backup as one JSON object, the same shape `backup.json` had (tweak id or `failover:<ifIndex>` → entry). It is opened in the 64-bit view. One value means one atomic write per save, as the atomic file replace gave before. Everyone can read it; only Administrators and SYSTEM can write it, by the ACL `HKLM\SOFTWARE` passes down. No ACL is set or checked by the app: nobody but an Administrator can create the key, so nobody can squat it.
2. **Readers keep working unelevated.** `config.load_backup()` reads the key; `config.save_backup()` writes it and fails with "access denied" in an unelevated process, which never writes anyway. The lock that serialises read-modify-write (`backup_lock`) stays a lock file in the user's data folder and is now re-entrant within a thread, so the import below can take it inside a caller that already holds it.
3. **`backup.json` is imported once per machine, by the first elevated process that reads backups**, whether or not a file is there. That run sets `LegacyImported` (REG_DWORD 1); from then on no `backup.json` is read by an elevated process again, at whatever path. The mark is per machine, not per path, because the path is not the user's to choose any more: a different `STABLEINTERNET_DATA`, or a junction in place of the data folder, would otherwise give a second import. An entry is imported only when it would be restored now:
   - a tweak entry must be an object with an `original` that passes the tweak's `check_original` (ADR-0017) **and** the tweak must be on: its `read()` says so, or its `backup_reading()` does (the backup belongs to a subject `read()` does not look at: `mtu_pmtu`, `dns_fastest` of a docked or VPN interface). A backup of a tweak that is off is stale, the rule `adopt_backup` already follows;
   - a failover entry must pass `check_metric_original` with its `failover:<digits>` key;
   - a key no tweak or failover path uses, an entry without an `original`, and an entry the store already has are dropped (ADR-0003's "never overwrite the original").

   An entry that is refused, or cannot be judged because the machine could not be read (the Wi‑Fi card is off), is not lost: it goes to the value `Quarantine` (REG_SZ, JSON, written by the elevated import, so its content is the file as it was at that one moment). Each elevated process tries the quarantine again once, and moves an entry into `Backup` as soon as it passes, unless the store has a backup for that key by then (taken by the app itself, which is newer). Nothing is renamed or deleted in the user's folder: an elevated rename in a user-writable folder can be redirected by a junction. The file stays where it was and is no longer read.

   Until the import has run, an unelevated reader shows the store plus the file's entries the store does not have, so "has a backup" and the uninstaller's decision stay right before the first UAC prompt; nothing elevated ever acts on that view.
4. **The uninstaller removes the backups.** `restore-all` (elevated) deletes the values `Backup` and `Quarantine` after every entry was restored and the store is empty; quarantined entries never passed the check, so they cannot be restored and go with it. If anything could not be restored the backups stay, as `backup.json` did. The key and `LegacyImported` stay on purpose: without them, a `backup.json` planted between an uninstall and a reinstall would be imported. The unelevated uninstaller asks for UAC when there are backups **or** a `Backup` / `Quarantine` value exists.
5. **Tests use a file backend**: with `STABLEINTERNET_BACKUP=file` the store is `backup.json` in the data folder, as before. The packaged build ignores the variable: user environment variables (`HKCU\Environment`) are writable without Admin and reach the elevated helper, so honouring it there would reopen the hole. For the same reason an elevated process of the packaged build ignores `STABLEINTERNET_DATA` and `STABLEINTERNET_USERDIR` (an unelevated one keeps them, for the build's smoke test). Tests never touch the real registry; `winreg` is replaced by a fake.

## Consequences

- ✅ A process without Admin rights can no longer change what the elevated helper restores: the forged `dns_fastest` server list of ADR-0017 is closed, and every other ADR-0017 check stays as a second line.
- ✅ Nothing to squat and no ACL code: the key can only come into being through an elevated process of this app (or another Administrator).
- ⚠️ The import trusts `backup.json` once, through the ADR-0017 checks and only for tweaks that are on. A `dns_fastest` entry forged *before* the first elevated run of this version, while `dns_fastest` reads on, is still imported (and an entry quarantined then may be imported later, when the tweak reads on). That window closes at the first UAC prompt after the upgrade, for every later file and every path.
- ⚠️ Backups are now per machine, not per Windows user. That matches what they record (machine settings): two users of the same PC see the same "has a backup", and uninstalling for one user restores and removes everything. The import is per machine, so only the `backup.json` of the user who runs the first elevated operation is imported; another user's tweaks from an older version fall back to the Windows default when turned off, or are refused when there is none.
- ⚠️ The lock is still per user: two Windows users' elevated processes writing at the same moment are not serialised. Both would have to change tweaks or failover in the same second.
- ⚠️ Uninstalling needs one UAC prompt whenever a backup or a quarantined entry is left, even if nothing is left to restore.
- ⚠️ Each elevated process reads the machine once more for the quarantine, as long as something waits there.
- ⚠️ `HKLM\SOFTWARE\Ambysto\Steady` with `LegacyImported` stays after uninstalling: one DWORD of machine state outside the program and data folders, described in [ARCHITECTURE.md](../ARCHITECTURE.md) and [PRIVACY.md](../../PRIVACY.md). Deleting it by hand only matters if an old `backup.json` should be imported again.
