"""Layer groups: the parts of one effect (a chat's bubbles, a checklist's rows, a phone and its frame) kept together.

The timeline shows a collapsed group as one row with one block; moving that block moves every layer in it. Groups
own their tracks (project.normalize_tracks never puts another layer on a grouped track), so they can collapse.
Every signature effect that adds 2+ layers groups them (ops/moves.py)."""

from __future__ import annotations

from ..project import Layer, LayerGroup, Project, VeditError, normalize_tracks


def get(p: Project, gid: str) -> LayerGroup:
    for g in p.groups:
        if g.id == gid:
            return g
    raise VeditError(f"No group '{gid}'. Groups: {', '.join(f'{g.id} ({g.name})' for g in p.groups) or 'none'}")


def members(p: Project, gid: str) -> list[Layer]:
    return [l for l in p.layers if l.group == gid]


def make(p: Project, layer_ids: list[str], name: str = "Group", collapsed: bool = True, min_layers: int = 2) -> LayerGroup:
    """Group layers (2+; captions may be one phrase). They move onto tracks of their own, above everything, keeping
    their order (layers that don't overlap in time share a track)."""
    ls = [p.layer(i) for i in dict.fromkeys(layer_ids)]
    if len(ls) < min_layers:
        raise VeditError("A group needs at least two layers")
    if any(l.on_video for l in ls):
        raise VeditError("Layers on the video track itself can't be grouped")
    taken = {g.id for g in p.groups}
    gid = next(f"g{n}" for n in range(1, len(taken) + 2) if f"g{n}" not in taken)
    g = LayerGroup(id=gid, name=(name or "Group").strip()[:60] or "Group", collapsed=collapsed)
    p.groups.append(g)
    order = {l.id: k for k, l in enumerate(p.layers)}
    for l in sorted(ls, key=lambda x: order[x.id]):
        under = l.track is not None and l.track < p.main_z
        l.group = gid
        if not under:  # new tracks on top for it (layers under the video keep their side)
            l.track = None
    normalize_tracks(p)
    return g


def summary(p: Project) -> list[dict]:
    out = []
    for g in p.groups:
        ls = members(p, g.id)
        ends = [(l.end if l.end is not None else p.duration) for l in ls]
        out.append({"id": g.id, "name": g.name, "collapsed": g.collapsed, "layers": [l.id for l in ls],
                    "start": round(min((l.start for l in ls), default=0.0), 3), "end": round(max(ends, default=0.0), 3)})
    return out


def set_group(p: Project, gid: str, name: str | None = None, collapsed: bool | None = None) -> LayerGroup:
    g = get(p, gid)
    if name is not None and name.strip():
        g.name = name.strip()[:60]
    if collapsed is not None:
        g.collapsed = bool(collapsed)
    return g


def move(p: Project, gid: str, delta: float | None = None, to: float | None = None) -> float:
    """Shift every layer of the group in time (by `delta`, or so the group starts at `to`). Returns the shift."""
    ls = members(p, gid)
    if not ls:
        raise VeditError(f"Group {gid} is empty")
    first = min(l.start for l in ls)
    d = (to - first) if to is not None else float(delta or 0.0)
    d = max(d, -first)  # never before 0
    for l in ls:
        l.start = round(l.start + d, 3)
        if l.end is not None:
            l.end = round(l.end + d, 3)
    normalize_tracks(p)
    return d


def remove(p: Project, gid: str) -> list[str]:
    """Delete the group and every layer in it."""
    get(p, gid)
    gone = [l.id for l in members(p, gid)]
    p.layers = [l for l in p.layers if l.group != gid]
    p.groups = [g for g in p.groups if g.id != gid]
    normalize_tracks(p)
    return gone


def ungroup(p: Project, gid: str) -> list[str]:
    """Keep the layers, drop the group (they become ordinary layers on their tracks)."""
    get(p, gid)
    ids = []
    for l in members(p, gid):
        l.group = None
        ids.append(l.id)
    p.groups = [g for g in p.groups if g.id != gid]
    normalize_tracks(p)
    return ids
