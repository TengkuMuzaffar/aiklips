# Design: Media library (study once, reuse everywhere) + layered AI context

Status: **implemented (phases 1–4)** · Date: 2026-11-20 · §9 lists what is still open

## 1. Why

Generated videos are only as good as the planner's understanding of the user's footage. Before this change:

- Uploads already lived in one global folder (`~/.aiklips/uploads/<sha16>/`, deduplicated by content), but there was
  no index, no study status, no versioning and no UI. Editor projects couldn't see it.
- The footage study (AI notes per segment, measurements, footage-or-sound check) ran **inside `batch_prepare`**, so
  the user waited at generate time. If an AI call failed, the planner only saw `"(not described)"`.
- Editor imports (`vedit import`, files dropped into the editor) were never studied at all.
- The planner got the full shot list of every file as JSON (up to 60 lines per file) in **every** prompt (creative,
  each edit plan, each revision). The prompt grew with the number of files, and nothing summarised the set as a whole.

Goal: one **media library** shared by generation and editor projects. Every file is studied **once** in the background
at import and the study is versioned. The AI gets a **layered, budgeted context** instead of raw dumps.

Decisions the user made: the library covers **both** generation and editor imports. Files are **copied in**. The AI
study runs **at import, in the background**.

## 2. Storage

- Files stay where they were: `~/.aiklips/uploads/<id>/` (id = first 16 hex chars of the content hash). Each folder
  holds `file.<ext>`, `meta.json`, `index.json` (segments), `measure.json`, `notes.json` (AI notes), `marks.json`
  (reviewer marks), `digest.json` (new), `cuts.json` (raw cuts, reused by a style study) and, when there is speech,
  `transcript.json`. A video used as a style reference also has `style/` (its Style DNA study, see §10).
- New tables in `library.db` (`src/vedit/media_library.py`):
  - `media(id, name, kind, status, progress, measure_version, study_version, tags, overrides, size_bytes, error,
    created_at, updated_at, last_used_at)`
  - `media_usage(media_id, kind 'project'|'batch', target, used_at)`: drives "used in", delete protection and
    "recently used".
- **No `project.json` change.** A source's library id is worked out from its path (`id_for_path`: the file sits in
  `uploads/<id>/`), so `Source` needs no new field and projects still open in older versions.
- `backfill()` at app start registers upload folders made before the library existed.

## 3. The study (one durable job per file: `media_study`)

`footage.add` registers every upload and queues the job. Steps:

1. **Measure (code, free):** `footage.segments` (cuts, long takes split into ≤3.5 s pieces), `footage.measure`
   (motion / light / saturation / warmth / speech score per segment), `footage._audio_stats` (beats, tempo, loudness).
2. **AI notes:** `footage.describe(..., limit=MAX_AI_SEGMENTS)`. A long recording is logged by up to 90 evenly spread
   segments (`footage.sample`), so one call stays small.
3. **Role:** `footage.classify` (footage or sound) for videos that have sound.
4. **Transcript:** only when speech is measured **and** `faster-whisper` is already installed. The study never
   installs it.
5. **Digest (code):** `write_digest` makes `digest.json`: usable seconds, best segments, subjects, roles, a summary,
   energy, speech, transcript, orientation, BPM for audio, tags.

Status: `queued → measuring → studying → ready | partial | failed`.

- **partial** means measured but some AI notes are missing. The file is still usable and is not re-sent on every
  start; "Study again" retries it.
- **Versioning:** `MEASURE_VERSION` (media_library) and `prompts.FOOTAGE_VERSION`. `restudy_stale()` at app start
  re-runs only the stale part. A new footage prompt deletes `notes.json`. A new measure version deletes only
  `measure.json`; `index.json` is kept because notes, marks and overrides are keyed by segment number.
- **User corrections:** `overrides` per segment (`{k: {description, quality, ...}}`). `apply_overrides` puts them on
  top of the AI notes in `footage.shot_log` and the digest, and they survive a re-study.

### Who runs the jobs

Only the app process starts worker threads (`jobs.IN_APP`, set in `gen_routes.mount`). A short CLI command like
`vedit import` only queues the job, for two reasons:

