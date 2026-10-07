"""Where tweak and failover backups live (ADR-0018): HKLM\\SOFTWARE\\Ambysto\\Steady, value `Backup`,
one JSON object. Everyone can read it; only Administrators and SYSTEM can write it, so a process of
the user can no longer choose what the elevated helper restores (ADR-0017's residual risk).

    load()     the store; the first elevated call imports the user's old backup.json once
    save()     the whole store (needs Admin)
    exists()   whether the key is there (the uninstaller removes it)
    remove()   delete the key once nothing is left in it (elevated restore-all)

STABLEINTERNET_BACKUP=file keeps backup.json in the data folder (tests). The packaged build ignores
it: user environment variables are writable without Admin and reach the elevated helper.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

from . import config, winutil

log = logging.getLogger("stableinternet.backupstore")

COMPANY_KEY = r"SOFTWARE\Ambysto"
STORE_KEY = COMPANY_KEY + r"\Steady"
BACKUP_VALUE = "Backup"
IMPORTED_VALUE = "ImportedFrom"
ENV_BACKEND = "STABLEINTERNET_BACKUP"
IMPORTED_SUFFIX = ".imported"

EntryCheck = Callable[[str, Any], None]   # raises ValueError when an old entry must not be imported


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

    @property
    def reg(self) -> Any:
        return self._reg or _winreg()

    def _open(self, access: int) -> Any:
        return self.reg.OpenKey(self.reg.HKEY_LOCAL_MACHINE, STORE_KEY, 0, access | self.reg.KEY_WOW64_64KEY)

    def _query(self, name: str) -> Any:
        try:
            with self._open(self.reg.KEY_READ) as key:
                return self.reg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    def exists(self) -> bool:
        try:
            with self._open(self.reg.KEY_READ):
                return True
        except FileNotFoundError:
            return False

    def read(self) -> dict[str, Any]:
        """The stored backup ({} when there is none). Raises on a value that is not a JSON object:
        a corrupt backup must not silently become "no backup" (see config.load_backup_file)."""
        text = self._query(BACKUP_VALUE)
        if text is None:
            return {}
        stored = json.loads(text)
        if not isinstance(stored, dict):
            raise ValueError(f"HKLM\\{STORE_KEY}\\{BACKUP_VALUE} is not a JSON object")
        return stored

    def write(self, backup: dict[str, Any]) -> None:
        reg = self.reg
        with reg.CreateKeyEx(reg.HKEY_LOCAL_MACHINE, STORE_KEY, 0, reg.KEY_WRITE | reg.KEY_WOW64_64KEY) as key:
            reg.SetValueEx(key, BACKUP_VALUE, 0, reg.REG_SZ, json.dumps(backup, ensure_ascii=False))

    def imported(self) -> list[str]:
        return list(self._query(IMPORTED_VALUE) or [])

    def mark_imported(self, path: str) -> None:
        reg = self.reg
        paths = self.imported()
        if path not in paths:
            with reg.CreateKeyEx(reg.HKEY_LOCAL_MACHINE, STORE_KEY, 0, reg.KEY_WRITE | reg.KEY_WOW64_64KEY) as key:
                reg.SetValueEx(key, IMPORTED_VALUE, 0, reg.REG_MULTI_SZ, [*paths, path])

    def remove(self) -> None:
        """Delete the key, then the company key if nothing else is in it."""
        reg = self.reg
        for name in (STORE_KEY, COMPANY_KEY):
            try:
                reg.DeleteKeyEx(reg.HKEY_LOCAL_MACHINE, name, reg.KEY_WOW64_64KEY, 0)
            except FileNotFoundError:
                pass
            except OSError:
                if name == STORE_KEY:
                    raise
                # the company key still holds something else: leave it


REGISTRY = RegistryStore()


# --- the old backup.json ------------------------------------------------------------------------

def legacy_id(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def entry_check(system: Any, catalog: list[Any]) -> EntryCheck:
    """An old backup.json entry is imported only if it would be restored anyway: it passes the
    tweak's check_original (ADR-0017) and the tweak is on now, or it is a valid failover metric."""
    from . import failover

    def check(key: str, entry: Any) -> None:
        if key.startswith(failover.BACKUP_PREFIX):
            index = key[len(failover.BACKUP_PREFIX):]
            if not (index.isascii() and index.isdigit()):
                raise ValueError(f"{key!r} is not a failover path")
            failover.check_metric_original(int(index), entry)
            return
        tweak = next((t for t in catalog if t.id == key), None)
        if tweak is None:
            raise ValueError(f"{key!r} is not a tweak")
        if not isinstance(entry, dict) or "original" not in entry:
            raise ValueError("the backup entry has no original")
        tweak.check_original(system, entry["original"])
        if not tweak.read(system).enabled:
            raise ValueError(f"{key} is off, its backup is stale")
    return check


def default_check(key: str, entry: Any) -> None:
    from . import tweaks
    from .winsys import WindowsSystem
    entry_check(WindowsSystem(), tweaks.CATALOG)(key, entry)


def import_legacy(store: RegistryStore, path: Path, check: EntryCheck = default_check) -> dict[str, str]:
    """Copy the entries of the old backup.json that pass `check` into the store (never over an
    entry the store has), mark the file as imported and rename it. Needs Admin and the backup lock.
    Returns {key: why it was refused}."""
    stored = config.read_backup_file(path)
    backup = store.read()
    refused: dict[str, str] = {}
    for key, entry in stored.items():
        if key in backup:
            refused[key] = "the store already has an entry"
            continue
        try:
            check(key, entry)
        except Exception as exc:   # an entry that cannot be judged is not imported either
            refused[key] = f"{type(exc).__name__}: {exc}"
            continue
        backup[key] = entry
    store.write(backup)
    store.mark_imported(legacy_id(path))
    try:
        path.replace(path.with_name(path.name + IMPORTED_SUFFIX))
    except OSError as exc:
        log.warning("could not rename %s after importing it: %s", path, exc)
    for key, why in refused.items():
        log.warning("backup entry %s not imported: %s", key, why)
    return refused


# --- what the rest of the app calls ------------------------------------------------------------

def load(store: RegistryStore | None = None, *, is_admin: Callable[[], bool] | None = None,
         check: EntryCheck | None = None) -> dict[str, Any]:
    if file_backend():
        return config.load_backup_file()
    store = store or REGISTRY
    legacy = config.backup_path()
    if not legacy.is_file() or legacy_id(legacy) in store.imported():
        return store.read()
    if (is_admin or winutil.is_admin)():
        with config.backup_lock():
            if legacy.is_file() and legacy_id(legacy) not in store.imported():
                import_legacy(store, legacy, check or default_check)
        return store.read()
    # Not imported yet and nobody here may write the store: show both, the store first. Only for
    # display and offers; the elevated side reads the store after importing.
    return {**config.read_backup_file(legacy), **store.read()}


def save(backup: dict[str, Any], store: RegistryStore | None = None) -> None:
    if file_backend():
        config.save_backup_file(backup)
    else:
        (store or REGISTRY).write(backup)


def exists(store: RegistryStore | None = None) -> bool:
    if file_backend():
        return config.backup_path().is_file()
    return (store or REGISTRY).exists()


def remove(store: RegistryStore | None = None) -> None:
    """Uninstall, after everything was restored: refuses while the store still holds a backup."""
    if load(store):
        raise RuntimeError("the backup store still holds entries")
    if file_backend():
        try:
            config.backup_path().unlink()
        except FileNotFoundError:
            pass
    else:
        (store or REGISTRY).remove()
