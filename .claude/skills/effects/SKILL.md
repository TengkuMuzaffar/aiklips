---
name: effects
description: Signature effects for short-form edits - snap zoom, digital zoom, punch-in, growth curve, checklist / checklist pop, headline storm, B-roll overlay, chat takeover, phone mockup, magazine cover, zoom portal, screencast cursor click, HUD lock-on, paper cut-out title, keyword pop, freeze title, flash / whip cuts. Use when the user asks for any of these by name, for "viral / TikTok-style / retention" effects, for zooms on a key moment, for showing results, steps, tips, news or hype, or for cutting to b-roll over a talking head.
---

# Signature effects

Every effect is one command, one undoable step: `uv run vedit --json fx apply <name> --at T ...`
(`vedit fx list` shows them all). They are built from normal layers / clip cuts, so the user can tweak any part
afterwards in the editor (Effects tab shows the same effects, with previews).

## Which effect for which moment

| Moment in the video | Effect | Command |
|---|---|---|
| A reaction, the key word, a detail to notice | **snap_zoom** | `fx apply snap_zoom --at 4.2 [--amount 1.35 --dur 1.0]` |
| Urgency, "wait for it", a reveal building | **digital_zoom** (3 stepped punch-ins) | `fx apply digital_zoom --at 7.0 [--amount 1.42 --dur 1.2]` |
| A beat drop / new sentence (plain jump-cut) | **punch_in** | `fx apply punch_in --at 3.1` |
| Results, growth, "we went from X to Y" | **growth_curve** | `fx apply growth_curve --at 9 --text "+340%" [--amount 4 --dur 3.5]` |
| Steps, a plan, requirements (calm) | **checklist** (ticks draw on) | `fx apply checklist --at 2 --text "Plan; Shoot; Edit"` |
| Tips, features, benefits (energetic) | **checklist_pop** (green badges pop) | `fx apply checklist_pop --at 2 --text "Fast; Cheap; Easy"` |
| News, controversy, "everyone is talking about it" | **headline_storm** | `fx apply headline_storm --at 0.3 --text "AI took my job; Is editing dead?; The new way"` |
| Showing what the speaker talks about | **broll_overlay** | `fx apply broll_overlay --at 5 --source s2 --dur 2.5` (import the b-roll first) |
| A conversation, DMs, "my friend texted me" | **chat_takeover** | `fx apply chat_takeover --at 2 --text "them: did you see this?; me: no?; them: we went viral"` |
| An app, a site, a post on a phone | **phone_mockup** | `fx apply phone_mockup --at 6 [--source s3 --dur 3]` (default: the shot under --at) |
| A person as the hero (intro, "meet X", a transformation) | **magazine_cover** | `fx apply magazine_cover --at 0.5 --dur 2.5 --text "ICON; The comeback; Inside the studio"` (needs a person in shot: keys them out with fast background removal) |
| Diving into a screen / a detail / a new world | **zoom_portal** | `fx apply zoom_portal --clip c5 [--amount 2.6]` |
| Software demos, "click here" (screencast kit) | **cursor_click** | `fx apply cursor_click --at 4 --text "30%,70% > 62%,40%"` |
| Pointing out a person / product / detail (HUD kit) | **hud_lock** | `fx apply hud_lock --at 3 --box 25,30,50,30 --text "THE SECRET"` |
| A warm, crafted title (paper cut-out kit) | **paper_title** | `fx apply paper_title --at 0.3 --text "Grandma's recipe; since 1962"` |
| One shouted word | **keyword_pop** | `fx apply keyword_pop --at 3 --text WAIT` |
| A fact to read, a frozen moment | **freeze_title** | `fx apply freeze_title --at 6 --text "Day 1"` |
| Into a transformation / new place | **flash_cut** / **whip** | `fx apply flash_cut --clip c4` |

## Numbers that make them work

- **Zooms**: snap 1.25-1.4x (never above 1.6 on faces), ramp 0.2 s on the snap curve; hold 0.8-1.5 s, then cut back
  (`--dur`). Digital zoom steps 0.18 s apart. At most one zoom every 3-4 s, on the word that matters (read the
  transcript / captions for the timing; never mid-word).
- **Lists**: 3-5 items, 1-3 words each; items 0.55-0.7 s apart; leave them up 1.5 s after the last one.
- **Growth curve**: one number, the result ("+340%", "$12k", "10x"); 3-4 s on screen.
- **Headline storm**: 3-5 headlines, under 6 words each, the last is the point (it lands biggest, in the middle).
- **B-roll**: 1.5-3 s each, cover the screen, keep the speaker's sound; change b-roll at sentence ends.
- **Chat**: 2-5 messages, short (under 8 words), `them:` / `me:`; about 1 s apart (typing dots come first for them).
  Text has no emoji support yet: write words, not emoji.
- **Phone / magazine**: 2-3 s. Magazine cover: one masthead word (4-8 letters) + 1-3 cover lines.
- **Zoom portal**: into a shot that answers the one before (a screen -> the app, a door -> the room).
- **Kits**: cursor moves 0.75 s then clicks; HUD box tight around the target (`vedit frame --at T` to find it, in
  % of the frame); paper titles move on twos (step_fps 12: `motion set <id> --step-fps 12` makes any layer stop-motion).
- **Budget**: 2-4 signature effects per 30 s. Don't stack two on the same second. Every effect should serve the
  story; say in `vedit why` what it does for the viewer.

## Groups

An effect with several layers becomes one group (`[g2]` next to its layers in `vedit info`, one row on the timeline).
Move it as a whole with `vedit group move g2 --to 6.5` (or `--by -0.5`), delete it with `vedit group remove g2`,
rename it with `vedit group set g2 --name "Intro chat"`. Change one part (a bubble's text, a colour) with the normal
`text edit` / `layer set` commands on that layer id.

## After applying

Check stills at the effect's moments (`vedit frame --at T,T+0.3,T+1`), read them (text inside the safe zone, not
over the face), then `vedit audit`.
