"""Builds the Windows app icon (app/assets/app.ico) from the Apple icon's artwork.

The Wi-Fi arcs, the dot and the "A"/"S" live in apple/Steady/AppIcon.icon/Assets/*.svg, and the tile
color in its icon.json (the dark appearance: graphite tile, white artwork), so there is one source.
Windows does not mask an icon the way iOS does, so the rounded tile is drawn here. A headless
Edge/Chrome renders the SVGs (Pillow cannot read SVG); the result is committed, so building the
app needs only Pillow.

    python scripts/make_app_icon.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "apple" / "Steady" / "AppIcon.icon"
OUT_DIR = ROOT / "app" / "assets"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
MARGIN = 0.03        # transparent border around the tile, like the tray icon's
RADIUS = 0.22        # corner radius as a share of the tile


LAYERS = ("arcs", "dot", "letters")   # Assets/<name>.svg, drawn white over the tile


def tile_color() -> str:
    """The tile of icon.json's dark appearance (solid or automatic-gradient fill), as #RRGGBB."""
    fills = json.loads((ICON_DIR / "icon.json").read_text())["fill-specializations"]
    dark = next(f for f in fills if f.get("appearance") == "dark")["value"]
    value = next(iter(dark.values()))
    r, g, b = (float(v) for v in value.removeprefix("srgb:").split(",")[:3])
    return "#%02X%02X%02X" % tuple(round(v * 255) for v in (r, g, b))


def render(png: Path, size: int = 1024) -> None:
    browser = next((b for b in BROWSERS if Path(b).exists()), None) or shutil.which("msedge")
    if not browser:
        raise SystemExit("Edge or Chrome is needed to render the SVG")
    svg = "".join((ICON_DIR / "Assets" / f"{name}.svg").read_text(encoding="utf-8") for name in LAYERS)
    pad = size * MARGIN
    html = (f"<!doctype html><meta charset=utf-8><style>html,body{{margin:0;background:transparent;overflow:hidden}}"
            f".tile{{position:absolute;left:{pad}px;top:{pad}px;width:{size - 2 * pad}px;height:{size - 2 * pad}px;"
            f"border-radius:{RADIUS * 100}%;background:{tile_color()}}}svg{{position:absolute;left:{pad}px;top:{pad}px;"
            f"width:{size - 2 * pad}px;height:{size - 2 * pad}px}}</style><div class=tile></div>{svg}")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run([browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--default-background-color=00000000",
                        f"--window-size={size},{size}", f"--screenshot={png}", page.as_uri()],
                       check=True, capture_output=True, timeout=60)


def main() -> int:
    from PIL import Image
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png = OUT_DIR / "app-icon.png"
    render(png)
    img = Image.open(png).convert("RGBA")
    if img.size != (1024, 1024):
        img = img.crop((0, 0, 1024, 1024))   # headless browsers may add a few rows of window chrome
    img.save(png)
    img.save(OUT_DIR / "app.ico", sizes=SIZES)
    print("wrote", png, "and", OUT_DIR / "app.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
