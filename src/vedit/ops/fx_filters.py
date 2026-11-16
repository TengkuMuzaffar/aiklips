"""Clip filter effects (blur, vignette, grain, RGB split, film, VHS, shake...): one registry for the editor's
Effects browser, the `vedit fx add` CLI and the renderer.

An applied filter is stored on the clip as `Effect(type="fx", params={"id", "key", "on", <param>: value...})`.
Filters run on the clip after fit and colour grade, at canvas size, so sizes are in canvas pixels (scaled
for the half-size preview) and every source looks the same through them. The order in `clip.effects` is the
order they are applied in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..project import Clip, Effect, Project, VeditError


@dataclass
class Param:
    name: str
    label: str
    default: float | str
    min: float = 0.0
    max: float = 1.0
    step: float = 0.01
    choices: tuple[str, ...] = ()  # a choice param instead of a number
    px: bool = False  # canvas pixels: scaled with the render size

    def coerce(self, v: Any) -> float | str:
        if self.choices:
            v = str(v)
            if v not in self.choices:
                raise VeditError(f"{self.label} must be one of: {', '.join(self.choices)}")
            return v
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise VeditError(f"{self.label} must be a number") from None
        return max(self.min, min(self.max, f))


@dataclass
class Ctx:
    W: int
    H: int
    scale: float  # render size / canvas size (0.5 for the preview)
    fps: float


@dataclass
class Fx:
    key: str
    label: str
    category: str
    about: str
    tags: tuple[str, ...]
    params: list[Param]
    build: Callable[[dict, Ctx], str]  # -> an FFmpeg filter chain (single input, single output)
    split: bool = False  # True when `build` returns a graph with [in]/[out] pads instead of a plain chain
    fields: dict[str, Param] = field(init=False)

    def __post_init__(self) -> None:
        self.fields = {p.name: p for p in self.params}


def _n(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _px(v: float, c: Ctx) -> float:
    return v * c.scale


# ---- filters -----------------------------------------------------------------------------------------------

def _blur(p, c):
    return f"gblur=sigma={_n(max(0.1, _px(p['amount'], c)))}"


def _sharpen(p, c):
    return f"unsharp=lx=5:ly=5:la={_n(p['amount'])}:cx=5:cy=5:ca=0"


def _denoise(p, c):
    s = p["amount"]
    return f"hqdn3d={_n(s)}:{_n(s * 0.75)}:{_n(s * 1.5)}:{_n(s * 1.1)}"


def _glow(p, c):
    sigma = max(1.0, _px(p["radius"], c))
    # screen-blend in RGB: blending YUV chroma planes with "screen" would shift the colours
    return (f"format=gbrp,split[a][b];[b]gblur=sigma={_n(sigma)}[g];"
            f"[a][g]blend=all_mode=screen:all_opacity={_n(p['amount'])},format=yuv420p")


def _vignette(p, c):
    return f"vignette=angle={_n(0.15 + 0.85 * p['amount'] * 0.785)}:mode=forward"


def _grain(p, c):
    return f"noise=alls={int(round(p['amount']))}:allf=t+u"


def _rgb_split(p, c):
    d = max(1, int(round(_px(p["amount"], c))))
    return f"rgbashift=rh={-d}:bh={d}:edge=smear"


def _edges(p, c):
    return "edgedetect=mode=colormix:high=0.2:low=0.05" if p["style"] == "colour" else "edgedetect=mode=wires:high=0.2:low=0.05,negate"


def _posterize(p, c):
    n = max(2, int(round(p["levels"])))
    step = 256 / n
    f = f"floor(val/{_n(step)})*{_n(step)}+{_n(step / 2)}"
    return f"lutrgb=r='{f}':g='{f}':b='{f}'"


def _invert(p, c):
    return "negate"


def _mirror(p, c):
    return {"horizontal": "hflip", "vertical": "vflip", "both": "hflip,vflip"}[p["direction"]]


def _pixelate(p, c):
    b = max(2, int(round(_px(p["size"], c))))
    return f"scale=max(2\\,trunc(iw/{b})):max(2\\,trunc(ih/{b})),scale={c.W}:{c.H}:flags=neighbor"


def _letterbox(p, c):
    target = {"2.39:1": 2.39, "2:1": 2.0, "1.85:1": 1.85, "16:9": 16 / 9}[p["ratio"]]
    bar = max(0, int(round((c.H - c.W / target) / 2)))
    if bar <= 0:  # a landscape frame is already that wide: bars on the sides would be wrong, so do nothing
        return "null"
    return f"drawbox=x=0:y=0:w=iw:h={bar}:c=black:t=fill,drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:c=black:t=fill"


def _film(p, c):
    a = p["amount"]
    return (f"eq=saturation={_n(1 - 0.25 * a)}:contrast={_n(1 + 0.08 * a)},"
            f"noise=alls={int(round(6 + 14 * a))}:allf=t+u,vignette=angle={_n(0.3 + 0.35 * a)}")


def _vhs(p, c):
    a = p["amount"]
    d = max(1, int(round(_px(4 + 6 * a, c))))
    return (f"rgbashift=rh={-d}:bh={d}:edge=smear,eq=saturation={_n(1 + 0.3 * a)}:contrast={_n(1 - 0.08 * a)},"
            f"gblur=sigma={_n(max(0.1, _px(1.2 * a, c)))}:sigmaV=0.1,noise=alls={int(round(8 + 16 * a))}:allf=t")


def _trails(p, c):
    n = max(2, int(round(p["frames"])))
    return f"tmix=frames={n}"


def _shake(p, c):
    amp = max(0.5, _px(p["amount"], c))
    f = p["speed"]
    z = 1 + 2.4 * amp / min(c.W, c.H)
    w, h = int(round(c.W * z / 2)) * 2, int(round(c.H * z / 2)) * 2
    return (f"scale={w}:{h},crop={c.W}:{c.H}:"
            f"x='(in_w-out_w)/2+sin(t*{_n(f * 2.1)})*{_n(amp)}':y='(in_h-out_h)/2+cos(t*{_n(f * 1.7)})*{_n(amp * 0.6)}'")


def _zoom_pulse(p, c):
    a, period = p["amount"], max(0.1, p["period"])
    z = f"(1+{_n(a)}*(0.5+0.5*sin(2*PI*t/{_n(period)})))"
    return f"scale=w='trunc(iw*{z}/2)*2':h='trunc(ih*{z}/2)*2':eval=frame,crop={c.W}:{c.H}"


FILTERS: dict[str, Fx] = {f.key: f for f in [
    # Blur & sharpen
    Fx("blur", "Blur", "Blur & sharpen", "Soft gaussian blur over the whole shot", ("soft", "defocus", "gaussian"),
       [Param("amount", "Amount", 12, 1, 80, 1, px=True)], _blur),
    Fx("sharpen", "Sharpen", "Blur & sharpen", "Crisper detail for soft footage", ("crisp", "detail", "unsharp"),
       [Param("amount", "Amount", 0.8, 0.1, 2.5, 0.05)], _sharpen),
    Fx("denoise", "Denoise", "Blur & sharpen", "Clean up grainy low-light footage", ("noise", "clean", "low light"),
       [Param("amount", "Strength", 4, 1, 12, 0.5)], _denoise),
    Fx("glow", "Glow", "Blur & sharpen", "Dreamy bloom around bright areas", ("bloom", "dreamy", "soft focus", "halation"),
       [Param("amount", "Strength", 0.45, 0.05, 1, 0.05), Param("radius", "Radius", 18, 2, 80, 1, px=True)], _glow, split=True),
    # Stylize
    Fx("vignette", "Vignette", "Stylize", "Darker corners that pull the eye to the centre", ("corners", "dark edges", "focus"),
       [Param("amount", "Strength", 0.5, 0.05, 1, 0.05)], _vignette),
    Fx("grain", "Film grain", "Stylize", "Moving film grain texture", ("noise", "texture", "analog"),
       [Param("amount", "Amount", 14, 2, 60, 1)], _grain),
    Fx("rgb_split", "RGB split", "Stylize", "Chromatic aberration: red and blue pulled apart", ("chromatic", "aberration", "glitch", "shift"),
       [Param("amount", "Offset", 8, 1, 40, 1, px=True)], _rgb_split),
    Fx("edges", "Sketch / edges", "Stylize", "Outline drawing look", ("sketch", "outline", "drawing", "edge detect"),
       [Param("style", "Style", "pencil", choices=("pencil", "colour"))], _edges),
    Fx("posterize", "Posterize", "Stylize", "Fewer colour levels, graphic poster look", ("comic", "graphic", "levels"),
       [Param("levels", "Levels", 5, 2, 16, 1)], _posterize),
    Fx("invert", "Invert", "Stylize", "Negative image", ("negative", "negate"), [], _invert),
    Fx("mirror", "Mirror / flip", "Stylize", "Flip the shot", ("flip", "reverse", "hflip"),
       [Param("direction", "Direction", "horizontal", choices=("horizontal", "vertical", "both"))], _mirror),
    Fx("pixelate", "Pixelate", "Stylize", "Big blocky pixels over the whole shot", ("mosaic", "8-bit", "retro", "censor"),
       [Param("size", "Block size", 24, 4, 120, 1, px=True)], _pixelate),
    # Looks
    Fx("letterbox", "Cinema bars", "Looks", "Black bars top and bottom for a widescreen feel", ("letterbox", "cinematic", "widescreen", "bars"),
       [Param("ratio", "Ratio", "2.39:1", choices=("2.39:1", "2:1", "1.85:1", "16:9"))], _letterbox),
    Fx("film", "Film look", "Looks", "Muted colour, grain and soft vignette", ("analog", "cinematic", "vintage", "35mm"),
       [Param("amount", "Strength", 0.6, 0.1, 1, 0.05)], _film),
    Fx("vhs", "VHS", "Looks", "Retro tape: colour bleed, softness and noise", ("retro", "tape", "90s", "glitch", "analog"),
       [Param("amount", "Strength", 0.6, 0.1, 1, 0.05)], _vhs),
    # Motion
    Fx("shake", "Camera shake", "Motion", "Handheld / impact shake", ("handheld", "impact", "earthquake", "energy"),
       [Param("amount", "Amount", 14, 2, 60, 1, px=True), Param("speed", "Speed", 9, 1, 30, 0.5)], _shake),
    Fx("zoom_pulse", "Zoom pulse", "Motion", "Breathing push-in and out", ("pulse", "beat", "breathing", "bounce"),
       [Param("amount", "Amount", 0.06, 0.01, 0.3, 0.01), Param("period", "Period (s)", 0.8, 0.2, 4, 0.05)], _zoom_pulse),
    Fx("trails", "Motion trails", "Motion", "Ghosting echo behind moving things", ("echo", "ghost", "motion blur", "smear"),
       [Param("frames", "Length (frames)", 4, 2, 10, 1)], _trails),
]}

CATEGORIES = ("Blur & sharpen", "Stylize", "Looks", "Motion")


def catalog() -> list[dict]:
    """The filter list for the UI / CLI: key, label, category, about, tags, params (with ranges)."""
    return [{
        "key": f.key, "label": f.label, "category": f.category, "about": f.about, "tags": list(f.tags),
        "params": [{"name": p.name, "label": p.label, "default": p.default, "min": p.min, "max": p.max, "step": p.step,
                    "choices": list(p.choices)} for p in f.params],
    } for f in FILTERS.values()]


def get(key: str) -> Fx:
    if key not in FILTERS:
        raise VeditError(f"Unknown effect '{key}'. Effects: {', '.join(FILTERS)}")
    return FILTERS[key]


def resolved(eff: Effect) -> dict:
    """The effect's params with defaults filled in and values clamped to their ranges."""
    fx = get(str(eff.params.get("key")))
    return {p.name: p.coerce(eff.params.get(p.name, p.default)) for p in fx.params}


