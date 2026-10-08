"""Paths and persistent configuration (settings.json; backups through app.backupstore).

All writes are atomic (temp file + replace) so a crash or power loss can never
leave a half-written file. The backups (the only copy of the original values a
tweak overwrote) live in HKLM since ADR-0018; backup.json is the old place and
the file backend of the tests.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parent.parent


def _override(name: str) -> str | None:
    """A folder override from the environment (tests, the build's smoke test). Ignored by an elevated
    process of the packaged build: user environment variables (HKCU\\Environment) are writable without
    Admin and reach the elevated helper, which must not read or write where the user points it (ADR-0018)."""
    value = os.environ.get(name)
    if value and getattr(sys, "frozen", False):
        from . import winutil
        if winutil.is_admin():
            return None
    return value or None


def data_dir() -> Path:
    """Runtime data directory; STABLEINTERNET_DATA overrides it (used by tests). The packaged
    build keeps data outside its install folder (an upgrade replaces that folder)."""
    override = _override("STABLEINTERNET_DATA")
    if override:
        path = Path(override)
    elif getattr(sys, "frozen", False):
        path = user_dir() / "data"
    else:
        path = ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_dir() -> Path:
    """Per-user private directory (%LOCALAPPDATA%\\StableInternet): the server runtime file and
    the results of elevated operations live here, not in the repo (other local accounts may read it).
    STABLEINTERNET_USERDIR overrides it (tests)."""
    override = _override("STABLEINTERNET_USERDIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "StableInternet"
    base.mkdir(parents=True, exist_ok=True)
    return base


def results_dir() -> Path:
    path = user_dir() / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return data_dir() / "settings.json"


def backup_path() -> Path:
    return data_dir() / "backup.json"


def db_path() -> Path:
    return data_dir() / "metrics.db"


DEFAULT_SETTINGS: dict[str, Any] = {
    "ping_interval_s": 1.0,
    "ping_timeout_ms": 900,
    "wifi_poll_s": 5,
    "gateway_refresh_s": 30,
    "targets": {"cloudflare": "1.1.1.1", "google": "8.8.8.8"},
    # Connectivity probes that do not rely on ICMP (see app/probe.py). Names become `target`
    # values in minute_stats, so they must not collide with the ICMP targets above.
    "probes": {"enabled": True, "interval_s": 10, "timeout_s": 3,
               "tcp": {"tcp_cloudflare": "1.1.1.1:443", "tcp_google": "8.8.8.8:443"},
               "http": {"http_cloudflare": "http://cp.cloudflare.com/generate_204"}},
    "retention_days": 30,
    # See docs/WATCHDOG.md. Off by default: it disconnects the network on purpose.
    "watchdog": {"enabled": False, "dry_run": False, "threshold_s": 15, "cooldown_s": 120, "max_per_hour": 6,
                 "verify_s": 60, "trip_after": 3, "tripped_at": None},
    "server": {"enabled": True, "port": 47613},
    # Display language (ADR-0006): a code with a catalog in app/locales, or "auto" = Windows language.
    "ui": {"language": "en"},
    # Switch to a backup path when the main one loses the Internet (ADR-0008). Off by default.
    "failover": {"enabled": False, "dry_run": False, "threshold_s": 20, "failback_s": 120, "cooldown_s": 60,
                 "max_per_hour": 6, "tripped_at": None},
    # Report DNS servers changing on the same network (app/dnswatch.py). Read-only.
    "dns_watch": {"enabled": True, "interval_s": 60},
    # Windows toasts for outages and watchdog actions (app/notify.py). outage_after_s: how long an
    # outage must last before the first toast.
    "notify": {"enabled": True, "outage_after_s": 30},
    # Throughput samples that find a line delivering a fraction of its speed while ping and probes are fine
    # (app/speedwatch.py, ADR-0021). Off by default: about 12 MB every interval_min. plan_*_mbps: the speed the
    # user pays for, 0 = unknown (the baseline is then learned from the samples).
    "speed": {"enabled": False, "interval_min": 30, "plan_down_mbps": 0, "plan_up_mbps": 0},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively overlay override on base; keys unknown to base are kept."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_settings() -> dict[str, Any]:
    """Settings with defaults filled in. A corrupt file falls back to defaults."""
    try:
        stored = _read_json(settings_path())
    except (json.JSONDecodeError, OSError):
        stored = None
    if not isinstance(stored, dict):
        return copy.deepcopy(DEFAULT_SETTINGS)
    return _merge(DEFAULT_SETTINGS, stored)


def save_settings(settings: dict[str, Any]) -> None:
    _write_json_atomic(settings_path(), settings)


def read_backup_file(path: Path) -> dict[str, Any]:
    """A backup.json file as a dict ({} when missing). Raises on a corrupt file.

    Unlike settings, a corrupt backup must not silently become "no backup":
    the caller would then capture the *tweaked* values as the originals.
    """
    stored = _read_json(path)
    if stored is None:
        return {}
    if not isinstance(stored, dict):
        raise ValueError(f"{path} is not a JSON object")
    return stored


def load_backup_file() -> dict[str, Any]:
    return read_backup_file(backup_path())


def save_backup_file(backup: dict[str, Any]) -> None:
    _write_json_atomic(backup_path(), backup)


def load_backup() -> dict[str, Any]:
    """Original tweak values keyed by tweak id (and failover:<ifIndex>), from the store only the
    elevated side can write (ADR-0018). Raises when the store cannot be read."""
    from . import backupstore
    return backupstore.load()


def save_backup(backup: dict[str, Any]) -> None:
    from . import backupstore
    backupstore.save(backup)


_backup_threads = threading.Lock()
_backup_held = threading.local()


@contextlib.contextmanager
def backup_lock(timeout: float = 15.0) -> Iterator[None]:
    """Serialises read-modify-write of the backups between threads and processes (the monitor,
    the elevated helper, the uninstaller). Without it one process can save a stale copy and
    drop an entry another process just added - the only record of an original value.
    Re-entrant within a thread: loading inside the lock may import the old backup.json (ADR-0018)."""
    if getattr(_backup_held, "depth", 0):
        _backup_held.depth += 1
        try:
            yield
        finally:
            _backup_held.depth -= 1
        return
    import msvcrt
    with _backup_threads:
        fd = os.open(data_dir() / "backup.json.lock", os.O_RDWR | os.O_CREAT)
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("backup.json is locked by another process") from None
                    time.sleep(0.05)
            _backup_held.depth = 1
            try:
                yield
            finally:
                _backup_held.depth = 0
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


def put_backup_entry(key: str, entry: dict[str, Any] | None, *,
                     load: Callable[[], dict[str, Any]] = load_backup,
                     save: Callable[[dict[str, Any]], None] = save_backup) -> None:
    """Sets one entry (None: removes it) against the file as it is now, never a copy read earlier."""
    with backup_lock():
        store = load()
        if entry is None:
            store.pop(key, None)
        else:
            store[key] = entry
        save(store)
