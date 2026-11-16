"""Preview renders must not corrupt each other (editor auto-render vs. `vedit frame`)."""

import threading

from vedit import media
from vedit.project import load
from vedit.render.runner import preview_dir, render_preview


def test_concurrent_preview_renders_stay_valid(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    errors: list[Exception] = []

    def go(force: bool) -> None:
        try:
            render_preview(load(d), d, force=force, progress=False)
        except Exception as e:  # pragma: no cover - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=go, args=(i % 2 == 0,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    out = preview_dir(d) / "preview.mp4"
    assert media.probe(out)["duration"] > 5
    assert not list(preview_dir(d).glob("preview.part*")), "temp files left behind"


def test_corrupt_cached_preview_is_rerendered(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    render_preview(load(d), d, progress=False)
    out = preview_dir(d) / "preview.mp4"
    out.write_bytes(b"not a video")  # hash still matches, file is broken

    res = render_preview(load(d), d, progress=False)
    assert res["cached"] is False
    assert media.probe(out)["duration"] > 5
