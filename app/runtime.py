"""How to start each part of the app, from source or from the packaged build (SIC-32).

From source:    pythonw.exe scripts\\monitor\\run_monitor.pyw   /  pythonw.exe -m app.elevated ...
Packaged:       "Ambysto Steady.exe" monitor          /  "...exe" elevated ...

Everything that launches another process (Task Scheduler, UAC, shortcuts) asks here, so the
two layouts can never drift apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import config

FROZEN = bool(getattr(sys, "frozen", False))
APP_NAME = "Ambysto Steady"
EXE_NAME = f"{APP_NAME}.exe"
ROLES = ("monitor", "desktop", "elevated")


def pythonw() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def install_dir() -> Path:
    """Folder of the packaged exe (frozen) or the repository (source)."""
    return Path(sys.executable).resolve().parent if FROZEN else config.ROOT


def program_files() -> Path:
    from . import winutil
    return Path(winutil.known_folder(winutil.FOLDERID_PROGRAM_FILES))


def protected_dir() -> Path:
    """Where the app is installed for all users: <Program Files>\\Ambysto Steady (ADR-0019)."""
    return program_files() / APP_NAME


def is_protected(folder: Path) -> bool:
    """True for the app's own folder under Program Files (or inside it), which a process without Admin rights
    cannot write (ADR-0019): the only place the packaged exe may run elevated from. Not any folder under
    Program Files: other software sometimes makes its own folder there writable."""
    try:
        Path(folder).resolve().relative_to(protected_dir().resolve())
        return True
    except (ValueError, OSError):
        return False


def elevation_allowed() -> bool:
    """May this copy ask for UAC to change Windows settings? From source always (development); the
    packaged exe only from a protected install folder (ADR-0019)."""
    return not FROZEN or is_protected(install_dir())


def command(role: str, *args: str, exe: Path | None = None) -> tuple[Path, list[str], Path]:
    """(program, arguments, working directory) to start `role`. `exe` points at a packaged
    exe somewhere else (the installer registers the copy it is about to create)."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if exe is not None or FROZEN:
        program = Path(exe) if exe is not None else Path(sys.executable).resolve()
        return program, [role, *args], program.parent
    root = config.ROOT
    if role == "monitor":
        return pythonw(), [str(root / "scripts" / "monitor" / "run_monitor.pyw"), *args], root
    if role == "desktop":
        return pythonw(), [str(root / "scripts" / "desktop" / "run_desktop.pyw"), *args], root
    return pythonw(), ["-m", "app.elevated", *args], root
