"""Builds every Ambysto Steady app icon from one drawing: the Ambysto brand's v3 app logo.

The v3 logo (the brand's `ambysto-steady-logo-v3`) is a dark tile with a small pixel caption, "A" in
ink and "S" in the app's tone (Steady Sky), top left, and three Wi-Fi arcs with a dot in the tone. It
is drawn here from its geometry (a 1000-unit canvas, the tile from 20 to 980), and the script writes:

- apple/Steady/AppIcon.icon: one SVG per layer (filled outlines only: the icon renderer lights open
  strokes as if they were closed) and icon.json with each layer's colour per appearance. Dark is the
  brand's logo; light keeps an off-white tile with ink and a darker Sky (the same hue, contrast 4.3:1);
  tinted is white, tinted by the system.
- site/assets/img/logo.svg (light and dark through prefers-color-scheme) and apple-touch-icon.png.
- app/assets/app-icon.png and app.ico, in the dark look: Windows does not mask an icon the way iOS
  does, so the rounded tile is drawn here. At 48 px and below the caption is left out (it cannot be
  read there), so those sizes show the arcs alone.

A headless Edge/Chrome renders the PNGs (Pillow cannot read SVG); the results are committed, so
building the app needs only Pillow.

    python scripts/make_app_icon.py
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "apple" / "Steady" / "AppIcon.icon"
OUT_DIR = ROOT / "app" / "assets"
SITE_IMG = ROOT / "site" / "assets" / "img"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
SMALL = 48           # sizes up to this one leave the caption out
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
MARGIN = 0.03        # transparent border around the Windows tile, like the tray icon's

# The brand's colours (ambysto-assets brand/DESIGN.md): ground, its edge, ink, dim, and Steady Sky
# (OKLCH 0.78 0.116 233). The light tile's Sky is the same hue at L 0.55.
DARK = {"tile": "#0B0B0C", "edge": "#222224", "ink": "#E8E8E6", "dim": "#3A3A3C", "tone": "#62C4F5"}
LIGHT = {"tile": "#F5F5F7", "edge": "#E0E0E2", "ink": "#0B0B0C", "dim": "#C7C7CC", "tone": "#007CAA"}

# The v3 logo's geometry, in its 1000-unit canvas.
TILE, TILE_RADIUS = (20, 980), 215
CELL, CAPTION_AT = 15, (130, 130)
LETTERS = {"A": [".XXX.", "X...X", "X...X", "XXXxX", "X...X", "X...X", "X...X"],
           "S": [".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."]}
ARC_SCALE, ARC_ORIGIN = 6.8, 220          # the arcs are drawn in a 100-unit box at (220, 220), x6.8
ARC_CENTRE, ARC_RADII, ARC_HALF_WIDTH, DOT_RADIUS = (50, 78), (20, 38, 56), 3.75, 7


def _arc_outline(cx: float, cy: float, r: float, w: float) -> str:
    """A 90-degree arc over the top, `2w` thick with round ends, as a filled outline."""
    s = math.sqrt(0.5)
    ro, ri = r + w, r - w
    pt = lambda radius, side: f"{cx + side * radius * s:.1f} {cy - radius * s:.1f}"   # noqa: E731
    return (f"M{pt(ro, -1)} A{ro:.1f} {ro:.1f} 0 0 1 {pt(ro, 1)} A{w:.1f} {w:.1f} 0 0 1 {pt(ri, 1)} "
            f"A{ri:.1f} {ri:.1f} 0 0 0 {pt(ri, -1)} A{w:.1f} {w:.1f} 0 0 1 {pt(ro, -1)} Z")


def shapes() -> dict[str, list[str]]:
    """Layer name -> SVG elements in the 1000-unit canvas: arcs, dot, and the caption's cells by colour."""
    cx = ARC_ORIGIN + ARC_SCALE * ARC_CENTRE[0]
    cy = ARC_ORIGIN + ARC_SCALE * ARC_CENTRE[1]
    out: dict[str, list[str]] = {
        "arcs": [f'<path d="{_arc_outline(cx, cy, ARC_SCALE * r, ARC_SCALE * ARC_HALF_WIDTH)}"/>' for r in ARC_RADII],
        "dot": [f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{ARC_SCALE * DOT_RADIUS:.1f}"/>'],
        "caption-ink": [], "caption-dim": [], "caption-tone": []}
    for k, letter in enumerate("AS"):
        x0 = CAPTION_AT[0] + k * 6 * CELL
        for j, row in enumerate(LETTERS[letter]):
            for i, ch in enumerate(row):
                if ch == ".":
                    continue
                layer = "caption-dim" if ch == "x" else "caption-tone" if letter == "S" else "caption-ink"
                out[layer].append(f'<rect x="{x0 + i * CELL + CELL * 0.04:.2f}" y="{CAPTION_AT[1] + j * CELL + CELL * 0.04:.2f}" '
                                  f'width="{CELL * 0.92:.2f}" height="{CELL * 0.92:.2f}"/>')
    return out


LAYER_COLOUR = {"arcs": "tone", "dot": "tone", "caption-ink": "ink", "caption-dim": "dim", "caption-tone": "tone"}
CAPTION = ("caption-ink", "caption-dim", "caption-tone")


def tile_svg(look: dict[str, str], caption: bool = True, full_bleed: bool = False) -> str:
    """The whole logo in one look, as one SVG."""
    layers = shapes()
    tile = ('<rect data-c="tile" x="0" y="0" width="1000" height="1000" fill="{tile}"/>' if full_bleed else
            f'<rect data-c="tile" x="{TILE[0]}" y="{TILE[0]}" width="{TILE[1] - TILE[0]}" height="{TILE[1] - TILE[0]}" '
            f'rx="{TILE_RADIUS}" fill="{{tile}}" stroke="{{edge}}" stroke-width="4"/>').format(**look)
    body = "".join(f'<g data-c="{LAYER_COLOUR[name]}" fill="{look[LAYER_COLOUR[name]]}">{"".join(items)}</g>'
                   for name, items in layers.items() if caption or name not in CAPTION)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000">{tile}{body}</svg>'


# --- Apple: one SVG per layer, colours in icon.json ------------------------------------------------

def _srgb(hex_colour: str) -> str:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return f"srgb:{r:.5f},{g:.5f},{b:.5f},1.00000"


def _fills(role: str) -> list[dict]:
    return [{"value": {"solid": _srgb(LIGHT[role])}},
            {"appearance": "dark", "value": {"solid": _srgb(DARK[role])}},
            {"appearance": "tinted", "value": {"solid": _srgb("#FFFFFF")}}]


def write_apple() -> None:
    """The layers are full-bleed 1024 canvases: the system draws the tile and masks it."""
    assets = ICON_DIR / "Assets"
    for old in assets.glob("*.svg"):
        old.unlink()
    scale = 1024 / (TILE[1] - TILE[0])
    for name, items in shapes().items():
        (assets / f"{name}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">\n'
            "  <!-- Generated by scripts/make_app_icon.py from the brand's v3 logo; do not edit. The colour\n"
            "       comes from icon.json. -->\n"
            f'  <g fill="#FFFFFF" transform="scale({scale:.6f}) translate(-{TILE[0]} -{TILE[0]})">\n'
            + "".join(f"    {item}\n" for item in items) + "  </g>\n</svg>\n", encoding="utf-8", newline="\n")
    group = lambda names, shadow, translucency: {   # noqa: E731
        "layers": [{"image-name": f"{n}.svg", "name": n, "fill-specializations": _fills(LAYER_COLOUR[n])} for n in names],
        "shadow": {"kind": "neutral", "opacity": shadow}, "translucency": {"enabled": True, "value": translucency}}
    icon = {"fill-specializations": [{"value": {"automatic-gradient": _srgb(LIGHT["tile"])}},
                                     {"appearance": "dark", "value": {"automatic-gradient": _srgb(DARK["tile"])}}],
            "groups": [group(("arcs", "dot"), 0.5, 0.4), group(CAPTION, 0.4, 0.3)],
            "supported-platforms": {"circles": ["watchOS"], "squares": "shared"}}
    (ICON_DIR / "icon.json").write_text(json.dumps(icon, indent=2) + "\n", encoding="utf-8", newline="\n")


# --- site and Windows ------------------------------------------------------------------------------

def write_site_logo() -> None:
    """One file, both looks: the light drawing, and the dark colours under prefers-color-scheme."""
    dark = "".join(f'[data-c="{role}"]{{fill:{DARK[role]}}}' for role in ("tile", "ink", "dim", "tone"))
    head = ("\n<!-- Ambysto Steady: the brand's v3 app logo, made by scripts/make_app_icon.py. -->\n"
            f'<style>@media (prefers-color-scheme: dark){{{dark}[data-c="tile"]{{stroke:{DARK["edge"]}}}}}</style>')
    svg = tile_svg(LIGHT).replace('viewBox="0 0 1000 1000">', 'viewBox="0 0 1000 1000">' + head, 1)
    (SITE_IMG / "logo.svg").write_text(svg + "\n", encoding="utf-8", newline="\n")


def render(svg: str, png: Path, size: int, margin: float) -> None:
    browser = next((b for b in BROWSERS if Path(b).exists()), None) or shutil.which("msedge")
    if not browser:
        raise SystemExit("Edge or Chrome is needed to render the SVG")
    pad = size * margin
    html = (f"<!doctype html><meta charset=utf-8><style>html,body{{margin:0;background:transparent;overflow:hidden}}"
            f"svg{{position:absolute;left:{pad}px;top:{pad}px;width:{size - 2 * pad}px;height:{size - 2 * pad}px}}"
            f"</style>{svg}")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run([browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--default-background-color=00000000",
                        f"--window-size={size},{size}", f"--screenshot={png}", page.as_uri()],
                       check=True, capture_output=True, timeout=60)


def _open(png: Path, size: int):
    from PIL import Image
    img = Image.open(png).convert("RGBA")
    return img.crop((0, 0, size, size)) if img.size != (size, size) else img   # headless chrome rows


def main() -> int:
    from PIL import Image
    write_apple()
    write_site_logo()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        full, small, touch = (Path(tmp) / n for n in ("full.png", "small.png", "touch.png"))
        # The tile's own corners and edge, inside the transparent margin (v3 draws them in its canvas).
        render(tile_svg(DARK).replace('viewBox="0 0 1000 1000"', 'viewBox="20 20 960 960"'), full, 1024, MARGIN)
        render(tile_svg(DARK, caption=False).replace('viewBox="0 0 1000 1000"', 'viewBox="20 20 960 960"'),
               small, 1024, MARGIN)
        render(tile_svg(DARK, full_bleed=True).replace('viewBox="0 0 1000 1000"', 'viewBox="20 20 960 960"'),
               touch, 180, 0)
        big, little = _open(full, 1024), _open(small, 1024)
        big.save(OUT_DIR / "app-icon.png")
        # Every size made here (Pillow takes a frame whose size matches exactly), the largest first.
        frames = [(little if s[0] <= SMALL else big).resize(s, Image.Resampling.LANCZOS) for s in reversed(SIZES)]
        frames[0].save(OUT_DIR / "app.ico", sizes=SIZES, append_images=frames[1:])
        _open(touch, 180).save(SITE_IMG / "apple-touch-icon.png")
    print("wrote", ICON_DIR, SITE_IMG / "logo.svg", SITE_IMG / "apple-touch-icon.png", OUT_DIR / "app.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
