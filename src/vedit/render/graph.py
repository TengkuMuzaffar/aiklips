"""Turn project.json into one FFmpeg command (inputs + filter_complex)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import media
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


def fit_filters(g: Graph, clip: Clip, src_label: str, W: int, H: int, fps: float, bg: str, dur: float) -> str:
    """Scale/crop/pad a clip onto the W x H canvas. Returns the output label."""
    z = max(1.0, clip.zoom)
    fx, fy = min(max(clip.focus_x, 0), 1), min(max(clip.focus_y, 0), 1)
    out = g.label("fit")
    tail = f"fps={_f(fps)},setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=0.2,trim=duration={_f(dur)},setpts=PTS-STARTPTS"
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
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={ffcolor(bg)},{tail}[{out}]"
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


def apply_effects(g: Graph, project: Project, pdir: Path, clip: Clip, cur: str, fps: float, log) -> str:
    if not clip.effects:
        return cur
    from ..ops import effects  # optional heavy deps are imported lazily inside

    src = project.sources[clip.source]
    dur_src = clip.out - clip.in_
    sw, sh = src.width or 1080, src.height or 1920
    for eff in clip.effects:
        art = effects.prepare(eff, project, pdir, clip.source, log)
        if "mask" in art:
            mi = g.add_input(["-ss", _f(clip.in_), "-t", _f(dur_src), "-i", str(art["mask"])])
            mk = g.label("mask")
            g.add(f"[{mi}:v]setpts=PTS-STARTPTS,scale={sw}:{sh},format=gray[{mk}]")
            out = g.label("fx")
            if eff.type == "bg":
                rep = str(eff.params.get("replace", "color:#00b140"))
                kind, _, val = rep.partition(":")
                fg = g.label("fg")
                if kind == "blur":
                    s1, s2, bb = g.label("s"), g.label("s"), g.label("bb")
                    g.add(f"[{cur}]split=2[{s1}][{s2}]")
                    g.add(f"[{s1}]gblur=sigma={_f(float(val or 25))}[{bb}]")
                    g.add(f"[{s2}][{mk}]alphamerge[{fg}]")
                    g.add(f"[{bb}][{fg}]overlay=0:0:format=auto[{out}]")
                else:
                    g.add(f"[{cur}][{mk}]alphamerge[{fg}]")
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
            else:
                raise VeditError(f"Effect {eff.type} produced a mask but has no compositor")
            cur = out
        if "overlay" in art:
            oi = g.add_input(["-ss", _f(clip.in_), "-t", _f(dur_src), "-i", str(art["overlay"])])
            ol, out = g.label("ovl"), g.label("fx")
            g.add(f"[{oi}:v]setpts=PTS-STARTPTS,scale={sw}:{sh}[{ol}]")
            g.add(f"[{cur}][{ol}]overlay=0:0:eof_action=pass:format=auto[{out}]")
            cur = out
    return cur


def build(project: Project, pdir: Path, out_path: Path, scale: float = 1.0, quality: str = "preview", log=None) -> tuple[list[str], float]:
    if not project.clips:
        raise VeditError("Timeline is empty. Import a video first: `vedit import <file>`")
    log = log or (lambda msg: None)
    canvas = project.canvas
    W, H = even(canvas.width * scale), even(canvas.height * scale)
    fps = canvas.fps
    total = project.duration
    g = Graph()
    seg: list[str] = []

    for k, clip in enumerate(project.clips):
        src = project.sources[clip.source]
        path = resolve_media(pdir, src.path)
        if not path.exists():
            raise VeditError(f"Source media missing: {path}")
        dur_src = clip.out - clip.in_
        dur = clip.duration
        idx = g.add_input(["-ss", _f(clip.in_), "-t", _f(dur_src), "-i", str(path)])
        v0 = g.label("v")
        if src.has_video:
            g.add(f"[{idx}:v]setpts=PTS-STARTPTS[{v0}]")
        else:  # audio-only source: show the canvas background
            g.add(f"color=c={ffcolor(canvas.background)}:s={W}x{H}:r={_f(fps)}:d={_f(dur_src)}[{v0}]")
        cur = apply_effects(g, project, pdir, clip, v0, fps, log) if src.has_video else v0
        sp = g.label("sp")
        g.add(f"[{cur}]setpts=PTS/{_f(clip.speed)}[{sp}]")
        vlab = fit_filters(g, clip, sp, W, H, fps, canvas.background, dur)
        alab = g.label("a")
        fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if src.has_audio:
            g.add(
                f"[{idx}:a]asetpts=PTS-STARTPTS,{atempo_chain(clip.speed)},volume={_f(clip.volume)},{fmt},"
                f"apad,atrim=duration={_f(dur)},asetpts=PTS-STARTPTS[{alab}]"
            )
        else:
            g.add(f"anullsrc=r=48000:cl=stereo,atrim=duration={_f(dur)},{fmt}[{alab}]")
        seg.append(f"[{vlab}][{alab}]")

    g.add(f"{''.join(seg)}concat=n={len(seg)}:v=1:a=1[vcat][acat]")
    vcur = "vcat"

    for layer in project.layers:
        start, ldur = layer_span(layer, total)
        f = ensure_layer(project, pdir, layer, scale)
        if f is None:
            continue
        li = g.add_input(["-i", str(f)])
        ll, out = g.label("L"), g.label("V")
        g.add(f"[{li}:v]setpts=PTS-STARTPTS+{_f(start)}/TB[{ll}]")
        g.add(f"[{vcur}][{ll}]overlay=0:0:eof_action=pass:format=auto[{out}]")
        vcur = out
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
        g.add(f"{ins}amix=inputs={len(music_labels) + 1}:duration=first:normalize=0:dropout_transition=0,atrim=duration={_f(total)}[aout]")
    else:
        g.add(f"[{acur}]anull[aout]")

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
    cmd += graph_flag + ["-map", "[vout]", "-map", "[aout]", *enc, "-pix_fmt", "yuv420p", "-r", _f(fps),
                         "-movflags", "+faststart", "-t", _f(total), str(out_path)]
    return cmd, total


def filter_text(project: Project, pdir: Path, scale: float = 1.0) -> str:
    """The filtergraph as text (handy for debugging / snapshot tests)."""
    tmp = state_dir(pdir) / "tmp" / "dryrun.mp4"
    build(project, pdir, tmp, scale, "preview")
    return (state_dir(pdir) / "tmp" / "graph-preview.txt").read_text()
