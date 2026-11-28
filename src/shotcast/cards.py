"""Link-preview images (1200x630) for LinkedIn and other sites, drawn with Pillow at build time."""
from __future__ import annotations

from pathlib import Path

W, H = 1200, 630
BG, INK, MUTED, GREEN, TURF = "#f6f5ef", "#1b201c", "#657067", "#16833a", "#e3f1d9"
DOT = {"goal": "#16833a", "saved": "#1479b8", "woodwork": "#c88a00", "off_target": "#d64545",
       "blocked": "#8a948d", "own_goal": "#8a948d"}
_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf", "/usr/share/fonts/dejavu/DejaVuSans{b}.ttf",
          "/Library/Fonts/Arial{b}.ttf", "/System/Library/Fonts/Supplemental/Arial{b}.ttf")


def available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for pattern in _FONTS:
        name = pattern.format(b="-Bold" if bold and "DejaVu" in pattern else (" Bold" if bold else ""))
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _fit(draw, text: str, bold: bool, start: int, max_w: int, floor: int = 34):
    size = start
    while size > floor:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(floor, bold)


def _pitch(draw, x0: int, y0: int, w: int, h: int, shots: list[dict]) -> None:
    draw.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=14, fill=TURF)
    pad, white = 16, "#ffffff"
    ix, iy, iw, ih = x0 + pad, y0 + pad + 14, w - 2 * pad, h - 2 * pad - 14
    draw.rectangle([ix, iy, ix + iw, iy + ih], outline=white, width=3)
    pw, pd = iw * 40.3 / 68, ih * 16.5 / 52.5
    draw.rectangle([ix + (iw - pw) / 2, iy, ix + (iw + pw) / 2, iy + pd], outline=white, width=3)
    sw, sd = iw * 18.32 / 68, ih * 5.5 / 52.5
    draw.rectangle([ix + (iw - sw) / 2, iy, ix + (iw + sw) / 2, iy + sd], outline=white, width=3)
    gw = iw * 7.32 / 68
    draw.rectangle([ix + (iw - gw) / 2, iy - 14, ix + (iw + gw) / 2, iy], outline=MUTED, width=3)
    for s in sorted(shots, key=lambda s: s.get("o") == "goal"):
        if s.get("x") is None or s["x"] < 50:
            continue
        cx, cy = ix + s["y"] / 100 * iw, iy + (100 - s["x"]) / 50 * ih
        r = 9 if s.get("o") == "goal" else 6
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DOT.get(s.get("o"), MUTED),
                     outline="#ffffff" if s.get("o") == "goal" else None, width=2)


def render_card(path: str | Path, title: str, subtitle: str = "", stats: list[tuple[str, str]] = (),
                shots: list[dict] | None = None, brand: str = "Shotcast",
                byline: str = "by Amir Mirkazemi") -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=GREEN)
    d.line([(70, 92), (70, 62), (106, 62), (106, 92)], fill=GREEN, width=6)       # goal-post mark
    d.ellipse([82, 74, 94, 86], fill=GREEN)
    d.text((124, 56), brand, font=_font(38, True), fill=INK)
    has_map = bool(shots)
    max_w = 580 if has_map else W - 140
    f = _fit(d, title, True, 84, max_w)
    d.text((70, 170), title, font=f, fill=INK)
    if subtitle:
        d.text((70, 170 + f.size + 20), subtitle, font=_fit(d, subtitle, False, 36, max_w, 24), fill=MUTED)
    step = 170 if has_map else 230
    for i, (value, label) in enumerate(stats[:4 if not has_map else 3]):
        x = 70 + i * step
        d.text((x, 430), str(value), font=_font(60, True), fill=GREEN)
        d.text((x, 505), label, font=_font(26), fill=MUTED)
    d.text((70, 575), byline, font=_font(26), fill=MUTED)
    if has_map:
        _pitch(d, 690, 60, 440, 510, shots)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out
