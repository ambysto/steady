"""Console-less launcher for the monitor (run by pythonw.exe from Task Scheduler).

Extra command-line arguments are passed on to `app.monitor`, e.g. `--seconds 10`.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import config, monitor  # noqa: E402

if __name__ == "__main__":
    log_file = config.data_dir() / "monitor.log"
    raise SystemExit(monitor.main(["--log-file", str(log_file), *sys.argv[1:]]))
