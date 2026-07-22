# Harry Potter Mode v2 — Plan

**Status: DRAFT for review — nothing built yet.**

The Slughorn hourglass, reversed: the kitchen fixture's moving-light effect speeds up as
the kitchen conversation gets more interesting. v1 (Jetson-era, Node-RED "Chaos" tab)
recorded fixed chunks, missed audio between them, and rewrote whole WLED presets. v2 is a
clean rebuild on the current stack: continuous tee off the kitchen satellite mic, scoring
on the GX10, direct WLED speed control, HA sensors for observability.

## What we keep from v1 (concepts only, no code)

- HA switch as the on/off ("harry potter" entity → recreate as `input_boolean.harry_potter_mode`).
- Kitchen fixture WLED at `http://192.168.30.37` — moving-lights preset (was preset 6;
  verify on device), speed = `sx` per segment, top/bottom zones = segments.
- Score → speed as the core mapping.

The Node-RED Chaos tab stays untouched (it still serves chaos color mode); the old
harrypotter MQTT wiring in it gets retired once v2 is live.

## Architecture

```
kitchen satellite (Pi, assistant.py)          GX10 (192.168.10.187)
┌─────────────────────────────┐    PCM     ┌──────────────────────────────────┐
│ arecord 16k mono            │  16k/mono  │ party-scorer (new, CPU-only)     │
│  ├─ wake / VAD / commands   │ ─────────► │  ├─ rolling 90s PCM buffer       │
│  └─ PartyTap tee (new)      │  WS push   │  ├─ FAST loop (~2s):             │
│     bounded queue,          │            │  │   RMS energy                  │
│     drop-oldest, never      │            │  │   Silero VAD → turn stats     │
│     blocks wake path        │            │  │   YAMNet → laughter/cheer     │
└─────────────────────────────┘            │  │   Sortformer :8092 → #spkrs,  │
                                           │  │     overlap (resident, 100ms) │
        HA (switch + sensors)              │  ├─ SLOW loop (~15s):            │
┌─────────────────────────────┐            │  │   window → Parakeet :8090     │
│ input_boolean → automation  │   MQTT     │  │   batch transcribe → Qwen     │
│ MQTT discovery sensors:     │ ◄────────► │  │   :8095 → {interest,          │
│  score/fast/slow/topic      │ party/#    │  │   coherence, topic}           │
└─────────────────────────────┘            │  ├─ blend + EMA + slew limiter   │
                                           │  └─ actuate ──► WLED /json/state │
                                           └──────────────────────────────────┘
```

### 1. Audio tap — inside the satellite, not beside it

A `PartyTap` worker class in `assistant.py`, same pattern as the existing streamer
workers: the arecord read layer `offer()`s every frame into a bounded queue
(drop-oldest); a worker thread pushes PCM over a single WebSocket to the scorer.

- **Why not a second capture process**: the mic is a single `arecord` on
  `plughw:CARD=microphone`; a parallel reader means dsnoop surgery on a tuned audio
  stack. The tee sees all frames in all satellite phases and adds one non-blocking
  `offer()` call to the hot path.
- Controlled by `POST /party/start|stop` on the satellite's existing HTTP server (:8781).
- **Pauses itself during voice turns and alarms** (wake → end of command/follow-up
  window): the assistant interaction shouldn't score as "conversation", and this keeps
  the two systems from fighting.
- Like mode→shadow, a satellite restart resets the tap to OFF. Scorer notices the dead
  socket and parks.
- Wake word keeps working during party mode — the tap is a passive copy.

### 2. Scorer service — new repo, runs on GX10

New repo (proposed: `/home/pi/party-scorer`, GitHub-pushed per git-is-backup
convention; deployed as a container on the GX10). **CPU-only** — no new GPU
residents (YAMNet ≈ 4MB CPU model; Parakeet/Qwen/Sortformer are already-resident
services we call), respecting the unified-memory OOM lesson.

**Fast channel** (every ~2s over the last ~5s of audio):
- RMS energy (dBFS, normalized against a slow noise floor).
- Silero VAD (same .onnx the satellite uses) → speech ratio + turn transitions/min.
- YAMNet → `max(laughter, giggle, cheering)` probability.
- Sortformer `POST localhost:8092 /diarize` on a 2.5s clip → active speaker count +
  overlap fraction. Resident and ~100ms, so effectively free — real speaker counting
  instead of VAD guesswork.
- `fast = w1·energy + w2·turn_rate + w3·laughter + w4·speakers` (weights are config,
  tuned against recorded clips).

**Slow channel** (every ~15s):
- Rolling last-60–90s window → `POST :8090 /parakeet/transcribe` as a short batch job.
  Deliberately **not** the realtime WS lane: capacity is 2 sessions (Windows STT uses
  them) and a party would squat one for hours; 15s-cadence batch calls on the
  interactive-batch lane cost ~nothing and can't starve anyone.