- worker threads it started would die when the command exits;
- `ensure_workers()` runs `recover()`, which would re-queue jobs an open app is running right now.

An open app's workers poll the queue every few seconds, so a CLI import is picked up within about 5 seconds. With no
app open, `vedit media show` studies the file inline (`study_now`), unless a worker is already on it.

## 4. Layered context for the planner (`src/vedit/generate/context.py`)

Replaces `planner._footage_brief` in the creative, concepts and edit prompts.

- **L0 Situation (code):** counts by kind, usable seconds vs the target length, which files have speech, and where the
  sound comes from. From these it picks a **recipe**:

  | Recipe | When |
  |---|---|
  | `image_motion` | no videos, only images |
  | `speech_led` | someone speaks in the footage |
  | `scarce_footage` | usable seconds < 0.8 × target length |
  | `abundant_pick_best` | usable seconds > 4 × target length |
  | `balanced` | anything else |

  It also picks a **sound plan**: `music_each` (several sound files), `music_file`, `clip_sound` (speech, no music)
  or `make_music`. Each recipe and sound plan comes with a short instruction in the prompt.
- **L1:** one line per file from its digest.
- **L2 shortlist:** segments ranked by code. The score uses the AI quality score, a bonus when `good_for` matches the
  roles in this variation's structure, a penalty for weak reviewer marks, a small bonus for good marks, and a penalty
  for unusable segments (quality < 3 or too dark). The budget is a line count across **all** files: `PLAN_LINES = 120`
  for the editor, 60 for concepts, `CREATIVE_LINES = 36` for the creative director. Each file keeps at least 3 lines.
  Lines keep the `f1.s3 (0.0-2.1s, q4): ...` form the editor cites.
- The full shot log is still what `validate_plan`, `fill_ranges` and `apply_patch` check against.

The recipe currently shapes **the prompt only**. `directives()` and `enforce()` don't read it yet (§9).

## 5. Generation wiring (`generate/batch.py`)

