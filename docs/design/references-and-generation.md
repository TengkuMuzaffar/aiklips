# Design: References library + AI video generation

Status: **implemented (P1–P4)** · Date: 2026-11-16 · see §12 for what differs from the proposal

## 1. Goal

Add two features next to **Projects** on the home screen:

1. **References.** The user uploads videos they like. The AI studies each one and works out its pattern: pacing, cuts, animation, text style, transitions and sound.
2. **Generate.** A wizard. The user ticks the references to copy the style of, gives their own footage and a short brief, and picks how many videos they want. The system makes N edits that look clearly different from each other, all in the referenced style. Each result is a normal claude-cut project the user can open and keep editing.

## 2. The constraint that shapes everything

**The only AI we have is a subscription LLM API.** That means Claude through the Agent SDK login, or any Anthropic-compatible provider in `editor/models.py`. We have no video-generation model, no GPU, no training and no fine-tuning.

So the work is split like this:

| Job | Who does it | Why |
|---|---|---|
| Measuring (cut times, BPM, beats, loudness, motion) | **Local code**: ffmpeg + numpy, which are already dependencies | Exact, free, repeatable. LLMs are bad at timing. |
| Seeing (text style, animation, layout, hook structure) | **Vision LLM**, shown sampled frames and contact sheets | This is what the LLM is good at. |
| Deciding (style profile, variation plans, edit plans) | **LLM**, with structured JSON output | Creative judgment. |
| Doing (cutting, animating, rendering) | **vedit**, unchanged | Deterministic, undoable, already tested. |

"Generate video" therefore means **assembling an edit from the user's own footage, images and music, in the referenced style**. It does not mean making pixels from nothing. Making new footage would need a video-generation API, which is out of scope under this constraint (see §11).

## 3. Information architecture

```
Home window
├── Projects     (today's home screen, unchanged)
├── References   (new: library of studied videos)
└── Generate     (new: wizard + batches of generated variations)
```

The tabs sit in the existing `home.html` header. The `+ New project` button stays on Projects. Generate has its own `+ New generation` button.

### 3.1 References tab

- A grid of reference cards: thumbnail, duration, status chip (`Queued` / `Studying…` / `Ready` / `Failed`), and tags such as `fast-cut`, `beat-sync`, `bold captions`.
- Add videos by drag-and-drop or "Add videos…". The file is hashed, so adding the same video twice does nothing.
- Clicking a card opens its **Style card**, a readable version of the Style DNA (§5):
  - **Pacing:** average shot length, cuts per 10s, and a cut timeline drawn over the waveform
  - **Hook:** what happens in the first 3s
  - **Text and captions:** font feel, position, colours, animation
  - **Motion:** zoom punches, Ken Burns, shake
  - **Transitions**
  - **Sound:** BPM, % of cuts on the beat, voice-over or not, SFX hits, music energy curve
- Users can correct a trait ("captions are actually yellow"). The correction is saved as an override on top of the AI result.
- Actions: Re-study, Rename, Tag, Delete.

### 3.2 Generate tab

- A list of batches, each a row of N variation thumbnails with status and progress.
- Clicking a variation opens its preview, and **Open in editor** opens the normal 4-pane editor.
- **Keep** and **Discard** on each variation. Kept or discarded is a signal we can feed back later (§10, P5).

### 3.3 Generation wizard (5 steps)

```
1 References ─► 2 Footage ─► 3 Brief ─► 4 Variations ─► 5 Review & generate
```

1. **References.** A checkbox grid of `Ready` references. Ticking two or more shows the merged traits live ("Pacing: fast, from A + B"). There's an optional "lead reference" radio button to break ties.
2. **Footage and assets.** Pick raw clips, product images, logo and music. Music comes only from the user's own files (see §9, copyright). This step is required, because generation needs material to work with.
3. **Brief.** Goal (promo / tutorial / story), hook line or "let AI write it", CTA, target length, format (9:16 by default), and language.
4. **Variations.**
   - Count: 1–10 (slider).
   - What may change between them (checkboxes, all on by default): hook, clip order, pacing, caption style, text animation, transitions, music section.
   - A live estimate of time and AI usage ("≈ 12 AI calls, ~6 min").
