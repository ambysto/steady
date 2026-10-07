"""Where tweak and failover backups live (ADR-0018): HKLM\\SOFTWARE\\Ambysto\\Steady, value `Backup`,
one JSON object. Everyone can read it; only Administrators and SYSTEM can write it, so a process of
the user can no longer choose what the elevated helper restores (ADR-0017's residual risk).

    load()      the store; the first elevated call on this machine imports the old backup.json once
    save()      the whole store (needs Admin)
    has_data()  whether there is anything for the uninstaller to remove
    remove()    delete the backups once nothing is left to restore (elevated restore-all)

Values of the key:
    Backup          the backups (REG_SZ, JSON)
    Quarantine      entries of the old backup.json that could not be imported yet (REG_SZ, JSON); retried
                    once per elevated process, moved to Backup as soon as they pass
    LegacyImported  1 once the old backup.json was read (REG_DWORD). Machine-wide and kept on uninstall:
                    a backup.json planted later (another path, a reinstall) is never imported

STABLEINTERNET_BACKUP=file keeps backup.json in the data folder (tests). The packaged build ignores
it: user environment variables are writable without Admin and reach the elevated helper.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Callable

from . import config, winutil

log = logging.getLogger("stableinternet.backupstore")

COMPANY_KEY = r"SOFTWARE\Ambysto"
STORE_KEY = COMPANY_KEY + r"\Steady"
BACKUP_VALUE = "Backup"
QUARANTINE_VALUE = "Quarantine"
IMPORTED_VALUE = "LegacyImported"
ENV_BACKEND = "STABLEINTERNET_BACKUP"

EntryCheck = Callable[[str, Any], None]   # raises ValueError when an old entry must not be imported (yet)


class UnknownEntry(ValueError):
    """A key no tweak or failover path uses: dropped, never quarantined."""


def _winreg() -> Any:
    import winreg
    return winreg


def file_backend() -> bool:
    return os.environ.get(ENV_BACKEND) == "file" and not getattr(sys, "frozen", False)


# --- the registry key ------------------------------------------------------------------------

class RegistryStore:
    """The HKLM key. `reg` is the winreg module; tests pass a fake."""

    def __init__(self, reg: Any = None) -> None:
        self._reg = reg
        self.quarantine_tried = False      # retried once per process (it reads the machine)

    @property
    def reg(self) -> Any:
        return self._reg or _winreg()

    def _query(self, name: str) -> Any:
        reg = self.reg
        try:
            with reg.OpenKey(reg.HKEY_LOCAL_MACHINE, STORE_KEY, 0, reg.KEY_READ | reg.KEY_WOW64_64KEY) as key:
                return reg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    def _set(self, name: str, kind: int, value: Any) -> None:
        reg = self.reg
        with reg.CreateKeyEx(reg.HKEY_LOCAL_MACHINE, STORE_KEY, 0, reg.KEY_WRITE | reg.KEY_WOW64_64KEY) as key:
            reg.SetValueEx(key, name, 0, kind, value)

    def _json(self, name: str) -> dict[str, Any]:
        """{} when there is none. Raises on a value that is not a JSON object: a corrupt backup must not
        silently become "no backup" (see config.read_backup_file)."""
        text = self._query(name)
        if text is None:
            return {}
        stored = json.loads(text)
        if not isinstance(stored, dict):
            raise ValueError(f"HKLM\\{STORE_KEY}\\{name} is not a JSON object")
        return stored

    def read(self) -> dict[str, Any]:
        return self._json(BACKUP_VALUE)

    def write(self, backup: dict[str, Any]) -> None:
        self._set(BACKUP_VALUE, self.reg.REG_SZ, json.dumps(backup, ensure_ascii=False))

    def quarantined(self) -> dict[str, Any]:
        return self._json(QUARANTINE_VALUE)

    def write_quarantine(self, entries: dict[str, Any]) -> None:
        if entries or self._query(QUARANTINE_VALUE) is not None:
            self._set(QUARANTINE_VALUE, self.reg.REG_SZ, json.dumps(entries, ensure_ascii=False))

    def legacy_imported(self) -> bool:
        return bool(self._query(IMPORTED_VALUE))

    def mark_imported(self) -> None:
        self._set(IMPORTED_VALUE, self.reg.REG_DWORD, 1)

    def has_data(self) -> bool:
        return any(self._query(name) is not None for name in (BACKUP_VALUE, QUARANTINE_VALUE))

    def remove(self) -> None:
        """Delete the backups and the quarantine. The key and LegacyImported stay (see the module doc)."""
        reg = self.reg
        try:
            key = reg.OpenKey(reg.HKEY_LOCAL_MACHINE, STORE_KEY, 0, reg.KEY_WRITE | reg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            return
        with key:
            for name in (BACKUP_VALUE, QUARANTINE_VALUE):
                try:
                    reg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass


REGISTRY = RegistryStore()


# --- the old backup.json ------------------------------------------------------------------------

def entry_check(system: Any, catalog: list[Any], metrics: Any = None) -> EntryCheck:
    """An old backup.json entry is imported only if it would be restored now: it passes the tweak's
    check_original (ADR-0017) and the change it was captured before is still in effect (backup_in_effect), or
    it is a failover metric whose switch is still in effect. `metrics` reads interface metrics (default: system)."""
    from . import failover
    metrics = metrics or system

    def check(key: str, entry: Any) -> None:
        if key.startswith(failover.BACKUP_PREFIX):
            index = key[len(failover.BACKUP_PREFIX):]
            if not (index.isascii() and index.isdigit()):
                raise UnknownEntry(f"{key!r} is not a failover path")
            original = failover.check_metric_original(int(index), entry)
            now = metrics.interface_metric_get(int(index))      # raises when the interface is gone: wait
            # prefer() always sets a manual metric: an automatic one, or the saved one, means no switch is in effect.
            if now["automatic"] or (not original["automatic"] and now["metric"] == original["metric"]):
                raise ValueError(f"no switch to path {index} is in effect, its backup is stale")
            return
        tweak = next((t for t in catalog if t.id == key), None)
        if tweak is None:
            raise UnknownEntry(f"{key!r} is not a tweak")
        if not isinstance(entry, dict) or "original" not in entry:
            raise UnknownEntry("the backup entry has no original")
        tweak.check_original(system, entry["original"])
        if not tweak.backup_in_effect(system, entry["original"]):
            raise ValueError(f"{key} is not in effect, its backup is stale")
    return check


def default_check(key: str, entry: Any) -> None:
    from . import tweaks
    from .winsys import WindowsSystem
    entry_check(WindowsSystem(), tweaks.CATALOG)(key, entry)


def _sort(entries: dict[str, Any], backup: dict[str, Any], check: EntryCheck) -> dict[str, Any]:
    """Move the entries that pass `check` into `backup` (never over an entry it has); return the others
    that may pass later. Entries the store has, or that nothing uses, are dropped."""
    waiting: dict[str, Any] = {}
    for key, entry in entries.items():
        if key in backup:
            log.warning("old backup entry %s dropped: the store already has one", key)
            continue
        try:
            check(key, entry)
        except UnknownEntry as exc:
            log.warning("old backup entry %s dropped: %s", key, exc)
            continue
        except Exception as exc:   # refused, or the machine could not be read to judge it: try again later
            log.warning("old backup entry %s not imported yet: %s: %s", key, type(exc).__name__, exc)
            waiting[key] = entry
            continue
        backup[key] = entry
    return waiting


def import_legacy(store: RegistryStore, check: EntryCheck = default_check) -> None:
    """Read the old backup.json once per machine (needs Admin and the backup lock), even when it is missing or
    corrupt: afterwards no backup.json is ever read by an elevated process again. Nothing is renamed
    or deleted in the user's folder: an elevated move there could be redirected by a junction."""
    path = config.backup_path()
    try:
        stored = config.read_backup_file(path) if path.is_file() else {}
    except (ValueError, OSError) as exc:
        # Unreadable: nothing in it can be checked, so nothing is imported, and the import is closed all the
        # same. Leaving it open would fail every elevated operation and keep the window open for a later file.
        log.warning("old backup %s not imported, it cannot be read: %s", path, exc)
        stored = {}
    backup = store.read()
    waiting = _sort(stored, backup, check)
    store.write(backup)
    store.write_quarantine({**store.quarantined(), **waiting})
    store.mark_imported()
    store.quarantine_tried = True


