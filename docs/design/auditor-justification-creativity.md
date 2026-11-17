# Design: Justified edits, an agentic auditor, and creativity

Status: **P1–P5 implemented** (2026-11-16) · Date: 2026-11-16 · Builds on: `references-and-generation.md`, colour + fonts (`ops/color.py`, `ops/fonts.py`)

## 1. Problem

Generated edits and chat edits are technically correct but read as generic, and nobody checks them properly:

| Area | Today | Gap |
|---|---|---|
| Creativity | One editor call writes the whole plan in one shot, limited to preset names. | No exploring of ideas, no signature moves, no learning from what users keep. |
| Justification | One optional `rationale` string per plan. Clips have no reason attached. | The AI can't say why a shot is there, and nothing checks that the reasons hold up. |
| Review | One critic pass on 12 frames that can only move or resize **text** layers. The chat editor has no review. | Pacing, colour, story, audio and clip choice go unchecked. No fix → re-check loop. |

## 2. Principles

1. **Code measures and the AI judges** (unchanged rule). Every check that can be a number is a number.
2. **Every choice carries a reason** that is stored with the edit and that a reviewer can check against the picture.
3. **The auditor is independent and read-only.** It gets the brief, the beat sheet, the reasons and the rendered result, but never the editor's chat or reasoning. It reports; the editor fixes. Separating the jobs keeps the auditor honest.
4. **Findings need evidence**: a timeline time plus a frame or a measured value. A finding with no evidence is dropped.
5. **Loops are bounded**: at most 2 fix rounds, stop if the score stops improving, and stay within a cost budget.

## 3. Justified edits (P1)

### 3.1 Beat sheet

The editor plan (`EditPlan`) gains:

```
beats: [{id: "b1", role: hook|problem|setup|demo|proof|benefit|payoff|cta|other, purpose: "one line"}]
```

Every `clip` op carries `beat`, `segment` (footage-log id, e.g. `f1.s3`) and `why` (≤ 15 words). `text` ops carry `beat` and `why`.

### 3.2 Plan validation (code)

`planner.validate_plan(plan, log)` returns concrete errors:

- the clip's segment id is unknown, or its start/end fall outside that segment (±0.25 s tolerance);
- `why` is missing;
- the beat id is unknown, or a beat has no clips;
- the plan has no hook beat.

On errors the editor is asked **once** more, given the errors. If the plan is still invalid it is built anyway, and the errors go into the variation report and the audit. A bad plan should never kill a whole batch.

### 3.3 Richer footage log

- **Measured by code**, one small-frame ffmpeg pass per file, cached in `measure.json`. Per segment:
  - `motion` (mean frame difference)
  - `luma`, `sat`, `warmth` (same maths as `ops/color.py`)
- **From the AI** (`SegmentNote`): `shot` (wide / medium / close / detail), `subject`, and `good_for` (the roles above).
  The model is told to trust the measured numbers ("motion 0.002 = static shot").

### 3.4 Reasons live in the project

- `Clip.meta`: `{beat, role, why, segment, quality}`.
- `Project.notes`: `{rationale, beats, target_shot_s, brief}`.

Both are written by the executor and settable by hand or by the chat agent (`vedit why <clip> "..." --role demo`). They are shown in the clip inspector ("Why this shot"), and the auditor and the chat agent can read them.

## 4. The auditor

### 4.1 `vedit audit`: code checks (P1)

| Category | Check | Severity |
|---|---|---|
| Hook | The first title starts after 1.0 s and the first shot is longer than 2.5 s (nothing grabs attention). | must-fix |
| Hook | The opening clip's segment quality is 3 or lower, when known. | should-fix |
| Readability | Title on screen < 0.6 s + 0.25 s per word. | should-fix |
| Safe zone | The rendered text box leaves x 8–88 % / y 12–75 % (captions: up to 82 %). | must-fix |
| Overlap | Two text/caption layers on screen at the same time with intersecting boxes. | must-fix |
| Typography | More than 2 fonts across titles. | should-fix |
| Typography | Titles use more than one text look. | note |
| Colour | Clip colour spread above 0.05 with no `color match`. | should-fix |
| Colour | Clips have different colour looks. | should-fix |
| Pacing | A shot longer than 3× the target shot length (or 6 s with no target). | note |
| Footage | The same source range used twice. | should-fix |
| Footage | A near-black clip (luma < 0.04). | must-fix |
| Audio | Integrated loudness outside −18…−10 LUFS, or true peak > −0.5 dBTP (measured on the preview). | should-fix |
| Justification | A clip with no `why` when the project has a beat sheet. | note |
| Justification | A beat with no clip. | should-fix |