5. **Review and generate.** A summary, then Generate. This creates a batch and goes straight to the Generate tab with live progress.

## 4. System architecture

```
┌──────────── home.html (Projects | References | Generate + wizard) ─────────────┐
└──────────────┬─────────────────────────────────────────────▲───────────────────┘
               │ REST                                        │ SSE /api/events (progress)
┌──────────────▼─────────────────────────────────────────────┴───────────────────┐
│ preview_server/app.py  (FastAPI: new /api/references/*, /api/batches/*)        │
└──────┬──────────────────────────┬──────────────────────────────────────────────┘
       │                          │
┌──────▼────────────┐   ┌─────────▼──────────────────────────────────────────────┐
│ jobs.py           │   │ SQLite library.db (+ references, batches, variations,  │
│ durable job queue │◄─►│ jobs tables)                                           │
│ (SQLite, resumable│   └────────────────────────────────────────────────────────┘
│ 2 LLM / 1 render  │
│ workers)          │
└──┬─────────────┬──┘
   │             │
┌──▼───────────┐ ┌▼──────────────────────────────────────────────────────────────┐
│ references/  │ │ generate/                                                     │
│  probe       │ │  footage_index  ─► planner (N specs) ─► editor (per variation)│
│  shots       │ │                                         │                     │
│  audio       │ │                                 executor: vedit ops ─► render │
│  sampler     │ │                                         │                     │
│  study (LLM) │ │                                 critic (1 vision pass)        │
│  → dna.json  │ └───────────────────────────────────────────────────────────────┘
└──────────────┘          all LLM calls go through ai/client.py (one place)
```

### New modules

```
src/vedit/
  ai/client.py            one wrapper for every LLM call: model routing, structured output,
                          prompt caching, retry/backoff, usage accounting, vision fallback
  jobs.py                 SQLite-backed job queue + worker pool, resumable after restart
  references/
    store.py              add/list/delete, content hash, folder layout
    probe.py              ffprobe metadata (reuse media.py)
    shots.py              cut detection (ffmpeg scene score) + motion energy (numpy)
    audio.py              onset envelope, BPM, beat grid, RMS curve, speech/silence (reuse ops/silence.py)
    sampler.py            contact sheets: one frame per shot + 4 fps over the first 3 s + text-appearance bursts
    study.py              LLM: metrics + sheets → StyleDNA
    dna.py                StyleDNA pydantic schema + merge() for several references
  generate/
    wizard.py             validates the wizard payload → BatchSpec
    footage_index.py      studies the user's footage (same pipeline) → shot list with descriptions
    planner.py            LLM: BatchSpec + merged DNA → N VariationSpecs (diversity enforced)
    editor.py             LLM: VariationSpec + footage index → EditPlan (list of vedit ops)
    timing.py             deterministic: snap cuts to beats, fit to target length
    executor.py           applies EditPlan through vedit's own functions; validates each op
    critic.py             renders a sheet, one vision check, at most one repair round
```

The existing `editor/agent.py` (the chat pane) stays as it is. Generation uses the cheaper **plan → execute** pattern instead of a free agent loop (§6.4).

## 5. Studying a reference (pipeline)

Each reference runs once and is cached by its content hash.

| Step | Tool | Output |
|---|---|---|
| 1. Probe | ffprobe | duration, size, fps, has audio |
| 2. Shots | `ffmpeg -vf "select='gt(scene,0.3)',showinfo"` | cut timestamps → shot lengths, cut rhythm |
| 3. Motion | numpy frame-diff on 160px frames at 10 fps | motion energy curve; zoom-punch and shake candidates (sudden scale change) |
| 4. Audio | ffmpeg → 22 kHz mono wav → numpy | BPM, beat grid, onsets (SFX hits), RMS energy curve, speech segments |
| 5. Cut↔beat | code | % of cuts within ±80 ms of a beat (`beat_sync_score`) |
| 6. Transcript (optional) | faster-whisper (existing `captions` extra) | hook words, structure, caption word count |
| 7. Sample frames | `sampler.py` | 2–4 contact sheets (JPG, ~1500 px) |
| 8. **Study** | **1 vision LLM call** | `dna.json` |