def is_filter(eff: Effect) -> bool:
    return eff.type == "fx"


def chain(eff: Effect, ctx: Ctx) -> str | None:
    """FFmpeg filter text for one applied filter, or None when it is switched off."""
    if not eff.params.get("on", True):
        return None
    fx = get(str(eff.params.get("key")))
    return fx.build(resolved(eff), ctx)


def apply_filters(g, clip: Clip, cur: str, ctx: Ctx) -> str:
    """Add this clip's filters to the graph `g` after `cur`; returns the new label."""
    for eff in clip.effects:
        if not is_filter(eff):
            continue
        text = chain(eff, ctx)
        if not text or text == "null":
            continue
        fx = get(str(eff.params["key"]))
        out = g.label("fxf")
        if fx.split:  # internal pads must be unique across the whole graph
            pre = g.label("p")
            body = text.replace("[a]", f"[{pre}a]").replace("[b]", f"[{pre}b]").replace("[g]", f"[{pre}g]")
            g.add(f"[{cur}]{body}[{out}]")
        else:
            g.add(f"[{cur}]{text}[{out}]")
        cur = out
    return cur


# ---- editing --------------------------------------------------------------------------------------------------

def _new_id(project: Project) -> str:
    used = {str(e.params.get("id")) for c in project.clips for e in c.effects}
    n = 1
    while f"fx{n}" in used:
        n += 1
    return f"fx{n}"


