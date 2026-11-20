# Distribution and self-update

How aiklips ships as a downloadable app for macOS, Windows and Linux, and how it updates itself.

## Pieces

| Piece | Where | Job |
|---|---|---|
| Launcher | `launcher/` (Go, stdlib only, ~7 MB) | What people download. Installs uv + FFmpeg, installs/updates the app, starts it, rolls back a broken update. Rarely changes. |
| Updater | `src/vedit/updater.py` | Inside the app: checks GitHub Releases (every 6 h, opt-out), *stages* an update in `app/pending/`, restarts with exit code 75. Installs optional extras on first use. |
| Release pipeline | `.github/workflows/release.yml` | On every push to `main` with a new `pyproject.toml` version: tests, signed manifest, payload, launchers, GitHub Release. |
| Signing tool | `launcher/cmd/relsign` | Key generation, manifest, ed25519 signature, verification. |
| Website | separate repo `aiklips-website` (sibling folder `website/`) | aiklips.muzdev.online on a static-assets Cloudflare Worker (observability off), deployed by Cloudflare on every push to its `main`. Serves the landing page and `latest.json`, the release index the app and launcher read first (GitHub API as fallback). The release workflow's `website` job commits a new `latest.json` there after each release. |

Why a launcher + uv instead of a frozen bundle (PyInstaller): the app's dependencies (MediaPipe, OpenCV, Torch for
BiRefNet) would make a 500 MB+ bundle re-downloaded on every update. With uv, the payload is the source (~2 MB),
dependencies come from the exact `uv.lock` and are shared through uv's cache, so an update downloads only what
changed. The payload keeps the repo layout, so `CLAUDE.md`, `.claude/skills` and `assets/` sit where the code expects.

## On disk

```
~/.aiklips/                 (also the app's own data: library.db, ai.json, templates/ …)
  bin/uv, bin/ffmpeg, bin/ffprobe       pinned builds, sha256-checked (FFmpeg only if the system has no 6+)
  app/versions/<v>/                     one install per version, each with its own .venv (keeps current + previous)
  app/current, app/previous, app/skip   version pointers; skip = a version that was rolled back
  app/pending/                          staged update: manifest.json, manifest.sig, payload
  app/extras.txt                        optional features installed (ai, bg-quality, captions, mask-ai)
  launcher.log, app.log
~/aiklips-projects/          projects: outside the version folders, never touched by updates
```

## Trust

- The launcher has the release **public key** built in (`launcher/pubkey.txt`). It installs nothing unless
  `manifest.json` carries a valid ed25519 signature and the payload matches the manifest's sha256. The app only
  downloads; the decision what code runs is the launcher's.
- uv and FFmpeg downloads are pinned to exact builds by sha256 in `launcher/tools.go`.
- Projects carry a format version (`SCHEMA_VERSION`, `project.migrate`): newer apps migrate older projects, and an
  older app refuses a newer project instead of re-saving it without fields it doesn't know (safe rollback).
- The builds are not code-signed/notarised yet (macOS: right-click → Open once; Windows: SmartScreen "Run anyway").
  The macOS app gets an ad-hoc signature so it runs on Apple silicon.

## Update flow

1. The app (`updater.check`) sees a newer release → **Update** button in the editor / home, Settings → Updates.
2. **Download update** → `updater.stage`: manifest, signature and payload into `app/pending/` (sha256 checked).
3. **Restart to update** → the app exits with 75 (refused while an export runs).
4. The launcher verifies the signature, unpacks into `app/versions/<v>/`, `uv sync --frozen --no-dev` (+ extras),
   smoke-tests `vedit --version`, then switches `current` (old one becomes `previous`) and starts it.
5. If the new version exits with an error within 20 s, the launcher switches back and marks it `skip`.
   Users can also go back by hand: Settings → Updates → Go back, or `vedit update rollback`.

## Setup window

First installs and updates show a small branded window instead of dialogs (`launcher/setup.go`, page
`launcher/ui/setup.html`, embedded). The launcher serves the page on 127.0.0.1 (random port, random token in the
URL) and streams progress over SSE (`/events`); `POST /retry` and `/open-log` back the error state's buttons.