def retry_quarantine(store: RegistryStore, check: EntryCheck = default_check) -> None:
    store.quarantine_tried = True
    waiting = store.quarantined()
    if not waiting:
        return
    backup = store.read()
    left = _sort(waiting, backup, check)
    if left != waiting:
        store.write(backup)
        store.write_quarantine(left)


# --- what the rest of the app calls ------------------------------------------------------------

def load(store: RegistryStore | None = None, *, is_admin: Callable[[], bool] | None = None,
         check: EntryCheck | None = None) -> dict[str, Any]:
    if file_backend():
        return config.load_backup_file()
    store = store or REGISTRY
    imported = store.legacy_imported()
    if imported and store.quarantine_tried:
        return store.read()
    if not (is_admin or winutil.is_admin)():
        if imported:
            return store.read()
        # Before the first elevated run nobody here may write the store: show both, the store first.
        # Only for display and offers; the elevated side reads the store after importing.
        path = config.backup_path()
        return {**(config.read_backup_file(path) if path.is_file() else {}), **store.read()}
    with config.backup_lock():
        if not store.legacy_imported():
            import_legacy(store, check or default_check)
        elif not store.quarantine_tried:
            retry_quarantine(store, check or default_check)
    return store.read()


def save(backup: dict[str, Any], store: RegistryStore | None = None) -> None:
    if file_backend():
        config.save_backup_file(backup)
    else:
        (store or REGISTRY).write(backup)


def has_data(store: RegistryStore | None = None) -> bool:
    if file_backend():
        return config.backup_path().is_file()
    return (store or REGISTRY).has_data()


def remove(store: RegistryStore | None = None) -> None:
    """Uninstall, after everything was restored: refuses while the store still holds a backup. Entries
    still in quarantine (they never passed the check, so they cannot be restored) go with it."""
    if load(store):
        raise RuntimeError("the backup store still holds entries")
    if file_backend():
        try:
            config.backup_path().unlink()
        except FileNotFoundError:
            pass
    else:
        store = store or REGISTRY
        for key in store.quarantined():
            log.warning("old backup entry %s removed on uninstall without being restored", key)
        store.remove()
