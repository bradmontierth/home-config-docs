# Homegrown Voice Assistant Plan

Status: shadow bench live; both wake candidates trained + benched on real
clips (2026-07-06). **Wake phrase decided: "okay computer".**
This is the persistent design doc for the kitchen voice assistant pipeline.

## Wake Model Bakeoff Result (2026-07-06)

Both candidates trained on GX10 (livekit-wakeword, conv_attention/medium,
110k steps, optimal threshold 0.66 from eval).

Synthetic validation (25.9h held-out TTS): hey_computer looked better
(recall 92% vs 87% at matched 0.077 FP/hr). **This was misleading.**

Real-clip replay (the data that decided it):
- Positives = 15 clips of the user's real voice the hey_livekit control
  MISSED (pessimistic sample), Parakeet-attributed to phrase.
  Recall: okay_computer 6/6 (all >=0.935), hey_computer 8/8 (0.66-0.98).
- Negatives = 173 real household clips that tripped the control (nobody said
  the phrase). False-fires @0.66: **okay_computer 7, hey_computer 29 (~4x)**.
  @0.8: 4 vs 11. @0.9: 1 vs 7.

Why the flip: "hey computer" trips on real household "hey…" onsets + chatter;
"okay computer" needs the specific "okay" attack. Synthetic negatives never
contained the family's speech patterns. **Lesson: bench on real audio.**

Winner: **okay_computer** — perfect recall on real voice at high confidence,
~4x fewer real-world false positives. Models saved on GX10 at
`/home/pi/wake-train/output/{okay,hey}_computer/.../*.onnx`; both copied to
kitchen-speaker `/home/pi/wake-bench/`. Replay scripts + CSVs in
`home_config/voice-assistant/training/` and the bench dir.
NOTE these FP counts are relative (clips pre-filtered by the control's 0.30
threshold), not an absolute per-hour rate; and all are STAGE 1 only.

