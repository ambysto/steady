"""Console-less launcher for the desktop window + tray icon (double-click, or a shortcut to
`pythonw.exe run_desktop.pyw`). Extra arguments go to `app.desktop`, e.g. `--minimized`.
Needs `pip install -r requirements.txt`.
"""
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import config, desktop  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(filename=config.user_dir() / "desktop.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(desktop.main(sys.argv[1:]))