def add(project: Project, clip: Clip, key: str, params: dict | None = None) -> Effect:
    fx = get(key)
    vals = {p.name: p.default for p in fx.params}
    for k, v in (params or {}).items():
        if k not in fx.fields:
            raise VeditError(f"{fx.label} has no setting '{k}'. Settings: {', '.join(fx.fields) or 'none'}")
        vals[k] = fx.fields[k].coerce(v)
    eff = Effect(type="fx", params={"id": _new_id(project), "key": key, "on": True, **vals})
    clip.effects.append(eff)
    return eff


def find(project: Project, fx_id: str) -> tuple[Clip, Effect]:
    for c in project.clips:
        for e in c.effects:
            if is_filter(e) and e.params.get("id") == fx_id:
                return c, e
    raise VeditError(f"No effect '{fx_id}'. Effects: " + (", ".join(
        f"{e.params.get('id')} ({e.params.get('key')} on {c.id})" for c in project.clips for e in c.effects if is_filter(e)) or "none"))


def set_params(project: Project, fx_id: str, values: dict) -> Effect:
    _, eff = find(project, fx_id)
    fx = get(str(eff.params["key"]))
    for k, v in values.items():
        if k == "on":
            eff.params["on"] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        elif k in fx.fields:
            eff.params[k] = fx.fields[k].coerce(v)
        else:
            raise VeditError(f"{fx.label} has no setting '{k}'. Settings: {', '.join(fx.fields) or 'none'}")
    return eff


