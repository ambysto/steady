"""Build the packaged app (SIC-32): a folder that runs without Python installed, plus a zip.

    pip install -r requirements.txt pyinstaller==6.22.3
    python scripts/build.py              -> dist/Ambysto Steady/  and  dist/AmbystoSteady-<version>-win64.zip
    python scripts/build.py --smoke      also start the built exe headless and check it serves the UI

Users unzip and double-click "Ambysto Steady.exe": it offers to install itself
(app/installer.py). Not code-signed yet: Windows SmartScreen will warn until it is.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import __version__, runtime  # noqa: E402

BUILD, DIST = ROOT / "build", ROOT / "dist"
APP_DIR = DIST / runtime.APP_NAME


def make_icon() -> Path:
    """The exe, shortcut and Apps & features icon: app/assets/app.ico (scripts/make_app_icon.py)."""
    ico = ROOT / "app" / "assets" / "app.ico"
    if not ico.is_file():
        raise SystemExit(f"{ico} is missing: run python scripts/make_app_icon.py")
    return ico


def build() -> None:
    import PyInstaller.__main__
    sep = os.pathsep
    PyInstaller.__main__.run([
        str(ROOT / "scripts" / "packaging" / "main.py"),
        "--name", runtime.APP_NAME, "--onedir", "--windowed", "--noconfirm", "--clean",
        "--icon", str(make_icon()),
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'web'}{sep}web",
        "--add-data", f"{ROOT / 'app' / 'locales'}{sep}app/locales",
        "--add-data", f"{ROOT / 'app' / 'assets'}{sep}app/assets",
        "--collect-submodules", "app",
        "--hidden-import", "pystray._win32",
        "--exclude-module", "tkinter",
    ])


BUNDLED = ("pywebview", "pystray", "pillow")      # requirements.txt; their dependencies are followed
EXTRA_NOTICES = (
    "PyInstaller bootloader - GPL-2.0-or-later with the bootloader exception, which allows any program "
    "built with it to be distributed under its own license. https://pyinstaller.org/en/stable/license.html",
    "Icons drawn in the style of Lucide (ISC License). https://lucide.dev/license",
)


def bundled_distributions(roots: tuple[str, ...] = BUNDLED) -> list:
    """The installed distributions the packaged app carries: the roots and everything they require
    on Windows (environment markers evaluated), each once."""
    from importlib import metadata
    import re as _re
    seen: dict[str, object] = {}
    todo = list(roots)
    while todo:
        name = _re.split(r"[ ;<>=!~\[(]", todo.pop(), 1)[0].strip().lower().replace("_", "-")
        if not name or name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[name] = dist
        for req in dist.requires or []:
            if ";" in req and "extra ==" in req:
                continue                     # optional extras are not installed with the app
            todo.append(req)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def third_party_notices() -> str:
    """Name, version, license and full license text of every bundled component, Python included."""
    parts = ["Ambysto Steady includes the following third-party software."]
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    parts.append(f"=== Python {sys.version.split()[0]} (PSF License) ===\n"
                 + (py_license.read_text(encoding="utf-8", errors="replace") if py_license.exists() else ""))
    for dist in bundled_distributions():
        meta = dist.metadata
        lic = meta.get("License-Expression") or (meta.get("License") or "").strip().splitlines()[:1]
        lic = lic[0] if isinstance(lic, list) and lic else lic or "see text"
        texts = []
        for f in dist.files or []:
            if any(k in f.name.upper() for k in ("LICENSE", "COPYING", "NOTICE")):
                try:
                    texts.append(Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
        parts.append(f"=== {meta['Name']} {meta['Version']} ({lic}) ===\n" + "\n".join(texts))
    parts += [f"=== {n} ===" for n in EXTRA_NOTICES]
    return "\n\n".join(parts) + "\n"


def write_notices() -> None:
    shutil.copyfile(ROOT / "LICENSE", APP_DIR / "LICENSE.txt")
    (APP_DIR / "THIRD-PARTY-NOTICES.txt").write_text(third_party_notices(), encoding="utf-8")


def make_zip() -> Path:
    out = DIST / f"AmbystoSteady-{__version__}-win64.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(APP_DIR.rglob("*")):
            z.write(path, Path(runtime.APP_NAME) / path.relative_to(APP_DIR))
    return out


def smoke(port: int = 47699, seconds: int = 25) -> dict:
    """Run the built monitor against a throwaway data folder on a spare port and fetch the UI."""
    exe = APP_DIR / runtime.EXE_NAME
    with tempfile.TemporaryDirectory() as tmp:
        # An elevated process of the packaged build ignores STABLEINTERNET_DATA/USERDIR (ADR-0018), and CI
        # runners are elevated: point LOCALAPPDATA at the same throwaway folder so both ways lead there.
        user = Path(tmp) / "StableInternet"          # as config.user_dir() names it under LOCALAPPDATA
        data = user / "data"
        data.mkdir(parents=True)
        (data / "settings.json").write_text(json.dumps({"server": {"enabled": True, "port": port},
                                                        "probes": {"enabled": False}}), encoding="utf-8")
        env = dict(os.environ, STABLEINTERNET_DATA=str(data), STABLEINTERNET_USERDIR=str(user), LOCALAPPDATA=tmp)
        proc = subprocess.Popen([str(exe), "monitor", "--seconds", str(seconds)], env=env)
        result: dict = {}
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(40):
                time.sleep(0.5)
                try:
                    html = urllib.request.urlopen(base + "/", timeout=2).read().decode()
                    break
                except OSError:
                    continue
            else:
                raise RuntimeError("the built monitor never served the UI")
            token = re.search(r'name="si-token" content="([^"]+)"', html).group(1)
            get = lambda p: urllib.request.urlopen(urllib.request.Request(base + p, headers={"X-Token": token}), timeout=10)
            result["index_has_app_js"] = 'src="js/app.js"' in html
            result["app_js_bytes"] = len(urllib.request.urlopen(base + "/js/app.js", timeout=5).read())
            i18n = json.load(get("/api/i18n"))
            result["languages"] = [a["code"] for a in i18n["available"]]
            result["state_version"] = json.load(get("/api/state"))["version"]
        finally:
            proc.wait(timeout=seconds + 30)
        result["exit_code"] = proc.returncode
        result["monitor_log"] = (data / "monitor.log").exists()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--no-build", action="store_true", help="only zip / smoke-test an existing build")
    args = ap.parse_args()
    if not args.no_build:
        shutil.rmtree(APP_DIR, ignore_errors=True)
        build()
        write_notices()
        print("zip:", make_zip())
    if args.smoke:
        print(json.dumps(smoke(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