The study call gets the metrics JSON, the transcript and the sheets. It must return **StyleDNA**, a schema whose values are **limited to what vedit can actually do**, so every trait can be turned into an edit:

```jsonc
{
  "version": 1,
  "pacing":      { "avg_shot_s": 1.4, "cuts_per_10s": 7, "rhythm": "accelerating", "speed_ramps": false },
  "hook":        { "duration_s": 2.5, "pattern": "bold_question_text", "first_cut_s": 0.6 },
  "structure":   ["hook", "problem", "demo", "demo", "cta"],
  "text":        { "position_y": "22%", "size": "large", "uppercase": true, "color": "#FFFFFF",
                   "stroke": true, "bg_box": false,
                   "animation_in": ["pop_in"], "animation_out": ["fade_out"] },          // enum = vedit presets
  "captions":    { "present": true, "style": "bold", "words_at_once": 2, "highlight": "#FFE600", "y": "72%" },
  "motion":      { "jumpcut_zoom": 1.12, "ken_burns": false, "zoom_punch_on_beat": true },
  "transitions": { "default": "none", "accents": ["zoomin", "whip"], "frequency": "rare" },  // enum = vedit transitions
  "sound":       { "bpm": 128, "beat_sync_score": 0.82, "cut_on_beat": true, "voiceover": false,
                   "music_energy": "high", "sfx_on_text": true, "ducking": false },
  "color_feel":  "high contrast, warm",
  "confidence":  { "text": 0.9, "motion": 0.6, "sound": 0.95 },
  "notes":       "Each demo shot ends on a downbeat; text pops in exactly on the beat."
}
```

- The output is checked against the pydantic schema. If it fails, the error goes back to the model once.
- The traits we measure in code (BPM, shot lengths, beat_sync) **overwrite** whatever the LLM says about them. The LLM only fills in what it is best placed to judge.
- **Merging several references:** `dna.merge(refs, lead=None)` averages the numbers, takes the majority value for enums, and uses the lead reference on ties. This is pure code with no LLM call, so it's instant in the wizard.

## 6. Generation pipeline

### 6.1 Batch layout

Each variation is a normal vedit project, so the editor, undo history and export all work without changes.

```
projects/batches/<batch-id>/
  batch.json            BatchSpec (refs, footage, brief, N, axes) + merged DNA snapshot
  footage_index.json
  v01/ project.json …   ← normal claude-cut project, registered in the library (hidden from Projects until "Keep")
  v02/ …
```

### 6.2 Steps and LLM calls

| # | Step | Runs | LLM calls |
|---|---|---|---|
| A | **Footage index**: same pipeline as §5 on the user's clips. Output: a shot list with a description, a quality score and the best moments | once per batch (cached per file) | 1 per ~5 min of footage (vision) |
| B | **Planner**: merged DNA + brief + N → N `VariationSpec`s | once | 1 |
| C | **Editor**: one VariationSpec → `EditPlan` | per variation | 1 |
| D | **Timing**: snap to beats, fit to length | per variation | 0 (code) |
| E | **Execute + render preview sheet** | per variation | 0 |
| F | **Critic**: look at the sheet, at most one repair | per variation | 1 (+1 if a repair is needed) |
| G | **Export mp4** | per variation | 0 |

For N = 5 that's about **1 + 1 + 5×2 = 12 calls**, or about 17 in the worst case. Footage and references are cached across batches.

### 6.3 Making the N variations actually different

LLMs asked for "5 variations" tend to return five near-copies. Diversity is therefore enforced in three layers:

