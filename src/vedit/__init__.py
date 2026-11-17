"""aiklips: a video editor you drive with Claude Code."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aiklips")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+src"
