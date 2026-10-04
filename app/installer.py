"""Install and uninstall the packaged app for the current Windows user (SIC-32). No Admin
rights needed to install; uninstalling asks once (UAC) only if tweaks must be put back.

    install    copy the app to %LOCALAPPDATA%\\Programs\\Ambysto Steady, register the
               monitor task, add Start menu + logon (tray) shortcuts and an "Apps & features" entry,
               start the monitor and open the window
    uninstall  stop the app, put every tweak and failover metric back to its original value,
               remove the task, shortcuts and entry, delete the program folder; measurement data is
               kept unless asked (and always kept while a backup could not be restored)

    "Ambysto Steady.exe" install [--target DIR] [--dry-run] [--yes]
    "Ambysto Steady.exe" uninstall [--delete-data] [--dry-run] [--yes]

Every side effect goes through Ops, so the plans are tested with a fake and --dry-run prints them.
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__, config, i18n, runtime
from .i18n import msg

log = logging.getLogger("stableinternet.installer")

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AmbystoSteady"
PUBLISHER = "Ambysto"
CREATE_NO_WINDOW, DETACHED, NEW_GROUP = 0x08000000, 0x00000008, 0x00000200


def default_target() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / runtime.APP_NAME


def programs_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def start_menu_link() -> Path:
    return programs_dir() / f"{runtime.APP_NAME}.lnk"


def startup_link() -> Path:
    return programs_dir() / "Startup" / f"{runtime.APP_NAME}.lnk"


# --- side effects ------------------------------------------------------------------------------

class Ops:
    """The real machine. Tests use a fake with the same methods."""

    def copy_tree(self, src: Path, dst: Path) -> None:
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def shortcut(self, link: Path, target: Path, args: str = "") -> None:
        from .winsys import ps_literal
        from .winutil import run_powershell
        link.parent.mkdir(parents=True, exist_ok=True)
        run_powershell(
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_literal(str(link))}); "
            f"$s.TargetPath = {ps_literal(str(target))}; $s.Arguments = {ps_literal(args)}; "
            f"$s.WorkingDirectory = {ps_literal(str(target.parent))}; $s.IconLocation = {ps_literal(str(target) + ',0')}; "
            "$s.Save()")

    def remove_file(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def register(self, values: dict[str, Any]) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            for name, value in values.items():
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(k, name, 0, kind, value)

    def unregister(self) -> None:
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except FileNotFoundError:
            pass

    def registered_location(self) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
                return winreg.QueryValueEx(k, "InstallLocation")[0]
        except OSError:
            return None

    def task_install(self, exe: Path) -> None:
        """Keeps "highest privileges" if the user had set it (an upgrade must not quietly turn
        automatic tweaks and failover into offers); that needs Admin, so fall back if refused."""
        from . import autostart
        highest = autostart.status().run_level == "HighestAvailable"
        res = autostart.install(highest=highest, exe=exe)
        if not res.ok and highest:
            log.warning("keeping highest privileges failed (%s); registering the task without", res.message)
            res = autostart.install(exe=exe)
        if not res.ok:
            raise RuntimeError(res.message)

    def task_start(self) -> None:
        from . import autostart
        autostart.run_now()

    def task_stop(self) -> None:
        """Ends the running monitor, if any; the task may not exist yet."""
        from . import autostart
        autostart.end_now()

    def task_stop_and_delete(self) -> None:
        from . import autostart
        autostart.end_now()
        if autostart.status().installed:
            res = autostart.uninstall()
            if not res.ok:
                raise RuntimeError(res.message)

    def launch(self, exe: Path, args: list[str]) -> None:
        subprocess.Popen([str(exe), *args], cwd=str(exe.parent), creationflags=DETACHED | CREATE_NO_WINDOW,
                         close_fds=True)

    def stop_other_instances(self, folder: Path) -> None:
        """Windows of this install (desktop/tray) - never this uninstaller itself."""
        from .winsys import ps_literal
        from .winutil import run_powershell
        prefix = str(folder).rstrip("\\/") + "\\"      # not "...\Ambysto Steady Beta\"
        run_powershell(
            "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
            f"$_.ExecutablePath.StartsWith({ps_literal(prefix)}, 'OrdinalIgnoreCase') -and $_.ProcessId -ne {os.getpid()} }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")

    def backups_left(self) -> bool:
        try:
            return bool(config.load_backup())
        except Exception:
            return True       # unreadable: assume there is something to protect

    def restore_everything(self) -> tuple[bool, Any]:
        from .elevation import run_elevated
        res = run_elevated("restore-all", "-")
        return res.ok, res.message

    def delete_tree(self, path: Path) -> None:
        check_deletable(path, marker=None)
        shutil.rmtree(path, ignore_errors=True)

    def delete_after_exit(self, folder: Path) -> None:
        """The program folder holds the running uninstaller (its exe and DLLs stay locked until
        the result box is closed): remove it once this process has ended."""
        import base64
        from .winsys import ps_literal
        check_deletable(folder, marker=runtime.EXE_NAME)
        script = (f"Wait-Process -Id {os.getpid()} -Timeout 3600 -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1; "
                  f"Remove-Item -LiteralPath {ps_literal(str(Path(folder).resolve()))} -Recurse -Force")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        # Not DETACHED: powershell.exe without any console exits at once and does nothing (seen on
        # Windows 11). A hidden console of its own outlives this process just as well.
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         cwd=os.environ.get("TEMP") or None,       # never inside the folder it deletes
                         creationflags=NEW_GROUP | CREATE_NO_WINDOW, close_fds=True)


def check_deletable(path: Path, marker: str | None) -> None:
    """Last line of defence before deleting a folder: never a drive or a top-level folder, and
    (program folder) only one that holds the app's exe."""
    path = Path(path).resolve()
    if len(path.parts) < 3:
        raise ValueError(f"refusing to delete {path}")
    if marker is not None and not (path / marker).is_file():
        raise ValueError(f"refusing to delete {path}: no {marker} in it")


