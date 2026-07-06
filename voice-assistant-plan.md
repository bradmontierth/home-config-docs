# Homegrown Voice Assistant Plan

Status: shadow wake-word bench being deployed to `kitchen-speaker` (2026-07-05).
This is the persistent design doc for the kitchen voice assistant pipeline.

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
│ Porcupine (permissive)      │             │  intent LLM (no think)   │──► qwen3-next :8095
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

All thresholds (Porcupine sensitivity, stage-2 fuzzy threshold, VAD silence
ms) hot-reloadable — no redeploy to tune.

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
- Dismissal layers: touchscreen tap (most reliable) > voice in gaps (any
  stage-1 wake mutes alarm instantly) > auto-stop.
- Follow-ups in schema from day one: time-left query, add N minutes, cancel
  by name, cancel-all, unlabeled timers ("10 min timer"). Ambiguous "stop the
  timer": ringing timer wins, else nearest-to-finish, always announce which.
- Audio note: alarm player shares bcm2835 Headphones card with
  squeezelite/librespot/gmediarender — verify ALSA dmix concurrent playback;
  fallback is MA announce feature (auto duck/resume, more latency).

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

`set_timer`, `timer_query`, `timer_adjust`, `timer_cancel`, `add_items`
(reuse companion analyze), `show_todos`, `show_shopping`, `complete_item`,
`set_reminder`, and later a tiny allowlisted HA set (blinds). Strict JSON,
temperature 0, low max_tokens, thinking off.

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
2. Satellite service: capture, Porcupine, VAD, MQTT mode, alarm/TTS playback.
3. Orchestrator on Beelink: stage-2 verify, Parakeet WS streaming, intent,
   timers, TTS dispatch, event fan-out, trigger log + review page.
4. Dashboard panel: badge states (listening/verifying/thinking/speaking),
   caption line, response, timer cards, todos/shopping views.
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
