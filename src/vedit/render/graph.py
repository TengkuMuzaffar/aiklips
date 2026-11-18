"""Turn project.json into one FFmpeg command (inputs + filter_complex)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import media
from ..ops import color
from ..project import Clip, Project, VeditError, resolve_media, state_dir
from .layers import ensure_layer, even, layer_span


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def atempo_chain(speed: float) -> str:
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-4:
        parts.append(f"atempo={_f(s)}")
    return ",".join(parts) or "anull"


def ffcolor(c: str) -> str:
    return "0x" + c[1:] if c.startswith("#") else c


@dataclass
class Graph:
    inputs: list[list[str]] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    n: int = 0

    def add_input(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1

    def label(self, base: str) -> str:
        self.n += 1
        return f"{base}{self.n}"

    def add(self, f: str) -> None:
        self.filters.append(f)


def fit_filters(g: Graph, clip: Clip, src_label: str, W: int, H: int, fps: float, bg: str, dur: float,
                hold_head: float = 0.0, hold_tail: float = 0.0, alpha: bool = False) -> str:
    """Scale/crop/pad a clip onto the W x H canvas. Returns the output label.

    hold_head / hold_tail: seconds of frozen first / last frame to add (transition
    handles when the source has no spare footage before / after the clip).
    alpha: keep transparency (tracks under the video show through): letterbox bars are transparent, not `bg`.
    """
    z = max(1.0, clip.zoom)
    fx, fy = min(max(clip.focus_x, 0), 1), min(max(clip.focus_y, 0), 1)
    out = g.label("fit")
    head = f"start_mode=clone:start_duration={_f(hold_head)}:" if hold_head > 1e-3 else ""
    tail = (f"fps={_f(fps)},setsar=1,format={'yuva420p' if alpha else 'yuv420p'},tpad={head}stop_mode=clone:stop_duration={_f(hold_tail + 0.2)},"
            f"trim=duration={_f(dur)},setpts=PTS-STARTPTS,settb=AVTB")
    if clip.zoom_to:
        z1 = max(1.0, clip.zoom_to)
        frames = max(1, round(dur * fps))
        g.add(
            f"[{src_label}]scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase,crop={2 * W}:{2 * H}:(iw-{2 * W})*{_f(fx)}:(ih-{2 * H})*{_f(fy)},"
            f"zoompan=z='{_f(z)}+({_f(z1)}-{_f(z)})*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={_f(fps)},{tail}[{out}]"
        )
        return out
    zw, zh = even(W * z), even(H * z)
    if clip.fit == "cover":
        g.add(
            f"[{src_label}]scale={zw}:{zh}:force_original_aspect_ratio=increase:force_divisible_by=2,"
            f"crop={W}:{H}:(iw-{W})*{_f(fx)}:(ih-{H})*{_f(fy)},{tail}[{out}]"
        )
    elif clip.fit == "contain":
        g.add(
            f"[{src_label}]scale={zw}:{zh}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"crop='min(iw,{W})':'min(ih,{H})':(iw-ow)*{_f(fx)}:(ih-oh)*{_f(fy)},"
            f"{'format=rgba,' if alpha else ''}pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={'black@0' if alpha else ffcolor(bg)},{tail}[{out}]"
        )
    else:  # blur: blurred cover background + contained foreground
        a, b, bgl, fgl = g.label("ba"), g.label("bb"), g.label("bbg"), g.label("bfg")
        g.add(f"[{src_label}]split=2[{a}][{b}]")
        g.add(
            f"[{a}]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
            f"gblur=sigma=12,eq=brightness=-0.08,scale={W}:{H}[{bgl}]"
        )
        g.add(
            f"[{b}]scale={zw}:{zh}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"crop='min(iw,{W})':'min(ih,{H})':(iw-ow)*{_f(fx)}:(ih-oh)*{_f(fy)}[{fgl}]"
        )
        g.add(f"[{bgl}][{fgl}]overlay=(W-w)/2:(H-h)/2,{tail}[{out}]")
    return out


def _focus_composite(g: Graph, prm: dict, cur: str, mk: str, out: str, sw: int, sh: int, fps: float, dur: float) -> None:
    """Highlight the tracked object. `mk` is white *outside* the object (focus masks are inverted).

    spotlight  blur + darken everything around it        colorpop  everything around it black & white
    brighten   lift the object itself                     glow      soft coloured halo around it
    outline    crisp coloured stroke around it
    Every style is compositing only (no re-tracking), and the ring for glow / outline is built at half size."""
    style = prm.get("style", "spotlight")
    k = min(max(float(prm.get("strength", 1.0)), 0.0), 2.0)
    if style in ("spotlight", "colorpop"):  # change the outside
        s1, s2, fxl, fxm = g.label("s"), g.label("s"), g.label("mf"), g.label("mm")
        g.add(f"[{cur}]split=2[{s1}][{s2}]")
        if style == "colorpop":
            chain = f"hue=s={_f(max(0.0, 1 - k))}"
        else:
            sigma, dim = float(prm.get("blur", 14)), min(max(float(prm.get("dim", 0.35)), 0.0), 1.0)
            kk = _f(1 - dim)
            chain = (f"gblur=sigma={_f(sigma)}," if sigma > 0 else "") + f"colorchannelmixer=rr={kk}:gg={kk}:bb={kk}"
        g.add(f"[{s1}]{chain}[{fxl}]")
        g.add(f"[{fxl}][{mk}]alphamerge[{fxm}]")
        g.add(f"[{s2}][{fxm}]overlay=0:0:format=auto[{out}]")
        return
    obj = g.label("obj")
    g.add(f"[{mk}]negate[{obj}]")
    if style == "brighten":
        s1, s2, fxl, fxm = g.label("s"), g.label("s"), g.label("mf"), g.label("mm")
        g.add(f"[{cur}]split=2[{s1}][{s2}]")
        g.add(f"[{s1}]eq=brightness={_f(0.09 * k)}:contrast={_f(1 + 0.08 * k)}:saturation={_f(1 + 0.3 * k)}[{fxl}]")
        g.add(f"[{fxl}][{obj}]alphamerge[{fxm}]")
        g.add(f"[{s2}][{fxm}]overlay=0:0:format=auto[{out}]")
        return
    if style not in ("glow", "outline"):
        raise VeditError(f"Unknown focus style {style}")
    # ring = (the object grown outwards) minus the object; width is in px of a 1080p frame
    wpx = max(1.0, float(prm.get("width", 30 if style == "glow" else 6)) * min(sw, sh) / 1080)
    o1, o2, grown, ring, col, halo = (g.label(x) for x in ("o", "o", "grow", "ring", "rc", "halo"))
    # hard-edge the object first: growing a feathered mask from its faint outer tail would add the feather to the width
    g.add(f"[{obj}]lut=y='if(gt(val,128),255,0)',split=2[{o1}][{o2}]")
    hw, hh = max(2, sw // 4 * 2), max(2, sh // 4 * 2)
    if style == "glow":  # soft falloff, strongest at the edge
        g.add(f"[{o1}]scale={hw}:{hh},gblur=sigma={_f(wpx / 2)},scale={sw}:{sh}[{grown}]")
        g.add(f"[{grown}][{o2}]blend=all_expr='min(255,max(A-B,0)*{_f(3.0 * k)})'[{ring}]")
    else:  # outline: grow by ~width (blur + threshold), cut out the object, anti-alias
        g.add(f"[{o1}]scale={hw}:{hh},gblur=sigma={_f(wpx / 3)},lut=y='if(gt(val,16),255,0)',scale={sw}:{sh}[{grown}]")
        g.add(f"[{grown}][{o2}]blend=all_expr='if(gt(B,128),0,A)',gblur=sigma=0.8[{ring}]")
    g.add(f"color=c={ffcolor(str(prm.get('color') or '#FFD400'))}:s={sw}x{sh}:r={_f(fps)}:d={_f(dur)}[{col}]")
    g.add(f"[{col}][{ring}]alphamerge[{halo}]")
    g.add(f"[{cur}][{halo}]overlay=0:0:shortest=1:format=auto[{out}]")


def keep_alpha(g: Graph, src: str, colour) -> str:
    """Run `colour(label) -> label` (a grade, filters: they drop transparency) on the colour of an RGBA `src` and put
    its alpha back afterwards."""
    c, a, al, out = g.label("ka"), g.label("kb"), g.label("kal"), g.label("kam")
    g.add(f"[{src}]split=2[{c}][{a}]")
    g.add(f"[{a}]alphaextract[{al}]")
    cur = colour(c)
    g.add(f"[{cur}][{al}]alphamerge,format=yuva420p[{out}]")
    return out


def _chroma_fg(g: Graph, src: str, params: dict) -> str:
    """Green / blue screen keyed out of `src` (RGBA out), with spill removed and the matte edge pulled in."""
    from ..ops import chroma

    keyed = g.label("ck")
    g.add(f"[{src}]{chroma.key_chain(params)}[{keyed}]")
    shrink = chroma.shrink_chain(int(params.get("shrink", chroma.DEFAULTS["shrink"])))
    if not shrink:
        return keyed
    a, b, m, fg = g.label("s"), g.label("s"), g.label("ckm"), g.label("fg")
    g.add(f"[{keyed}]split=2[{a}][{b}]")
    g.add(f"[{b}]{shrink}[{m}]")
    g.add(f"[{a}][{m}]alphamerge[{fg}]")
    return fg


def _bg_composite(g: Graph, project: Project, pdir: Path, eff, cur: str, make_fg, out: str,
                  sw: int, sh: int, fps: float, dur_src: float) -> None:
    """Put the cut-out person (`make_fg(label)` -> RGBA label) over the replacement background -> `out`.
    Shared by the AI modes (alpha from a mask video) and chroma keying (alpha from the key)."""
    kind, _, val = str(eff.params.get("replace", "color:#00b140")).partition(":")
    if kind == "none":  # transparent: the tracks under the video show through
        fg = make_fg(cur)
        g.add(f"[{fg}]format=yuva420p[{out}]")
        return
    if kind == "blur":
        s1, s2, bb = g.label("s"), g.label("s"), g.label("bb")
        g.add(f"[{cur}]split=2[{s1}][{s2}]")
        g.add(f"[{s1}]gblur=sigma={_f(float(val or 25))}[{bb}]")
        fg = make_fg(s2)
        g.add(f"[{bb}][{fg}]overlay=0:0:format=auto[{out}]")
        return
    fg = make_fg(cur)
    bgl = g.label("bgsrc")
    if kind in ("image", "video"):
        p = resolve_media(pdir, val)
        if not p.exists():
            raise VeditError(f"Background {kind} not found: {p}")
        args = (["-loop", "1"] if kind == "image" else ["-stream_loop", "-1"]) + ["-t", _f(dur_src), "-i", str(p)]
        bi = g.add_input(args)
        g.add(
            f"[{bi}:v]fps={_f(fps)},scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
            f"setsar=1,setpts=PTS-STARTPTS[{bgl}]"
        )
    else:
        color = val if kind == "color" else project.canvas.background
        g.add(f"color=c={ffcolor(color)}:s={sw}x{sh}:r={_f(fps)}:d={_f(dur_src)}[{bgl}]")
    g.add(f"[{bgl}][{fg}]overlay=0:0:shortest=1:format=auto[{out}]")


def apply_effects(g: Graph, project: Project, pdir: Path, clip: Clip, cur: str, fps: float, log,
                  src_start: float | None = None, src_len: float | None = None) -> str:
    if not any(e.type != "fx" for e in clip.effects):  # filters ("fx") run later, at canvas size
        return cur
    from ..ops import effects  # optional heavy deps are imported lazily inside

    src = project.sources[clip.source]
    s0 = clip.in_ if src_start is None else src_start
    dur_src = clip.out - clip.in_ if src_len is None else src_len
    sw, sh = src.width or 1080, src.height or 1920
    for eff in clip.effects:
        if eff.type == "fx":
            continue
        art = effects.prepare(eff, project, pdir, clip.source, log)
        if eff.type == "bg" and eff.params.get("mode") == "chroma":
            out = g.label("fx")
            _bg_composite(g, project, pdir, eff, cur, lambda src: _chroma_fg(g, src, eff.params), out, sw, sh, fps, dur_src)
            cur = out
            continue
        if "mask" in art:
            mi = g.add_input(["-ss", _f(s0), "-t", _f(dur_src), "-i", str(art["mask"])])
            mk = g.label("mask")
            g.add(f"[{mi}:v]setpts=PTS-STARTPTS,scale={sw}:{sh},format=gray[{mk}]")
            out = g.label("fx")
            if eff.type == "bg":
                def alpha_from_mask(src: str, mk=mk) -> str:
                    fg = g.label("fg")
                    g.add(f"[{src}][{mk}]alphamerge[{fg}]")
                    return fg

                _bg_composite(g, project, pdir, eff, cur, alpha_from_mask, out, sw, sh, fps, dur_src)
            elif eff.type == "face_blur":
                s1, s2, bl, blm = g.label("s"), g.label("s"), g.label("bl"), g.label("blm")
                g.add(f"[{cur}]split=2[{s1}][{s2}]")
                if eff.params.get("style", "blur") == "pixelate":
                    block = int(eff.params.get("strength", 24))
                    g.add(f"[{s1}]scale=max(2\\,trunc(iw/{block})):max(2\\,trunc(ih/{block})),scale={sw}:{sh}:flags=neighbor[{bl}]")
                else:
                    g.add(f"[{s1}]gblur=sigma={_f(float(eff.params.get('strength', 30)))}[{bl}]")
                g.add(f"[{bl}][{mk}]alphamerge[{blm}]")
                g.add(f"[{s2}][{blm}]overlay=0:0:format=auto[{out}]")
            elif eff.type == "focus":
                _focus_composite(g, eff.params, cur, mk, out, sw, sh, fps, dur_src)
            elif eff.type == "mask":
                prm = eff.params
                s1, s2, fxl, fxm = g.label("s"), g.label("s"), g.label("mf"), g.label("mm")
                g.add(f"[{cur}]split=2[{s1}][{s2}]")
                style = prm.get("style", "blur")
                if style == "pixelate":
                    block = max(2, int(prm.get("strength", 28)))
                    g.add(f"[{s1}]scale=max(2\\,trunc(iw/{block})):max(2\\,trunc(ih/{block})),scale={sw}:{sh}:flags=neighbor[{fxl}]")
                elif style == "fill":
                    g.add(f"[{s1}]drawbox=c={ffcolor(str(prm.get('color', '#000000')))}:t=fill[{fxl}]")
                elif style == "dim":
                    k = _f(1 - min(max(float(prm.get("strength", 0.6)), 0.0), 1.0))
                    g.add(f"[{s1}]colorchannelmixer=rr={k}:gg={k}:bb={k}[{fxl}]")
                else:  # blur: two passes so even big, high-contrast text is unreadable
                    sigma = float(prm.get("strength", 40))
                    g.add(f"[{s1}]gblur=sigma={_f(sigma)}:steps=3,gblur=sigma={_f(sigma / 2)}[{fxl}]")
                g.add(f"[{fxl}][{mk}]alphamerge[{fxm}]")
                g.add(f"[{s2}][{fxm}]overlay=0:0:format=auto[{out}]")
            else:
                raise VeditError(f"Effect {eff.type} produced a mask but has no compositor")
            cur = out
        if "overlay" in art:
            oi = g.add_input(["-ss", _f(s0), "-t", _f(dur_src), "-i", str(art["overlay"])])
            ol, out = g.label("ovl"), g.label("fx")
            g.add(f"[{oi}:v]setpts=PTS-STARTPTS,scale={sw}:{sh}[{ol}]")
            g.add(f"[{cur}][{ol}]overlay=0:0:eof_action=pass:format=auto[{out}]")
            cur = out
    for eff in clip.effects:  # reframing last: masks above are in full-frame source pixels
        if eff.type == "focus" and eff.params.get("mode", "spotlight") in ("follow", "both"):
            from ..ops import track

            out = g.label("fx")
            g.add(f"[{cur}]{track.follow_crop(project, pdir, clip, eff, s0, dur_src, log)}[{out}]")
            cur = out
    return cur


# speech clean-up per clip: "clean" = cut rumble + broadband noise + even out the level; "studio" adds a warmer,
# clearer EQ, a de-esser and a limiter (the podcast sound)
VOICE_CHAINS = {
    "clean": "highpass=f=80,afftdn=nf=-25,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=2",
    "studio": "highpass=f=90,afftdn=nf=-28,equalizer=f=220:t=q:w=1:g=-2,equalizer=f=3500:t=q:w=1.2:g=3,"
              "deesser=i=0.4,acompressor=threshold=-22dB:ratio=4:attack=5:release=60:makeup=3,alimiter=limit=0.95",
}


def clip_audio_tail(clip: Clip, dur: float) -> str:
    """Voice clean-up + sound fades for a clip's audio, after it is trimmed to `dur` (empty string = nothing)."""
    parts = []
    if clip.voice in VOICE_CHAINS:
        parts.append(VOICE_CHAINS[clip.voice])
    fi, fo = min(clip.fade_in, dur / 2), min(clip.fade_out, dur / 2)
    if fi > 0.01:
        parts.append(f"afade=t=in:d={_f(fi)}")
    if fo > 0.01:
        parts.append(f"afade=t=out:st={_f(dur - fo)}:d={_f(fo)}")
    return ",".join(parts)


