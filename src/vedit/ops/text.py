"""Text, caption and image sprites rendered with Pillow."""

from __future__ import annotations

import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from ..project import TextStyle, VeditError
from . import fonts as font_lib

REPO_FONTS = font_lib.FONT_DIR

DEFAULT_FONTS = [
    str(font_lib.FONT_DIR / font_lib.FONTS[font_lib.DEFAULT_FONT][0]),  # bundled, same look on every OS
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
        bundled = font_lib.path(name)
        if bundled and bundled.exists():
            return str(bundled)
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
                # fc-match never fails: it falls back to some default font (DejaVu…). Only accept a real match.
                out = subprocess.run(["fc-match", "-f", "%{family}\n%{file}", name], capture_output=True, text=True).stdout
                family, _, file = out.partition("\n")
                fams = [x.lower().replace(" ", "").replace("-", "") for x in family.split(",")]
                if file and Path(file).exists() and any(f and (f in want or want.startswith(f)) for f in fams):
                    return file
            except FileNotFoundError:
                pass
        raise VeditError(f"Font '{name}' not found. Bundled fonts: {', '.join(font_lib.FONTS)} (`vedit fonts`). "
                         "Or put a .ttf/.otf in assets/fonts/ or pass a full path.")
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


def _wlen(font, word: str, ls: float) -> float:
    """Width of a word including letter spacing."""
    return font.getlength(word) + ls * max(0, len(word) - 1)


def _layout(words_by_line: list[list[str]], font, max_w: float, ls: float = 0.0) -> list[list[tuple[int, str]]]:
    """Wrap words into lines. Returns lines of (global_word_index, word)."""
    space = font.getlength(" ") + ls
    lines: list[list[tuple[int, str]]] = []
    idx = 0
    for para in words_by_line:
        line: list[tuple[int, str]] = []
        w = 0.0
        for word in para:
            ww = _wlen(font, word, ls)
            if line and w + space + ww > max_w:
                lines.append(line)
                line, w = [], 0.0
            w = ww if not line else w + space + ww
            line.append((idx, word))
            idx += 1
        lines.append(line)
    return lines


def _gradient(colors: list[str], w: int, h: int, top: float, bottom: float) -> Image.Image:
    """Vertical gradient through `colors`, spanning rows top..bottom (clamped outside)."""
    cs = [rgba(c) for c in colors] or [(255, 255, 255, 255)]
    if len(cs) == 1:
        return Image.new("RGBA", (w, h), cs[0])
    col = Image.new("RGBA", (1, h))
    span = max(1.0, bottom - top)
    for y in range(h):
        p = min(1.0, max(0.0, (y - top) / span)) * (len(cs) - 1)
        i = min(len(cs) - 2, int(p))
        f = p - i
        col.putpixel((0, y), tuple(round(cs[i][k] + (cs[i + 1][k] - cs[i][k]) * f) for k in range(4)))
    return col.resize((w, h))


def render_text(text: str, style: TextStyle, scale: float = 1.0, highlight: int | None = None, reveal: float = 1.0,
                spans: list | None = None) -> Image.Image:
    """Render text to a tight RGBA sprite. `highlight` = word index drawn in highlight colour. `spans`, when given,
    is filled with where each word went (sprite px, before italic slant): {li, x0, x1, top, bottom, letters: [(x0, x1)]}
    plus one {"meta": ...} entry (pad, line_h, ink height, italic shear), for kinetic text (ops/kinetic.py)."""
    if style.uppercase:
        text = text.upper()
    if style.list_style != "none":  # every paragraph (line of the source text) becomes a list item
        paras = [p for p in text.split("\n")]
        text = "\n".join((f"{i}. " if style.list_style == "number" else "\u2022 ") + p.strip()
                         for i, p in enumerate(paras, 1) if p.strip())
    size = max(6, round(style.size * scale))
    font = load_font(style.font, size)
    stroke = round(style.stroke_width * scale) if style.stroke_width else 0
    embolden = max(1, round(size * 0.028)) if style.bold else 0  # extra weight, drawn in the fill colour
    ls = style.letter_spacing * scale + embolden * 1.6  # bold letters need the room they grew into
    words = [ln.split() for ln in text.split("\n")]
    lines = _layout(words, font, style.max_width * scale, ls)
    ascent, descent = font.getmetrics()
    line_h = (ascent + descent) * style.line_spacing
    space = font.getlength(" ") + ls
    widths = [sum(_wlen(font, w, ls) for _, w in ln) + space * max(0, len(ln) - 1) for ln in lines]
    block_w = max(widths) if widths else 1
    block_h = line_h * len(lines) - (line_h - (ascent + descent))
    pad_bg = round(style.bg_padding * scale) if style.bg_color else 0
    shadow_off = round(5 * scale) if style.shadow else 0
    glow = round(style.glow_size * scale) if style.glow_color else 0
    pad = stroke + embolden + max(pad_bg, glow * 2) + shadow_off * 2 + 2
    W, H = int(block_w + 2 * pad) + 1, int(block_h + 2 * pad) + 1
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    if style.bg_color:
        ImageDraw.Draw(img).rounded_rectangle(
            [pad - pad_bg, pad - pad_bg, pad + block_w + pad_bg, pad + block_h + pad_bg],
            radius=round(style.bg_radius * scale), fill=rgba(style.bg_color),
        )

    total_chars = sum(len(w) for ln in lines for _, w in ln)
    budget = round(total_chars * reveal)

    def put(draw: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, **kw) -> None:
        if not ls:
            draw.text(xy, s, font=font, **kw)
            return
        for i, ch in enumerate(s):  # keep kerning: offset each letter by the width of the text before it
            draw.text((xy[0] + font.getlength(s[:i]) + i * ls, xy[1]), ch, font=font, **kw)

    deco: list[tuple[float, float, float]] = []  # (x0, x1, line top) of each drawn line, for underline / strike

    def draw_words(draw: ImageDraw.ImageDraw, mode: str) -> None:
        """mode: shadow | glow | fill | mask (fill-only alpha of the non-highlighted words, for gradients)."""
        left = budget
        sw = stroke + embolden
        for li, ln in enumerate(lines):
            if style.align == "center":
                x = pad + (block_w - widths[li]) / 2
            elif style.align == "right":
                x = pad + block_w - widths[li]
            else:
                x = pad
            y = pad + li * line_h
            x0, x_end = x, None
            for wi, word in ln:
                shown = word[: max(0, left)]
                left -= len(word)
                hi = highlight is not None and wi == highlight
                if shown:
                    if mode == "shadow":
                        put(draw, (x + shadow_off, y + shadow_off), shown, fill=(0, 0, 0, 150),
                            stroke_width=sw, stroke_fill=(0, 0, 0, 150))
                    elif mode == "glow":
                        put(draw, (x, y), shown, fill=rgba(style.glow_color), stroke_width=sw + glow // 3,
                            stroke_fill=rgba(style.glow_color))
                    elif mode == "mask":
                        if not hi:
                            put(draw, (x, y), shown, fill=255, stroke_width=embolden, stroke_fill=255)
                    else:
                        color = style.highlight_color if hi else style.color
                        if stroke:  # the outline goes around the emboldened letters
                            put(draw, (x, y), shown, fill=rgba(style.stroke_color), stroke_width=sw,
                                stroke_fill=rgba(style.stroke_color))
                        put(draw, (x, y), shown, fill=rgba(color), stroke_width=embolden,
                            stroke_fill=rgba(color) if embolden else None)
                    x_end = x + _wlen(font, shown, ls)
                if spans is not None and mode == "fill":
                    spans.append({"li": li, "x0": x, "x1": x + _wlen(font, word, ls), "top": y, "bottom": y + ascent + descent,
                                  "letters": [(x + font.getlength(word[:i]) + i * ls, x + font.getlength(word[:i + 1]) + i * ls)
                                              for i in range(len(word))]})
                x += _wlen(font, word, ls) + space
            if mode == "fill" and x_end is not None:
                deco.append((x0, x_end, y))

    if style.glow_color:
        gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw_words(ImageDraw.Draw(gl), "glow")
        gl = gl.filter(ImageFilter.GaussianBlur(max(1, glow)))
        img = Image.alpha_composite(Image.alpha_composite(img, gl), gl)  # twice: a glow should read at phone size
    if style.shadow:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw_words(ImageDraw.Draw(sh), "shadow")
        sh = sh.filter(ImageFilter.GaussianBlur(max(1, 3 * scale)))
        img = Image.alpha_composite(img, sh)
    draw_words(ImageDraw.Draw(img), "fill")
    if style.underline or style.strike:
        d = ImageDraw.Draw(img)
        th = max(2, round(size * 0.065))
        for x0, x1, top in deco:
            for on, at in ((style.underline, top + ascent + descent * 0.45), (style.strike, top + ascent * 0.62)):
                if on:
                    d.rectangle([x0, at - th / 2, x1, at + th / 2], fill=rgba(style.color),
                                outline=rgba(style.stroke_color) if stroke else None, width=min(stroke, 3) if stroke else 0)
    if style.gradient:
        mask = Image.new("L", (W, H), 0)
        draw_words(ImageDraw.Draw(mask), "mask")
        box = mask.getbbox()  # span the letters themselves, not the line box, so both colours fully show
        if box:
            img.paste(_gradient(style.gradient, W, H, box[1], box[3] - 1), (0, 0), mask)
    if spans is not None:
        spans.append({"meta": True, "W": W, "H": H, "shear": 0.2 if style.italic else 0.0})
    if style.italic:  # slant: shift each row right by how far it is above the bottom
        k = 0.2
        extra = int(k * H) + 1
        img = img.transform((W + extra, H), Image.AFFINE, (1, k, -k * H, 0, 1, 0), resample=Image.BICUBIC)
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
