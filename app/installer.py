"""Install and uninstall the packaged app for all users of the PC (SIC-32, ADR-0019). Installing and
uninstalling each ask once for Administrator approval (UAC).

    install    elevated, for all users: copy the app to <Program Files>\\Ambysto Steady, add the "Apps &
               features" entry (HKLM) and the Start menu shortcut. Then, for this user: remove a per-user
               copy of an earlier version (%LOCALAPPDATA%\\Programs), register the monitor task, add the
               sign-in (tray) shortcut, start the monitor and open the window
    uninstall  stop the app; elevated: put every tweak and failover metric back to its original value,
               remove the all-users entry and shortcut, delete the program folder; then this user's task
               and shortcut. Measurement data is kept unless asked (and always kept while a backup could
               not be restored)

    "Ambysto Steady.exe" install [--dry-run] [--yes]
    "Ambysto Steady.exe" uninstall [--delete-data] [--dry-run] [--yes]

The elevated part runs as another account under over-the-shoulder UAC, so it never touches per-user
things (task, Startup shortcut, data). Every side effect goes through Ops, so the plans are tested with
a fake and --dry-run prints them.
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

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AmbystoSteady"   # HKLM; HKCU up to 0.5.0
PUBLISHER = "Ambysto"
CREATE_NO_WINDOW, DETACHED, NEW_GROUP = 0x08000000, 0x00000008, 0x00000200


def default_target() -> Path:
    return runtime.program_files() / runtime.APP_NAME


def programs_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def start_menu_link() -> Path:
    """This user's Start menu shortcut: what a per-user install up to 0.5.0 created."""
    return programs_dir() / f"{runtime.APP_NAME}.lnk"


def common_start_menu_link() -> Path:
    from . import winutil
    return Path(winutil.known_folder(winutil.FOLDERID_COMMON_PROGRAMS)) / f"{runtime.APP_NAME}.lnk"


def startup_link() -> Path:
    return programs_dir() / "Startup" / f"{runtime.APP_NAME}.lnk"


# --- side effects ------------------------------------------------------------------------------