1. **Code picks the axes before the LLM runs.** For each variation, `planner.py` makes a spread-out assignment over the axes the user ticked, for example a Latin-square-style spread: v1 gets a question hook, fast pacing and pop captions; v2 gets a stat hook, the reference's pacing and typewriter captions. The LLM fills in the creative content *inside* those fixed choices.
2. **One planner call for all N.** The planner sees every sibling spec at once and is told to make the hooks, orders and openings different from each other.
3. **Code checks the result.** Each plan gets a fingerprint (hook type, first 3 shot IDs, caption style, transition set, music offset). If two fingerprints are too close, that one variation is planned again with a "differ from vX on …" instruction.

Everything stays inside the DNA's ranges, so the variations differ from each other but all still match the reference style.

### 6.4 Plan → execute (no free agent loop)

The editor call returns a JSON **EditPlan**, a list of typed ops that map one-to-one onto vedit commands:

```jsonc
{ "variation": "v02", "target_s": 18,
  "ops": [
    { "op": "import",   "files": ["clip3.mp4", "clip1.mp4"] },
    { "op": "keep",     "clip": "clip3", "from": 4.2, "to": 6.0 },
    { "op": "music",    "file": "track.mp3", "offset": 31.5, "duck": false },
    { "op": "cut_on_beats", "pattern": "every_2_beats" },        // resolved by timing.py, not the LLM
    { "op": "text",     "text": "Still editing by hand?", "at": "hook", "preset": ["pop_in", "fade_out"], "y": "22%" },
    { "op": "jumpcut_zoom", "level": 1.12 },
    { "op": "transition", "clip": "@3", "type": "zoomin", "duration": 0.3 },
    { "op": "captions_auto", "style": "bold", "words": 2 }       // always last (CLAUDE.md rule)
  ] }
```

- **Why not let an agent run vedit freely?** A free loop costs 10–40 turns per video. A plan costs one call. A plan is also deterministic, so it can be re-run with no AI cost, and it can be checked before anything runs.
- **Executor:**
  - It calls vedit's Python functions directly. No shell, no permission prompts.
  - Each op is validated first. An invalid op is either fixed (clamped to the timeline) or reported back.
  - Every op is a vedit history step, so the user can undo inside the editor afterwards.
- **Timing is done in code.**
  - The LLM speaks in symbols: `"at": "hook"`, `cut_on_beats`, `"@3"` (the 3rd clip).
  - `timing.py` turns these into exact seconds, using the beat grid of the chosen music and the target length.

### 6.5 Critic pass

- After rendering, `vedit sheet` produces a contact sheet. The critic call gets the sheet, the plan and the DNA, and answers `{ok, issues[], patch_ops[]}`.
- It checks: text inside the safe zones, text readable, hook matches the brief, looks like the reference.
- If it isn't OK, the patch ops are applied **once**, then the video exports anyway. No unbounded loops.

## 7. AI developer notes (`ai/client.py`)

- **One entry point.** Every call goes through `ai.call(task, inputs, schema, images=[])`. Each task has a fixed model tier, system prompt, schema and token budget.
- **Model routing.** It defaults to whatever the user configured in `models.py`:

  | Task | Default | Fallback |
  |---|---|---|
  | study, footage index, critic (vision) | Sonnet | any vision model; if none, metrics-only DNA flagged "low confidence" |
  | planner | Opus (one call; quality matters most here) | Sonnet |
  | editor | Sonnet | Haiku for N > 6 if the user picks "economy" |

- **Structured output.**
  - Every response is checked against a pydantic schema, and enums come from vedit's real preset and transition lists.
  - On a validation error, retry once with the error message, then fail that job, not the whole batch.
