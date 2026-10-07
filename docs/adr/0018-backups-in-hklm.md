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
3. **`backup.json` is imported once, by the first elevated process that reads backups.** Entries are imported only when they would be restored anyway:
   - a tweak entry must be an object with an `original` that passes the tweak's `check_original` (ADR-0017) **and** the tweak must be on right now (the rule `adopt_backup` already follows: the backup of a tweak that is off is stale);
   - a failover entry must pass `check_metric_original` with its `failover:<digits>` key;
   - any other key is dropped; an entry already in the store is never overwritten (ADR-0003's "never overwrite the original").

   The file is then renamed `backup.json.imported` (kept for the record, refused entries included), and its full path is added to the value `ImportedFrom` (REG_MULTI_SZ) so that a new `backup.json` at the same path is never imported again. Until its file is imported, an unelevated reader shows the store plus the file's entries the store does not have, so "has a backup" and the uninstaller's decision stay right before the first UAC prompt; nothing elevated ever acts on that view.
4. **The uninstaller removes the store.** `restore-all` (elevated) deletes the key, and `HKLM\SOFTWARE\Ambysto` when it is left empty, after every entry was restored and the store is empty. If anything could not be restored the key stays with its backups, as `backup.json` did. The unelevated uninstaller asks for UAC when there are backups **or** the key exists, so an empty store left behind is removed too.
5. **Tests use a file backend**: with `STABLEINTERNET_BACKUP=file` the store is `backup.json` in the data folder, as before. The packaged build ignores the variable: user environment variables (`HKCU\Environment`) are writable without Admin and reach the elevated helper, so honouring it there would reopen the hole. Tests never touch the real registry; `winreg` is replaced by a fake.

## Consequences

- ✅ A process without Admin rights can no longer change what the elevated helper restores: the forged `dns_fastest` server list of ADR-0017 is closed, and every other ADR-0017 check stays as a second line.
- ✅ Nothing to squat and no ACL code: the key can only come into being through an elevated process of this app (or another Administrator).
- ⚠️ The import trusts `backup.json` once, through the ADR-0017 checks and only for tweaks that are on. A `dns_fastest` entry forged *before* the first elevated run of this version, while `dns_fastest` is on, is still imported. That window closes at the first UAC prompt after the upgrade.
- ⚠️ Backups are now per machine, not per Windows user. That matches what they record (machine settings): two users of the same PC see the same "has a backup", and uninstalling for one user restores and removes everything. The import is per file, so each user's `backup.json` is imported by that user's first elevated run.
- ⚠️ The lock is still per user: two Windows users' elevated processes writing at the same moment are not serialised. Both would have to change tweaks or failover in the same second.
- ⚠️ Uninstalling now needs one UAC prompt whenever the app ever wrote a backup, even if nothing is left to restore, to remove the key.
- ⚠️ `HKLM\SOFTWARE\Ambysto\Steady` is machine state outside the program and data folders; it is described in [ARCHITECTURE.md](../ARCHITECTURE.md) and removed only by the uninstaller.