BLEND_FILTER = {"screen": "screen", "multiply": "multiply", "overlay": "overlay", "add": "addition",
                "lighten": "lighten", "darken": "darken"}


def _blend_layer(g: Graph, base: str, li: int, start: float, mode: str, out: str) -> str:
    """Composite a full-canvas RGBA layer video over `base` with a blend mode, limited to the layer's own alpha.

    blend works on colour only, so: blend the base with the layer's colours, give that result the layer's alpha,
    and lay it over the base. The layer is padded with transparent frames until its start so both line up."""
    lp, l1, l2, b1, b2, bb, lr, bl, al, blm = (g.label(x) for x in ("lp", "l1", "l2", "b1", "b2", "bb", "lr", "bl", "al", "blm"))
    pad = f"tpad=start_duration={_f(start)}:start_mode=add:color=black@0," if start > 1e-3 else ""
    g.add(f"[{li}:v]setpts=PTS-STARTPTS,{pad}format=rgba[{lp}]")
    g.add(f"[{lp}]split=2[{l1}][{l2}]")
    g.add(f"[{base}]split=2[{b1}][{b2}]")
    g.add(f"[{b1}]format=gbrp[{bb}]")
    g.add(f"[{l1}]format=gbrp[{lr}]")
    g.add(f"[{bb}][{lr}]blend=all_mode={BLEND_FILTER[mode]}:eof_action=pass:shortest=0[{bl}]")
    g.add(f"[{l2}]alphaextract[{al}]")
    g.add(f"[{bl}][{al}]alphamerge[{blm}]")
    g.add(f"[{b2}][{blm}]overlay=0:0:eof_action=pass:format=auto[{out}]")
    return out