- **Prompt caching.** For the N editor calls, the system prompt, the vedit op reference, the DNA and the footage index are identical, so they go first and are cached. Only the VariationSpec changes. This is the biggest saving on cost and speed.
- **Image budget.** Sheets are downscaled to about 1500 px. At most 4 images per call. Frames are never sent one by one.
- **Subscription limits.** A subscription has rate limits and usage caps rather than per-token billing. So:
  - Use a global semaphore of 2 concurrent LLM calls.
  - On 429 or overload, back off exponentially with jitter.
  - A job that is still being rate-limited after the retries is **paused, not failed**, and the batch shows "Waiting for AI quota…".
  - Jobs survive an app restart (SQLite queue).
- **Usage accounting.** Store tokens in and out per job, so the wizard estimate learns from real numbers over time.
- **Privacy.** Matches the current README promise: only metrics, transcripts and downscaled sheets are sent. Video files are never uploaded.
- **Prompt files** live in `src/vedit/ai/prompts/*.md`, with a version number in each. `dna.json` and plans record which prompt version made them, so changing a prompt can trigger a re-study.
- **Evals.**
  - Keep `tests/fixtures/refs/` with 5–10 short licensed clips and hand-written expected DNA. A test checks the code-measured fields exactly, plus key enum fields.
  - For generation, a test checks that the plans validate, that the fingerprints are distinct, and that every exported video exists and has the right length. The LLM is mocked in CI and called for real only in a manual `pytest -m live` run.

## 8. Data model (added to `library.db`)

```sql
CREATE TABLE refs (
  id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  path TEXT NOT NULL,                -- original file (never modified, never copied unless the user opts in)
  dir TEXT NOT NULL,                 -- ~/.claude-cut/references/<sha>/  (metrics, sheets, dna.json)
  status TEXT NOT NULL,              -- queued | studying | ready | failed
  tags TEXT, overrides TEXT,         -- JSON
  prompt_version INTEGER, created_at REAL, error TEXT
);
CREATE TABLE batches (
  id TEXT PRIMARY KEY, spec TEXT NOT NULL,  -- BatchSpec JSON
  status TEXT NOT NULL, created_at REAL, usage TEXT
);
CREATE TABLE variations (
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
  idx INTEGER, project_id INTEGER REFERENCES projects(id),
  spec TEXT, plan TEXT, fingerprint TEXT, status TEXT, verdict TEXT  -- keep | discard | null
);
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,  -- study:ref:12, edit:var:v02 …
  deps TEXT, status TEXT NOT NULL, attempts INTEGER DEFAULT 0,
  not_before REAL, usage TEXT, error TEXT, updated_at REAL
);
```

## 9. API (FastAPI, next to the existing routes)

```
GET    /api/references                     list
POST   /api/references                     upload / register files → queues study jobs
GET    /api/references/{id}                metrics + dna + overrides
PATCH  /api/references/{id}                rename, tags, trait overrides
POST   /api/references/{id}/restudy
DELETE /api/references/{id}
GET    /api/references/{id}/sheet/{n}.jpg
POST   /api/references/merge               {ids, lead} → merged DNA (wizard live preview, no LLM)

POST   /api/batches/estimate               wizard payload → {calls, minutes}
POST   /api/batches                        wizard payload → batch id
GET    /api/batches  /  GET /api/batches/{id}
POST   /api/batches/{id}/cancel | /resume
POST   /api/variations/{id}/keep | /discard | /regenerate
GET    /api/events                         existing SSE stream, gains job-progress events
```

**Copyright and safety, built into the product.** We study patterns: timing, structure, style. We never copy the reference's media. A reference's audio and frames are never put into an output; the executor only accepts files from the user's footage and music list. The References tab says this in one line.

## 10. Delivery plan

| Phase | Scope | Done when |
|---|---|---|
| **P1** | Tabs in `home.html`; `refs` table and store; local pipeline (shots, audio, sampler); Style card showing metrics only | Add a video and see BPM, cut timeline and sheets, with no AI involved |
| **P2** | `ai/client.py`, `study.py`, StyleDNA schema, overrides, merge | A Ready reference shows a full Style card; fixture evals pass |
| **P3** | `jobs.py`; wizard steps 1–3 + 5 with N = 1; footage index, editor, timing, executor, critic | One generated variation opens in the editor with full undo |
| **P4** | Step 4: N variations, planner, diversity fingerprints, batch grid, keep/discard, estimate | 5 variations that a person judges clearly different but on-style |
| **P5** | Learning loop: kept/discarded variations adjust axis weights per reference set; "more like this" | Variation regenerate uses the verdict history |