- Transcript → `qwen3-next` at `:8095/v1/chat/completions`, prompt framed for
  far-field multi-speaker party audio ("expect fragments and splices; rate topical
  engagement; do not penalize incoherence"), `enable_thinking=false`, forced-JSON:
  `{interest: 0-100, coherence: 0-100, topic: "one word"}`.

**Blend + smoothing** (the show-feel layer):
- Dynamic weighting: when `coherence` is low **and** acoustic energy is high, downweight
  the text score — garbled-but-loud is peak excitement, not boredom (the Parakeet
  dominant-voice-latch failure becomes a feature).
- EMA + slew limiter on the composite: speed may only change N units/sec, so the light
  breathes instead of twitching.
- Idle decay: no speech for 2 min → drift back to baseline crawl.
- Peak latch: composite ≥ threshold sustained ~20s → distinct climax state (see open
  questions), with hysteresis so it doesn't flap.

### 3. Actuation — scorer talks to WLED directly

- On session start: snapshot current WLED state, select the moving preset once
  (`{"ps": N}`).
- Every tick: `POST /json/state {"seg":[{"id":0,"sx":S},{"id":1,"sx":S}], "tt": …}` —
  speed only, no preset rewriting (v1's clunkiest part).
- On stop: restore the snapshot.
- v2 keeps both zones at one speed; splitting top=fast / bottom=slow is a config flag to
  try during tuning (phase 3).

Why direct and not via Node-RED: one moving part, sub-second latency, and the score is
still published on MQTT so Node-RED/HA can layer extras (CT whites choreography) without
being in the critical path.

### 4. HA / MQTT integration

- Broker: mosquitto (192.168.10.250 or beelink 192.168.10.217 — confirm which is canonical).
- `party/set` (on/off, retained) — HA automation mirrors the input_boolean here; scorer
  subscribes, and on start POSTs the satellite `/party/start` (on stop, `/party/stop` +
  WLED restore).
- MQTT discovery sensors: `party/state` JSON → composite score, fast, slow, coherence,
  laughter, speaker count, topic word, session state. Free HA history graphs — this is
  the tuning instrument, and later automation fodder ("kitchen is lively").
- Auto-off after 4h so it never runs overnight.

### 5. Privacy

- Transcripts of guests' conversation live in memory only; nothing persisted by default.
  Scores/metrics only in logs and MQTT. A `DEBUG_TRANSCRIPTS=1` env flag can log
  transcripts during tuning sessions; default off.
- Mode is explicit-on via the HA switch, auto-off after 4h, and dies with the satellite.

## Failure modes and answers

| Failure | Behavior |
| --- | --- |
| Scorer dies mid-party | Satellite tap queue drops frames silently; wake path unaffected. WLED left at last speed — acceptable; HA sensor goes stale as the tell. |
| Parakeet 429 (lane full) | Skip that slow tick; fast channel keeps driving; EMA coasts. |
| Qwen slow/down | Same — slow channel is additive, never blocking. |
| Satellite restarts | Tap resets OFF (existing behavior pattern); scorer parks on dead socket; HA switch still ON → automation or scorer re-POSTs `/party/start` on reconnect. |
| Voice command during party | Tap pauses for the turn; scorer sees a gap, EMA coasts through it. |

## Phases

**Phase 1 — resurrect the trick (slow channel only).**
Satellite PartyTap → scorer skeleton → Parakeet batch → Qwen score → EMA/slew → WLED
speed + HA sensors + HA switch wiring. Already a working, demo-able show.

**Phase 2 — the hybrid.**
Fast channel (energy, VAD turns, YAMNet, Sortformer) + dynamic coherence-aware blending +
idle decay + peak latch. Tune weights against recorded real-dinner clips (reuse the
verify-clip corpus harness pattern from the wake work) before going live.

**Phase 3 — show polish (pick from):**
- Two-zone split (top=fast channel, bottom=slow channel).
- CT whites slowly dim as composite climbs (via Node-RED/Hubitat off the MQTT score).
- Climax state effect.
- Topic word / score gauge on the kitchen dashboard display.

## Open questions for review

1. **Climax behavior** — when the score pins at max: freeze the effect (canonical
   Slughorn), a shimmer/color snap, or just max speed? Needs a decision before phase 2.
2. **Repo/service name** — `party-scorer`? `slughorn`? Naming the repo `slughorn` is
   admittedly on-brand.
3. **Scorer placement** — plan says GX10 (everything it calls is localhost, incl.
   Sortformer which is internal-only). Beelink would work but adds LAN hops and loses
   Sortformer. Any objection to another container on the GX10?
4. **HA switch** — reuse/rename the old "harry potter" entity or create fresh
   `input_boolean.harry_potter_mode`? (Old Node-RED wiring gets retired either way.)
5. **Mic reality check** — before building phase 2, record a few minutes of actual
   multi-person kitchen chatter via the tap and eyeball what Parakeet + the acoustic
   features each produce. Cheap, and it de-risks the far-field question. OK to make this
   the first live milestone after phase 1?

## Reference endpoints (recon 2026-07-12)

- Parakeet batch: `POST http://192.168.10.187:8090/parakeet/transcribe` (429 on full lane)
- Qwen chat: `POST http://192.168.10.187:8095/v1/chat/completions`, model `qwen3-next`
- Sortformer diarize: `:8092` on GX10 localhost (resident, ~100ms / 2.5s clip)
- Kitchen fixture WLED: `http://192.168.30.37/json` (segments = top/bottom zones)
- Satellite HTTP: `:8781` on kitchen Pi (`/party/start`, `/party/stop` to be added)
- Orchestrator (untouched by this project): `http://192.168.10.217:8785`