- `_prepare_inner` waits for files still being studied (`media_library.wait_list` → `jobs.Retry(3)`, "Waiting for
  N file(s) to be studied"), then records usage.
- It still calls `footage.describe`, which only spends an AI call on segments a partial study couldn't log. For a
  file studied at import this is a no-op.
- Trade-off: files are now logged one AI call per file at import. Before, up to 4 files shared one call at generate
  time. A little more costly in calls, but nothing waits at generate time.

## 6. Editor and CLI

- **`vedit import`** adds through the library by default: `media_library.store_for_project` copies the file in,
  records project usage, and the source points at the library copy. `--no-library` keeps the old behaviour (the
  project points at the original file). `--copy` still copies into the project's `media/`. Types the library doesn't
  take stay where they are.
- **`vedit music add`** goes through the library too, so beats and tempo are measured once.
- **Editor drop / upload** (`editor/actions.import_file`) moves the uploaded temp file into the library (no second
  copy). Other file types still go to `media/`.
- **Portability trade-off:** media dropped into a project now lives in `~/.aiklips/uploads`, not in the project's
  `media/`. Projects work as before on this machine, but a project folder copied to another computer doesn't carry its
  dropped media.
- **`vedit info`:** a `study` entry per source (`project_media`).
- **`<editor_context>`:** one line per studied source, up to 8 (`media_line`).
- **CLI `vedit media`:**

  | Command | What it does |
  |---|---|
  | `list [--kind --search]` | everything in the library, most recently used first |
  | `add <files>` | add files without putting them in a project |
  | `show <id\|sN>` | the digest and every shot (note, quality, roles, measurements, marks) |
  | `tag <id\|sN> TAGS...` | replace the file's tags |
  | `note <id\|sN> <k> [--text --quality --clear]` | correct one shot; wins over the AI's note |
  | `restudy <id\|sN>` | measure and log again from scratch, keeping notes and tags |

  `media list` / `media show` are read-only for the chat agent (`_VEDIT_READ_SUB`). `media` is `DENIED` in generated
  plans (`generate/commands.py`).

## 7. UI (`preview_server/static/home.html`)

- **Media tab** (sidebar under Home, key `5`):
  - a grid with thumbnail, kind, length and live study status (polls while busy);
  - search over names, tags, every shot note, subjects and transcript (`_search_text`);
  - kind and used / unused filters, and disk usage.
- **Detail dialog:**
  - a player (`/api/media/{id}/file`), rename and tags;
  - digest facts, transcript and "used in";
  - per-shot rows: click the time to play from there, change quality, rewrite the note (saved as an override, with
    "Undo my edit");
  - Study again, and Delete (warns when the file is in use; forced only after confirming).
- **Generation wizard:** "pick from your media" in the Footage and Sound steps (`pickMedia`; shared `addToWizard`
  with upload).
- Fixed at the same time: at phone width the sidebar tab row made the whole page scroll sideways. It now scrolls
  within itself.
- **API** (`gen_routes.py`): `GET/POST /api/media`, `GET/PATCH/DELETE /api/media/{id}`,
  `POST /api/media/{id}/restudy`, `GET /api/media/{id}/thumb.jpg`, `GET /api/media/{id}/file`. `/api/uploads` stays
  for the wizard.

## 8. Tests

`tests/test_media_library.py` (16 tests), covering:

- study once and reuse;
- a new footage prompt re-studies only the AI part;
- a failed AI call leaves a usable partial file;
- overrides survive a re-study;
- delete protection and backfill;
- each recipe in the situation matrix;
- the context budget with 12 files;
- the media API;
- CLI import through the library, inline study, note, tag and search;
- editor drop import;
- sampling of long recordings.

Other test changes:

- `tests/conftest.py` turns job workers off for the whole run (`pytest_configure`), because a worker thread outlives
  its test and takes later tests' jobs.
- `test_editor`, `test_import_images` and `test_proxies` were updated for files living in the library.

Also fixed along the way:

- `footage.add` turns an unreadable file into a `VeditError` (no PIL traceback).
- `tracks.remove_sources` no longer counts an image layer twice when two sources share one file.

## 9. Open

- `overlay add` (b-roll overlays) doesn't go through the library yet.
- The recipe only shapes the prompt; `directives()` / `enforce()` could apply it (pacing, shot reuse, audio mix).
- A per-batch `context.json` snapshot, so regenerate and improve reuse exactly the same context.
- Matching sounds to variations by energy and BPM. `assign_sounds` still takes them in turn.
- A `whats_new.py` entry for the Media tab.

## 10. References are a role on a media video (2026-11-20)

One file, two studies. The media study answers "what is in each shot" (content, for cutting); the style study
(`references/store.py`, StyleDNA) answers "how is it made" (pacing, hook, text, motion, sound, design). Both stay, but
the file lives once, in `uploads/<id>/`, and a reference is a video in the library with a style study added:

- `refs.media_id` links the reference row (integer id kept: `Project.notes.refs`, `BatchSpec.refs`, `@ref<id>` and
  old batches keep working) to the media item. Its study files are in `uploads/<id>/style/`.
- `store.promote(media_id)` makes a video a reference (no copy; queues the `study` job); audio and images are refused.
  `store.add(file)` = `footage.add` + `promote`. `store.resolve(token)` takes a ref id, media id, `sN` or a file.
  `store.delete(rid)` removes only the role; `media_library.delete` removes the role with the file.
- The style study reuses what the media study measured on the same file: raw cuts (`cuts.json`) and the transcript
  (`transcript.json`, a better whisper model); motion, colour, palette and the three sheets stay its own.
- `store.migrate_to_media()` (app start, and on the first `promote`) moves older references (own copy in
  `~/.aiklips/references/<sha16>/`) into the library and their study into `style/`, keeping the DNA: no re-study.
- Making a video a reference is explicit (a vision call): "Use as reference" in the Media tab, `vedit media ref`, or
  picking a media video as the style in chat (@ → "style · name") or in Generate (step 1 lists Media videos;
  `BatchSpec.refs` takes media ids, promoted by `batch.promote_refs` on create).
- API: `POST/DELETE /api/media/{id}/reference`, `GET /api/media?role=reference`; `media` rows carry `ref_id`,
  `ref_status`. Shared plumbing (JSON loading, the job / fail / quota-wait trio): `library_store.py`.
- Tests: `tests/test_media_references.py`.