**Risks and how each is handled:**

- **Animation detection from still frames is approximate.** Sample in bursts (12 fps) around the moments text appears, and show confidence per trait. The user can override.
- **The LLM gets timing wrong.** Timing is never the LLM's job (`timing.py`).
- **Subscription quota.** Show the estimate, queue the work, pause and resume.
- **Variations look the same.** Axes are assigned in code, and fingerprints are checked.
- **Footage doesn't fit the reference** (e.g. 3 clips but the reference has 25 cuts). The planner may reuse sub-ranges, use Ken Burns on images, or slow the pacing. The critic flags it, and the batch row shows a "low footage" warning.

## 11. Out of scope / later

- Creating new footage (b-roll, AI avatars). This needs a video-generation provider. If we add one later, it plugs in as a new `op` type ("generate_broll") behind the same executor, without changing the design.
- Downloading references from TikTok or Instagram URLs: terms-of-service and copyright risk. Files only.
- Cloud or multi-user SaaS. The same modules work behind a web server later, but jobs and storage would need to move from SQLite to Postgres plus object storage.

## 12. Implementation notes (2026-11-16)

Implemented: phases P1–P4. P5 (learning from keep/discard) is not built yet; the verdicts are already stored in `variations.verdict`, ready for it.

| Design | Code |
|---|---|
| `ai/client.py` | `src/vedit/ai/client.py`, prompts in `ai/prompts.py` (`PROMPT_VERSION`) |
| references pipeline | `references/analysis.py` (cuts, motion, BPM, beat grid, onsets, sheets), `dna.py`, `store.py` |
| generation | `generate/spec.py`, `footage.py`, `planner.py`, `timing.py`, `executor.py`, `batch.py` |
| job queue | `jobs.py` (2 workers, `render_lock`, `Retry`, rate-limit pause, `recover()` on start) |
| API + UI | `preview_server/gen_routes.py`, tabs in `static/home.html` |
| CLI | `vedit ref …`, `vedit gen run / list` |
| tests | `tests/test_references_generate.py` (fake LLM via `set_backend`) |

Where the code differs from the proposal, and why:
- **LLM transport.** Calls go through the Claude Agent SDK, with no tools, a JSON-schema `output_format` and images inline as base64. That's the same login as the editor's chat, so it works on the user's subscription and on any provider from Manage models.
- **Quality levels** (best / balanced / economy) pick the model per tier. `~/.claude-cut/ai.json` or `$VEDIT_AI_MODEL` can override them.
- **Uploads are copied** into `~/.claude-cut/references/` and `~/.claude-cut/uploads/`, because the browser hands us bytes, not paths. They're deduplicated by content hash, and originals are never touched.
- **Progress uses polling** (every 2–2.5 s while something is active) instead of new SSE events. It's simpler, and the existing `/api/events` stream stays editor-only.
- **The footage log** is one vision call per 4 files. The result is cached per file, so a clip is only described once across batches.
- **`enforce()`** re-applies the style directives after the editor call (text style and animation, captions, music, jump-cut zoom, transitions). The model only decides clips, order and wording, so weaker or cheaper models still produce on-style edits.
- **Text animation detection** uses the 4 fps hook sheet. There's no extra burst sampling around text appearances yet, so per-trait confidence is shown and the user can override.
- **Images** can be used only as overlays (logo / product), not as timeline clips: vedit sources must have a duration.

A measured run on the real API (Sonnet + Opus, "balanced"): one reference studied in 7 s for about $0.05. A batch of 2 × 10 s variations with review took 82 s, 6 calls, about $0.14.