- **Window:** Windows uses Edge in app mode (`msedge --app`, a throwaway profile; Chrome, then the default browser as
  fallbacks). macOS runs `launcher/ui/window.js` with `osascript -l JavaScript`: an NSWindow with a WKWebView and the
  aiklips dock icon (no cgo). Linux: Chromium/Chrome/Edge/Brave `--app`, else `xdg-open`.
- **When:** only if there is work (`plan()` in main.go) and it takes longer than `showDelay` (0.9 s). An ordinary
  start shows nothing. `AIKLIPS_QUIET=1` never opens it (tests, CI).
- **Progress:** downloads are byte-counted tasks (`tracker`); install steps ease towards done over an expected time.
  The window closes when the app creates `AIKLIPS_READY_FILE` (an env var the launcher passes), the app exits, or
  after 15 s.
- **Speed:** uv, FFmpeg/FFprobe and the release payload download at once; `uv python install 3.12` starts as soon as
  uv has arrived. `uv sync --compile-bytecode` makes the install a little longer and the first start much quicker.
- **Errors:** a plain sentence (`friendly()`), with Try again / Copy details / Show log. Without a window (quiet, or
  it couldn't open) the old native dialog is the fallback.
- **Check it by eye:** `AIKLIPS_PREVIEW=1 go test -count=1 -run TestPreviewStates .` serves the download, error and
  done states on ports 47811-47813 (`?t=x`). `AIKLIPS_WINDOW_TEST=1 go test -run TestMacWindow .` builds the real
  macOS window off screen.

Icons: `assets/brand/` (generated from `logo.svg`). The release workflow embeds `icon.ico` in the Windows exe
(go-winres → a `.syso`, never committed) and puts `icon.icns` in the macOS bundle; Linux gets the PNG with its menu
entry.

## Releasing (maintainers)

One-time setup:

- **Website:** see the website repo's README (Cloudflare Git integration, no CNAME needed when muzdev.online's DNS
  is on Cloudflare). Here: add the secret `WEBSITE_DEPLOY_TOKEN` (fine-grained, *Contents: write* on the website repo
  only). `latest.json` is unsigned on purpose: it only says where the newest release is; the signed manifest decides
  what installs.
- **Signing key:**

```bash
cd launcher && go run ./cmd/relsign keygen -pub pubkey.txt   # prints the PRIVATE key
```

Add the printed private key as the repository secret **RELEASE_SIGNING_KEY** (Settings → Secrets → Actions), keep a
backup somewhere safe (losing it means existing installs can't verify new releases until people re-download the
launcher), and commit `launcher/pubkey.txt`.

Every release: work on `develop`, bump `version` in `pyproject.toml` (CI refuses a PR into `main` without it), merge
into `main`. The workflow tests on all three systems and publishes `v<version>` with the notes taken from the
commit messages. Release candidates: publish a GitHub *pre-release* by hand; `releases/latest` ignores those.

When the project format changes: bump `SCHEMA_VERSION` and add a step to `project.MIGRATIONS`. When a release needs
a newer launcher (new install steps): bump `launcherAPI` in `launcher/main.go` and pass `-min-launcher` in the
workflow; older launchers then tell people to download the new one.

## Local end-to-end test

`AIKLIPS_HOME` (data folder), `AIKLIPS_RELEASE_BASE` (serve manifest/payload from a folder),
`AIKLIPS_APP_ARGS` (run e.g. `--version` instead of opening the editor), `AIKLIPS_QUIET=1` (no dialogs) and
`-ldflags "-X main.PubKeyOverride=<test key>"` let you run install → update → rollback against a local
`python3 -m http.server` with a throwaway key.

## The rename (claude-cut → aiklips, 0.2)

The first start of 0.2 moves `~/.claude-cut` to `~/.aiklips` and leaves a link (symlink, or a junction on Windows) at
the old path, because projects store absolute paths into it (uploaded footage). The library's own records are
rewritten to the new path (`library.adopt_old_home`, `_fix_old_paths`; launcher: `adoptOldHome`). The command is
still `vedit`; `aiklips` is an alias.
