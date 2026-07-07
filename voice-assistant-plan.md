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