### Stage-2 verification proven (2026-07-06) — the metric that matters
Ran all 13 okay_computer stage-1 false-fires (@0.5) through Parakeet and
applied stage-2 logic (transcript must contain "okay computer"):
**13/13 REJECTED** — transcripts were unrelated speech ("I just don't want
kids", "yogurt beer on the yogurt", etc.) or empty/noise. Zero false
positives reach the house even at the permissive 0.5 threshold. And Parakeet
transcribed all 6 real "okay computer" positives cleanly → stage-2 PASSES
them → zero added false negatives. Two-stage design validated end-to-end.
Small sample (13 FP / 6 TP); live bench now widening it; real orchestrator
will log verify verdicts at scale. This confirms: keep stage-1 permissive
(~0.5), let free/perfect Parakeet verify clean up.

## Vision

Not a smart-home voice controller. A kitchen companion for:

1. **Cooking timers** (primary use case): named parallel timers, dashboard
   countdown cards, spoken announcements naming the timer, themed fun sounds
   for kids (chicken cluck for chicken, sizzle for tofu, etc.).
2. **Lists**: shopping / todo / reminders, reusing the voice-notes companion
   backend. "Show me my todos" switches the kitchen dashboard view.
3. **Dashboard as Echo-Show-style window**: wake badge, live transcription as
   you speak, LLM response text, timers, and lists on the 13.3" Waveshare
   kitchen display.
4. A small allowlisted set of HA entities (blinds) later. Nothing open-ended.

Rejected: Home Assistant Assist pipeline (slow, format-restricted, tied to HA
update lifecycle) and openWakeWord (false positive/negative rates are a
deal-breaker; its synthetic training data is the root cause).

## Existing Components (all already running)

| Piece | Where | Notes |
| --- | --- | --- |
| STT | GX10 `http://192.168.10.187:8090` | Parakeet; batch + realtime WS (`/parakeet/realtime`, PCM16 in, ~2s micro-batch finals). Phrase biasing available. |
| LLM | GX10 `http://192.168.10.187:8095/v1`, model `qwen3-next` | Respects `chat_template_kwargs: {"enable_thinking": false}` (doorbell shim pattern, `doorbell_tts/shim/app.py:451`). Measured 0.8s end-to-end for a JSON intent parse. Also strip `<think>…</think>` defensively. |
| TTS fast path | Beelink router `http://192.168.10.217:8891/v1/audio/speech` | `KOKORO_FORCE_FALLBACK=true` → Kokoro on GX10 `:8880`. Disk-cached. |
| Lists backend | voice-notes companion `http://192.168.10.217:8768` | `/api/items` (reminder/todo/shopping), analyze prompt with confidence + due-date rules, complete/delete, FCM reminder pushes. Reuse nearly untouched. |
| Dashboard | `dashboard_webapp` on Beelink `:8777` | FastAPI + `/api/live` WebSocket already pushing to the kiosk. Add assistant event types. Kiosk multi-touch works. |
| Satellite host | `kitchen-speaker` / 192.168.10.24, Pi 4 | Audio out 3.5mm (squeezelite `Kitchen-Big-Speakers` → MA queue `e4:5f:01:67:1e:56`, librespot, gmediarender). NFC jukebox reader on USB. **Mic: TONOR G11 USB (card 3, `plughw:CARD=microphone`), 16k mono capture verified 2026-07-05.** |
| MQTT | Mosquitto on Beelink | Transport for the mode kill switch. |

## Architecture

```
kitchen-speaker Pi (satellite)                Beelink (orchestrator)                    GX10
┌─────────────────────────────┐   trigger   ┌──────────────────────────┐
│ ALSA 16k mono ← TONOR G11   │ ──────────► │ voice-orchestrator       │──► Parakeet :8090
│ ring buffer (pre-roll)      │  + audio WS │  stage-2 wake verify     │    (verify + realtime WS)
│ livekit-wakeword (permiss.) │             │  intent LLM (no think)   │──► qwen3-next :8095
│ Silero VAD endpointing      │ ◄────────── │  timers engine (SQLite)  │──► Kokoro via router :8891
│ duck MA + play TTS/alarms   │  tts audio  │  tools: companion :8768, │
│ MQTT mode subscriber        │             │   HA proxy, timers       │
└─────────────────────────────┘             │  events → dashboard WS   │
                                            └───────────┬──────────────┘
                                                        ▼  /api/live
                                            kitchen kiosk: badge → live captions → response,
                                            timer cards, todos/shopping views
```

Satellite stays dumb (capture, wake, VAD, playback, duck). All smarts on the
Beelink. Makes satellite #2/#3 trivial later.

## Wake Word: Two-Stage Design

This mirrors what Amazon/Google actually do (permissive on-device detector +
cloud-side wake verification on buffered audio).

1. **Stage 1 (Pi)**: livekit-wakeword at a permissive threshold + rolling
   ~3s pre-roll ring buffer. Attacks false negatives directly.
2. **Stage 2 (GX10)**: ship pre-roll to server; Parakeet transcribes it
   (~100-200ms) and fuzzy/phonetic-matches the wake phrase. Only then does
   anything light up or duck. False accepts multiply: stage-1 FAs × stage-2
   rejection ≈ a false response every few weeks instead of daily.
3. Wake phrase: 3-4 syllables, uncommon phonemes, not kitchen/TV vocabulary.
   Custom phrase trained locally with the livekit-wakeword pipeline (GPU on
   GX10; synthetic data via espeak/VoxCPM, single-YAML config).

**Engine history (2026-07-05)**: Porcupine was the original pick, but
Picovoice discontinued its free tier on 2026-06-30 (existing free AccessKeys
disabled; 7-day enterprise trial only now). Replaced with
**livekit-wakeword** (github.com/livekit/livekit-wakeword): Apache-2.0,
ONNX conv-attention classifier on the openWakeWord mel/embedding frontend,
claims ~100x fewer FP/hour vs openWakeWord, open training pipeline, no
license server. Stateless API: `predict(2s int16 window)` → score 0-1.
Measured on the Pi 4: ~240ms/call with 1 ORT thread (session options
patched in shadow_wake.py; defaults spin all cores for no gain), ~60% of
one core at a 352ms hop. Continuous scores mean the bench logs raw
peak/near-miss scores → threshold curves from one deployment.

Known weak spot: music on the big speakers. The TONOR's AEC can't cancel audio
it doesn't play. Mitigation: duck/pause MA queue on stage-1 trigger.

## Kill Switch: 3-State Mode

MQTT retained topic `voice/kitchen/mode`, HA select entity via MQTT discovery:

- `active`: full pipeline. Still logs every trigger + clip.
- `shadow`: detection + logging runs; no badge, no duck, no audio. Flip to
  this from the phone when tuning is needed; data collection continues.
- `off`: mic pipeline stops (privacy/guests).

Scope: gates the mic → wake path ONLY. Running timers still fire, dashboard
touch interactions still work.

**Deployed 2026-07-06 (active/shadow switch)**: satellite exposes HTTP
`POST /mode {active|shadow|off}` (defaults shadow). HA control is
`switch.kitchen_voice_assistant_active` — a **2-state** switch (ON→active,
OFF→shadow) created via **MQTT discovery published from Node-RED**, deployed
through the NR Admin API (`POST /flow`, tab `7d4069eae9ec1eda`) with **no HA or
Node-RED restart**. Command topic `voice/kitchen/assistant_active/set` →
satellite /mode; state synced back from a 30s /health poll. Flow + deploy
script version-controlled in `voice-assistant/node-red/`. The third `off`
state (full mic pause) is in the /mode endpoint but not yet exposed as an
entity — add a select later.

All thresholds (livekit-wakeword stage-1 threshold, stage-2 fuzzy threshold,
VAD silence ms) hot-reloadable — no redeploy to tune.

## Shadow Bench + Labeling Protocol (LIVE NOW)

Deployed on `kitchen-speaker` at `/home/pi/wake-bench/` before the rest is
built, to collect real-house FP/FN data early. See "Shadow Bench Operations"
below for run details.

Labeling insight: **with a wake word nobody says naturally, every unmarked
trigger is a false positive by definition.** So labeling needs one button:

- **"I just said the wake word"** (`POST /mark`, page at `:8781`): if a
  trigger fired in the previous ~8s → auto-label **true positive**. If not →
  **false negative**, and the continuous rolling buffer saves the last ~12s of
  audio anyway (the clip that explains the miss).
- Any trigger without a nearby mark = **false positive** (review page allows
  correcting edge cases).
- No popup/sound needed; shadow stays silent. TP/FN data comes from deliberate
  test sessions (different spots, cooking noise, music playing).
- Mark button also lives on the kitchen dashboard header ("🎙 Said the wake
  word", calls the bench cross-origin; bench sends CORS `*`). Deployed
  2026-07-05 (index.html/app.js/styles.css + container rebuild + kiosk
  reload).
- Remote mark from anywhere: HA toggle `input_boolean.wake_word_mark`
  (created via HA websocket API) → Node-RED tab "Wake Word Bench"
  (b8ae4e9783309aec, docker node-red :1880) → POST bench /mark → auto
  toggle-off + `notify.notify` result push to phones. Acts as a momentary
  button in the HA app; verified end-to-end 2026-07-05.
- Sample spacing protocol: one press per utterance, press within a few
  seconds of speaking (8s match window), ≥15s between attempts (FN clips
  capture the last 12s — closer attempts bleed into one clip).

Wake phrase candidates (Star Trek theme): **"okay computer"** (favorite) and
**"hey computer"**. Bare "computer" rejected — everyday conversation word.
Plan: train BOTH with the livekit pipeline on the GX10 and run them
side-by-side in the shadow bench (WakeWordModel accepts multiple models,
scores per model) with hey_livekit kept as control; pick the winner by data.

Bench logs the raw score for every trigger (`peak_score`) and throttled
`nearmiss` events (score ≥ 0.12 below the 0.30 trigger threshold), so one
deployment produces full threshold curves. Also logs ambient RMS every 5 min
to map household noise patterns. Verified end-to-end 2026-07-05 by playing
"Hey livekit! Set a timer for three minutes" (Kokoro TTS) through the kitchen
speakers: trigger peak 0.677, clip saved.

## Timers Design

Engine in the orchestrator (NOT HA). `{id, label, ends_at, duration,
sound_theme, state}` in SQLite (absolute `ends_at` → restart-safe).

- **No tick streaming**: dashboard receives timer objects on
  create/cancel/complete over `/api/live` and ticks locally.
- **Pre-render announcements at creation** ("Your tofu timer is done") via
  tts-router → cached WAV → alarm playback is instant and GX10-independent.
- **Sound themes**: intent-parse JSON includes `sound_theme` chosen by the LLM
  from an enum of locally-stored CC0 clips (cluck, moo, sizzle, steam-whistle,
  bubbling, oven-ding, marimba fallback). Clips stored on the satellite.
- Alarm sequence: duck/pause MA → themed sound → announcement → loop with
  ~4s gaps (voice-dismiss window) → auto-stop after ~10 repeats.
- Dismissal layers: touchscreen tap (most reliable) > **barge-in voice: while
  ringing, the satellite transcribes the mic CONTINUOUSLY via Parakeet for
  "stop"/"cancel"/"okay computer" — NO wake word needed** (Echo/Nest-style,
  added 2026-07-06 after live test: saying "stop" did nothing because it wasn't
  the wake word). Alarm PLAYBACK runs in a thread; the main capture loop is the
  single mic reader and does the listening (~1.2s chunks). The themed sound /
  announcement never contain a dismiss word, so no false stops. > HTTP
  /alarm/dismiss > auto-stop.
- Wake-turn capture (2026-07-06 live test): grab the stream continuously (like
  Echo/Nest — supports "okay computer set a timer" run together). DO NOT drain
  after the chime (that loses the run-together command). Instead capture for a
  MIN_CAPTURE window (~3s) during which VAD cannot endpoint, then endpoint on
  ~700ms trailing silence. The min window is what stops the wake-chime bleed
  from ending capture before the user speaks. (A first attempt drained the mic
  after the chime; wrong — reverted.)
- Follow-ups in schema from day one: time-left query, add N minutes, cancel
  by name, cancel-all, unlabeled timers ("10 min timer"). Ambiguous "stop the
  timer": ringing timer wins, else nearest-to-finish, always announce which.
- Audio note: alarm player shares bcm2835 Headphones card with
  squeezelite/librespot/gmediarender — verify ALSA dmix concurrent playback;
  fallback is MA announce feature (auto duck/resume, more latency).
- Playback path (2026-07-06): ALL assistant audio — chimes, alarm sounds, AND
  Kokoro TTS (announcements + replies) — plays via direct `aplay` on the
  satellite's Headphones card. NONE goes through Music Assistant. Music is a
  separate stream on the same card (ducking TBD).
- Dynamic volume (2026-07-06): satellite applies **software gain to its own
  audio only** (mixer untouched → music unaffected), driven by the existing
  Node-RED global `mode` via `POST /volume` (tab 0f4b1b8a369d5d91, polls every
  2 min). Tiers Day 60 / Early Morning+Evening 40 / Night 30. Alarm floored at
  50% so cooking timers stay audible at night. Persists in data/volume.

## Incident 2026-07-05: training OOMed the GX10 (power cycle required)

Six minutes after VITS generation started, NVRM (GPU driver) unified-memory
allocations exhausted the GB10 pool. Kernel OOM killer killed vllm (:8095)
first — GPU-side allocations are invisible to RSS accounting, so it kept
killing the wrong processes (Parakeet, Kokoro followed) while the box
livelocked; ssh unreachable (banner timeout), only memory-resident HTTP
servers answered. Recovery: physical power cycle. Containers with restart
policies self-healed; manually `docker start music-llm-qwen36-aeon-dflash-test
kokoro-gb10-bench` (no restart policy — the :8095 LLM is that "test"-named
container). Telemetry = user timer `gx10-telemetry-publish.timer`, self-healed.

Guards now baked into run_training.sh / the docker run:
1. Container `--memory=32g --memory-swap=32g` (CPU side).
2. `sitecustomize.py` in-container: `torch.cuda.set_per_process_memory_fraction(0.12)`
   — caps GPU/unified allocations; training gets CUDA OOM instead of
   killing the host. (Docker memory cgroups do NOT govern NVRM allocations.)
3. `tts_batch_size` 100 → 32.
4. `HF_HUB_DOWNLOAD_TIMEOUT=30` (earlier incident: MUSAN download hung ~2h
   on a dead unauthenticated HF connection with no timeout).

Lesson: anything using CUDA on the GX10 shares one unified pool with vllm's
pre-reserved slice. Cap torch memory fraction for ALL new GPU workloads.

## Improvement Loop (post-training, data-driven)

Ordered by value-per-risk; real clips from the bench feed all three:

1. **Calibration/model selection** (validation only): marked clips pick the
   winning candidate + per-model thresholds. Zero risk. Week one.
2. **Hard-negative mining** (the production flywheel): real FP clips from
   shadow/active mode appended to training negatives, re-run final phases.
   Directly attacks the FP deal-breaker with household-specific confusers.
3. **Positive fine-tuning** (only if data shows a gap): synthetic positives
   are ~900 adult TTS voices — the likely gap is the kids. If their marks
   score consistently low, mix real positives in at low LR, KEEP full
   negative batches (avoid forgetting), augment real clips through the same
   RIR/noise pipeline. Accepts personalization (family voices > guests).

Hygiene: hold out validation clips from any training; keep the mark button
habit after go-live (active mode logs all triggers too).

## Intent Schema (deliberately small)

`set_timer`, `timer_query`, `timer_adjust`, `timer_cancel`, `ask` (knowledge
question → smart model, see below), `add_items` (reuse companion analyze),
`show_todos`, `show_shopping`, `complete_item`, `set_reminder`, and later a tiny
allowlisted HA set (blinds). Strict JSON, temperature 0, low max_tokens,
thinking off.

Key insight: **qwen is already the classifier** — every command makes one
intent-parse call (~0.8s). Adding `ask` (and the lists intents) costs ZERO
extra latency for timers; only an `ask` classification triggers a second call
to the smart model. No "ask" keyword needed — natural phrasing routes via qwen.

## Follow-up / Continued Conversation (design locked 2026-07-07)

Talk again after a reply WITHOUT re-saying "okay computer" — "add eggs to my
shopping list" … "also add milk" … "actually take those off". This is Alexa
**Follow-Up Mode** / Google **Continued Conversation**.

**How the big two do it (and what we copy):** after the assistant speaks, it
reopens the mic for a SHORT bounded window (Alexa ~5s, Google ~8s), re-armed
each time you actually speak; the ring stays lit as the "you can talk" cue; a
device-directedness check drops speech not addressed to it SILENTLY; and
dialog/session context carries across turns for "those/also/undo". It is NEVER
an indefinitely-open mic — that's the openWakeWord false-accept trap we already
rejected. We copy: bounded reopen (re-armed by speech), silent drop of
non-actionable speech, session context.

**Decisions (user, 2026-07-07):** default ON; window **7s**; NOT suppressed
after "set a timer" ("set a timer … also add eggs" is a real kitchen flow).

**Where the work is (satellite + orchestrator; NO new model — reuse qwen):**
- Satellite `run_turn`: after playing the reply, enter a follow-up loop —
  `capture_command` with a ~7s speech-ONSET window (no wake chime, no capture
  chime; the spoken reply is the ack). On speech → POST `/command/audio?followup=1`
  → play reply → re-arm. Exit when: the window passes silent, the orchestrator
  returns intent `none` (not addressed / not actionable → SILENT, no audio), an
  alarm is ringing/queued (bail so a timer isn't held off the mic), or a
  FOLLOWUP_MAX_TURNS safety cap. Needs `capture_command` split into
  min-capture-ms (chime-bleed guard, wake turn only) vs onset-ms (how long to
  wait for speech to start).
- Orchestrator: **session state** (module-level, single satellite, ~90s TTL):
  last intent + added/completed items + timer + transcript. `intent.parse` gains
  an optional `context`; with it, a follow-up system-prompt variant that says
  "this may be a continuation OR unrelated background speech — only act if
  clearly addressed and actionable, else intent `none`", stricter about `none`
  than the post-wake parser (the wake word didn't gate this turn).
  `handle_command(followup=True)`: parse with context FIRST; on `none` return
  silently (emit nothing — a dropped follow-up is invisible: no chime, no
  "sorry", no dashboard flash); on actionable, proceed + update session.
  `/command/audio` reads a `followup` flag and returns the intent so the
  satellite knows whether to continue.

**Built (2026-07-07):** window + session context + silent none; `remove_items`
(delete by name OR "scratch my last"/"undo" via session's last-added item ids);
`unclear` intent (addressed-but-unmapped follow-up → brief spoken retry, keeps
session alive — vs `none` which drops silently and ends it); and the dashboard
**"Listening…" badge** during every follow-up window (satellite pings
`POST /session/listening` → orch emits `followup_listening` → kiosk shows the
badge without clearing the last response). The `none`-ends-session-silently trap
was the "scratch my last did nothing" bug: an addressed-but-unmapped utterance
looked identical to background chatter; `unclear` + `remove_items` fix it.

**Gotcha fixed:** companion `analyze` returns items WITHOUT ids, so undo
(delete-by-id from the session) matched nothing — `add_from_text` now resolves
added items back to their stored active rows (match on type+lowercased text) so
callers get ids. Note `unclear` is deliberately conservative (qwen biases to
`none` — we do NOT want it talking back to the room), so many unsupported-but-
addressed phrases still drop silently, Alexa-style.

**Still TODO:** richer reference resolution ("take those off" mid-list).

**Smart / bulk list ops — BUILT 2026-07-07.** `remove_items` and `complete_item`
now run an LLM **resolver** (`lists.resolve_targets`): qwen gets the current list
(`id=<n> <type>: <text>` lines) + the user's phrase and returns which ids match —
one item, several ("milk and bread"), a category ("the dairy"), a property
("everything orange"), or "all". New `clear_list` intent (+ `list_type` field)
wipes a whole list. **Confirmation gate:** any op that would remove >1 item, or a
clear, sets a session `pending` op and asks ("That'll remove 3 items: … Say yes
to confirm"); the follow-up window catches the spoken yes/no (`_affirmation` in
app.py; a non-yes/no reply abandons the pending op and is parsed fresh). Single
removes, undo ("scratch my last"), and all completes execute immediately (not
destructive). Replaced the old rapidfuzz single-item matchers. NOTE resolver is a
model judgment call — "everything orange" matched oranges/orange-juice but not
carrots (read the word, not the color); tune the resolver prompt if needed.
CAUTION: testing bulk removes/clears on the LIVE shared family list is dangerous
— use fake tokens and CANCEL clears; I polluted the real list once (2026-07-07).

## Knowledge / Ask Mode — BUILT + DEPLOYED 2026-07-07 (design locked 2026-07-06)

**Status: live.** qwen classifies `ask` (with cleaned `query`); orchestrator
streams GPT-5.4 via OpenRouter and splits at `===MORE===` short-first. Verified
end-to-end via `/command`: "how many tablespoons in a cup?" → intent ask →
spoken "There are 16 tablespoons in 1 cup." + TTS, ~2.8s; full answer streamed to
the dashboard body. Files: `orchestrator/openrouter.py` (streaming client, key
from mounted `/secrets/openrouter.env`), `orchestrator/ask.py` (sentinel split +
background `_stream_full`), `intent.py` (`ask` + `query`), `app.py` ask branch.
Compose mounts `/home/pi/cecret_lake/openrouter/.env:/secrets/openrouter.env:ro`
+ env `OPENROUTER_MODEL=openai/gpt-5.4`. Dashboard: `#assistant-body` +
`ask_thinking`/`ask_stream`/`ask_full` handlers (40px scrollable body under the
88px spoken headline; ask popups linger 45s). **⚠️ OpenRouter balance was ~$0.61
on 2026-07-07 (~60–100 asks) — top up.** Original design below.



For factual/general questions ("when do babies start walking?", "how many
tablespoons in a cup?"). Routing: qwen intent-parse tags it `ask` with a
cleaned `query` field — no keyword, no extra latency for timers (same single
qwen call). Only `ask` fires the smart model.

**Smart model: GPT-5.4 via OpenRouter** (user choice: best price/knowledge
balance). OpenAI-compatible: base `https://openrouter.ai/api/v1`, `Bearer`
auth. Key + model config already exist in `voice-notes-local/.env`
(`OPENROUTER_API_KEY_FILE`, `OPENROUTER_MODEL`); client pattern to copy:
`podcastv2/feedwriter/feedwriter/openrouter_client.py`. Make model an env var
(default slug ~`openai/gpt-5.4`, confirm exact slug at build).

**Two-part answer, streamed, short-first (NOT JSON mode).** JSON can't be
parsed until closed, so it fights streaming. Instead prompt the model to emit:
```
<1–2 sentence spoken answer>
===MORE===
<full, richer answer for the dashboard>
```
Flow (all in the orchestrator; **satellite needs NO changes**):
1. Stream tokens from OpenRouter.
2. The instant `===MORE===` appears → the spoken answer is complete → Kokoro
   TTS it → return `{response: spoken, audio_url}` to the satellite (~2s, fast;
   speaks regardless of how long the full answer is). Emit `response` event
   (spoken text) to dashboard.
3. In a background task, keep streaming the full part → emit batched
   `ask_stream` events (~300ms / sentence) so the dashboard fills in live while
   TTS reads the short version. Emit final `ask_full` on completion.
- Prompt the spoken part for 1–2 sentences, kitchen-glance brevity; full part
  richer. Robustness: if the model omits `===MORE===`, treat the whole reply as
  the answer + auto-truncate a spoken version.
- UX rationale (user): short answer read aloud fast; if it piques interest, the
  full answer is already on the dashboard to read. "12 months" → speak it;
  don't force reading the rest.

Build tasks: (a) add `ask` intent to `intent.py` schema + validation (add
`query` field). (b) orchestrator `openrouter.py` streaming client + sentinel
parser + background full-stream task. (c) `handle_command` ask branch. (d)
dashboard: consume `ask_stream`/`ask_full` → show streaming full answer under
the spoken headline (reuse assistant popup, maybe an expandable body).

## Lists — orchestrator BUILT + validated 2026-07-07 (dashboard views pending)

todo/shopping/reminders reusing the voice-notes companion at
`http://192.168.10.217:8768`. Intents `add_items`, `set_reminder`, `show_todos`,
`show_shopping`, `complete_item` added to `intent.py` (+ `item_text` field).
Companion also does FCM reminder pushes.

**Companion contract (ground truth = its `/openapi.json`; source at
`/home/pi/voice-notes-android/companion/app/main.py`, container
`voice-notes-companion`).** It is NOTE-centric, not item-centric — adding is a
two-step reuse of its own analyze LLM:
`POST /api/notes/sync` (create a note holding the raw text) then
`POST /api/notes/{id}/analyze {source_text}` → it extracts typed items
(reminder/todo/shopping), parses due dates, scores confidence, dedupes. So we
forward the user's WHOLE command to analyze — its prompt keys off framing words
("shopping list", "remind me", "todo") to pick each item's type; pre-parsing
would break typing. Read: `GET /api/items?status=active` (per-row `type`).
Mutate: `POST /api/items/{id}/complete`, `DELETE /api/items/{id}`. Valid users
are `brad`/`adrienne` only.

**Lists are SHARED** (2026-07-07 user call — one household, no good way to
isolate by voice, reminders play on the shared device). Reads span all users (no
`user` filter); new items are filed under `LIST_OWNER` (default brad) purely
because the companion requires a valid owner on write — display never filters by
it. `complete_item` fuzzy-matches (rapidfuzz partial/token_set, threshold 70)
across the shared active list.

Orchestrator files: `orchestrator/lists.py` (companion client), `config.py`
(`COMPANION_URL`, `LIST_OWNER`, `LIST_MATCH_THRESHOLD`), `format.py` list
phrasing (strips the companion's "Buy " prefix for speech), `app.py` branches.
Emits dashboard events `show_list {list_type, items}` and
`list_updated {items, added|completed}` — **no consumer yet; dashboard list
views are the remaining work** (todo/shopping views, "show my todos" switches
the kiosk view). NEEDS a container rebuild on Beelink `~/voice-pipeline` to
deploy. Validated 2026-07-07 against the live companion: all 5 intents route
(qwen), add/fetch/complete round-trip; timers + ask regressions intact.

## Backlog / Future Features (discussed 2026-07-07, not yet built)

Three features brainstormed + designed on 2026-07-07. All judged sound; pieces
mostly already exist. Not sequenced — capture-for-later.

### A. Live streaming transcription on the dashboard (highest perceived-intelligence win)
Google/Alexa show words appearing ~500ms after you speak them, with earlier
words self-correcting as context grows. Today the kiosk just says "Listening" —
functional but feels dumb. This is about **responsiveness**, not total
transcription time (Parakeet is ~200ms batch after VAD).

**Approach (user's insight, and it's the right one): repeated full-buffer
batch, NOT a sliding window.** Every ~400ms during capture, re-decode the
*entire* audio-so-far as a normal batch and emit it as the partial. Because each
partial is a full-context batch (not a limited-right-context stream), there is
**zero accuracy penalty** — the only "word changes retroactively" behavior is the
genuine one where real future context improves an earlier guess (exactly the
Google-Home effect). The final partial after endpoint already *is* the
authoritative full-utterance decode; no separate "replace with batch" pass
needed. Cost = redundant GPU (~7 decodes for a 3s utterance); free on an
otherwise-idle home GPU (Parakeet RTF ≈ 0.05, a 3s clip ≈ 150ms).

**Work (revised 2026-07-08 review):** (1) satellite: keep the capture path
byte-for-byte untouched (min-capture window, Silero endpointing, chime-bleed
guard are hard-won) — during capture, fire a best-effort `POST /partial` with
the ENTIRE buffer-so-far every ~400ms. A 3s utterance is 96 KB of PCM16 —
trivial on LAN. No persistent socket, no protocol change; a bug in partials
can't break commands because the final `/command/audio` path is unchanged, and
full-buffer snapshots natively match the snapshot/seq front-end design below.
(2) orchestrator: `/partial` re-decodes the buffer → emits `partial_transcript
{text, seq}`; endpoint → existing final decode → `transcript`. (3) dashboard:
new `partial_transcript` handler, the "Listening" pill morphs into live growing
text, finalized by `transcript`. **Hard rule: partials are display-only; intent
parsing ALWAYS runs on the final authoritative `transcript`, never a partial.**
Doesn't touch intent/lists/timers logic at all. The reuse-`/parakeet/realtime`
question is ANSWERED (2026-07-08): its docs state it is micro-batch per ~2s
chunk with NO partial/revision events — choppy 2s finals, no cross-chunk
context. Do NOT reuse it; the re-batch loop wins on accuracy AND latency.

**Front-end reconciliation (all client-side; server stays dumb).** Every partial
is a **complete snapshot, not a delta** — that single property gives both
flicker-free rendering and immunity to falling behind. Server emits
`partial_transcript {text, seq}` full strings every ~400ms + one final
`transcript {text}`.

- *Flicker-free render = word-level spans + longest-common-prefix diff.* Do NOT
  `textContent = wholeString` each tick (restarts per-word animations, relays out
  the whole node). Render **one `<span>` per word**, keep a parallel word array.
  On each partial: tokenize → walk from index 0 while `old[i]===new[i]` (the
  **stable prefix** — those spans are never touched, so no flicker/no animation
  restart) → from the first divergence (the **unstable tail**) update existing
  spans in place, append new spans with a ~120ms opacity fade-in so new words
  "arrive", and drop trailing spans if a revision *shortened* the hypothesis
  (rare). Word-granularity on purpose: ASR revisions are whole-word ("to"→"two")
  and live in the trailing/recent-audio region, so a prefix compare matches how
  hypotheses actually move; a mid-sentence flip re-rendering its tail is
  acceptable and rare once right-context locks the prefix.
- *Never fall behind = monotonic snapshot guard.* Reconcile is microseconds
  (dozens of words), never the bottleneck; the real risk is bursty/out-of-order
  WS delivery. Because each message is self-sufficient, just render the newest
  and discard stale ones: track `lastSeq`, `if (msg.seq <= lastSeq) return`. Under
  load you can skip intermediate frames and the display stays correct — it jumps
  forward. (Deltas wouldn't allow this; snapshots do — the payoff for sending
  cumulative full strings.)
- *"Gray then commits" polish (optional, but it's the ack cue).* Tail/new words
  render ~60% opacity; a word that survives ≥1 cycle in the stable prefix gets a
  `committed` class (full opacity) — the prefix visibly hardening behind the live
  tail *is* the "yep, I heard that" acknowledgement. Final `transcript` → one last
  reconcile, freeze all to committed, snap (no fade).
- *Layout:* growing caption in a `flex-wrap` container anchored at a fixed top,
  words wrapping downward, so new words extend rather than shoving existing ones
  onto new lines. Reconciliation lives in the existing `handleAssistantEvent`
  (`app.js`). One thing NOT to do yet: have the server compute a "stable prefix
  length" from its own successive decodes — the client diff is cheap enough that
  coupling the server to it isn't worth it.

**BUILT 2026-07-08.** Implemented exactly as designed: satellite
`PartialStreamer` (single daemon worker, latest-snapshot-only so a slow decode
can never back-pressure the mic loop; process-lifetime monotonic seq),
`capture_command(partials=True)` offers the whole buffer every ~400ms once
speech starts; orchestrator `POST /partial?seq=N` re-decodes and emits
`partial_transcript {text, seq}` (empty decode = no emit); dashboard word-span
reconciler (`captionRender`) with committed-prefix hardening, in-place
revisions, fade-in tail, seq guard, and a 1.5s post-final straggler block. New
turns without a `wake_confirmed` (follow-ups) reset via a `turnDone` flag set
on `response`. Env knobs: `PARTIALS_ENABLED` (default on), `PARTIAL_INTERVAL_MS`
(400) on the satellite.
**Partials fire on wake AND follow-up turns (user call, 2026-07-08).** I first
gated them to wake turns to protect the silent-drop rule (follow-up captures
hear any room speech, before the intent gate runs). Brad's reasoning for
including follow-ups: the unobtrusiveness rule is about AUDIO (no chime, no
spoken "sorry") — captions are silent and only visible if you're already
looking at the dashboard, and it's a local pipeline so live transcription of
room speech isn't a privacy signal to hide. Mechanism: a dropped-chatter
follow-up sends nothing after its partials, so the dashboard re-arms a 6s hide
timer on every partial (never pins the popup) and the caption just fades;
`thinking`/`transcript` clear that timer once a turn turns actionable.
Verified E2E with simulated growing-prefix posts (TTS clip → 400ms PCM
prefixes → `/partial` → dashboard `/api/live` websocket): captions grew
`'Set a tim' → 'Set a timer for' → … → 'Set a timer for 10 minutes for the
pasta'`, mid-word truncations self-corrected on the next snapshot, decode kept
the 400ms cadence. Reconciler unit-tested (grow / revise-in-place / shorten /
final-freeze / stable-prefix untouched). Live-voice tested 2026-07-08: works.

**Capture bug found via the first live captions test (fixed 2026-07-08): the
max-command cap counted AUDIO duration, not wall time.** The capture buffer
opens with ~1.7s of chime/verify bleed that reads in ~0ms of real time
(deliberately un-drained for run-together commands), so `MAX_COMMAND_S=8`
tripped after only ~6.3s of real speaking and cut mid-word (logs:
`reason=max_command total=8000ms wall=6290ms tail_silence=0ms`). Same bug
class as the onset timeout fixed in 1e5ef10. Fix: the cap is now wall-clock
from SPEECH ONSET — you get the full cap of actual speaking time regardless of
bleed. Silero endpointing itself was verified healthy in the same logs
(follow-up turn endpointed at exactly 704ms trailing silence, sane voiced%).
Follow-up decision (same day): **MAX_COMMAND_S re-scoped from human budget to
runaway guard and raised 8 → 20s.** With Silero endpointing working, the only
thing that hits the cap is speech that never stops — i.e. a TV/radio near the
mic (genuine speech, so silence-endpointing correctly never fires); humans
should never encounter it. The real cost of a long capture was that the
'stop' barge-in listener lives in the main loop, so a mid-capture alarm
couldn't be dismissed by voice — added an **alarm bail** inside the capture
loop (reason=alarm_bail: a timer starting to ring aborts capture with
whatever was said so far, freeing the mic for the dismiss listener).

### B. Music via Music Assistant ("okay computer, play Raffi") — BUILT 2026-07-08

**Status: built + deployed 2026-07-08 (orchestrator + satellite + dashboard
all live). Validated end-to-end via /command text bypass + dashboard WS;
live-voice test — especially wake recall with music at normal volume — still
pending a person at the mic.**

What shipped:
- `orchestrator/music.py`: one persistent `music-assistant-client` (1.4.2)
  WS connection in a reconnect-forever background task; MA down never breaks
  timers/lists/ask (spoken "can't reach the music player" instead). GOTCHA:
  the client's DEFAULT search media-types include GENRE, which the schema-26
  server rejects with NotImplementedError — always pass media_types
  explicitly.
- Intents `play_music` (query + optional media_type), `music_control`
  (pause/resume/stop/next/previous/volume_up/volume_down), `music_query`
  ("what's playing"). Bare "stop"/"pause" route to music_control (timers get
  cancelled, not stopped; alarm dismiss never reaches intent parsing).
- **Ranking as revised above, plus one addition learned from live data: the
  general pass runs TWICE — library-only candidates first, then all
  providers.** Spotify search is full of traps: junk "artists" literally named
  after songs ("Wheels on the Bus", "Baby Beluga") and random user playlists
  name-match 100 and would steal queries the household library should win
  ("play baby beluga" must hit the owned album). Thresholds: playlist ≥92,
  artist ≥80, album ≥85, track ≥80 (rapidfuzz token_sort on normalized
  names); low confidence still plays the best guess. Verified live: "play
  raffi" → library artist shuffled (streams filesystem_local per track —
  local-first confirmed automatic), "play baby beluga" → owned album 17
  (dual-mapped beats spotify-only library album 8 via a has-local tiebreak),
  "play the best of raffi" → local playlist 41.
- **qwen media_type gotcha: it inferred media_type="album" for "the best of
  raffi" (sounds like an album), which bypassed playlist ranking.** The prompt
  now demands the type word be LITERALLY SPOKEN and carries that exact
  counter-example.
- **Ducking (satellite)**: fire-and-forget POST /music/duck on every stage-1
  active trigger and on alarm start; /music/unduck when run_turn (incl.
  follow-ups) or the alarm ends. The orchestrator refcounts nested pairs (a
  turn and an alarm can overlap), ducks to max(5, 25%·vol) only when actually
  playing, and a 240s watchdog restores volume if the satellite dies mid-turn
  and the unduck never arrives. volume_up/down spoken DURING a duck adjusts
  the restore target, not the live (ducked) volume. Note ducking happens
  post-trigger, so it helps verify/capture and the alarm 'stop' listener —
  it cannot help stage-1 wake recall itself (hence the pending live test).
- **Dashboard = the existing NFC jukebox modal; no new card was needed.** The
  jukebox service's /api/now-playing reads LIVE MA queue state, so
  voice-started music renders there exactly like an NFC scan: the
  orchestrator emits `show_music` (on play_music and on "what's playing"),
  app.js opens the modal. Transport buttons/scrubber/volume were already
  built and drive the same queue.
- **Library-index fuzzy resolver (added same day after the first live-voice
  test): ASR misspellings defeated MA search entirely.** Parakeet produced
  "Rafi" / "raffie" / "Lenny Rafi" across three tries; MA's search is literal,
  so the LIBRARY returned nothing while Spotify returned real junk artists
  literally named "Rafi"/"Raffie" — library-first ranking never had a
  candidate. Fix in music.py: keep every library name in memory (~5.3k
  entries via paged get_library_* — NOTE an unlimited call silently returns
  one 500-row server page; warm on connect, background refresh at 15-min TTL,
  MUSIC_INDEX_TTL_S) and fuzzy-match the query against it BEFORE MA search
  (rapidfuzz indel ratio on normalized names + a doubled-letter-collapse
  variant that makes "rafi"→"raffi" score 100, + a "by <artist>"-tail-stripped
  variant, + per-token rescue for single-word ARTIST names at ≥90 only —
  at 80 it hijacked "toxic by britney spears" via "spears"≈track "Sparks").
  Same bucket precedence/thresholds; ties prefer locally-mapped items (two
  library "Baby Beluga" albums — play the owned files). Confident hit plays
  directly (Spotify junk never enters the race); otherwise fall through to
  MA search unchanged for non-library discovery. ~6ms per resolve on the
  Beelink vs ~800ms for the qwen parse. Verified: all three real mangled
  transcripts → library://artist/41.

Original design below.

Fully replace Google Home for the NFC-jukebox use case (3-yr-old scans a card
today; add voice). New `play_music` intent → deterministic MA call → the
existing squeezelite/MA jukebox player (`Kitchen-Big-Speakers`, MA queue
`e4:5f:01:67:1e:56`).

**Verified against the live server 2026-07-08 (MA 2.6.3, Beelink :8095).** The
API is **WebSocket JSON-RPC at `/ws`** — there is NO REST `/search`. Use the
official `music-assistant-client` pip package (async, fits the FastAPI
orchestrator); commands `music/search` + `player_queues/play_media`. Providers
confirmed live: `filesystem_local--4Z5yRhf7` and `spotify--my6S2t5X`.

**Local-first is mostly free — MA merges providers in the library.** Real
"Raffi" search: library artist `library://artist/41` carries BOTH spotify and
filesystem mappings; owned albums (Baby Beluga) likewise dual-mapped. Playing a
`library://` URI lets MA pick the stream provider PER TRACK, preferring higher
quality — local lossless beats Spotify lossy automatically. Rule is simply
**prefer results with a `library://` URI over provider-only URIs**; no
threshold-and-fallback ranking layer. Verify with one test play; escape hatch
if MA ever picks Spotify for an owned track: pass the `filesystem_local`
provider_mapping URI explicitly.

**Ranking (revised 2026-07-08 review): playlist wins only on strong/near-exact
name match; bare artist name → library artist, shuffled.** The old "playlist
named X > artist" order breaks on real data: TWO local playlists match "Raffi"
("The Best of Raffi", "Raffi kids songs") — playlist-first needs a tiebreak and
freezes "play Raffi" onto one stale list forever. Artist-shuffle still beats a
single track (the original concern), plays local files, stays fresh. If the
3-yr-old needs one canonical mix, name the playlist something she'd actually
say. Flow: qwen extracts raw `query` + *optional* `media_type` hint → MA search
→ strong playlist name-match > library artist > album > track. Low confidence →
just play best guess (no "did you mean" mid-cooking). Transport intents
(`stop`/`pause`/`next`/`volume`/"what's playing") come nearly free and coexist
with the NFC jukebox (both drive the same player).

**Ducking is part of THIS feature, not TBD.** Today music-defeats-wake is
theoretical; `play_music` makes it constant. Build the duck (pause or volume
drop via the same MA client) on stage-1 trigger, and TEST wake recall with
music at normal volume before trusting voice "stop" (the TONOR can't AEC audio
it didn't play). Add a dashboard now-playing card with pause/skip/stop buttons
as the guaranteed fallback (matches the timer-card pattern).

### C. Companion Android APK: offline-first list sync + seamless auto-VPN — BUILT 2026-07-08

**Status: built, installed on Brad's Pixel (v0.2.0), published to
`/home/pi/apks/voice-notes-latest.apk`.** Verified live on-device: shared
unfiltered reads render the same items the companion serves; quick-add ("+ Add"
on a list tab) → outbox → companion note-sync+analyze → typed item round-trips;
offline test (companion container stopped): Done on an item hid it locally,
amber "Offline — showing saved list · synced 1m ago · 1 change waiting" banner
appeared, and on reconnect the op flushed (item completed server-side) with NO
resurrection. WireGuard contract verified against the installed app
(1.0.20260315): `TunnelManager$IntentReceiver` present, `CONTROL_TUNNELS` is
protectionLevel=dangerous → runtime grant wired into settings (pre-granted on
Brad's phone via adb). **Away-test PASSED 2026-07-08**: tunnel name "wg" set in
settings; wifi dropped, app reopened → VPN auto-connected and synced. Source
pushed to github-illuminate:bradmontierth/voice-notes-android.git. REMAINING:
install + permission + tunnel name on Adrienne's phone. Implementation notes in
`voice-notes-android/README.md`. Original design below.
The APK (voice recorder + todos/shopping tabs, shares the companion :8768
backend) is the perfect out-of-home companion: say "add lemon juice" at home,
pull up the list at Costco. Nothing is exposed to the internet — it's all
LAN/WireGuard.

**Core reframe: decouple read from write.** Render list views from a local
cache, never a live fetch — and **reads are ALREADY cache-backed** (2026-07-08
code review: item fetch is best-effort, rendering always reads the local DB).
The write side is the gap, and it's a live BUG, not a nice-to-have:
`completeTask`/`deleteTask` (MainActivity) call the companion, SILENTLY swallow
network failure, and update the local DB anyway — the next successful sync
`replaceItems` from the server **resurrects everything checked off offline**.
Check off 20 items at Costco with VPN down → all 20 come back. Fix: an
**outbox** table of pending ops; on sync, replay the outbox FIRST (404 on
delete = success), THEN pull `replaceItems` — and NEVER pull while the outbox
is non-empty (that ordering is what prevents resurrection). Sync on
open/resume + tab switch reconciles; a nightly background poll stays
unnecessary. **Gotcha:** probe the actual companion endpoint with a short
timeout — don't trust Android's "internet available" (true on Costco wifi while
the LAN backend is unreachable).

**Shared-list mismatch (MUST fix before this replaces Keep):** the app fetches
`GET /api/items?user=<device user>`, but the kitchen assistant files everything
under LIST_OWNER=brad and lists are household-shared (2026-07-07 decision) — on
Adrienne's phone, voice-added items NEVER appear. The companion already
supports unfiltered reads (`user` is optional on `/api/items`). Drop the user
filter for item reads; keep the device user for note authorship only.

**Offline adds need a pending state:** adds go through the companion's
server-side LLM analyze, so an offline add can't produce a typed item locally.
Queue the raw text in the outbox and render a "pending" placeholder row so an
add at Costco isn't invisible until sync.

**Auto-VPN (user already has WireGuard's "Allow remote control from other apps"
ON):** on backend-probe failure, proactively fire the WireGuard control intent so
future opens are seamless without her thinking about VPN. It's **fire-and-verify**
(the broadcast returns nothing; the reachability probe is the source of truth):
1. Probe backend (short timeout, real endpoint).
2. Fail → broadcast `com.wireguard.android.action.SET_TUNNEL_UP`,
   `setPackage("com.wireguard.android")`, extra
   `com.wireguard.android.extra.TUNNEL_NAME=<tunnel>` (idempotent — no-op if
   already up, so no need to query state first). App likely needs the
   `com.wireguard.android.permission.CONTROL_TUNNELS` permission in its manifest.
3. Poll the probe with backoff (~500ms up to ~5s, brief "Connecting…") — WG's
   handshake takes a second or two.
4. Pass → sync + flush outbox (silent; VPN consent already granted since she uses
   WG, so no dialog). Timeout → stale banner ("Couldn't reach home — showing
   saved list"), the safety net for when the server is genuinely down.

**Guardrails:** only ever `SET_TUNNEL_UP`, never `DOWN` (she leaves it on; don't
manage teardown). Attempt once per open (or rate-limit) — don't re-raise in a
loop if the server's actually down. Start on-open only; a foreground
network-change listener is a nice-to-have; do NOT auto-raise VPN from the
background (invasive + Android background-execution limits). Tunnel name is a
**per-device setting** (two phones, likely two tunnel names). Rejected
alternatives: embedding a tunnel via wireguard-android GoBackend (becomes its own
VPN app — overkill) and a manual "Connect" button (auto is smoother since the
remote-control setting is already on). Caveat: WG's intent API has drifted across
app versions — verify action strings against the installed build before relying
on it.

**Optional polish (later):** companion already does FCM pushes → a silent
data-push on list change ("something changed, sync when convenient") buys
Keep-grade freshness with no background polling. And a small "recently
completed" strike-through section (companion keeps `status=completed` rows)
preserves the in-store "did I already grab that?" glance that Keep gives.

## Latency Budget (measured where noted)

| Stage | Time |
| --- | --- |
| VAD endpoint (silence cutoff) | ~0.6s |
| Parakeet final on remaining audio | ~0.1s |
| Intent parse (measured 2026-07-05) | ~0.8s |
| Kokoro TTS via router | ~0.2s |
| **First audio out** | **~1.7s** |

Display-only intents skip TTS → dashboard reacts ~1.5s, live captions during
speech via Parakeet realtime WS.

## Build Order (all at once, tuned-in-production behind the mode switch)

1. ✅ Shadow bench LIVE on kitchen-speaker (pretrained "hey livekit" model) —
   collecting FP baseline + deliberate TP/FN sessions. Next: train custom
   phrase with the livekit pipeline on the GX10.
2. ✅ **Satellite service BUILT + deployed 2026-07-06**
   (`home_config/voice-assistant/satellite/assistant.py`, on kitchen-speaker at
   `~/voice-pipeline/`, `voice-assistant.service`, HTTP :8781). Subsumes the
   shadow bench (owns the mic; wake-bench.service stopped+disabled, kept as
   fallback). Continuous livekit-wakeword stage-1 (okay_computer @0.5) →
   two-phase pipeline: snapshot ~2.5s pre-roll → POST /verify → glock wake chime
   → webrtcvad command endpoint → chirp VAD chime → POST /command/audio → play
   spoken reply. Hosts POST `/alarm` (theme sound + announcement loop, voice/
   touch/timeout dismiss). **3-state MODE kill switch** (active/shadow/off) via
   HTTP POST `/mode`, **defaults shadow** (safe: deploy changes nothing audible
   until flipped active). Chimes in `~/voice-pipeline/sounds/` (chime_tts, MIT).
   Verified: health, Beelink→satellite reachability, mode switch. Pending live
   voice test + alarm-audio test (needs a person to speak). Deferred follow-ups:
   MQTT mode (HTTP /mode works now), Music Assistant ducking, real animal foley
   (themes fall back to marimba), port bench /mark labeling.
3. ✅ **Orchestrator TIMERS VERTICAL SLICE built + validated 2026-07-06**
   (`home_config/voice-assistant/orchestrator/`, port 8785). Proven end to end
   against live services: `/wake` raw-WAV → Parakeet → stage-2 verify (real
   synthesized speech: "okay computer…" score 100 pass, off-phrase score 42
   reject) → intent LLM (thinking off) → SQLite timer engine (absolute
   `ends_at`, restart-safe, catch-up firing of expiries missed while down) →
   pre-rendered announcement WAV + spoken reply → dashboard `/api/live`
   fan-out. Full pipeline **~1.6s** incl. two serial TTS renders. LLM picks
   themes correctly (chicken→cluck, rice→steam_whistle). Intents live:
   set/query/adjust/cancel/cancel-all. Remaining orchestrator work: Parakeet
   realtime-WS live captions, lists/HA intents, trigger log + review page.
   Dashboard gained `POST /api/assistant/event` → re-broadcasts as
   `{"type":"assistant","event":{…}}`.
4. ✅ **Dashboard panel BUILT 2026-07-06** (`dashboard_webapp` frontend):
   Echo-Show-style popup consumes `{type:"assistant",event}` over /api/live —
   `wake_confirmed`→"Listening…", `transcript`→caption, `thinking`→"Thinking…",
   `response`→answer text (auto-hide 8s). Live **timer cards** (top-right stack)
   from `timer_*` events, tick locally off `ends_at`; `timer_done`→ringing card
   (tap to stop → satellite /alarm/dismiss) + "⏰ done" banner. Kiosk restores
   cards after reload via new dashboard proxy `GET /api/assistant/timers` →
   orchestrator (orchestrator has no CORS). Files: index.html/styles.css/app.js.
   **Swipe-to-cancel** (2026-07-06): horizontal swipe on a timer card flings it
   off and cancels it — running card → dashboard proxy `POST
   /api/assistant/timers/{id}/cancel` → orchestrator; ringing card → satellite
   /alarm/dismiss. Pointer events + `touch-action:pan-y` so vertical drag still
   scrolls. Cards sized for across-the-room glancing (countdown 172px).
   Remaining: todos/shopping views (lists phase).
5. Custom wake word once bench data validates thresholds; tuning week with
   the mode select as pressure valve.

## Shadow Bench Operations

```
Host:      kitchen-speaker (192.168.10.24)
Dir:       /home/pi/wake-bench/  (source of truth: home_config/voice-assistant/satellite/)
Service:   wake-bench.service (system unit, User=pi)
Engine:    livekit-wakeword, model /home/pi/wake-bench/hey_livekit.onnx
Config:    /home/pi/wake-bench/.env (threshold, hop, floor — restart to apply)
Mark page: http://192.168.10.24:8781/        (big TP button + recent events)
Health:    http://192.168.10.24:8781/health
Events:    http://192.168.10.24:8781/events  (JSON tail of events.jsonl)
Data:      /home/pi/wake-bench/data/events.jsonl + clips/*.wav
Mic:       plughw:CARD=microphone (TONOR G11, card 3, gain maxed + alsactl stored)
```

```bash
ssh kitchen-speaker 'sudo systemctl status wake-bench --no-pager'
ssh kitchen-speaker 'tail -20 /home/pi/wake-bench/data/events.jsonl'
```
