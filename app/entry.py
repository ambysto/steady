"""Entry point of the packaged "Ambysto Steady.exe" (SIC-32, scripts/build.py).

    (no argument)   not installed yet: offer to install; installed: open the window
    monitor         the 24/7 monitor + local server (what the logon task runs)
    desktop         the window + tray icon            (--minimized: tray only)
    elevated ...    the Admin helper started through UAC (ADR-0005)
    install / uninstall [options]                      see app/installer.py
    diagnostics ... the diagnostics CLI
"""
from __future__ import annotations

import logging
import sys

from . import config


def _log_to(name: str) -> None:
    logging.basicConfig(filename=config.user_dir() / name, encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command, rest = (args[0], args[1:]) if args else ("start", [])
    if command == "monitor":
        from . import monitor
        return monitor.main(["--log-file", str(config.data_dir() / "monitor.log"), *rest])
    if command == "desktop":
        from . import desktop
        _log_to("desktop.log")
        return desktop.main(rest)
    if command == "elevated":
        from . import elevated
        return elevated.main(rest)
    if command in ("install", "uninstall"):
        from . import installer
        _log_to("installer.log")
        return installer.main([command, *rest])
    if command == "diagnostics":
        from . import diagnostics
        return diagnostics.main(rest)
    if command == "start":
        from . import installer
        if not installer.is_installed():
            _log_to("installer.log")
            return installer.main(["install"])
        from . import desktop
        _log_to("desktop.log")
        return desktop.main([])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
