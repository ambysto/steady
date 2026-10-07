"""Entry point of the elevated helper process (started through UAC, see ADR-0005).

It runs ONE operation from a fixed list and writes the outcome as JSON. It does not trust
its caller: every argument is re-validated here, and the result may only be written to
%LOCALAPPDATA%\\StableInternet\\results\\<32 hex>.json.

    python -m app.elevated tweak-enable <tweak_id>  --result-file <path>
    python -m app.elevated tweak-enable <tweak_id>  --measurement <base64 JSON> --result-file <path>
                                                    (a measured tweak, ADR-0016: the value is derived here)
    python -m app.elevated tweak-disable <tweak_id> --result-file <path>
    python -m app.elevated restart-adapter <name>   --result-file <path>
    python -m app.elevated path-prefer <ifIndex>    --result-file <path>
    python -m app.elevated path-restore <ifIndex|all> --result-file <path>
    python -m app.elevated restore-all -              --result-file <path>   (uninstall)
    "<exe>" elevated install-machine <caller pid>     --result-file <path>   (ADR-0019: copy this exe's folder
                                                      under Program Files, register it for all users)
    "<exe>" elevated uninstall-machine <caller pid>   --result-file <path>   (restore everything, then remove
                                                      the all-users parts and the program folder)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import config, winutil
from .i18n import msg

log = logging.getLogger("stableinternet.elevated")

OPS = ("tweak-enable", "tweak-disable", "restart-adapter", "path-prefer", "path-restore", "restore-all",
       "install-machine", "uninstall-machine")
_RESULT_NAME = re.compile(r"^[0-9a-f]{32}\.json$")


def check_result_path(path: str) -> Path:
    p = Path(path).resolve()
    if p.parent != config.results_dir().resolve() or not _RESULT_NAME.match(p.name):
        raise ValueError(f"result file must be <32 hex>.json in {config.results_dir()}")
    return p


def _write(path: Path, payload: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)


def run_op(op: str, value: str, measurement: str | None = None) -> dict[str, Any]:
    """measurement: base64 JSON for a measured tweak; only checked and handed to the manager, which
    validates it again and derives the value itself (the caller never names the value)."""
    if op not in OPS:
        return {"ok": False, "message": f"unknown operation {op!r}"}
    if measurement is not None and op != "tweak-enable":
        return {"ok": False, "message": f"{op} takes no measurement"}
    if not winutil.is_admin():
        return {"ok": False, "message": msg("elevation.not_admin")}
    from . import runtime
    if op == "install-machine":
        if not (value.isascii() and value.isdigit()):          # the source is always this exe's own folder
            return {"ok": False, "message": f"{op} takes the caller's process id"}
        from . import installer
        return installer.install_machine(int(value))
    if not runtime.elevation_allowed():
        return {"ok": False, "message": msg("elevation.not_installed")}
    _close_backup_import()
    if op == "uninstall-machine":
        if not (value.isascii() and value.isdigit()):
            return {"ok": False, "message": f"{op} takes the caller's process id"}
        from . import installer
        return installer.uninstall_machine(int(value))
    if op in ("tweak-enable", "tweak-disable"):
        from . import calibration, tweaks
        from .storage import Storage
        if value not in {t.id for t in tweaks.CATALOG}:
            return {"ok": False, "message": msg("elevation.unknown_tweak", tweak=value)}
        decoded = None
        if measurement is not None:
            try:
                decoded = calibration.decode(measurement)
            except ValueError as exc:
                return {"ok": False, "message": msg("tweak.result.bad_measurement", error=str(exc))}
        with Storage(config.db_path()) as storage:
            mgr = tweaks.default_manager(storage)
            out = mgr.enable(value, decoded) if op == "tweak-enable" else mgr.disable(value)
        return {"ok": out.ok, "changed": out.changed, "message": out.message}
    if op in ("path-prefer", "path-restore"):
        return _path_op(op, value)
    if op == "restore-all":
        return restore_everything()
    from .actions import Actions
    try:
        res = Actions().restart_adapter(value)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": res.ok, "message": res.message}


def _close_backup_import() -> None:
    """Every operation reads the backups once, so the first UAC prompt of this version imports the old
    backup.json and closes that window for good (ADR-0018), even for an operation that needs no backup."""
    try:
        config.load_backup()
    except Exception as exc:   # the operation reports its own trouble with the store, if it needs it
        log.warning("reading the backups failed: %s", exc)


def restore_everything() -> dict[str, Any]:
    """Uninstall: every tweak that has a backup goes back to its original value, and so do
    failover's interface metrics. Whatever cannot be restored keeps its backup; once nothing is
    left, the backup store itself is removed (HKLM, ADR-0018)."""
    from . import backupstore, failover, tweaks
    from .storage import Storage
    from .winsys import WindowsSystem
    failed: list[Any] = []
    tweak_ids = {t.id for t in tweaks.CATALOG}
    with Storage(config.db_path()) as storage:
        mgr = tweaks.default_manager(storage)
        for tid in [k for k in config.load_backup() if k in tweak_ids]:
            out = mgr.disable(tid)
            if not out.ok:
                failed.append(out.message)
    for res in failover.MetricSwitch(WindowsSystem()).restore_all():
        if not res.ok:
            failed.append(res.message)
    if failed:
        return {"ok": False, "message": failed[0], "failed": len(failed)}
    try:
        backupstore.remove()
    except Exception as exc:
        return {"ok": False, "message": msg("installer.store_kept", error=f"{type(exc).__name__}: {exc}")}
    return {"ok": True, "message": msg("installer.restored_all")}


def _path_op(op: str, value: str) -> dict[str, Any]:
    """Failover (ADR-0008). path-prefer <ifIndex>: make that path the way out; path-restore
    <ifIndex|all>: put the original metrics back. Paths are re-read here, never taken from the caller."""
    from . import failover
    from .winsys import WindowsSystem
    from .storage import Storage
    switch = failover.MetricSwitch(WindowsSystem())

    def logged(action: str, res: Any) -> Any:
        with Storage(config.db_path()) as storage:
            failover.record_user_switch(storage, action, res)
        return res

    if op == "path-restore":
        if value == "all":
            results = [logged("restore", r) for r in switch.restore_all()]
            bad = [r for r in results if not r.ok]
            return {"ok": not bad, "message": (bad[0] if bad else results[0]).message if results
                    else msg("failover.result.nothing")}
        if not value.isdigit():
            return {"ok": False, "message": msg("failover.result.unknown_path", path=value)}
        res = logged("restore", switch.restore(int(value)))
        return {"ok": res.ok, "message": res.message}
    paths = failover.usable_paths(winutil.get_paths())
    backup = next((p for p in paths if value.isdigit() and p.index == int(value)), None)
    if backup is None:
        return {"ok": False, "message": msg("failover.result.unknown_path", path=value)}
    route = winutil.default_route_native()
    refusal = failover.in_use_refusal(backup.index, route, backup.name)
    if refusal is not None:
        return {"ok": False, "message": refusal}
    home = next((p for p in paths if route and p.index == route["interface_index"] and p.index != backup.index), None)
    res = logged("prefer", switch.prefer(backup, home, source=failover.user_source()))
    return {"ok": res.ok, "message": res.message}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.elevated")
    ap.add_argument("op", choices=OPS)
    ap.add_argument("value")
    ap.add_argument("--result-file", required=True)
    ap.add_argument("--measurement")
    args = ap.parse_args(argv)
    try:
        path: Path | None = check_result_path(args.result_file)
    except ValueError as exc:
        # Over-the-shoulder UAC: this process runs as another account, so the caller's results folder is not
        # this one's. Installing and uninstalling must still happen (the caller checks their effect, ADR-0019);
        # the result is never written into another profile. Every other operation stops here, as before.
        if args.op not in ("install-machine", "uninstall-machine"):
            print(exc, file=sys.stderr)
            return 2
        log.warning("%s: the result cannot be written (%s); running it anyway", args.op, exc)
        path = None
    try:
        result = run_op(args.op, args.value, args.measurement)
    except Exception as exc:  # report, never leave the caller without an answer
        result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    if path is not None:
        _write(path, result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