Output: `{score 0-100, passed, findings: [{id, severity, category, at, message, evidence, fix}]}`. Each `fix` is a suggested vedit command. `passed` = no must-fix findings.

### 4.2 AI reviewer (P2)

- **Input:** the rendered contact sheet with time labels, the hook strip (first 3 s at 4 fps), the code-check findings, the brief, the beats and reasons, and the Style DNA summary.
- **Fresh context and an adversarial prompt:** "find what's wrong; for each clip, does it do the job its `why` claims?"
- **Scores** (1–5): hook, story / justification, pacing, typography, colour, audio, creativity.
- **Findings** must cite a time and a frame index. Fixes are vedit commands (swap clip to segment, retime, change look, restyle text…).
- The model is configurable. The default is the same model as the editor (decision §7.3).

### 4.3 Where audits run

- **Batch generation (P2):** build → render → audit → fixer (editor model, given the findings, returns ops) → re-render → re-audit.
  - Maximum 2 rounds. This replaces the text-only critic.
  - Variations that still fail are exported and marked **Needs attention** with their findings (decision §7.2).
- **Chat editor (P3):** a Stop hook on the Agent SDK session (supported since SDK 0.2.x).
  - It runs when a turn changed the history: `vedit audit`, with code checks always and the AI reviewer if **Auto-audit** is on.
  - Must-fix findings → `{"decision": "block", "reason": <findings>}` sends the agent back to fix them. Maximum 2 rounds; then it reports the remaining issues to the user.
  - An audit card appears in chat with the score, findings and frames.

## 5. Creativity (P4–P5)

- **Concepts before plans.** 3–4 concepts per variation (emotional arc, visual motif, one signature move). A cheap ranking call picks one, and the editor builds it.
- **Signature move library.** Code-executed recipes:
  - zoom punch on the beat drop
  - keyword highlight
  - speed ramp into a reveal
  - freeze frame + title
  - before/after split
  - whip on the beat
  - flash cut
- **Study captures moves.** It records the reference's moves with timestamps, confirms them from measurements (flashes, motion spikes, cuts on the beat), and maps them onto the library.
- **Creative-constraint axis:** rhythmic montage / text-led story / one-take feel / before-after.
- **Taste memory (P5).** Kept and discarded variations, plus audit outcomes, become few-shot examples for the planner.

## 6. Phases and cost

| Phase | Scope | Extra AI calls per video |
|---|---|---|
| P1 | Beat sheet, reasons, plan validation, richer footage log, `vedit audit` code checks, `vedit why`, "Why this shot" in the inspector | 0 (bigger schemas only; at most +1 editor call if the plan is invalid) |
| P2 | AI reviewer + generation audit loop (replaces critic) + "Needs attention" | +1–3 vision calls |
| P3 | Chat Stop-hook auto-audit, audit card, toggle | +1 per finished turn when on |
| P4 | Concepts, signature moves, study captures moves, constraint axis | +1–2 |
| P5 | Taste memory | 0 |

## 7. Decisions (implemented as these defaults)

1. **Auto-audit in chat:** code checks always on (free). The AI reviewer is **off by default** with a toggle, because it adds 15–40 s and one vision call per finished turn.
2. **Videos still failing after 2 rounds:** export, marked **Needs attention** with findings; never hidden.
3. **Auditor model:** same as the editor by default, with a setting to pick a stronger one.

## 8. P1 as built

- `EditPlan.beats` / `Op.beat|segment|why`; `planner.fill_ranges()` + `planner.validate_plan()`, with one corrective
  retry. Remaining errors go to `EditPlan.problems` and the variation report (`plan_problems`).
- `footage.measure()` (motion, luma, sat, warmth per segment, `measure.json`); `SegmentNote.shot|subject|good_for`.
- `Clip.meta` (`ClipMeta`), `Layer.why`, `Project.notes` (`EditNotes`, `Beat`), written by `executor.execute(..., log, brief,
  target_shot_s)`.