# --- plans -------------------------------------------------------------------------------------

@dataclass
class Step:
    text: Any                  # a message
    run: Callable[[], None]
    required: bool = True      # a failed required step stops the plan


def check_target(source: Path, target: Path) -> None:
    """Install only into an empty folder or over an earlier copy: uninstalling deletes the whole
    folder, so it must never be one that holds anything else (e.g. --target Downloads)."""
    target = Path(target)
    if target.resolve() == Path(source).resolve() or not target.exists():
        return
    if not target.is_dir() or (any(target.iterdir()) and not (target / runtime.EXE_NAME).is_file()):
        raise ValueError(i18n.t("installer.target_not_empty", target=str(target)))


def install_steps(source: Path, target: Path, ops: Ops) -> list[Step]:
    exe = target / runtime.EXE_NAME
    # An earlier copy may be running (upgrade, or the monitor started from source): its exe is
    # locked against the copy, and its monitor would keep the port and ignore the new task.
    steps = [Step(msg("installer.step.stop"), lambda: (ops.stop_other_instances(target), ops.task_stop()),
                  required=False)]
    if source.resolve() != target.resolve():
        steps.append(Step(msg("installer.step.copy", target=str(target)), lambda: ops.copy_tree(source, target)))
    steps += [
        Step(msg("installer.step.task"), lambda: ops.task_install(exe)),
        Step(msg("installer.step.shortcuts"), lambda: (ops.shortcut(start_menu_link(), exe),
                                                       ops.shortcut(startup_link(), exe, "desktop --minimized"))),
        Step(msg("installer.step.register"), lambda: ops.register({
            "DisplayName": runtime.APP_NAME, "DisplayVersion": __version__, "Publisher": PUBLISHER,
            "InstallLocation": str(target), "DisplayIcon": f"{exe},0",
            "UninstallString": f'"{exe}" uninstall', "QuietUninstallString": f'"{exe}" uninstall --yes',
            "NoModify": 1, "NoRepair": 1})),
        Step(msg("installer.step.start"), lambda: (ops.task_start(), ops.launch(exe, ["desktop"])), required=False),
    ]
    return steps


def uninstall_steps(target: Path, ops: Ops, delete_data: bool) -> list[Step]:
    state: dict[str, bool] = {"restored": True}

    def restore() -> None:
        if not ops.backups_left():
            return
        ok, message = ops.restore_everything()
        state["restored"] = ok
        if not ok:
            raise RuntimeError(i18n.render(message))

    def data() -> None:
        if not delete_data:
            return
        if not state["restored"] or ops.backups_left():
            raise RuntimeError(i18n.t("installer.keep_backup"))   # backup.json is the only copy of the originals
        ops.delete_tree(config.user_dir())

    # Put everything back first, with the app stopped so it cannot switch anything meanwhile. If
    # that fails (UAC declined...) stop there: the app stays installed, able to try again later.
    return [
        Step(msg("installer.step.stop"), lambda: (ops.stop_other_instances(target), ops.task_stop()), required=False),
        Step(msg("installer.step.restore"), restore),
        Step(msg("installer.step.untask"), ops.task_stop_and_delete, required=False),
        Step(msg("installer.step.unshortcut"), lambda: (ops.remove_file(start_menu_link()), ops.remove_file(startup_link())),
             required=False),
        Step(msg("installer.step.unregister"), ops.unregister, required=False),
        Step(msg("installer.step.data" if delete_data else "installer.step.keep_data"), data, required=False),
        Step(msg("installer.step.remove", target=str(target)), lambda: ops.delete_after_exit(target), required=False),
    ]


