"""Feature switches for parts of the app that are not ready for users yet."""

from __future__ import annotations

import os

from .project import VeditError

DESIGN_MSG = ("The Design studio is in development and switched off for now. Motion graphics (kinetic text, counters, "
              "charts, draw-on lines, morphs, motion styles) work in a normal project: `vedit init`, then `vedit style "
              "set`, `motion`, `counter`, `chart` ...")


def design_enabled() -> bool:
    """The Design studio / design projects (`AIKLIPS_DESIGN=1` turns them back on, e.g. for development and tests)."""
    return os.environ.get("AIKLIPS_DESIGN", "") == "1"


def require_design() -> None:
    if not design_enabled():
        raise VeditError(DESIGN_MSG)
