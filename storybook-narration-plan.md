# Storybook narration for Simon's room — plan

Status 2026-10-25: POC harness built in `/home/pi/storybook` (not yet a git repo)
and run end to end on *The King Penguin* (29 single-page photos Brad took).
Hardware not ordered. Nothing deployed to Simon's room.

## Goal

Photograph a picture book page by page, OCR it, render an audiobook-style
narration (narrator + character voices + page-turn sounds) on the GX10, and
play it on the `ma_simon_room` snapcast zone when Simon taps an NFC card on a
reader in his room. Then delete the page photos from Immich so they do not
clutter the viewer or the S3 restic backup.

## Pipeline (what exists)

| Step | Where | Status |
|---|---|---|
| Fetch page photos | Immich search by time window → `books/<slug>/pages/*.jpg` (preview size, 1440 px) | manual curl so far |
| OCR + layout | `ocr_pages.py` → `pages.json`; one vision call per page to qwen3-next `:8095` with a json_schema (reading-ordered blocks: narration / speech / sign / title, speaker, tone, page_side) | works: ~20 s/page, every speech bubble captured, crossed-out word honoured |
| Script | `build_script.py` + `cast.json` → `script.json` (segments: say/sfx, voice, direction) | works; cast assignment is regex, see gaps |
| Render | `render_book.py` → `book.wav` / `book.mp3`; Breeze TTS 2 raw API `:7860`, narrator = pure clone of `calm_explainer_f`, characters = same reference + direction string; Parakeet verify per piece; page-turn SFX between spreads | works, ~1× real time |
| Publish | copy MP3 into the music library so Music Assistant sees it; enroll NFC card in the jukebox UI | NOT built — library is NFS from the media host `.150:/media/externalHDD/media/music`, read-only on the beelink |
| Play | Simon-room reader → jukebox API `:8769` with a per-source queue (`ma_simon_room`) | NOT built — jukebox has a single hard-coded queue (`MUSIC_ASSISTANT_QUEUE_ID`) |
| Cleanup | delete page assets from Immich + empty trash | DONE for the King Penguin 2026-10-25 with a dedicated key (`asset.read` + `asset.delete`) in `cecret_lake/storybook/` |

Page-turn sound: Mixkit "Page turn single" (sfx 1104, 0.5 s) in `sfx/page_turn_single.wav`,
"Big paper page turn" (1105) as the alternative. Mixkit licence, not CC0.

## Page turns — proposal

A page turn is the gap between two spreads, not between two photos. Three ways to know
where the spreads are, in order of reliability:

1. **Photograph spreads, not pages.** One photo per open book = exactly one page turn
   per photo, half as many images, and the model reads left page then right page. The
   vision model handled a page with 7 bubbles fine, so a full spread at preview size is
   within reach. Recommended for the next book.
2. **Model emits `page_side`** (left/right from where the gutter is). Measured on the
   King Penguin: right on roughly 3 in 4 photos. Rule used now: a turn goes between two
   photos unless they are `left` then `right`. Wrong sides only cost a missing or extra
   page-turn sound, never words.
3. **Alternation prior**: photos mostly alternate L/R; text-free pages break it. Could
   be combined with 2 as a Viterbi-style smoother. Not worth it if 1 is adopted.

## Voices — proposal

- Narrator: frozen reference clip, pure clone, no direction (the 9.5/10 Listen recipe).
  `calm_explainer_f` for the POC; audition a warmer storyteller later.
- Characters: same reference + a per-character direction string frozen in `cast.json`,
  so a character sounds the same on every page. Tone from OCR is appended per line.
- Direction mode shifts identity (sim 0.49–0.67 vs 0.72–0.87 pure clone): fine here,
  it is the narrator "doing a voice".

## Review app (built 2026-10-25)