- `vedit audit` (`audit.py`), `vedit why`; editor: `clip_why` op, `POST /api/audit`. Inspector panels: "Why this shot"
  (clip), "Edit plan" + "Audit" (project).
- Agent instructions (CLAUDE.md workflow step 5, editor system prompt): justify, audit, fix must-fix before "done".
  This is the soft version of the P3 Stop hook.
- Safe-zone severity is graded: more than 5 % past the line, or cut off = must-fix, a little past = should-fix.
  Generated titles default to x 48 %, max width 820 px so they sit inside x 8–88 %.
- Not yet: frozen-frame detection (needs a decoded-frame pass of the render), face detection in the footage log.

## 9. P2–P5 as built

**P2: AI reviewer + generation loop** (`review.py`). The fixer described here was replaced in revision 2 (§10).
- `full_audit()`: renders the preview, runs the code checks, then the AI reviewer (`auditor` task, `prompts.AUDITOR`).
  - The reviewer sees a 16-frame overview sheet (numbered, with times) plus a hook strip.
  - `verify()` drops findings that point at no frame, and snaps a mistyped time to the frame cited.
  - `merge()`: score = ½ × deduction score + ½ × rubric average; passed = no must-fix in code or AI findings.
- `audit_loop()`: audit → `fix_plan()` (`fixer` task, `prompts.FIXER`, `FixOp`s) → `apply_fixes()` as history steps → re-audit.
  - At most 2 fix rounds. It stops when there's no improvement, and checks out the best round if a fix made it worse.
  - `FixOp` covers: swap_clip (to another logged segment; overlays after the clip shift), trim_clip, move_clip,
    remove_clip, text, text_look, color_look, color_match, transition, volume, music_volume, why.
- `batch.run_variation` runs the loop. Code checks always run; the AI reviewer and fixes run when `review` is on.
  - The report goes in `report["audit"]`.
  - `_public_var` exposes `audit` and `attention`; the batch cards show "Needs attention · Audit NN/100".
