#!/usr/bin/env bash
# claude-cut setup for macOS / Linux.  Usage: scripts/setup.sh [--ai] [--captions] [--all]
set -euo pipefail
cd "$(dirname "$0")/.."

extras=()
for a in "$@"; do
  case "$a" in
    --ai) extras+=(--extra ai) ;;
    --captions) extras+=(--extra captions) ;;
    --all) extras+=(--extra ai --extra captions --extra bg-quality) ;;
  esac
done

if ! command -v ffmpeg >/dev/null; then
  echo "Installing FFmpeg..."
  if command -v brew >/dev/null; then brew install ffmpeg
  elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y ffmpeg
  elif command -v dnf >/dev/null; then sudo dnf install -y ffmpeg
  else echo "Please install FFmpeg manually: https://ffmpeg.org/download.html"; exit 1; fi
fi

if ! command -v uv >/dev/null; then
  echo "Installing uv (Python package manager)..."
  if command -v brew >/dev/null; then brew install uv
  else curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH"; fi
fi

uv sync "${extras[@]}"
echo
uv run vedit --help >/dev/null && echo "✅ claude-cut is ready. Open this folder in Claude Code (run: claude) and say what you want to edit."