def _adjust_layer(g: Graph, project: Project, pdir: Path, layer, base: str, start: float, ldur: float,
                  W: int, H: int, scale: float, fps: float) -> str:
    """An adjustment layer: a filtered + graded copy of the picture, shown over it only between start and end."""
    from ..ops import fx_filters

    grade = color.filter_chain(layer.grade, pdir)
    active = any(e.type == "fx" and e.params.get("on", True) for e in layer.effects)
    if ldur <= 0.01 or not (grade or active):
        return base
    a, b = g.label("adj"), g.label("adjf")
    g.add(f"[{base}]split=2[{a}][{b}]")
    cur = fx_filters.apply_filters(g, layer, b, fx_filters.Ctx(W, H, scale, fps))
    if grade:
        gl = g.label("adjg")
        g.add(f"[{cur}]{grade},format=yuv420p[{gl}]")
        cur = gl
    out = g.label("V")
    g.add(f"[{a}][{cur}]overlay=0:0:eof_action=pass:enable='between(t,{_f(start)},{_f(start + ldur - 1e-3)})'[{out}]")
    return out


def build(project: Project, pdir: Path, out_path: Path, scale: float = 1.0, quality: str = "preview", log=None,
          encode: list[str] | None = None, audio: bool = True, layers: bool = True) -> tuple[list[str], float]:
    """`encode` replaces the default H.264 encoder arguments (codec, quality, pixel format, container flags) for exports
    in other formats; `audio=False` writes a silent file (GIF). `layers=False` leaves out the overlay layers: the
    editor's live preview draws them in the browser over this "base" video."""
    total = project.duration
    if total <= 0.01:
        raise VeditError("Timeline is empty. Import a video first: `vedit import <file>`")
    log = log or (lambda msg: None)
    canvas = project.canvas
    W, H = even(canvas.width * scale), even(canvas.height * scale)
    fps = canvas.fps
    # equal tracks: layers on tracks under the video track (track < main_z) are drawn first, and the video track goes
    # over them with transparent gaps / letterbox bars / keyed backgrounds. Without any, the old opaque path is used.
    below = [l for l in project.layers if not l.hidden and not l.on_video and (l.track or 0) < project.main_z]
    # the video track's own layers (in its gaps / past its clips) come first: drawn right over the video
    above = ([l for l in project.layers if not l.hidden and l.on_video]
             + [l for l in project.layers if not l.hidden and not l.on_video and (l.track or 0) >= project.main_z])
    alpha = bool(below)
    stacked = alpha or not project.clips or project.clips_end < total - 1e-3
    g = Graph()
    vsegs: list[str] = []
    asegs: list[str] = []
    from ..ops.cut import transition_lengths

    trans = transition_lengths(project) + [0.0]
    starts = project.clip_starts()

    from .. import proxies

    preview = quality in ("preview", "frame")  # previews may decode small proxies; exports never do
    for k, clip in enumerate(project.clips):
        src = project.sources[clip.source]
        path = proxies.media_for(pdir, project, clip, preview)
        if not path.exists():
            raise VeditError(f"Source media missing: {path}")
        dur_src = clip.out - clip.in_
        dur = clip.duration
        # transition handles: half of each blend happens on either side of the cut
        pre, post = trans[k] / 2, trans[k + 1] / 2
        ext_in = min(pre * clip.speed, clip.in_)
        ext_out = min(post * clip.speed, max(0.0, src.duration - clip.out))
        hold_head = max(0.0, pre - ext_in / clip.speed)
        hold_tail = max(0.0, post - ext_out / clip.speed)
        s0, slen = clip.in_ - ext_in, dur_src + ext_in + ext_out
        idx = g.add_input(["-ss", _f(s0), "-t", _f(slen), "-i", str(path)])
        v0 = g.label("v")
        show = src.has_video and not project.main_hidden
        if alpha and (clip.gap or not show):  # nothing here on the video track: the tracks under it show
            show = False
            g.add(f"color=c=black@0:s={W}x{H}:r={_f(fps)}:d={_f(slen)},format=yuva420p[{v0}]")
        elif show:
            g.add(f"[{idx}:v]setpts=PTS-STARTPTS[{v0}]")
        else:  # audio-only source, or the main track is hidden: show the canvas background
            g.add(f"color=c={ffcolor(canvas.background)}:s={W}x{H}:r={_f(fps)}:d={_f(slen)}[{v0}]")
        cur = apply_effects(g, project, pdir, clip, v0, fps, log, s0, slen) if show else v0
        sp = g.label("sp")
        g.add(f"[{cur}]setpts=PTS/{_f(clip.speed)}[{sp}]")
        vlab = fit_filters(g, clip, sp, W, H, fps, canvas.background, dur + pre + post, hold_head, hold_tail, alpha)
        grade = color.filter_chain(clip.grade, pdir) if show else None
        if grade:  # at canvas size: cheaper than at source size, and the blurred fill gets the same grade
            def graded(lab: str, grade: str = grade) -> str:
                gl = g.label("grade")
                g.add(f"[{lab}]{grade},format=yuv420p[{gl}]")
                return gl

            vlab = keep_alpha(g, vlab, graded) if alpha else graded(vlab)
        if show and any(e.type == "fx" for e in clip.effects):  # filters: blur, vignette, grain, shake...
            from ..ops import fx_filters

            # the clip's first frame is at `pre` here (the transition handle before it): timed filters count from it
            def filtered(lab: str, clip: Clip = clip, pre: float = pre, dur: float = dur) -> str:
                return fx_filters.apply_filters(g, clip, lab, fx_filters.Ctx(W, H, scale, fps), offset=pre, dur=dur)

            vlab = keep_alpha(g, vlab, filtered) if alpha else filtered(vlab)
        alab = g.label("a")
        fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if src.has_audio:
            tail = clip_audio_tail(clip, dur)
            g.add(
                f"[{idx}:a]atrim=start={_f(ext_in)},asetpts=PTS-STARTPTS,{atempo_chain(clip.speed)},volume={_f(clip.volume)},{fmt},"
                f"apad,atrim=duration={_f(dur)},asetpts=PTS-STARTPTS{',' + tail if tail else ''}[{alab}]"
            )
        else:
            g.add(f"anullsrc=r=48000:cl=stereo,atrim=duration={_f(dur)},{fmt}[{alab}]")
        vsegs.append(vlab)
        asegs.append(f"[{alab}]")

    # video: hard cuts are concatenated, transitions blend across the cut point
    vcur = vsegs[0] if vsegs else ""
    if len(vsegs) > 1 and not any(trans):
        g.add(f"{''.join(f'[{v}]' for v in vsegs)}concat=n={len(vsegs)}:v=1:a=0[vcat]")
        vcur = "vcat"
    else:
        for k in range(1, len(vsegs)):
            out = g.label("X")
            if trans[k] > 0:
                tt = project.clips[k].transition.type  # type: ignore[union-attr]
                # xfade needs constant-frame-rate inputs; some FFmpeg builds report 1/0 after
                # concat/xfade/settb, so pin the rate explicitly on both sides.
                xa, xb = g.label("xa"), g.label("xb")
                g.add(f"[{vcur}]fps={_f(fps)}[{xa}]")
                g.add(f"[{vsegs[k]}]fps={_f(fps)}[{xb}]")
                g.add(f"[{xa}][{xb}]xfade=transition={tt}:duration={_f(trans[k])}:offset={_f(starts[k] - trans[k] / 2)}[{out}]")
            else:
                g.add(f"[{vcur}][{vsegs[k]}]concat=n=2:v=1:a=0,settb=AVTB[{out}]")
            vcur = out
    # sound of video overlays (picture-in-picture with its voice, ...) mixes with the clips' sound
    ov_audio = []
    for layer in (project.layers if layers else []):
        if layer.type != "video" or layer.volume <= 0 or layer.source not in project.sources:
            continue
        src = project.sources[layer.source]
        start, ldur = layer_span(layer, total)
        if not src.has_audio or ldur <= 0.01:
            continue
        ai = g.add_input((["-stream_loop", "-1"] if layer.loop else []) + ["-ss", _f(layer.src_in), "-t", _f(ldur),
                                                                         "-i", str(resolve_media(pdir, src.path))])
        al, ms = g.label("ova"), int(start * 1000)
        g.add(f"[{ai}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,"
              f"volume={_f(layer.volume)},adelay={ms}|{ms}[{al}]")
        ov_audio.append(al)
    # the clips' sound, as long as the whole timeline (silence where the video track has nothing)
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if asegs:
        g.add(f"{''.join(asegs)}concat=n={len(asegs)}:v=0:a=1,apad=whole_dur={_f(total)}[aclips]")
    else:
        g.add(f"anullsrc=r=48000:cl=stereo,atrim=duration={_f(total)},{fmt}[aclips]")
    asegs = ["[aclips]"]
    if ov_audio:
        g.add(f"{''.join(asegs)}concat=n={len(asegs)}:v=0:a=1[acat0]")
        g.add(f"[acat0]{''.join(f'[{x}]' for x in ov_audio)}amix=inputs={len(ov_audio) + 1}:duration=first:normalize=0:"
              f"dropout_transition=0[acat]")
    else:
        g.add(f"{''.join(asegs)}concat=n={len(asegs)}:v=0:a=1[acat]")


    def draw(layer, vcur: str) -> str:
        start, ldur = layer_span(layer, total)
        if layer.type == "adjust":
            return _adjust_layer(g, project, pdir, layer, vcur, start, ldur, W, H, scale, fps)
        f = ensure_layer(project, pdir, layer, scale)
        if f is None:
            return vcur
        li = g.add_input(["-i", str(f)])
        ll, out = g.label("L"), g.label("V")
        if layer.blend == "normal":
            g.add(f"[{li}:v]setpts=PTS-STARTPTS+{_f(start)}/TB[{ll}]")
            g.add(f"[{vcur}][{ll}]overlay=0:0:eof_action=pass:format=auto[{out}]")
            return out
        return _blend_layer(g, vcur, li, start, layer.blend, out)

    if stacked:  # canvas colour, the tracks under the video (always: the editor's base video has them too), the video
        cur = g.label("base")
        g.add(f"color=c={ffcolor(canvas.background)}:s={W}x{H}:r={_f(fps)}:d={_f(total)},format=yuv420p[{cur}]")
        for layer in below:
            cur = draw(layer, cur)
        if vcur:
            out = g.label("V")
            g.add(f"[{cur}][{vcur}]overlay=0:0:eof_action=pass:format=auto[{out}]")
            cur = out
        vcur = cur
    # layers=False (the editor's base video) still applies adjustment layers: they change the picture itself
    for layer in [l for l in above if layers or l.type == "adjust"]:
        vcur = draw(layer, vcur)
    g.add(f"[{vcur}]format=yuv420p[vout]")

    # music
    acur = "acat"
    music_labels = []
    ducked = [m for m in project.music if m.duck]
    sc_labels: list[str] = []
    if ducked:
        n = len(ducked) + 1
        outs = [g.label("as") for _ in range(n)]
        g.add(f"[acat]asplit={n}{''.join(f'[{o}]' for o in outs)}")
        acur, sc_labels = outs[0], outs[1:]
    for m in project.music:
        if m.start >= total:
            continue
        src = project.sources[m.source]
        L = total - m.start
        if m.end is not None:
            L = min(L, m.end - m.start)
        if not m.loop:
            L = min(L, src.duration - m.in_)
        if L <= 0.05:
            continue
        mi = g.add_input((["-stream_loop", "-1"] if m.loop else []) + ["-ss", _f(m.in_), "-i", str(resolve_media(pdir, src.path))])
        ml = g.label("mus")
        fo = min(m.fade_out, L / 2)
        ms = int(m.start * 1000)
        g.add(
            f"[{mi}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration={_f(L)},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={_f(min(m.fade_in, L / 2))},afade=t=out:st={_f(L - fo)}:d={_f(fo)},volume={_f(m.volume)},"
            f"adelay={ms}|{ms}[{ml}]"
        )
        if m.duck:
            dl = g.label("duck")
            g.add(f"[{ml}][{sc_labels.pop(0)}]sidechaincompress=threshold=0.015:ratio=12:attack=20:release=400[{dl}]")
            ml = dl
        music_labels.append(ml)
    if music_labels:
        ins = "".join(f"[{x}]" for x in [acur, *music_labels])
        g.add(f"{ins}amix=inputs={len(music_labels) + 1}:duration=first:normalize=0:dropout_transition=0,atrim=duration={_f(total)}[amix]")
    else:
        g.add(f"[{acur}]anull[amix]")
    if not audio:  # silent export (GIF, or sound switched off): the audio chain must still end somewhere
        g.add("[amix]anullsink")
    elif canvas.loudness is not None and (music_labels or ov_audio or any(
            project.sources[c.source].has_audio and not c.gap for c in project.clips)):
        # one consistent level on every phone, and no clipping (pure silence would turn into NaN)
        g.add(f"[amix]loudnorm=I={_f(canvas.loudness)}:TP=-1.5:LRA=11,aresample=48000,atrim=duration={_f(total)}[aout]")
    else:
        g.add("[amix]anull[aout]")

    # write the filter graph to a file (keeps the command short on Windows)
    tmpdir = state_dir(pdir) / "tmp"
    tmpdir.mkdir(exist_ok=True)
    gfile = tmpdir / f"graph-{quality}.txt"
    gfile.write_text(";\n".join(g.filters))
    graph_flag = ["-/filter_complex", str(gfile)] if media.ffmpeg_major() >= 7 else ["-filter_complex_script", str(gfile)]

    if quality == "preview":
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "128k"]
    else:
        enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k"]

    cmd = [media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    for a in g.inputs:
        cmd += a
    if encode is None:
        encode = [*enc, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    maps = ["-map", "[vout]"] + (["-map", "[aout]"] if audio else ["-an"])
    cmd += graph_flag + [*maps, *encode, "-r", _f(fps), "-t", _f(total), str(out_path)]
    return cmd, total


def filter_text(project: Project, pdir: Path, scale: float = 1.0) -> str:
    """The filtergraph as text (handy for debugging / snapshot tests)."""
    tmp = state_dir(pdir) / "tmp" / "dryrun.mp4"
    build(project, pdir, tmp, scale, "preview")
    return (state_dir(pdir) / "tmp" / "graph-preview.txt").read_text()