def reset(project: Project, fx_id: str) -> Effect:
    _, eff = find(project, fx_id)
    fx = get(str(eff.params["key"]))
    for p in fx.params:
        eff.params[p.name] = p.default
    eff.params["on"] = True
    return eff


def remove(project: Project, fx_id: str) -> Clip:
    clip, eff = find(project, fx_id)
    clip.effects = [e for e in clip.effects if e is not eff]
    return clip


def move(project: Project, fx_id: str, index: int) -> None:
    """Reorder among the clip's filters (index counts filters only; other effect types keep their places)."""
    clip, eff = find(project, fx_id)
    fl = [e for e in clip.effects if is_filter(e)]
    fl.remove(eff)
    fl.insert(max(0, min(index, len(fl))), eff)
    it = iter(fl)
    clip.effects = [next(it) if is_filter(e) else e for e in clip.effects]


def copy_to(project: Project, src_clip: Clip, targets: list[Clip]) -> int:
    """Copy src_clip's filters (replacing the targets' own filters). Returns how many clips changed."""
    fl = [e for e in src_clip.effects if is_filter(e)]
    n = 0
    for t in targets:
        if t is src_clip:
            continue
        t.effects = [e for e in t.effects if not is_filter(e)]
        for e in fl:
            t.effects.append(Effect(type="fx", params={**e.params, "id": _new_id(project)}))
        n += 1
    return n
