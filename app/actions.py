"""One-off recovery actions. Every function here interrupts the network on purpose, so
callers (the watchdog, the UI) must be explicitly enabled by the user.

Arguments reach netsh/ipconfig as separate argv entries (no shell); PowerShell gets
strings only through ps_literal (base64).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .winsys import ps_literal
from .winutil import CREATE_NO_WINDOW, PowerShellError, _oem_codepage, run_powershell

_SAFE_NAME = re.compile(r'^[^"\r\n\x00]{1,256}$')


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: Any          # plain text or an i18n.msg() dict (ADR-0006)


def _check(name: str, what: str) -> str:
    if not _SAFE_NAME.match(name or ""):
        raise ValueError(f"unusable {what}: {name!r}")
    return name


class Actions:
    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 ps: Callable[..., str] = run_powershell) -> None:
        self._run, self._ps = run, ps

    def _exec(self, args: list[str], timeout: float = 30) -> ActionResult:
        try:
            proc = self._run(args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return ActionResult(False, f"{args[0]}: {exc}")
        out = proc.stdout.decode(_oem_codepage(), errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
        text = " ".join((out or "").split())[:300]
        return ActionResult(proc.returncode == 0, text or f"exit {proc.returncode}")

    def reconnect(self, interface: str, profile: str, force: bool = False) -> ActionResult:
        """Connect the Wi-Fi interface to `profile`. With force, disconnect first so a
        connected-but-silent link is torn down and re-associated."""
        iface, prof = _check(interface, "interface"), _check(profile, "profile")
        if force:
            down = self._exec(["netsh", "wlan", "disconnect", f"interface={iface}"])
            if not down.ok:
                return ActionResult(False, f"disconnect: {down.message}")
        up = self._exec(["netsh", "wlan", "connect", f"name={prof}", f"interface={iface}"])
        return ActionResult(up.ok, ("disconnect + " if force else "") + f"connect {prof}: {up.message}")

    def restart_adapter(self, interface: str) -> ActionResult:
        """Restart-NetAdapter. Needs Administrator."""
        try:
            self._ps(f"Restart-NetAdapter -Name {ps_literal(_check(interface, 'interface'))} -Confirm:$false "
                     "-ErrorAction Stop", timeout=60)
        except PowerShellError as exc:
            return ActionResult(False, f"Restart-NetAdapter: {exc}")
        return ActionResult(True, f"restarted {interface}")

    def flush_dns(self) -> ActionResult:
        return self._exec(["ipconfig", "/flushdns"])

    def renew_dhcp(self, interface: str) -> ActionResult:
        return self._exec(["ipconfig", "/renew", _check(interface, "interface")], timeout=60)
