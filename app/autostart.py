"""Start the monitor at logon via Task Scheduler.

Reading status is always safe. install()/uninstall() change the machine, so the CLI
only prints what it would do unless --apply is given:

    python -m app.autostart status
    python -m app.autostart validate           # checks the task XML with Task Scheduler, registers nothing
    python -m app.autostart install            # dry run
    python -m app.autostart install --apply
    python -m app.autostart uninstall --apply

The task is created from XML, not from `schtasks /SC ONLOGON` switches: those leave
Task Scheduler's defaults in place, which stop a task after 72 hours and refuse to run
it on battery - fatal for a 24/7 monitor. The XML disables both and restarts the
monitor if it crashes.

The task runs at limited rights by default: the monitor only sends ICMP and reads
state. Pass --highest when the elevated parts of the tool (watchdog, tweaks) need it.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from . import config
from .winutil import PowerShellError, _oem_codepage, run_powershell

TASK_NAME = "Ambysto Steady Monitor"
LEGACY_TASK_NAMES = ("StableInternet Monitor",)   # before the app was named Ambysto Steady
LOGON_DELAY = "PT20S"            # let the network stack come up first
LEGACY_SHORTCUT = "StableInternet ping-logger.lnk"
CREATE_NO_WINDOW = 0x08000000

Runner = Callable[..., subprocess.CompletedProcess]


def launcher_path() -> Path:
    return config.ROOT / "scripts" / "monitor" / "run_monitor.pyw"


def pythonw_path() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def current_user() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    if not user:
        raise RuntimeError("cannot determine the current user (USERNAME is not set)")
    return f"{domain}\\{user}" if domain else user


def _task_command(exe: Path | None) -> tuple[Path, str, Path]:
    """(program, arguments, working dir) the task runs: the packaged exe (`exe`, or this process
    when frozen) with "monitor", else pythonw + run_monitor.pyw from the repository."""
    from . import runtime
    if exe is None and not runtime.FROZEN:
        return pythonw_path(), subprocess.list2cmdline([str(launcher_path())]), config.ROOT
    program, args, cwd = runtime.command("monitor", exe=exe)
    return program, subprocess.list2cmdline(args), cwd


def build_task_xml(highest: bool = False, user: str | None = None, exe: Path | None = None) -> str:
    """Task definition. Every value that comes from the environment is XML-escaped."""
    user = escape(user or current_user())
    program, arguments, cwd = _task_command(exe)
    run_level = "HighestAvailable" if highest else "LeastPrivilege"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Ambysto Steady: network monitor (read-only). Created by python -m app.autostart.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>{LOGON_DELAY}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>{run_level}</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(program))}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(cwd))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def build_create_args(xml_path: str | Path) -> list[str]:
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"]


def build_delete_args() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def build_query_args() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME, "/XML"]


@dataclass(frozen=True)
class TaskStatus:
    installed: bool
    run_level: str | None = None      # "LeastPrivilege" | "HighestAvailable"
    command: str | None = None
    arguments: str | None = None
    time_limit: str | None = None     # ExecutionTimeLimit; PT0S = unlimited
    legacy_shortcut: bool = False     # old PowerShell logger still starts at logon

    @property
    def problems(self) -> list[str]:
        """Settings that would break a 24/7 monitor (e.g. a task made by hand with schtasks)."""
        out = []
        if self.installed and self.time_limit not in ("PT0S", None):
            out.append(f"task is stopped after {self.time_limit}")
        return out


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    command: list[str]


def _tag(xml: str, name: str) -> str | None:
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return m.group(1).strip() if m else None


def parse_query_xml(xml: str) -> TaskStatus:
    """Pull the interesting fields out of `schtasks /Query /XML` output."""
    if "<Task" not in xml:
        return TaskStatus(False)
    # Task Scheduler omits elements left at their defaults: RunLevel (LeastPrivilege) and
    # ExecutionTimeLimit (72 hours).
    return TaskStatus(True, run_level=_tag(xml, "RunLevel") or "LeastPrivilege", command=_tag(xml, "Command"),
                      arguments=_tag(xml, "Arguments"), time_limit=_tag(xml, "ExecutionTimeLimit") or "PT72H")


def startup_folder() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def legacy_shortcut_present() -> bool:
    return (startup_folder() / LEGACY_SHORTCUT).exists()


def _run(args: list[str], runner: Runner | None = None) -> subprocess.CompletedProcess:
    runner = runner or subprocess.run
    return runner(args, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)


def _text(data: bytes | str) -> str:
    return data if isinstance(data, str) else data.decode(_oem_codepage(), errors="replace")


def status(runner: Runner | None = None) -> TaskStatus:
    """Read-only."""
    proc = _run(build_query_args(), runner)
    parsed = parse_query_xml(_text(proc.stdout)) if proc.returncode == 0 else TaskStatus(False)
    return TaskStatus(parsed.installed, parsed.run_level, parsed.command, parsed.arguments, parsed.time_limit,
                      legacy_shortcut_present())


# TASK_VALIDATE_ONLY (1) makes Task Scheduler parse and check the definition without
# registering anything. The XML travels as base64 so nothing is spliced into the script.
_VALIDATE_PS = r"""
$xml = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}'))
$svc = New-Object -ComObject Schedule.Service
$svc.Connect()
try { [void]$svc.GetFolder('\').RegisterTask($null, $xml, 1, $null, $null, 3, $null); 'OK' }
catch { 'ERROR: ' + $_.Exception.Message }
"""


def validate_xml(xml: str) -> tuple[bool, str]:
    """Ask Task Scheduler whether it would accept this definition. Changes nothing."""
    b64 = base64.b64encode(xml.encode("utf-8")).decode("ascii")
    try:
        out = run_powershell(_VALIDATE_PS.replace("{b64}", b64), timeout=60).strip()
    except PowerShellError as exc:
        return False, str(exc)
    return out == "OK", out


def _apply(args: list[str], ok_message: str, runner: Runner | None) -> ActionResult:
    proc = _run(args, runner)
    if proc.returncode == 0:
        return ActionResult(True, ok_message, args)
    detail = (_text(proc.stderr) or _text(proc.stdout)).strip()
    return ActionResult(False, f"schtasks exited {proc.returncode}: {detail}", args)


def install(highest: bool = False, runner: Runner | None = None, exe: Path | None = None) -> ActionResult:
    """Creates/replaces the logon task. Changes the machine. `exe`: a packaged build to run."""
    fd, path = tempfile.mkstemp(prefix="stableinternet-task-", suffix=".xml")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(build_task_xml(highest, exe=exe).encode("utf-16"))  # schtasks /XML expects UTF-16 with BOM
        result = _apply(build_create_args(path), f"task '{TASK_NAME}' created", runner)
        if result.ok:
            remove_legacy_tasks(runner)
        return result
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def remove_legacy_tasks(runner: Runner | None = None, sleep: Callable[[float], None] | None = None) -> list[str]:
    """The same monitor registered under an earlier name would start a second copy at logon
    (it exits on the data-folder mutex, but the task stays). Removes it; returns what it removed."""
    removed = []
    for name in LEGACY_TASK_NAMES:
        if _run(["schtasks", "/Query", "/TN", name], runner).returncode != 0:
            continue
        _run(["schtasks", "/End", "/TN", name], runner)
        if _run(["schtasks", "/Delete", "/TN", name, "/F"], runner).returncode == 0:
            removed.append(name)
    if removed:
        # /End returns before the old monitor has exited; a monitor started right away would
        # find its mutex still held and quit (seen when renaming the task on the dev PC).
        (sleep or time.sleep)(3)
    return removed


def run_now(runner: Runner | None = None) -> ActionResult:
    """Starts the task now (after installing). Changes nothing else."""
    return _apply(["schtasks", "/Run", "/TN", TASK_NAME], f"task '{TASK_NAME}' started", runner)


def end_now(runner: Runner | None = None) -> ActionResult:
    """Stops the running monitor (before uninstalling)."""
    return _apply(["schtasks", "/End", "/TN", TASK_NAME], f"task '{TASK_NAME}' stopped", runner)


def uninstall(runner: Runner | None = None) -> ActionResult:
    """Removes the logon task. Changes the machine."""
    return _apply(build_delete_args(), f"task '{TASK_NAME}' removed", runner)


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.autostart", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "validate", "install", "uninstall"])
    ap.add_argument("--apply", action="store_true", help="actually change the machine (default: print only)")
    ap.add_argument("--highest", action="store_true", help="run the task with highest privileges (needs Admin)")
    args = ap.parse_args(argv)

    if args.action == "status":
        st = status(runner)
        print(f"task '{TASK_NAME}': {'installed' if st.installed else 'not installed'}")
        if st.installed:
            print(f"  run level:  {st.run_level}\n  command:    {st.command} {st.arguments or ''}".rstrip())
            print(f"  time limit: {st.time_limit}")
            for p in st.problems:
                print(f"  problem: {p} - reinstall with: python -m app.autostart install --apply")
        if st.legacy_shortcut:
            print("  note: the old PowerShell logger's Startup shortcut is still present; "
                  "both would log at logon")
        return 0

    if args.action == "validate":
        ok, message = validate_xml(build_task_xml(args.highest))
        print("Task Scheduler accepts the task definition" if ok else f"rejected: {message}")
        return 0 if ok else 1

    if not args.apply:
        if args.action == "install":
            print("dry run - would register this task with schtasks /Create /XML:\n")
            print(build_task_xml(args.highest))
        else:
            print("dry run - would execute:\n  " + subprocess.list2cmdline(build_delete_args()))
        print("run again with --apply to do it")
        return 0
    result = install(args.highest, runner) if args.action == "install" else uninstall(runner)
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