`review_app.py` on the beelink `:8811` (stdlib, started with nohup, no systemd unit yet).
Each page photo sits beside its editable blocks (kind / speaker / text / tone, page side,
add/remove block); `cast.json` is editable; "Rebuild + render" runs `build_script.py` then
`render_book.py` in the background and tails the log. The render cache is keyed by a hash
of voice + direction + text, so an edited line re-renders alone.

Brad's round-1 listen (same day): "very good, 96% there". Fixed: "NOT YOU TOO, SARDINES"
re-attributed to Percival, "EMEROR" → "EMPEROR" (OCR dropped a letter), 2.5 s pause after
the page-turn sound. Open picks on `:8899`: caps emphasis (`kp-eat-*.mp3`: Breeze pure
clone ignores "EAT" in caps; a pause or a direction string is needed) and page-turn sound
(`kp-turn-*.mp3`).

## Cast pass (built 2026-10-25, Brad approved)

`cast_pass.py BOOK` runs after OCR (also a "Cast pass" button in the review app):
1. One text-only call to qwen3-next with every page's blocks: returns the cast, a cast
   name for every speech block (schema `enum` of speech ids, compact JSON, or it runs
   out of tokens), and doubts. Prompt rules: a name after a comma is who is spoken TO;
   characters repeat their own speech patterns.
2. Run 3 times (temp 0.1, 0.4, 0.4); a block where the runs disagree is flagged
   medium/low. Self-reported confidence alone was useless: runs agreed on wrong answers.
3. Names map onto the approved `cast.json` voices (substring, keywords, fuzzy, invented
   "X penguin" -> Colony), so approved directions never change.
4. A focused typo call over the book's distinct words; suggestions kept only when they
   are a near-spelling (difflib >= 0.75), else "check spelling". Suggestions are never
   applied automatically.
5. Hand-set voices are stored as confidence `manual` and survive re-runs.

The OCR prompt now asks for the speaker as "who the bubble's tail points at, described
by what they wear" ("penguin wearing a crown"). That fixed the sardines and "OH." lines,
which text alone could not.

Result on the King Penguin (full re-OCR + cast pass): 41 speech lines, 1 wrong voice
(page 25 apology, given to the Emperor with high confidence), 2 soft flags, 4 OCR typos
of which the typo pass caught 3. The review pass took 5 edits. OCR text varies run to
run (new typos appeared), so the typo pass matters.

## Gaps found on the real book

- Cast assignment from speaker descriptions is fragile ("penguin with crown",
  "penguin proud", "large penguin"); fix = one LLM pass over the whole `pages.json`
  with the book's cast to assign speakers consistently, human-editable in `cast.json`.
- OCR typos survive ("persisited", "emeror" is the book's joke spelling, keep); add a
  spell-normalise step gated to narration only.
- Signs (ROYAL DECREES list) are read by the narrator as a run-on; want a list cadence.
- Onomatopoeia (GRRRRRR, SQUAAAWK) is clamped to 3 letters; a sound-effect voice would be better.
- Breeze containers have no restart policy; GX10 memory rule from the OOM incident applies.

## Hardware (Simon's room reader)

ESP32-WROOM-32 DevKitC + PN532 V3 module on SPI (DIP switch), NTAG215 cards, USB-C
PSU, printed case. ESPHome `pn532` `on_tag` → HA event → Node-RED → jukebox API.
No audio on the device; playback is the existing snapcast zone.

## Voice auditions (2026-10-25)

Listening page: `http://192.168.10.217:8899/story-audition/` (8 narrator candidates + the
current voice on pages 1, 2, 4; narrator-does-voices vs full cast on pages 4-6, 19-21, 25).
Script: `storybook/tools/audition.py`; designed clips frozen in `storybook/voices/audition/`.

Breeze limits found: reference clips over ~10 s fail (CUBLAS 500 at GPU_MEM_FRACTION 0.13),
and some designed clips fail as references at any length (3 of 6 cartoon character clips).
Every designed clip is now test-cloned before it is kept. Brad picked the librarian and the kids' TV host; librarian ships with the light 'more energy' direction (v2), the default for new books.
