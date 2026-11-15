"""Text, caption and image sprites rendered with Pillow."""

from __future__ import annotations

import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from ..project import TextStyle, VeditError

REPO_FONTS = Path(__file__).resolve().parents[3] / "assets" / "fonts"

DEFAULT_FONTS = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _font_dirs() -> list[Path]:
    dirs = [REPO_FONTS]
    if sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    elif sys.platform == "win32":
        dirs += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path.home() / ".local/share/fonts", Path.home() / ".fonts"]
    return [d for d in dirs if d.exists()]


@lru_cache
def find_font(name: str | None) -> str | None:
    """Font path from a file path, a file name, or a family-ish name ("Montserrat Bold")."""
    if name:
        p = Path(name).expanduser()
        if p.exists():
            return str(p)
        want = name.lower().replace(" ", "").replace("-", "")
        best = None
        for d in _font_dirs():
            for f in d.rglob("*"):
                if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                    continue
                stem = f.stem.lower().replace(" ", "").replace("-", "")
                if stem == want:
                    return str(f)
                if best is None and want in stem:
                    best = str(f)
        if best:
            return best
        if sys.platform.startswith("linux"):
            try:
                out = subprocess.run(["fc-match", "-f", "%{file}", name], capture_output=True, text=True).stdout
                if out and Path(out).exists():
                    return out
            except FileNotFoundError:
                pass
        raise VeditError(f"Font '{name}' not found. Put a .ttf/.otf in assets/fonts/ or pass a full path.")
    for f in DEFAULT_FONTS:
        if Path(f).exists():
            return f
    return None


@lru_cache(maxsize=64)
def load_font(name: str | None, size: int) -> ImageFont.FreeTypeFont:
    path = find_font(name)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def rgba(color: str, alpha: int | None = None) -> tuple[int, int, int, int]:
    try:
        c = ImageColor.getcolor(color, "RGBA")
    except ValueError:
        raise VeditError(f"Invalid colour '{color}' (use #RRGGBB, #RRGGBBAA or a CSS name)") from None
    return (c[0], c[1], c[2], c[3] if alpha is None else alpha)


def _layout(words_by_line: list[list[str]], font, max_w: float) -> list[list[tuple[int, str]]]:
    """Wrap words into lines. Returns lines of (global_word_index, word)."""
    space = font.getlength(" ")
    lines: list[list[tuple[int, str]]] = []
    idx = 0
    for para in words_by_line:
        line: list[tuple[int, str]] = []
        w = 0.0
        for word in para:
            ww = font.getlength(word)
            if line and w + space + ww > max_w:
                lines.append(line)
                line, w = [], 0.0
            w = ww if not line else w + space + ww
            line.append((idx, word))
            idx += 1
        lines.append(line)
    return lines


def render_text(text: str, style: TextStyle, scale: float = 1.0, highlight: int | None = None, reveal: float = 1.0) -> Image.Image:
    """Render text to a tight RGBA sprite. `highlight` = word index drawn in highlight colour."""
    if style.uppercase:
        text = text.upper()
    size = max(6, round(style.size * scale))
    font = load_font(style.font, size)
    stroke = round(style.stroke_width * scale) if style.stroke_width else 0
    words = [ln.split() for ln in text.split("\n")]
    lines = _layout(words, font, style.max_width * scale)
    ascent, descent = font.getmetrics()
    line_h = (ascent + descent) * style.line_spacing
    space = font.getlength(" ")
    widths = [sum(font.getlength(w) for _, w in ln) + space * max(0, len(ln) - 1) for ln in lines]
    block_w = max(widths) if widths else 1
    block_h = line_h * len(lines) - (line_h - (ascent + descent))
    pad_bg = round(style.bg_padding * scale) if style.bg_color else 0
    shadow_off = round(5 * scale) if style.shadow else 0
    pad = stroke + pad_bg + shadow_off * 2 + 2
    W, H = int(block_w + 2 * pad) + 1, int(block_h + 2 * pad) + 1
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    if style.bg_color:
        ImageDraw.Draw(img).rounded_rectangle(
            [pad - pad_bg, pad - pad_bg, pad + block_w + pad_bg, pad + block_h + pad_bg],
            radius=round(style.bg_radius * scale), fill=rgba(style.bg_color),
        )

    total_chars = sum(len(w) for ln in lines for _, w in ln)
    budget = round(total_chars * reveal)

    def draw_words(draw: ImageDraw.ImageDraw, shadow: bool) -> None:
        left = budget
        for li, ln in enumerate(lines):
            if style.align == "center":
                x = pad + (block_w - widths[li]) / 2
            elif style.align == "right":
                x = pad + block_w - widths[li]
            else:
                x = pad
            y = pad + li * line_h
            for wi, word in ln:
                shown = word[: max(0, left)]
                left -= len(word)
                if shown:
                    if shadow:
                        draw.text((x + shadow_off, y + shadow_off), shown, font=font, fill=(0, 0, 0, 150),
                                  stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
                    else:
                        color = style.highlight_color if highlight is not None and wi == highlight else style.color
                        draw.text((x, y), shown, font=font, fill=rgba(color), stroke_width=stroke,
                                  stroke_fill=rgba(style.stroke_color) if stroke else None)
                x += font.getlength(word) + space

    if style.shadow:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw_words(ImageDraw.Draw(sh), shadow=True)
        sh = sh.filter(ImageFilter.GaussianBlur(max(1, 3 * scale)))
        img = Image.alpha_composite(img, sh)
    draw_words(ImageDraw.Draw(img), shadow=False)
    return img


def render_image(path: Path, width: int | None, scale: float = 1.0) -> Image.Image:
    if not path.exists():
        raise VeditError(f"Image not found: {path}")
    img = Image.open(path).convert("RGBA")
    if width:
        w = max(1, round(width * scale))
        img = img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS)
    elif scale != 1.0:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    return img