class Ops:
    """The real machine. Tests use a fake with the same methods."""

    def shortcut(self, link: Path, target: Path, args: str = "") -> None:
        from .winsys import ps_literal
        from .winutil import run_powershell
        link.parent.mkdir(parents=True, exist_ok=True)
        run_powershell(
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_literal(str(link))}); "
            f"$s.TargetPath = {ps_literal(str(target))}; $s.Arguments = {ps_literal(args)}; "
            f"$s.WorkingDirectory = {ps_literal(str(target.parent))}; $s.IconLocation = {ps_literal(str(target) + ',0')}; "
            "$s.Save()")

    def is_file(self, path: Path) -> bool:
        return Path(path).is_file()

    def remove_file(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def register(self, values: dict[str, Any]) -> None:
        """The all-users "Apps & features" entry (HKLM, 64-bit view). Needs Admin."""
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0,
                                winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
            for name, value in values.items():
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(k, name, 0, kind, value)

    def unregister(self) -> None:
        import winreg
        try:
            winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            pass

    def registered_location(self) -> str | None:
        return _install_value("InstallLocation", machine=True)

    def registered_version(self) -> str | None:
        return _install_value("DisplayVersion", machine=True)

    def legacy_location(self) -> str | None:
        """Where this user's per-user install of 0.5.0 or earlier is, if any (its HKCU entry)."""
        return _install_value("InstallLocation", machine=False)

    def unregister_legacy(self) -> None:
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except FileNotFoundError:
            pass

    def install_machine(self) -> Any:
        """The elevated half of install (one UAC prompt); returns the ElevationResult."""
        from .elevation import run_elevated
        return run_elevated("install-machine", str(os.getpid()), timeout_s=600)

    def uninstall_machine(self) -> Any:
        """The elevated half of uninstall; it deletes the program folder once this process has exited."""
        from .elevation import run_elevated
        return run_elevated("uninstall-machine", str(os.getpid()), timeout_s=600)

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

    def stop_other_instances(self, folder: Path, keep: int | None = None) -> None:
        """Processes running from `folder` (monitor, window, tray) - never this one, nor `keep` (the
        installer or uninstaller that asked the elevated helper)."""
        from .winsys import ps_literal
        from .winutil import run_powershell
        prefix = str(folder).rstrip("\\/") + "\\"      # not "...\Ambysto Steady Beta\"
        spared = ",".join(str(p) for p in (os.getpid(), keep) if p)
        run_powershell(
            "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
            f"$_.ExecutablePath.StartsWith({ps_literal(prefix)}, 'OrdinalIgnoreCase') -and $_.ProcessId -notin @({spared}) }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")

    def retire_unprotected_task(self, target: Path) -> None:
        """The monitor task (one per PC, by name) is ended and removed when it runs anything but the exe in
        `target`: a task from a per-user install may have highest privileges and point at a folder any
        process can write. Elevated, so it can end an elevated monitor too; the user's half registers it again."""
        from . import autostart
        autostart.remove_legacy_tasks()       # the same monitor under an earlier name ("StableInternet Monitor")
        st = autostart.status()
        if not st.installed or not st.command:
            return
        try:
            same = Path(st.command.strip('"')).resolve() == (Path(target) / runtime.EXE_NAME).resolve()
        except OSError:
            same = False
        if same:
            return
        autostart.end_now()
        res = autostart.uninstall()
        if not res.ok:
            raise RuntimeError(res.message)

    def backups_left(self) -> bool:
        try:
            return bool(config.load_backup())
        except Exception:
            return True       # unreadable: assume there is something to protect

    def delete_tree(self, path: Path) -> None:
        check_deletable(path, marker=None)
        shutil.rmtree(path, ignore_errors=True)

    def delete_program_folder(self, folder: Path) -> None:
        """A program folder that runs nothing any more (an earlier copy, stopped first)."""
        check_deletable(folder, marker=runtime.EXE_NAME)
        shutil.rmtree(folder)

    def replace_tree(self, src: Path, dst: Path) -> None:
        """The program folder becomes an exact copy of `src`: an earlier version's files must not linger."""
        if Path(dst).exists() and (Path(dst) / runtime.EXE_NAME).is_file():
            check_deletable(dst, marker=runtime.EXE_NAME)
            shutil.rmtree(dst)
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def delete_after_exit(self, folder: Path, also_wait: int | None = None) -> None:
        """The program folder holds this running process (its exe and DLLs stay locked until it ends,
        and so does the uninstaller's, `also_wait`): remove it once both have exited."""
        import base64
        from .winsys import ps_literal
        check_deletable(folder, marker=runtime.EXE_NAME)
        pids = ",".join(str(p) for p in (os.getpid(), also_wait) if p)
        from .winutil import SAFE_MODULE_PATH, powershell_exe, safe_env
        script = (SAFE_MODULE_PATH + f"Wait-Process -Id {pids} -Timeout 3600 -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1; "
                  f"Remove-Item -LiteralPath {ps_literal(str(Path(folder).resolve()))} -Recurse -Force")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        # Not DETACHED: powershell.exe without any console exits at once and does nothing (seen on
        # Windows 11). A hidden console of its own outlives this process just as well.
        subprocess.Popen([powershell_exe(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=safe_env(),
                         cwd=os.environ.get("TEMP") or None,       # never inside the folder it deletes
                         creationflags=NEW_GROUP | CREATE_NO_WINDOW, close_fds=True)


def _install_value(name: str, machine: bool) -> str | None:
    import winreg
    root, access = ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) if machine
                    else (winreg.HKEY_CURRENT_USER, winreg.KEY_READ))
    try:
        with winreg.OpenKeyEx(root, UNINSTALL_KEY, 0, access) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


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
    folder, so it must never be one that holds anything else."""
    target = Path(target)
    if target.resolve() == Path(source).resolve() or not target.exists():
        return
    if not target.is_dir() or (any(target.iterdir()) and not (target / runtime.EXE_NAME).is_file()):
        raise ValueError(i18n.t("installer.target_not_empty", target=str(target)))


def machine_steps(source: Path, target: Path, ops: Ops, caller: int | None = None) -> list[Step]:
    """The elevated half of install, for all users (ADR-0019). `source` is the folder of the exe that runs
    it: the helper copies its own folder, never a path its caller names. `caller` (the installer that
    asked) is spared when copies running from the target are stopped."""
    exe = target / runtime.EXE_NAME
    # An earlier copy may be running (upgrade): its exe is locked against the copy.
    steps = [Step(msg("installer.step.stop"), lambda: ops.stop_other_instances(target, keep=caller), required=False)]
    if source.resolve() != target.resolve():
        steps.append(Step(msg("installer.step.copy", target=str(target)), lambda: ops.replace_tree(source, target)))
    steps += [
        Step(msg("installer.step.register"), lambda: ops.register({
            "DisplayName": runtime.APP_NAME, "DisplayVersion": __version__, "Publisher": PUBLISHER,
            "InstallLocation": str(target), "DisplayIcon": f"{exe},0",
            "UninstallString": f'"{exe}" uninstall', "QuietUninstallString": f'"{exe}" uninstall --yes',
            "NoModify": 1, "NoRepair": 1})),
        # Only once the new copy is in place: a failed copy must not leave the old monitor without its task.
        Step(msg("installer.step.retire_task"), lambda: ops.retire_unprotected_task(target)),
        Step(msg("installer.step.shortcut_all"), lambda: ops.shortcut(common_start_menu_link(), exe)),
    ]
    return steps


def remove_legacy(folder: Path, ops: Ops) -> None:
    """A per-user install of 0.5.0 or earlier: stop it, drop its entry and shortcut, delete its folder.
    Its data folder and the backups are shared with the new install and stay."""
    ops.stop_other_instances(folder)
    ops.remove_file(start_menu_link())
    ops.unregister_legacy()
    ops.delete_program_folder(folder)


def user_steps(target: Path, ops: Ops) -> list[Step]:
    """The half of install that belongs to the user who runs it, not elevated (ADR-0019)."""
    exe = target / runtime.EXE_NAME
    # The task is registered for the new copy before an earlier copy's folder goes (its task must never
    # point at a folder that no longer exists, and so could be re-created by anyone).
    steps = [Step(msg("installer.step.task"), lambda: ops.task_install(exe))]
    legacy = ops.legacy_location()
    if legacy and Path(legacy).resolve() != target.resolve():
        steps.append(Step(msg("installer.step.legacy", folder=legacy), lambda: remove_legacy(Path(legacy), ops),
                          required=False))
    steps += [
        Step(msg("installer.step.startup"), lambda: ops.shortcut(startup_link(), exe, "desktop --minimized")),
        Step(msg("installer.step.start"), lambda: (ops.task_start(), ops.launch(exe, ["desktop"])), required=False),
    ]
    return steps


def install(target: Path, ops: Ops) -> list[tuple[str, bool, str]]:
    """The elevated half first (one UAC prompt), then this user's half. Under over-the-shoulder UAC the
    helper's result cannot reach this user (ADR-0005), so a missing result is checked by its effect."""
    exe = target / runtime.EXE_NAME
    text = i18n.render(msg("installer.step.machine", target=str(target)))
    res = ops.install_machine()
    if getattr(res, "cancelled", False):
        return [(text, False, i18n.render(res.message))]
    no_result = getattr(res, "result", None) is None and i18n.is_message(res.message) \
        and res.message.get("key") == "elevation.no_result"
    done = res.ok or (no_result and ops.registered_location() == str(target)
                      and ops.registered_version() == __version__ and ops.is_file(exe))
    if not done:
        return [(text, False, i18n.render(res.message))]
    return [(text, True, "")] + run_steps(user_steps(target, ops))


def install_machine(caller: int | None = None, *, ops: Ops | None = None) -> dict[str, Any]:
    """Run by the elevated helper (`install-machine`): this exe's folder becomes the all-users install.
    `caller`: the installer that asked, spared when copies running from the target are stopped."""
    ops = ops or Ops()
    if not runtime.FROZEN:
        return {"ok": False, "message": msg("installer.packaged_only")}
    source, target = runtime.install_dir(), default_target()
    try:
        check_target(source, target)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    results = run_steps(machine_steps(source, target, ops, caller))
    failed = [f"{text}: {err}" for text, ok, err in results if not ok]
    return {"ok": not failed, "message": "; ".join(failed) if failed else msg("installer.installed"),
            "target": str(target)}


def uninstall_steps(target: Path, ops: Ops, delete_data: bool) -> list[Step]:
    state: dict[str, bool] = {"restored": True}

    def machine() -> None:
        """Elevated: restore everything, then remove the all-users parts and (after exit) the folder."""
        res = ops.uninstall_machine()
        if res.ok or (not getattr(res, "cancelled", False) and ops.registered_location() is None):
            return     # done (or, under over-the-shoulder UAC, no result but the entry is gone)
        state["restored"] = False
        raise RuntimeError(i18n.render(res.message))

    def data() -> None:
        if not delete_data:
            return
        if not state["restored"] or ops.backups_left():
            raise RuntimeError(i18n.t("installer.keep_backup"))   # the backups are the only copy of the originals
        ops.delete_tree(config.user_dir())

    # Put everything back first, with the app stopped so it cannot switch anything meanwhile. If
    # that fails (UAC declined...) stop there: the app stays installed, able to try again later.
    return [
        Step(msg("installer.step.stop"), lambda: (ops.stop_other_instances(target), ops.task_stop()), required=False),
        Step(msg("installer.step.machine_uninstall"), machine),
        Step(msg("installer.step.untask"), ops.task_stop_and_delete, required=False),
        Step(msg("installer.step.unshortcut"), lambda: (ops.remove_file(startup_link()), ops.remove_file(start_menu_link())),
             required=False),
        Step(msg("installer.step.data" if delete_data else "installer.step.keep_data"), data, required=False),
    ]


def uninstall_machine(wait_pid: int, *, ops: Ops | None = None) -> dict[str, Any]:
    """Run by the elevated helper (`uninstall-machine`): put everything back; only then remove the
    all-users entry and shortcut, and delete the program folder once the helper and the uninstaller
    (`wait_pid`) have exited. If anything cannot be restored, the app stays installed."""
    from .elevated import restore_everything
    ops = ops or Ops()
    target = runtime.install_dir()
    if not (runtime.FROZEN and is_installed(ops)):
        return {"ok": False, "message": msg("installer.not_installed", folder=str(target))}
    try:     # other users' copies too (the uninstaller could only stop its own user's)
        ops.stop_other_instances(target, keep=wait_pid)
    except Exception as exc:
        log.warning("stopping running copies failed: %r", exc)
    restored = restore_everything()
    if not restored.get("ok"):
        return restored
    for step in (ops.unregister, lambda: ops.remove_file(common_start_menu_link()),
                 lambda: ops.delete_after_exit(target, also_wait=wait_pid)):
        try:
            step()
        except Exception as exc:
            log.warning("uninstall step failed: %r", exc)
    return restored


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
    """True when this exe runs from its registered all-users install folder."""
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
    ap.add_argument("--delete-data", action="store_true", help="uninstall: also delete measurements and settings")
    ap.add_argument("--dry-run", action="store_true", help="only list the steps")
    ap.add_argument("--yes", action="store_true", help="do not ask")
    args = ap.parse_args(argv)
    ops = ops or Ops()
    if args.action == "install":
        target = default_target()
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
        if args.dry_run:
            plan = machine_steps(runtime.install_dir(), target, ops) + user_steps(target, ops)
            print("\n".join(text for text, _, _ in run_steps(plan, dry_run=True)))
            return 0
        if not args.yes and _message_box(i18n.t("installer.confirm_install", target=str(target)), runtime.APP_NAME,
                                         MB_YESNO | MB_ICONQUESTION) != IDYES:
            return 1
        return report(install(target, ops), "installer.installed", "installer.install_failed", quiet=args.yes)
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