def run_steps(steps: list[Step], dry_run: bool = False) -> list[tuple[str, bool, str]]:
    """[(step text, ok, error)]; stops at the first failed required step."""
    out = []
    for step in steps:
        text = i18n.render(step.text)
        if dry_run:
            out.append((text, True, ""))
            continue
        try:
            step.run()
            out.append((text, True, ""))
        except Exception as exc:
            log.warning("%s failed: %r", text, exc)
            out.append((text, False, str(exc)))
            if step.required:
                break
    return out


def is_installed(ops: Ops | None = None) -> bool:
    """True when this exe runs from its registered install folder."""
    location = (ops or Ops()).registered_location()
    return bool(location) and Path(location).resolve() == runtime.install_dir().resolve()


# --- user interface (packaged app has no console) -----------------------------------------------

def _message_box(text: str, title: str, flags: int) -> int:
    if sys.stdout is not None and not runtime.FROZEN:
        print(f"{title}\n{text}")
        return 6   # IDYES
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


MB_YESNO, MB_OK, MB_ICONQUESTION, MB_ICONINFO, MB_ICONWARN, IDYES = 0x4, 0x0, 0x20, 0x40, 0x30, 6


def report(results: list[tuple[str, bool, str]], ok_key: str, failed_key: str, quiet: bool = False) -> int:
    """Logs the outcome; shows it too unless `quiet` (--yes: scripted, nobody to click OK)."""
    failed = [r for r in results if not r[1]]
    lines = [f"{'✓' if ok else '✗'} {text}" + (f" — {err}" if err else "") for text, ok, err in results]
    summary = i18n.t(failed_key if failed else ok_key)
    log.info("%s\n%s", summary, "\n".join(lines))
    if not quiet:
        _message_box(summary + "\n\n" + "\n".join(lines), runtime.APP_NAME,
                     MB_OK | (MB_ICONWARN if failed else MB_ICONINFO))
    return 1 if failed else 0


def main(argv: list[str] | None = None, ops: Ops | None = None) -> int:
    ap = argparse.ArgumentParser(prog=runtime.EXE_NAME, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["install", "uninstall"])
    ap.add_argument("--target", help="install folder (default: %%LOCALAPPDATA%%\\Programs\\Ambysto Steady)")
    ap.add_argument("--delete-data", action="store_true", help="uninstall: also delete measurements and settings")
    ap.add_argument("--dry-run", action="store_true", help="only list the steps")
    ap.add_argument("--yes", action="store_true", help="do not ask")
    args = ap.parse_args(argv)
    ops = ops or Ops()
    if args.action == "install":
        target = Path(args.target) if args.target else default_target()
        if not runtime.FROZEN and not args.dry_run:
            print("install works from the packaged build only (scripts/build.py); use --dry-run here")
            return 2
        try:
            check_target(runtime.install_dir(), target)
        except ValueError as exc:
            log.warning("%s", exc)
            if not args.yes:
                _message_box(str(exc), runtime.APP_NAME, MB_OK | MB_ICONWARN)
            return 2
        steps = install_steps(runtime.install_dir(), target, ops)
        if not args.yes and not args.dry_run and _message_box(
                i18n.t("installer.confirm_install", target=str(target)), runtime.APP_NAME,
                MB_YESNO | MB_ICONQUESTION) != IDYES:
            return 1
        results = run_steps(steps, args.dry_run)
        if args.dry_run:
            print("\n".join(text for text, _, _ in results))
            return 0
        return report(results, "installer.installed", "installer.install_failed", quiet=args.yes)
    target = runtime.install_dir()
    # Uninstalling deletes the program folder: only ever the packaged app's own, registered folder.
    # From source (that would be the repository) or from an unzipped copy that was never installed: refuse.
    if not args.dry_run and not (runtime.FROZEN and is_installed(ops)):
        refusal = i18n.t("installer.not_installed", folder=str(target))
        log.warning(refusal)
        if not args.yes:
            _message_box(refusal, runtime.APP_NAME, MB_OK | MB_ICONWARN)
        return 2
    delete_data = args.delete_data
    if not args.yes and not args.dry_run:
        if _message_box(i18n.t("installer.confirm_uninstall"), runtime.APP_NAME, MB_YESNO | MB_ICONQUESTION) != IDYES:
            return 1
        delete_data = delete_data or _message_box(i18n.t("installer.ask_delete_data", folder=str(config.user_dir())),
                                                  runtime.APP_NAME, MB_YESNO | MB_ICONQUESTION) == IDYES
    steps = uninstall_steps(target, ops, delete_data)
    results = run_steps(steps, args.dry_run)
    if args.dry_run:
        print("\n".join(text for text, _, _ in results))
        return 0
    if len(results) < len(steps):          # stopped early: still installed, so keep it running
        try:
            ops.task_start()
        except Exception as exc:
            log.warning("cannot restart the monitor: %r", exc)
    return report(results, "installer.uninstalled", "installer.uninstall_failed", quiet=args.yes)