- The old text-only critic (`planner.review`, `executor.apply_fixes`, `Review`/`Fix`, `prompts.CRITIC`) was removed.
- `vedit audit --ai`; editor Audit panel "+ AI reviewer"; `POST /api/audit {"ai": true}`.
- Model: new `auditor` tier (default = the editor's model for that quality), set with `vedit ai-models --auditor`.

**P3: chat Stop hook** (`editor/agent.py`)
- `ClaudeAgentOptions(hooks={"Stop": [HookMatcher(hooks=[_on_stop], timeout=300)]})`.
- `_on_stop` audits only when the turn changed the history, and only if the edit changed since the last audit.
  - Code checks always; the AI reviewer when the chat-pane **AI auditor** toggle is on (stored in `.vedit/chat.json`).
  - Must-fix findings → `{"decision": "block", "reason": ...}`, at most `MAX_AUDIT_ROUNDS = 2`. After that, the user decides.
  - Each audit posts an `audit` event, shown as a card in the chat.
- Verified live with Claude Code 2.1.325: the hook blocked the stop and the agent came back. Asked to keep a title in
  the bottom zone on purpose, it explained why instead of moving it, and the next stop wasn't re-audited.

**P4: creativity**
- `ops/moves.py`: 8 deterministic signature moves; `vedit fx list|apply`; plan op `move` (max 2, run after other ops).
- Study: `StyleDNA.signature_moves` (AI). `confirm_moves()` marks the ones the measurements support:
  flash (brightness flashes), punch-in (motion spike away from a cut), whip (spike at a cut), freeze (near-zero
  motion), speed ramp (pacing). `PROMPT_VERSION = 3`.
- Concepts: `planner.explore()` makes one `concepts` call per variation (3 concepts each), then one `rank` call that judges all
  siblings together so the picks stay distinct. The chosen concept goes into `Creative.concept` and the editor prompt
  ("CONCEPT TO BUILD"). `BatchSpec.explore` (default on; wizard checkbox).
- New axis `structure`: rhythmic_montage / text_led / one_take / before_after / problem_solution (`STRUCTURE_HINT`).

**P5: taste memory** (`generate/taste.py`)
- Keep / Discard appends a row to `~/.aiklips/taste.jsonl`: hook, concept, move, beat roles, opening reason, axes, audit score.
- `taste.examples()` (latest 5 kept, 5 discarded) is added to the creative, concepts, rank and editor prompts.
- `vedit taste [--clear]`.

**Cost per video with the defaults:** concepts 1 + editor 1 (+1 if the plan is invalid) + auditor 1–3 + fixer 0–2;
per batch, planner 1 + rank 1.

## 10. Revision 2 (2026-11-16): the editor fixes, sound auditor, pass mark

User feedback on the first P2 build: "why doesn't the auditor just pass the comments back to the editor?", footage
sound fights the music, and "loop until the rating is over a threshold".

**The editor fixes; there is no separate fixer.** `FixOp`/`fix_plan`/`apply_fixes`, `prompts.FIXER` and the `fixer`
task are removed.
- **Generation** (`batch.run_variation`): build → `review.full_audit` → `planner.revise_plan(..., plan, review, threshold)`.
  - The editor gets its own current plan, the reviewer's summary and scores, and every non-note finding. It returns a
    complete revised plan, which is resolved and rebuilt.
  - Each attempt is built in its own folder (`vNN`, `vNN.r1`, ...). The best-scoring one becomes the variation; the
    others are deleted.
  - Report: `report.audit.{threshold, reached, rounds[], kept_round}`. Cards show "Audit 84 (pass 80) · 1 revision: 62 → 84".
- **Chat editor:** the Stop hook already handed findings to the editing agent. It now blocks while
  `not good_enough(score, threshold)`, not only for must-fix findings.

**Pass mark and stopping** (`review.settings()`, `~/.aiklips/audit.json`, `vedit audit-settings`,
editor Audit panel, wizard):
- `threshold` (default 80) and `max_rounds` (default 3, hard cap 8).
- The loop stops at the first round that is `passed and score >= threshold`, at `max_rounds`, or after 2 revisions in
  a row that don't beat the best score (`PLATEAU`). Bad footage can make a threshold unreachable, and an uncapped loop
  would spend the user's AI quota without end.
- Per batch: `BatchSpec.audit_threshold` / `audit_rounds` (`gen run --threshold --max-rounds`).

**Sound auditor** (`sound.py`, code only; the LLMs can't hear):
- Per clip: level (dB), peak, and a speech score from voice-band energy × level swing × onsets per second (words and
  syllables). Measured: speech 0.9–1.0, talking in wind ~0.5, a tone with a pause, music, wind and hum 0.
- Speech under loud music in the footage itself scores ~0, so the planner also uses the log's talking flag, and an
  explicit `ClipMeta.audio` always wins.
- Findings (category `sound`, part of `vedit audit`):
  - background noise under music (must-fix above −26 dB);
  - music not ducked under speech (must-fix);
  - speech muted;
  - a cut that chops a word (still loud in the last 60 ms);
  - speech volume jumps over 8 dB between clips;
  - non-looping music that ends before the video;
  - no speech and no music.
- Generation: each footage segment carries `audio_db` and `voice` in the log, and the editor sets each clip's
  `audio` = voice | ambient | mute (→ volume 1 / 0.35 / 0).
  - `planner.audio_defaults` fills gaps: speech → voice; otherwise mute under music, ambient without.
  - If any clip keeps speech, the music ducks (sidechain) at ≤ 0.45. With b-roll muted it carries those parts at
    full level.
- The reviewer and the editor read each clip's measured sound in `describe_edit`.
- `vedit sound report | auto`.

## 11. Choosing the model for each role (2026-11-16)

Four roles with plain names (`ai.ROLES`): **Watcher** (`vision` tier: study, footage log), **Creative director**
(`planner`: creative, concepts, rank), **Editor** (`editor`: plans and revisions), **Auditor** (`auditor`: the reviewer).
Watcher and Auditor look at frames, so `check_models()` only accepts vision models for them.

- **Precedence** in `model_for()`: `VEDIT_AI_MODEL` (tests/CI) > this job's choice (`ai.use_models()`, thread-local, so
  batches running side by side don't mix) > the saved default (`~/.aiklips/ai.json`) > the quality tier.
- **Per generation:** `BatchSpec.models` ({tier: key}), validated in `batch.validate`, and applied around the
  `batch_prepare` and `variation` jobs. `gen run --model auditor=anthropic/claude-opus-5-5`.
  - Batch rows show the model per role ("AI team" line).
  - References studied outside the batch keep their study.
- **Defaults:** `GET/POST /api/ai/roles`, the wizard's "AI team" panel ("Make these my defaults"), the editor Audit panel's
  "AI reviewer" picker (used by the chat Stop hook's AI review), and `vedit ai-models`.

## 12. Keep improving, and judging revisions side by side (2026-11-16)

- **Keep improving** one finished video: `batch.improve(vid, rounds)` → `variation_improve` job (`improve:<vid>:<n>`) →
  `_loop(start=best)`. Revisions start from the best version, and the editor gets every earlier attempt's score and
  verdict (`revise_plan(attempts=, best_round=)`).
  - Refused when the project's content differs from the AI's last version (`history.same_as`), so hand edits
    are never thrown away.
  - UI: the player's "Keep improving" control; CLI: `vedit gen improve`.
- **Noise.** Measured live: the same unchanged video scored 64, then 53, in two separate reviews. The original 64 was a lucky
  draw that no revision could "beat".
- **The fix: side-by-side judging.** A revision's review also shows the best version's sheets and score
  (`ai_review(compare=)`, 4 images), asks for scores on that scale and a verdict (better / same / worse).
  `review.beats()` keeps a revision when the verdict is "better" and it adds no must-fix; without a verdict
  (code-only audit) the score decides.
- **Stopping.** Generation stops after `PLATEAU = 2` non-improving revisions; an explicit improve run after 3.
  `audit.stopped = {reason: passed|plateau|rounds, ran, asked}` is shown in the player.
- **Footage limits.** `review.recurring()`: a must-fix category flagged in each of the last 3 rounds. The edit can't fix
  it, the footage probably can't either. The player shows what footage to add, or suggests lowering the pass mark.
- **History bug fixed.** `history.sync` compared raw files, so new format fields (`canvas.loudness`) were recorded as
  "manual edit" steps. It now normalises snapshots through the current model.
- **Loudness.** `canvas.loudness = -14` (`loudnorm` on the final mix), so "too quiet" stops being a finding the editor can't fix.

## 13. Making the loop converge (2026-11-16): patches, issue list, footage memory

**Observed:** 8 revisions of one video, none better than the original. The causes:
- every round was a full rewrite, so changes amounted to a random walk;
- each review was a fresh critique, so the target kept moving;
- the editor never saw the footage;
- nothing remembered which footage the reviewer kept rejecting.

**A. Targeted patches** (`planner.patch_plan`, `apply_patch`, `PlanPatch`/`PatchOp`, `prompts.PATCHER`).
- The editor sees its edit as numbered lines (`plan_listing`: C1..Cn clips, T1..Tn titles, MUSIC, MOVE) and the
  top 3 open issues.
- It returns at most 6 changes: replace / trim / move / remove / insert clip, set_audio, edit / add / remove text,
  set_music, set_move. Everything else is locked.
- A patch that names unknown targets, or creates new plan errors, is rejected. Then, or when the patch call fails,
  the loop falls back to a full `revise_plan`.

**B. Issue list** (`review.track`, `IssueCheck`, `AIReview.issue_checks`).
- Every must-fix / should-fix becomes an issue with a stable id (I1, I2...).
- Code issues stay open while their check fires; they are keyed by check + message with numbers stripped.
- The AI reviewer checks each open AI issue (fixed / open / worse), then adds at most 3 new ones; restatements of an
  open issue are dropped.
- `beats()` never accepts a revision that adds must-fix problems or makes an issue worse. It accepts "better", and
  "same" when the revision fixed issues.
- The report and the player carry the list ("Issues: 3 fixed · 2 open").

**C. Footage memory** (`footage.mark/marks/marks_text/clear_marks`, `~/.aiklips/uploads/<id>/marks.json`).
- Each picture finding (hook / story / footage / colour / pacing) with a time marks the segment playing there as
  weak for that clip's role, with the reason.
- Segments brought in by a revision that won get a "good" mark; ones brought in by a revision judged worse get a
  "weak" mark.
- `shot_log` carries the marks, and every prompt shows them ("reviewer: weak as hook x3 (blurry, face turned away)").
  The editor and the first-draft planner are told never to reuse a segment in a role it is weak for.
- The log is refreshed after every round, so learning applies within the same run, and to later videos and batches
  from the same footage.
- `vedit taste --clear-marks` resets the marks.
