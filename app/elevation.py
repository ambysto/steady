"""Run one operation of app.elevated with Administrator rights, through a UAC prompt.

ShellExecuteExW with the "runas" verb is the only supported way for a non-elevated process
to start an elevated one; Windows shows the consent dialog every time (ADR-0005).
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable

from . import config, i18n
from .i18n import msg
from .elevated import OPS

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
ERROR_CANCELLED = 1223
WAIT_OBJECT_0, WAIT_TIMEOUT = 0x0, 0x102


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


@dataclass(frozen=True)
class Launch:
    started: bool
    exit_code: int | None = None
    error: int | None = None      # Win32 error when not started (1223 = user said no)
    timed_out: bool = False


def shell_execute_wait(file: str, params: str, directory: str, verb: str = "runas",
                       timeout_s: float = 180) -> Launch:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb, info.lpFile, info.lpParameters, info.lpDirectory = verb, file, params, directory
    info.nShow = SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        return Launch(False, error=ctypes.get_last_error())
    if not info.hProcess:
        return Launch(True)
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout_s * 1000)) == WAIT_TIMEOUT:
            return Launch(True, timed_out=True)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return Launch(True, exit_code=code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


@dataclass(frozen=True)
class ElevationResult:
    ok: bool
    message: Any          # plain text or an i18n.msg() dict
    cancelled: bool = False
    result: dict[str, Any] | None = None


Launcher = Callable[[str, str, str, str, float], Launch]


def run_elevated(op: str, value: str, *, measurement: dict[str, Any] | None = None, verb: str = "runas",
                 timeout_s: float = 180, launcher: Launcher = shell_execute_wait,
                 module: str = "app.elevated") -> ElevationResult:
    """Prompts UAC, runs `python -m app.elevated <op> <value>` (packaged: `<exe> elevated <op> <value>`),
    returns its JSON outcome. `measurement` goes along for a measured tweak (ADR-0015)."""
    if op not in OPS:
        raise ValueError(f"unknown elevated operation {op!r}")
    from . import calibration, runtime
    path = config.results_dir() / f"{uuid.uuid4().hex}.json"
    extra = ["--measurement", calibration.encode(measurement)] if measurement is not None else []
    if runtime.FROZEN:
        program, args, cwd = runtime.command("elevated", op, value, *extra, "--result-file", str(path))
    else:
        from .autostart import pythonw_path
        program, args, cwd = (pythonw_path(), ["-m", module, op, value, *extra, "--result-file", str(path)],
                              config.ROOT)
    launch = launcher(str(program), subprocess.list2cmdline(args), str(cwd), verb, timeout_s)
    try:
        if not launch.started:
            if launch.error == ERROR_CANCELLED:
                return ElevationResult(False, msg("elevation.cancelled"), cancelled=True)
            return ElevationResult(False, msg("elevation.launch_failed", error=launch.error))
        if launch.timed_out:
            return ElevationResult(False, msg("elevation.timed_out"))
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ElevationResult(False, msg("elevation.no_result", exit_code=launch.exit_code))
        message = result.get("message", "")
        return ElevationResult(bool(result.get("ok")), message if i18n.is_message(message) else str(message),
                               result=result)
    finally:
        try:
            path.unlink()
        except OSError:
            pass
