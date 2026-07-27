# Voice Assistant Backlog — investigated 2026-07-09

Brain dump from Brad (2026-07-09 evening), each item investigated same night
against code + live logs. This doc records diagnosis, proposed fix, priority,
and build status. Companion to `voice-assistant-plan.md`.

Priority order (pain × effort): **5 → 3 → 8 → 7 → 2 → 4 → 6 → 1.**
Batching by component: Batch 1 = 5+3+8 (all satellite/assistant.py, one
deploy — **DEPLOYED 2026-07-09 evening, live-voice validation pending**);
Batch 2 = 7 (dashboard) + 2 (orchestrator); then 4, 6, 1.

---

## 5. Premature VAD endpoint right after wake ("okay computer" → instant chime) — P1, FIXED+DEPLOYED 2026-07-09

**Symptom (family demo, ~17:30–18:00 2026-07-09):** wake chime immediately
followed by the VAD-done chime before Brad started speaking.

**Evidence (satellite journal, all three failures 17:52–17:53):**
```
capture reason=silence_endpoint total=1216ms voiced=320ms (26%) tail_silence=704ms wall=62ms
capture reason=silence_endpoint total=1568ms voiced=160ms (10%) tail_silence=704ms wall=126ms
capture reason=silence_endpoint total=1440ms voiced=64ms  (4%)  tail_silence=704ms wall=119ms
```
`wall=62ms` is the smoking gun: the ENTIRE capture — speech onset AND the
704ms silence endpoint — happened inside audio that was already buffered
before capture started (~1.2–1.6s of trigger→verify→chime bleed, read at
disk speed). The user never had a chance to speak.

**Mechanism:** capture deliberately doesn't drain the buffer (run-together
commands live there). Silero flags a short blip in the bleed as speech —
wake-word tail, chime edge, or **ducked music** (all three failures happened
while "demons" by Josiah Queen was playing ducked; two "successes" in the same
window captured 320/64ms of "voice" that produced intent `none` / "no command
captured"). Once `speech=True`, 704ms of buffered non-speech endpoints the
turn instantly. `WAKE_MIN_CAPTURE_MS=400` counts AUDIO time, so the buffer
blows through it. Same bug family as the two wall-clock fixes of 2026-07-08.

**Fix (SHIPPED):** a silence endpoint with `voiced_ms < MIN_COMMAND_VOICED_MS`
(500) **AND** `lag > ENDPOINT_LAG_SPURIOUS_MS` (400 — i.e. the decision
happened in buffered, not live, audio) is a spurious onset: discard the blip
(`frames.clear()`), reset, keep waiting within the wall-clock onset window
(logs `spurious onset discarded voiced= lag=`). The lag guard is what protects
short LIVE utterances — follow-ups drain first so "yes"/"next" have lag≈0 and
still land. Verified by simulation on the satellite (fake mic/VAD, 4 scenarios:
bug-case blip→later speech captured; run-together endpoints at wall=0ms;
short live "yes" kept; blip-only → clean no_speech_onset). Live-voice test
pending: wake with music playing, pause, then speak.

## 3. Saying "stop" to a ringing timer takes 3–4 tries — P1, FIXED+DEPLOYED 2026-07-09

**Evidence (two live alarms today):** dismiss took 16s and 13s. The listener
DOES work (`alarm-listen heard: 'stop.' → dismiss`) but hears fragments:
`'camera.'`, `"where's my"`, `'yeah.'`, `'hey, cookie.'` before finally `'stop.'`

**Diagnosis (assistant.py `alarm_listen_chunk`):**
1. **Non-overlapping 1s chunks** (`STOP_CHUNK_MS=1000`): a ~400ms "stop"
   straddling a boundary is split across two chunks and transcribes in
   neither. ~40% of utterances straddle. This alone explains most misses.
2. Each chunk then blocks on POST `/transcribe` (~0.3–1s) before the next
   read, adding dead time between windows.
3. The marimba/theme WAV plays at ≥50% floor over the speech (mic hears it
   full blast; music is ducked but the alarm itself is the masker). The 2s
   `ALARM_GAP_S` is the clean window — 1s chunks only half-cover it.
4. Exact substring match on `DISMISS_WORDS` — Parakeet writing "stopp"/"stops"
   still matches (`in t`), but "staw"/"top" doesn't.

**Fix (SHIPPED):** rolling 2.5s window offered to a `DismissChecker` worker
every 1s (`ALARM_WINDOW_MS`/`ALARM_HOP_MS`) — overlap means no word straddles
untranscribed, and the transcribe POST runs off-thread (PartialStreamer
latest-snapshot pattern) so mic reads never block. Fuzzy layer: per-token
difflib ≥0.8 against stop/cancel/dismiss/enough/quiet **with a first-letter
guard** ("stopp" matches, but "top (shelf/it off)" no longer dismisses via
fuzzy — note "X it off" still dismisses via the pre-existing " off" substring
rule, deliberately). Matcher unit-tested on-device. **Defer** the trained
"stop" wake-word model (viable via the livekit pipeline; only if this still
misses). Live test pending: set a timer, say "stop" ONCE while it rings.

**Round 2 (2026-07-23/24, Adrienne verdict "bad feature" — 3 tries):** root
cause is acoustic: the 2.5s window always contains beep audio (gap is only
2.0s), and Parakeet mangles masked "stop" ("stay"/"banned"). Three fixes:
1. `kitchen-alarm` GX10 bias profile (16 stop/cancel phrases) on ring-window
   transcribes via orchestrator `/transcribe?client=` + `ALARM_ASR_CLIENT`.
   First live ring after: ~2nd-try dismiss, zero mangled fragments.
2. Trained "stop" livekit-wakeword model (un-deferred): okay_computer recipe
   + 400 alarm-ring background clips (real themes at 2.0s gap cadence, 30%
   with cached TTS announcements over top) + target_fp_per_hour 0.5. Synth
   eval recall 89.6% / FPPH 0.93 @ threshold 0.5. Satellite scores it every
   224ms ONLY in the alarm branch (zero idle cost; replaces wake scoring, so
   ring CPU ≈ idle CPU); fires the same STATE.dismiss; ASR path kept for
   cancel/turn-it-off; scores ≥0.2 logged for threshold tuning
   (STOP_MODEL_PATH/STOP_THRESHOLD/STOP_HOP_MS envs). DEPLOYED both
   satellites 2026-07-24. On-device bench: ring-only peak 0.038, ring+"stop"
   0.99 — the alarm can't self-trigger the model.
3. **Coffee self-dismiss bug (2026-07-24 morning):** `_dismiss_in`'s
   `w.strip() in t` reduced `" off"` to bare substring "off", which matched
   c-OFF-ee — the announcement "Your coffee timer is done" dismissed its own
   alarm on the first window. Fixed: word-start regex matching (keeps
   stopped/cancelled), "off" exact-word (office/offer don't match),
   punctuation normalized ("Okay, computer." now matches too). Unit-tested.
Live test pending: a ringing timer dismissed on ONE "stop", ideally by
Adrienne.

**Round 2 result (2026-07-24 13:05): stop model FALSE-FIRED on the real
first ding** — 0.833/0.729 within 650ms of ring start, two self-dismissals.
The synthetic bench (ring-only 0.038) did not transfer to speakers + room +
mic acoustics. Mitigation (85453d1): STOP_THRESHOLD=2.0 in both satellite
.envs = shadow mode (scores logged, can't fire; bias+ASR dismisses again),
and every ring's mic audio is now saved to data/alarm_rings/ (keep 40).
v2 plan: retrain with captured REAL ring audio as backgrounds (replacing or
augmenting the synthetic clips), eval on captured clips incl. real spoken
"stop", and only re-arm (threshold from real DET data) after replay proves
ring-only stays quiet. Never arm on synthetic bench alone.

**v2 data ready (2026-07-24 eve):** 19 real ring captures on the kitchen
satellite — Brad 13:16-13:31 (13, incl. oven_ding + steam_whistle themes,
distractor speech, singing) + 14:32 deliberate speech-only negative
(swipe-dismissed) + 16:08 organic + Adrienne 17:43-17:45 (4, incl. a
"cancel"-dismiss negative). All staged to GX10 `wake-train/data/real_rings/`
with `manifest-20260724.csv` (label/stop-onset/holdout/transcript; onsets
from parakeet segments cross-checked vs live journal dismiss times).
**BRAD EAR-REVIEWED all contested clips 2026-07-24 eve** via review page
(beelink :8790, scratchpad/review, flags-round1.jsonl): final = 16
positives, 3 negatives (132738/143209/174431). Corrections from his ears:
132738 says "random STUFF", never stop — the LIVE dismissal at 13:27:57
was a kitchen-alarm-bias ASR mishear (stuff→"random stop"), i.e. the bias
profile can FALSE-DISMISS on stop-adjacent words (known cost, logged here
as the first observed case); its full 18.75s is now a training body (the
mishear tail is a premium hard negative). 131706 has TWO stops (mid
~2.6s + end ~6s) — live ASR missed BOTH (+ kiosk-tap dismiss);
174346 has TWO clear stops at the end (~13.5-15s), live caught one,
offline parakeet neither. Holdouts (eval-only, no background carve):
131706, 132716 (oven theme), 143209 (speech negative), 174346 (0.92
ring-only false-fire). Non-holdout clips carved into stop-free ring
bodies (tails cut at stop onset −0.3s; 132738 full-length) →
`data/real_ring_backgrounds/` ×10 dupes (90 files) for draw weight;
configs/stop.yaml v2 adds that path, output → /work/output/stop_v2.
Kickoff (GX10): `docker rm wake-train-stop; docker run -d --name
wake-train-stop-v2 --runtime nvidia -v /home/pi/wake-train:/work
nvcr.io/nvidia/pytorch:25.10-py3 bash /work/run_stop.sh` (logs:
/home/pi/wake-train/train_stop.log). Post-train: replay DET over
real_rings (esp. the 4 holdouts) before picking a threshold; shadow scores
in kitchen journalctl are the baseline (v1 ring-only peaks 0.9+).

**v2 TRAINED + EVALUATED 2026-07-25 — NOT ARMED. Root cause found and
fixed, but neither model is armable yet.**

Training ran clean (2h, output /work/output/stop_v2/stop/stop.onnx, synth
eval recall 88.1% FPPH 0.93 ≈ v1 — the synthetic bench is still useless
for this decision). Real-ring replay is what mattered, and it took three
attempts because the first two eval scripts were WRONG:

1. *Bad onsets.* Parakeet merges "Your timer is done. Stop." into ONE
   segment, so `stop_onset_s` pointed at the ANNOUNCEMENT on 5/16
   positives (off by up to 11s). Fixed by cross-checking every onset
   against the live journal dismiss timestamp → `stop_start_s` column
   (parakeet where delta ≤2.5s, else journal_anchor − 1.2s median ASR
   lag). AUDITED: every bad onset erred EARLY, so carved training bodies
   were truncated/skipped, never leaked a stop — v2's training data was
   valid.
2. *"Ring-only" regions aren't stop-free.* 131830 scored 0.98 in its
   "ring-only" region; the live journal shows ASR heard "So" at +7.25s =
   a real stop mangled by ring masking. That 0.98 was a CORRECT detection.
   Region-peak metrics can't be trusted where labels are imperfect; use
   ear-verified negatives + peak-vs-anchor alignment instead.
3. *THE REAL BUG — startup transient.* `stop_window` is reset to ZEROS at
   alarm start and scored immediately, so for the first 2s the model sees
   mostly digital silence + a sliver of ring — an input it never saw in
   training. A naive replay slides a full 2s window and NEVER reproduces
   this state, which is why the synthetic bench and both earlier scripts
   missed it. Faithful replay (`training/replay_stop_faithful.py`,
   reproduces the live loop chunk-for-chunk) shows v1 peaking 0.5-0.92 in
   that pre-fill window on **18/19 clips** — including clips with no
   spoken stop — vs ≤0.26 steady state. v2: 17/19. This IS the
   2026-07-24 incident: the sim reproduces that ring's live-logged scores
   (0.241/0.785/0.920/0.455/0.529) to 3 decimals.

FIX DEPLOYED both satellites 2026-07-25: `stop_filled` counter gates
scoring until the window holds WINDOW_SAMPLES of real audio. Live-verified
on a real ring — no scores before +2s (was 0.241 at +0.37s). Costs no real
recall (announcement still playing, ASR path covers it).

STILL NOT ARMABLE. A 38s unattended test ring (ring-20260725-160136.wav,
parakeet confirms ONLY the TTS announcement, zero human speech — our purest
and longest negative) scores **v1 peak 0.830 / v2 peak 0.798** in steady
state, with 21/18 windows ≥0.5. So the earlier 0.259 "ceiling" was an
artifact of short, attended negatives. Operating points (16 positives,
peak must align with the spoken stop):
  thr 0.50 → v1 62% / v2 56% recall, but BOTH below the false-fire ceiling
  thr 0.80 → v2 50% recall, v2-safe only
  thr 0.85 → both 44%, both above ceiling (margin only 0.05)
  thr 0.90 → v1 38% / v2 31%
v2 is the better candidate (0 excursions ≥0.8 on the pure ring vs v1's 5)
but 44% recall with a 0.05 margin on ONE long negative is not arm-worthy.

v3 plan: the v2 backgrounds were SHORT bodies carved from ATTENDED rings —
wrong distribution. Collect several LONG unattended rings (just let timers
time out; capture is already on) as backgrounds, drop the ×10 duplication
(90 dupes of 9 clips likely overfit — v2 beat v1 on contaminated clips and
lost on unseen ones), and add more spoken-stop positives, ideally
Adrienne's. Model's unique value is real: it caught 131830's mangled stop
(0.949) that ASR missed entirely.

Second-look analysis 2026-07-25 (consecutive-window voting, independent
re-eval of stop-v2-eval-20260725.json + fresh faithful replay of the pure
ring on .251):
- Pure ring false-fires are PERIODIC at ~2.5s spacing = the ring tone's
  repeat period. The model fires on a recurring element of the ring itself
  — systematic, not noise. Long-ring backgrounds in v3 attack exactly this.
- 2-consecutive-window rule: zeroes all three short negatives (≤0.26) and
  keeps most real-stop recall, BUT does not unlock arming today:
  v1 sustains on the pure ring (2-consec 0.812, 3-consec 0.677 — voting
  can't save v1); v2 drops to 2-consec 0.577 / 3-consec 0.541, so thr 0.6
  leaves only 0.023 margin on n=1 long negative. Not arm-worthy.
- KEEP the 2-consec (or 3-consec) rule as the arming criterion for v3:
  real stops sustain across windows (people draw out "stooop"), ring
  excursions mostly don't once real-ring negatives suppress the periodic
  element. Eval v3 on: 2-consec ceiling across SEVERAL long unattended
  rings vs 2-consec recall at the spoken stop.

## 8. Long ask answered on screen but never spoke — P1, FIXED+DEPLOYED 2026-07-09

**Symptom (Brad, 2026-07-09 ~17:21):** asked for the France World Cup score;
answer appeared on the dashboard (read from across the room) but audio never
played. Same event as the "timed out" side finding below — now fully traced.

**Trace (orchestrator log, UTC):** 23:21:36 intent=ask "What was the score of
the game today" → filler spoke → GPT generation ran **61s**
(`web_searches=9 reasoning_tokens=2187 cost=$0.2174 tokens=43128`) → satellite's
`/command/audio` POST hit its **30s default timeout** at 23:22:04 and abandoned
the turn (log: `/command/audio failed: timed out`; also unducked music) →
orchestrator finished at 23:22:38, pushed the answer to the dashboard over the
WS (satellite not involved — that's why the screen showed it), rendered TTS,
and returned the audio URL to a dead socket.

**Fix (SHIPPED):** `COMMAND_TIMEOUT_S=120` on both the wake-turn and follow-up
`/command/audio` POSTs. While waiting, wake detection pauses (single mic
reader) — acceptable; alarm playback still fires via the HTTP thread.

**Follow-ups (not yet built):**
- A second, delayed "still checking…" filler at ~10s (the first filler covers
  ~2s, then dead air) — orchestrator-side, already a known candidate.
- **Cost/latency watch:** that one ask cost $0.22 and 9 searches (5–10x the
  expected worst case). Consider capping the search loop via prompt ("at most
  3 searches") or dropping web_context high→medium if this recurs.

## 7. Jukebox play/pause button lags / toggles wrong way — P1, FIXED+DEPLOYED 2026-07-09

**Diagnosis (`dashboard_webapp/app/static/app.js`):** the button decides
play-vs-pause from the *server-reported* state (1.5s poll of
`/api/jukebox/now-playing`, which reads live MA queue state — itself lagging
the actual player by a beat). Press pause → state still says "playing" for
2–3s → a second press sends *pause again* (no-op) or, after a stale flip,
*play*. Rapid presses fight the poll. Wife pressed 10×; the winning strategy
(press, wait 3s, press) is exactly the poll+MA lag.

**Fix (SHIPPED, dashboard_webapp da147fc):** the click already flipped
optimistically, but `jukeboxCommand` schedules a poll 400ms later that
re-applied MA's lagging state and flipped the button back. `renderJukebox` now
holds `jukebox.pendingState` over stale polls until the server reports it (or
a 4s deadline). Clicks always toggle from the displayed state. Same rebuild
also shipped the caption seq **epoch tolerance** (huge backward seq jump =
new epoch, don't mute captions). Kiosk reloaded. Live test: play music, mash
the pause button — it should track presses 1:1 now.

## 2. Weather: current + forecast — P2, BUILT+DEPLOYED 2026-07-09

All data already flows to the dashboard from HA:
- Current: `sensor.weather_station_outdoor_temperature` (+ humidity, wind,
  `weather.forecast_home_2` state = condition) — Brad's own station, accurate.
- Forecast: `weather.get_forecasts` service on `weather.forecast_home_2`
  (dashboard's `ha_client.py:108` already does hourly; daily also available).

**Built (orchestrator only):** `weather` intent + `weather_when` slot
(now|today|tonight|tomorrow|day-name) in intent.py; new
`orchestrator/weather.py` — HA REST (states + `get_forecasts?return_response`
daily), token mounted from `cecret_lake/dashboard_webapp/ha_token` (same one
the dashboard uses; HA_URL 127.0.0.1:8123 via host network). Current answer =
weather-station temp + met.no condition word (+ wind when ≥10mph); forecast =
condition/high/low per day, rain note from precipitation amount (met.no gives
NO probability, only inches). `handle()` returns None on HA-down / day beyond
the 6-day window → falls back to the ask path (sports pattern), and answers
seed ask history so "what about the weekend?" follow-ups have context.
Validated live via /command: now/today/tomorrow/saturday all correct + timer
and music regressions pass. Later polish: dashboard forecast card event;
"inside temperature" queries (currently routed to none/ask).

## 4. Wake word impossible while music playing — P2 (instrument, then bypass)

**What we know:** ducking fires only *post*-trigger, so stage-1 sees raw
music+voice. ~0/24 recall per Brad. But today's log shows 4 confirmed wakes
WHILE "demons" was playing (17:52–17:53, peaks 0.79–0.96) — so it's not
absolute; likely depends on volume/song density. Whether stage-1 (no trigger
at all) or stage-2 (verify reject) fails in the bad cases is **not yet
measured** — only one music-time verify reject in 48h of orch logs.

**Step 1 — instrument (cheap):** satellite already logs every stage-1 trigger;
add near-miss score logging (bench had it; satellite dropped it) gated to
when music is playing (orchestrator `GET /music/state` exists). A few
deliberate test sessions tell us stage-1 vs stage-2.

**Step 2 — music-mode text wake (Brad's idea; sound, do it):** while
`/music/state` says playing, satellite ALSO streams the mic to Parakeet
continuously — rolling ~3s window POSTed every ~1s (reuse the
PartialStreamer worker + `/transcribe`) — and fuzzy-matches "okay computer"
in the text (same matcher as stage-2 verify). Hit → duck + jump straight
into the turn (stage-1+2 bypassed; Parakeet IS the verification). Lyric
collision with "okay computer" is negligible; GX10 is idle (RTF 0.05, a 3s
decode ≈ 150ms — continuous costs ~15% of one GPU stream only while music
plays). Satellite learns music state via a low-rate poll of `/music/state`
(10s) or a push on play/stop. Keeps stage-1 as-is for the silent house.

## 6. Kids' songs by phonetics ("Day-O" → "Deo") — BUILT+DEPLOYED 2026-07-09 (23a5c78)

**Evidence (orch log 23:51–23:53):** Parakeet wrote "Deo" / "Deo by Rafi".
The song EXISTS locally (`Raffi/Best Of Raffi/06 - Day O.flac`), but
normalized "deo" vs "dayo" scores ~57 indel — below the 80 track threshold —
so the library index whiffed and MA search returned **Spotify track 'demons'
as a no-confidence best guess** (`score=None`). Second try ("Deo by Rafi")
salvaged artist-shuffle Raffi via the by-artist tail. User is right: this is
phonetics, not semantics — embeddings wouldn't fix it.

**Shipped (music.py only, three layers):**
1. **Phonetic skeleton variant** in the library index: spaces stripped, vowel
   RUNS collapsed to one marker (y and h count as vowel-ish, so "day oh"
   works), doubled consonants squashed — "deo"/"day o"/"day oh" all → `dV`.
   EXACT-equality only (short skeletons are too promiscuous for fuzzy),
   scores 90.x — clears artist/album/track bars, stays below playlist's 92 —
   with plain ratio as tiebreak. NOT metaphone (design changed at build time):
   metaphone keys short titles down to single consonants that match half the
   library, and it encodes "deo"/"day o" DIFFERENTLY anyway.
2. **"X by Y" piece rule** (added after testing showed "deo by rafi" still
   shuffled the artist): a by-tail matching the artist of a strong
   track/album hit plays that piece, not an artist shuffle.
3. **Relaxed local fallback** (the cost-asymmetry rule): when MA search's
   winner is a below-threshold guess, a ≥60 library hit plays instead.
   GOTCHA found in testing: relaxed floors + bucket precedence = a 57-scoring
   artist ('Eden') stole from the 90-scoring Day O track — relaxed mode takes
   the GLOBAL best score, no precedence.
   Battery vs the live 5.3k index: all four real/likely Day-O transcripts →
   the owned track; baby-beluga/rafi/best-of-raffi/wheels/ballade-4/britney
   unchanged. Live voice test pending ("okay computer, play day-o").
   **Known pre-existing trap (not tonight's change, watch it):** a junk
   Spotify user playlist literally NAMED "toxic by britney spears!!"
   name-matches ~100 and wins that query's general pass — exact-name junk
   playlists can steal non-library queries.
4. **(PINNED) Parakeet phrase biasing** — would fix the transcript itself,
   but the API (`POST /parakeet/bias`) is GLOBAL across all resident workers,
   and Brad already uses it from the Windows STT-keyboard client for WORK
   terms ("dbt", "Tuva Project") that must NOT leak into house-wide
   transcription (nor music names into work dictation). Prereq: per-request
   bias support in the Parakeet server; then each client sends its own list.

## 1. Immich slideshow (replace GrandKid feed) + tap-to-fullscreen — BUILT + DEPLOYED 2026-07-09

**Architecture decision (Brad asked copy-vs-reference):** REFERENCE the
Immich library in place — no photo copies. The my_photo_app copy pipeline
(S3 + encryption) exists because the grandparent frames are REMOTE; the
kitchen display and Immich share the Beelink. Immich already maintains
~1440px previews (~300KB JPEG) for every asset — ideal for the 1080p panel.
Sidecar SQLite holds curation state only; a bounded LRU disk cache (2GB cap)
of previews decouples the kiosk from Immich restarts.

**Service: `/home/pi/immich-slideshow`** (new repo, port :9021, FastAPI):
- Hourly FULL metadata sweep (deliberate, not incremental: ~40 pages/key,
  LAN-local, and unseen-in-sweep = deleted/archived → auto-excluded).
  `POST /api/search/metadata` with `withPeople`+`withExif` returns people
  NAMES inline — no per-person queries needed. Both users' keys swept,
  deduped (partner sharing: each key sees all 19,330); owner's key used for
  image fetches.
- Buckets: family (any of FAMILY_NAMES=Claire,Simon,Brad,Adrienne — exact
  lowercase match, so "Brady" doesn't leak in) / faces / scenic. Sweep
  results: 18,527 usable = 5,179 family / 1,625 faces / 11,723 scenic;
  803 excluded (videos, archived, screenshot heuristic: PNG-without-camera-
  make or "screenshot" in filename).
- Selection per feed batch: weight = recency exp-decay (half-life 90d,
  floor 0.03 so the archive still surfaces) × favorite ×1.6; shown <24h ago
  ×0.02, <7d ×0.3. Weighted sampling w/o replacement under 60/20/20 quota
  (shortfalls spill to family). `GET /api/feed?n=30`, `GET /img/{id}`
  (27ms cold / 6ms cached), `/healthz`, `POST /api/sync-now`.
- Viewer page (`/`) ports the my_photo_app review-console Viewer design
  (scrims, clock, caption, ticks, 0.9s crossfade, 20s dwell); `body.widget`
  mode (viewport <700×450) hides clock/ticks and shrinks the caption, so ONE
  page serves both the tile and fullscreen — the fullscreen toggle never
  reloads the iframe.
- Secrets: `/home/pi/cecret_lake/immich_slideshow/.env` (keys copied from
  my_photo_app's — note they're comma-joined in ONE `IMMICH_API_KEY` var
  there, which is why a naive read 401s). Repo guard passed; data/ ignored.
  GitHub remote NOT yet created (needs Brad — no gh CLI on box).

**Dashboard (dashboard_webapp 42999d8, deployed + kiosk reloaded):**
`SLIDESHOW_BASE_URL` → `:9021` (.env, runtime — no rebuild needed for URL);
transparent `#slideshow-tap` overlay (iframes eat taps) toggles
`.tile.family.fullscreen` (fixed, z-index 900 — below app/voice overlays
≥1000 so captions render over photos), 2-min auto-return timer.

**Deploy gotcha (NEW, recurring-risk):** kiosk chromium relaunch hung on a
GNOME keyring "choose password" dialog — page never loaded, zero HTTP
traffic, empty chromium log. Screenshot via `grim` on display-pi found it.
Fix: launch chromium with `--password-store=basic` (now part of the reload
runbook). Also seen on the screenshot: display-pi LOW VOLTAGE warning —
power supply may need attention.

**Live-verified:** kiosk pulls feed + images; widget rendering confirmed by
screenshot (scenic photo w/ location · date caption). NOT yet finger-tested:
the fullscreen tap itself (no input-injection tool on display-pi).

**Retained:** grandparents' rpi_client PoC still runs on :9010 (untouched) —
retire whenever Brad wants. Favorites already act as a ×1.6 boost; albums
unused as a signal so far.

**Round 2 (2026-07-10, Brad's requests): family-only + swipes + videos +
bigger captions.**
- Pool narrowed to family-only (Simon/Claire/Adrienne/Brad OR-logic) from
  2023-01-01 (old embarrassing photos were surfacing). Pure config:
  MIX 1.0/0/0 + new MIN_TAKEN_AT knob (92614ea). Pool = 2,984 photos
  (+166 videos when enabled) — at 20s dwell that's < a day of slides, so
  everything repeats ~daily; suppression just evens rotation.
- Swipe navigation: dashboard overlay discriminates tap (fullscreen toggle)
  vs horizontal swipe via pointer events (`touch-action:none` REQUIRED or
  the browser converts drags to pointercancel); swipes postMessage into the
  viewer iframe (`{type:"slideshow-nav",dir:±1}`) — the iframe never sees
  kiosk touches itself. Viewer keeps a 100-item history stack so
  swipe-right goes back. Swiping re-arms the fullscreen auto-return.
  (dashboard 3fa50cc, viewer in immich-slideshow 35e60eb)
- Captions bumped for kitchen viewing distance: 34/19px fullscreen,
  22/14px tile (+text-shadow).
- **Videos: built but GATED OFF (SHOW_VIDEOS=0). Hardware video decode
  wedged the kiosk Pi's GPU** — chromium GPU process spams `Unable to
  initialize SkSurface`/`MakeFromBackendTexture() failed` and TEXT TILES
  SILENTLY STOP REPAINTING (header clock froze at the second the first
  video started while the video itself + radar kept animating — deeply
  confusing signature; page JS provably alive via 20s img cadence in
  service logs). Fix chain: (a) kiosk chromium now REQUIRES
  `--disable-accelerated-video-decode` (runbook updated; sw-decode proven
  healthy live — clock ticked to-the-second through multiple videos, zero
  SkSurface errors); (b) Brad built a pre-transcode pipeline (ffmpeg
  ≤720p H.264 2.5Mbps CRF26, nice -n 10 + 2 threads, /data/vcache,
  video_ready gate — feed never offers a video whose light copy isn't
  ready; /video serves vcache first, Immich-proxy fallback). Backfill of
  all 166 family videos runs in the background.
- **Videos v2 = TAP-TO-PLAY, LIVE (SHOW_VIDEOS=1, 2026-07-10).** Brad's
  design: videos never auto-play — they rotate as poster stills (Immich
  poster frame via /img) with a play badge + duration; tapping expands to
  fullscreen and plays ONCE (MUTED — the display has no speaker; see
  below), then collapses and resumes rotation. Idle video slides are
  plain JPEGs so rotation carries zero decode risk — safe even before
  the power-supply fix. Mechanics: taps
  forward into the iframe as `slideshow-tap`; the VIEWER decides meaning
  (photo=fullscreen toggle, video=expand+play) and answers
  `slideshow-fullscreen {on,off,toggle}` — two-way postMessage, dashboard
  checks e.source. Tap during
  playback stops; swipe-away stops but stays expanded. /api/feed?types=
  filter added (test hook). **E2E-verified on the real kiosk via CDP**
  (remote-debugging-port + ssh tunnel + websocket-client
  suppress_origin=True; Input.dispatchTouchEvent real taps/swipes):
  poster+badge → touch tap → fullscreen+/video stream → clock stayed
  live throughout → auto-collapse on ended → rotation resumed; photo
  tap toggle + swipe re-verified. Commits: immich-slideshow 9cf9106,
  dashboard bff7c0e.
- **Videos are MUTED (2026-07-10)** — the display has no speaker.
  Viewer forces `V.muted = true` (immich-slideshow 15ad05a) and the
  kiosk launch line dropped `--autoplay-policy=no-user-gesture-required`
  (home_config cc2ab43). To restore sound: add a speaker to display-pi
  (best — perfect lip-sync, two-line change: unmute + re-add the flag;
  pair with the power-supply fix). Relaying audio to the kitchen-speaker
  satellite (aplay + existing orchestrator duck) is buildable but lands
  ~¼–½ s of lip-sync offset; MA announcements are a non-starter
  (snapcast buffers ~1 s, queue pause/resume jarring for long clips).
- **Portrait pairing (2026-07-10, immich-slideshow ad080fd).** Brad
  flagged heads getting cut off: the viewer's `object-fit: cover`
  center-cropped portraits on the 16:9 display, and 72% of the family
  pool (2,160/2,984) is portrait. Fix = the grandparent-frame trick he
  remembered: portraits show TWO at a time, side by side, contain-fit
  (never cropped) over a blurred copy of themselves, per-pane captions.
  Landscape singles stay full-bleed cover but crop from `50% 30%`
  (faces are in the upper third). Lone portraits (no partner in queue)
  get contain+blur solo. Video playback now contain (vertical clips
  no longer crop); videos never pair. Sweep stores orientation-corrected
  width/height (EXIF orientation 5-8 = swap dims); feed adds `portrait`.
  History entries are now slides (1-2 items) so swipe-back restores
  pairs. CDP-verified on the kiosk: pairs in tile + fullscreen, swipe
  fwd/back, tap collapse, video tap-to-play all pass.
- **Video audio → kitchen satellite (2026-07-11, immich-slideshow
  f3f8e5d + home_config d874185).** Brad's call: son wants sound on
  home videos; small A/V offset OK until the display-pi PSU is fixed
  (then a local USB speaker = perfect sync). Pipeline: transcode worker
  extracts 44.1k stereo WAVs from the vcache mp4s (378 extracted, 3
  silent → .none markers, vcache 5.6G); slideshow serves /audio/{id};
  viewer POST /api/av/start → satellite POST /media/play (fetches the
  WAV, gain-scaled to the assistant speech tier, own killable aplay —
  NOT PLAYBACK_LOCK, so clips never queue replies/alarms) and the
  viewer starts the picture only on the relay's reply (or silent after
  1.5s if relay down). Music ducks for exactly the clip lifetime
  (watcher thread owns unduck; orchestrator refcount). Auto-stopped by
  stage-1 wake trigger + alarm start; viewer /api/av/stop on swipe-away/
  stop-tap. Config: SATELLITE_URL/SELF_URL in cecret .env. Satellite
  deployed (mode restored active). Verified e2e: kiosk tap → video
  t=3.2s while satellite aplay ran the matching clip; natural end →
  aplay exits + unduck balanced; manual stop kills instantly. NOT yet
  ear-verified for sync feel — ask Brad/Simon.
- **Caption size + scrim fix (2026-07-10, immich-slideshow f0512a5).**
  Brad: captions still too small at viewing distance + text "fading
  out" at the bottom. The fade was a pairing-refactor REGRESSION:
  captions moved inside the photo layers, which paint UNDER the global
  bottom scrim gradient — the scrim was dimming the text itself (both
  modes; dashboard adds no fade of its own). Scrim-bottom removed
  entirely (user call); fonts 34/19→56/30 fullscreen, 22/14→30/18
  widget, weight 700, heavy double text-shadow for readability on
  bright photos. Scrim-top kept (clock sits above it, unaffected).
  CDP-screenshot verified both modes.
- **Kid ages in captions (2026-07-10, immich-slideshow 77b769b).**
  Sweep syncs name→birthDate from Immich /api/people (only people with
  a birthDate set get ages — kids yes, parents no, nothing hardcoded;
  set/change birthdays in Immich and it flows through within the hour).
  Feed decorates names with age at photo time: newborn / "8 mo" /
  half-year steps to 3 ("1", "1½", "2", "2½") / whole years after.
  Server-side only; viewer untouched.

## 9. Speaker ID (voice identification) for user-dependent list items — IDEA, not scheduled (added 2026-07-13)

**Context:** as of 2026-07-13 reminders and to-dos are per-person in the
voice-notes phone app and portal (shopping stays household-shared). The
kitchen assistant files everything under `LIST_OWNER=brad`
(orchestrator config.py), so a reminder/to-do spoken by Adrienne at the
satellite lands on Brad's lists and Brad's phone gets the push. Speaker ID
would route these to the actual speaker.

**Brad's design sketch (sound — lazy path):** do NOT run speaker ID on every
turn. The fast path — music, timers, questions, and especially shopping adds
(the dominant kitchen use case) — is user-independent and skips
identification entirely. Only when intent parsing yields a user-DEPENDENT
item (reminder/to-do add) does the pipeline run speaker ID over the
already-captured command audio. Marginal cost is therefore zero for ~all
turns and one embedding pass for the rare personal item; latency doesn't
matter there because item extraction is already async to the spoken reply.

Notes for whenever this gets built:
- Prereq: the command WAV must outlive intent parsing. The satellite already
  POSTs the full capture; the orchestrator just has to keep it for the turn.
- Two-person closed set is the easiest speaker-ID problem there is: enroll
  ~30s per person, ECAPA-TDNN / resemblyzer embedding + cosine distance,
  threshold with an "unsure" band. No diarization needed (one speaker per
  command).
- Fallback rule matters more than the model: below-threshold confidence
  should file under `LIST_OWNER` (today's behavior), never guess —
  a misrouted reminder is worse than the status quo because NEITHER phone
  surfaces it to the right person in time.
- UI: dashboard badge showing who it heard ("→ Adrienne's reminders"), and
  the TTS confirmation can name the list owner — cheap trust-building plus
  an audible correction path when it's wrong.

---

## Batch 1 live-test results (Brad, 2026-07-09 night)

- Item 5: no premature endpoints in short tests + a ramble test — good but
  not conclusive; real-world watch continues (grep satellite journal for
  `spurious onset discarded` to see the guard actually firing).
- Item 3: alarm stop improved — one dismiss on the FIRST "stop", one took two.
  Better than 3-4×; leave as-is unless it degrades (next lever: trained
  "stop" wake model).
- **Regression found + fixed (c1cfd96): live captions died after the deploy.**
  PartialStreamer's seq restarted at 1 on service restart while the kiosk's
  `caption.lastSeq` kept its pre-restart high-water mark → every partial
  dropped as stale. Every earlier satellite restart shipped with a kiosk
  reload, which masked it. Fix: seed seq from `int(time.time())` — always
  larger after a restart. Verified the chain with a TTS clip → `/partial?seq=
  <clock>` → decode → dashboard event; then restarted once more so the live
  seed outran the test seq. **Rule for the future: satellite seq semantics
  must survive restarts, or the kiosk must treat a big backward seq jump as a
  new epoch (candidate hardening for the next dashboard rebuild).**

## Side findings (logged while investigating, not on the list)

- ~~**`/command/audio failed: timed out` 17:22:04**~~ — promoted to item 8
  above after Brad reported the France-score ask never spoke.
- **arecord `overrun!!!` (2–27s) after every reply playback** — main loop
  blocks on aplay while the mic pipe overflows. `resync()`/drain covers the
  wake path, and follow-up capture drains first, so likely benign — but it's
  exactly the kind of thing that eats the first follow-up word on slow turns.
  Cheap insurance: bump arecord's `--buffer-time`, or drain before playback.
- Satellite restart still resets mode→shadow (known; memory note exists).

## Family-room second mic (phase 2 satellite) — scoped 2026-07-19

Second ReSpeaker into the ethernet Pi across the family room (currently only
polling Powerwalls via local TED API — cores mostly free). Mic-only fallback
satellite: better hearing from the far end, and during music it beats the
kitchen mic that now sits next to the big speakers (no AEC) — most of
"wake-over-music" solved by geometry.

**v1 (build first):**
- Run the standard satellite locally on the Pi (stage-1 dual models, lazy
  HOP is fine — fallback mic, latency budget generous; verify Pi model +
  thermals first).
- Playback relay: far satellite's chime/TTS/alarm audio redirects to the
  kitchen satellite (kitchen box remains the only voice). Alarms unchanged
  (kitchen-only); far-mic "stop the timer" is just a normal turn.
- Orchestrator wake arbitration: first verified request wins the turn;
  suppress the other satellite for ~2-3s. Deterministic (arrival order).
  Built-in correctness: during music the drowned kitchen mic fails verify,
  so the far mic wins by default, not by racing.
- Loser posts its shadow-captured command audio anyway (no user-visible
  effect) so the logs show when the losing mic had the cleaner capture.

**v2 (only if v1 logs prove mis-hears the other mic would have fixed):**
- Dual-transcribe the *command* (never the wake — would add 300-500ms to
  every chime): both captures hit Parakeet, orchestrator picks the better
  transcript before intent parsing. Deterministic chooser: length-normalized
  Parakeet Hypothesis score, tie-break on satellite voiced-ms/RMS metadata,
  final tie-break kitchen. Doubles Parakeet load only on actual wakes.

## Unattended-timer phone escalation (Voice Notes push) — BUILT+DEPLOYED 2026-07-22

Timer rings in the kitchen, everyone is upstairs/with the kids, it times out
un-dismissed and dinner burns. Escalate to phones — same rationale as the
list sync: the phones are the household's always-on surface.

**All the parts already exist — this is glue, not construction:**
- Voice Notes app (both phones) has FCM push + `voice_notes_reminders`
  notification channel (`VoiceNotesMessagingService` posts title/body from
  data messages).
- Companion (:8768) has FCM v1 send (`send_fcm_to_token`, service account in
  cecret_lake/voice-notes/) and per-device registered tokens.
- Satellite `alarm_playback` already tracks dismissed-vs-timeout per ring.

**Design:**
- Satellite: on alarm start, arm a one-shot ~15s watchdog thread; if
  `STATE.dismiss` still unset when it fires, POST orchestrator
  `/timers/{tid}/unattended` (satellite stays dumb about phones). Cancel on
  dismiss. No change to ring behavior.
- Orchestrator: new route formats the message from timer state ("Waffle
  timer (12 min) has been ringing for 15s — nobody's stopped it") and
  relays to companion.
- Companion: new endpoint `/alert` (LAN, same auth as scanner) that fans a
  data message to ALL registered device tokens.
- Threshold 15s (not ring-timeout): actual full ring is ~45-90s
  (ALARM_MAX_LOOPS=14 × sound+2s gap — longer than the felt "10-15s");
  waiting for timeout delays the phone by a minute. 15s un-dismissed already
  means nobody's in the kitchen.
- Optional second notification at ring timeout ("gave up ringing") — only if
  the 15s one proves insufficient in practice.

**Explicitly out of scope (v1):** dismissing from the phone (notification is
informational; tap opens Voice Notes), per-person routing, quiet hours.

**Built as designed 2026-07-22, one deviation:** the watchdog lives in
`alarm_playback` as a thread waiting on `STATE.dismiss.wait(UNATTENDED_ALERT_S)`
— any dismiss cancels it for free, no explicit cancel path needed. Skipped for
anonymous alarms (no timer_id). Companion `/api/alert` fans to ALL push tokens
(both users) and prunes unregistered ones. Orchestrator route
`/timers/{id}/unattended` no-ops unless the timer is still RINGING, formats
"The waffle timer (12 minutes) is ringing in the kitchen and nobody has
stopped it.", relays via `events.phone_alert`, and emits a `timer_unattended`
dashboard event. E2E-verified live: ring 15s untouched → both phones got the
push (companion log sent=2). Android app needed no change (it renders any
data message's notification_title/body).

## Orchestrator cancel/dismiss should silence the satellite alarm — BUILT+DEPLOYED 2026-07-22

**Incident (2026-07-20):** timer rang while music played, voice stop failed
(item 4 territory), Brad swiped the ringing card on the kiosk — card flew off,
ringing continued to timeout. Root cause was a dashboard bug, already fixed:
`dismissAlarm()` still referenced `WAKE_BENCH_URL` after d1a252a renamed it to
`SATELLITE_URL` → ReferenceError on every tap/swipe of a ringing card (and
before that rename the URL pointed at the powered-off .24 Pi, so touch dismiss
had been silently broken since the mini PC swap). Fixed + deployed + pushed
(dashboard_webapp f959ca5); toast would have said "Couldn't stop alarm:
WAKE_BENCH_URL is not defined" — nobody reads toasts mid-alarm.

**Remaining structural gap (the actual backlog item):** the only thing that
stops the ringing sound is the kiosk's DIRECT POST to satellite
`/alarm/dismiss`. The orchestrator's own `/timers/{id}/cancel` and
`/timers/{id}/dismiss` routes just flip SQLite state + emit dashboard events —
they never notify the satellite. Any current or future client that cancels a
ringing timer through the orchestrator REST (phone app, voice-notes portal,
curl, a second dashboard) reproduces the "card gone, still ringing" symptom.

**Fix sketch (small):**
- `events.py`: add `alarm_stop()` posting satellite `/alarm/dismiss`
  (best-effort, same pattern as `alarm()`); satellite endpoint is idempotent
  (`STATE.dismiss.set()`, cleared before each new alarm) so blind-firing is
  safe.
- `app.py`: call it from `cancel_timer`/`dismiss_timer` when the timer being
  cancelled was in state `ringing` (check BEFORE `cancel_by_id` mutates), and
  from `timer_cancel` intent's `cancel_all` path if any cancelled timer was
  ringing.
- Gotcha: satellite `alarm_playback` ends by POSTing `/timers/{tid}/dismiss`
  back to the orchestrator — harmless 404 today (timer already cancelled),
  keep it that way (don't let that 404 become an error path).
- Optionally the kiosk's `dismissAlarm()` then collapses to the orchestrator
  route and loses its hardcoded satellite IP — one less config duplicate
  (kiosk currently can't stop an alarm if the orchestrator is up but the
  satellite IP changes again).

**Built as sketched 2026-07-22.** `events.alarm_stop()` POSTs
`SATELLITE_ALARM_DISMISS_URL` (config-derived from SATELLITE_ALARM_URL, so one
env var moves both); fired from `cancel_timer`/`dismiss_timer` REST routes and
both `timer_cancel` intent paths (ringing state snapshotted BEFORE the engine
mutates it). Race note: the satellite's own end-of-ring POST to
`/timers/{tid}/dismiss` now triggers alarm_stop back at the satellite — safe
ONLY because the route awaits alarm_stop before responding, and the satellite
starts its next queued alarm (which clears the dismiss flag) only after that
response returns. Don't move alarm_stop to a background task there.
E2E-verified live: REST cancel of a ringing timer silenced the speaker <1s
(`ALARM end (dismissed)` in satellite journal). Kiosk collapse ALSO done
same day (dashboard abe2b10 + orchestrator `POST /alarm/stop`): ringing-card
tap/swipe → dashboard proxy `/api/assistant/alarm/stop` → orchestrator, which
dismisses whatever's ringing AND blind-fires the satellite silence — state
skew can't keep the alarm ringing, and the kiosk's alarm path no longer
hardcodes the satellite IP (SATELLITE_URL remains only for the mic button +
wake review). Proxy path e2e-verified live; kiosk reloaded (finger tap on a
real ringing card still pending).

## Broadcast / intercom to whole-home audio ("tell Simon to come eat") — BUILT+DEPLOYED 2026-07-24 (loft-verified)

**Use case (Brad):** kid is upstairs, Brad is at the kitchen satellite —
"tell Simon to come eat dinner" plays the message on Simon's room's
snapclient; "tell the kids it's time to eat" (or no target) plays on all
five whole-home audio rooms.

**Delivery goes through Node-RED, NOT Music Assistant.** The snapclients
are finicky via MA and the working path already lives in a Node-RED
subflow with two hard-won workarounds that must not be reimplemented:
(1) ~2s of silence appended to every clip because snapserver sync cuts
the tail off (known bug); (2) pseudo amp-state tracking — if a room
hasn't been pinged in ~15 min the amp is in standby, so a known chime
with enough dB is prepended to wake it reliably. That chime doubles as
the attention-getting earcon before the message.

**Pipeline:**
1. New `broadcast` intent in intent.py (LLM parse) → `{message, target?}`.
2. TTS via existing `clients.synthesize` → WAV into `ANNOUNCE_CACHE_DIR`,
   served over the existing orchestrator media route (same as timer
   announcement WAVs).
3. Orchestrator publishes ONE MQTT message (e.g. `voice/broadcast`) with
   JSON payload `{"rooms": ["simon"] | "all", "url": "http://…/….wav"}`.
   Decided over per-room HA buttons (Brad agreed 2026-07-23): buttons are
   stateless and can't carry the arbitrary message URL; one topic +
   payload also means adding a room is a lookup-table edit, not a new
   flow.
4. New Node-RED tab: subscriber resolves rooms → snapclient devices and
   fans out into the existing padding/amp-wake subflow (per-room or the
   full-array path for "all").
5. Kitchen satellite speaks a short confirm ("Sent to Simon's room" /
   "Broadcasting upstairs").

**Intent parse rules (few-shot in intent.py):**
- Primary trigger is "tell <name/group> …"; "broadcast …" and
  "announce …" map to the same intent.
- Reported → direct speech: "tell Simon to come eat dinner" broadcasts
  "Simon, come eat dinner", never the literal "to come eat dinner".
- "tell me …" is NEVER broadcast (joke/weather/ask stay where they are) —
  the discriminator is the target; few-shot both directions.
- Room resolution reuses the home_control pattern: fuzz.ratio ≥80 against
  a hot-reloadable alias table (per-kid names + group aliases "the kids"/
  "everyone"/"the boys" → all), editable from the phone alias editor.
- Fallback: no target OR near-miss on a known name → all rooms; a
  completely unknown target ("tell Grandma happy birthday") still goes to
  all but the confirm says so ("I don't know Grandma's room, sent it
  everywhere") — never silently pretend it matched.
- Add the kids' names to the kitchen parakeet bias profile — room targets
  are exactly the proper nouns ASR mangles.

**Optional layer on top:** real dashboard/phone buttons for canned
messages (a "Dinner time" button publishing a pre-rendered WAV to
`rooms: all`) — buttons for humans, topic-with-payload for the
orchestrator.

**Open items at build time:** confirm idle per-room snapclient volumes
are sane; pick the MQTT topic + room→device lookup-table home (Node-RED
flow context vs. file). v2 idea (deferred): intercom mode replaying
Brad's actual captured voice instead of TTS — more attention-grabbing,
but trimming wake phrase + "tell Simon" prefix from the clip is fiddly.

**Build record (2026-07-24, deviations from the design above):**
- **No orchestrator TTS.** Discovery: the existing Amp Speakers subflow
  (`e711d48f74f78209`) takes TEXT (`msg.alexa`) + `msg.players` (HA
  media_player entities) + `msg.volume` (0-100) and does its own TTS
  (tts.openai via HA, voice picard:calm), tail-pad via tts-pad-service
  :8097, snapclient isolate via home-audio-adapter :8461, and the amp
  standby wake chime (14-min `wholeHomeAmpLikelyOn` global). So the
  orchestrator publishes text, not a WAV URL — announce-cache plumbing
  never needed. It also filters master bedroom when
  `DisableBedroomAnnouncements`/`adrienneWorkingDisableAnnounce` is set.
- **Node-RED tab "Voice Broadcast" `e3a9d4391d545738`**: mqtt-in
  `voice/broadcast` (broker `82f540b7378c2e35`, same as Voice Buttons)
  → resolve function (canonical keys loft/claire/simon/master/shower →
  entities, "all"/null → all five, unknown keys warn+skip, fail-closed)
  → Amp Speakers subflow instance.
- **Orchestrator** publishes via HA REST `mqtt.publish` (no broker client
  dep; verified HA → local mosquitto container). New `broadcast.py`:
  hot-reload alias table `broadcast_rooms.json` (seed in repo, live copy
  /data, home_commands pattern), fuzz.ratio ≥80, entries carry a rooms
  LIST so "the kids" → simon+claire; no-target → all (matched), unknown
  target → all + "I don't know where X is, so I sent it everywhere."
  intent.py: `broadcast` intent + `broadcast_target` field. app.py:
  home_control-style dispatch, no ask fallback. 9 unit tests
  (test_broadcast.py) + home_control regression pass in-image.
- **Config:** BROADCAST_TOPIC / BROADCAST_ROOMS_FILE / BROADCAST_VOLUME.
  The volume=10 rollout guard used during the 2026-07-24 build tests was
  removed same day (Brad: live tests should be regular volume) —
  BROADCAST_VOLUME is now unset, so broadcasts fall through to Node-RED's
  defaultSpeakerVolume global (50), exactly like doorbell announcements.
- **Verified live (loft only, volume 10, per Brad — master bedroom
  occupied):** raw MQTT → wake chime + padded TTS on Loft (MA log);
  full /command E2E "tell the loft that this is a voice broadcast test"
  → intent broadcast, score-100 resolve, played on Loft, confirm "Sent
  to the loft.", 3.5s. Parse checks: reported→direct rewrite works
  ("tell simon to come eat dinner" → "Simon, come eat dinner"), "tell
  me a joke"→ask, "where is simon"→ask. timer_query + weather
  regressions pass.
- **Pending:** live-voice test from the kitchen mic; all-rooms live test
  (deliberately NOT run — bedroom occupied); kid names into the kitchen
  parakeet bias profile; "adrienne"/"mom" have no room mapping yet
  (falls back to all) — add to broadcast_rooms.json (/data copy or seed)
  if she wants master as her default; optional canned-message dashboard
  buttons.

**Phone app round (2026-07-24, same day):** Brad wants to broadcast when
not at a satellite; explicit UI beats parsing (a broadcast is an audible
multi-room side effect — nothing should be inferred). APK chosen over a
web tile (Brad's call; his F-Droid HTTPS enrollment now CONFIRMED).
- Orchestrator REST for the app: `GET /broadcast/rooms` (ordered chip
  list from broadcast_rooms.json — table edits reach the app with no
  release) + `POST /broadcast {rooms: "all"|[keys], message, volume?}`
  (validates keys, no fuzzy/no intent parse; 400 unknown rooms, 502
  publish failure). broadcast.py send()/rooms_list(); 14 unit tests.
  Deployed + loft-verified (vol-10 courtesy test).
- Voice Notes app (voice-notes-android 8ffb592, versionCode 1784904218
  published via publish.sh): fifth tab "Broadcast" — room chips
  (single-select, All default, prefs-cached fetch + built-in fallback),
  message box, Send → toast "Sent to <spoken>". Mic button on this tab
  DICTATES: VoiceRecorder → cache wav → Parakeet with the existing
  notes-<user> bias profile → fills the message box for review (never
  auto-sends). Note-recording carried across a tab switch still ends as
  a note (broadcastDictation flag guards both directions). Orchestrator
  base in Settings (orch_base pref). Tab bar now five tabs @12sp.
- **Pending:** on-phone confirmation (F-Droid auto-update or manual pull
  of voice-notes-latest.apk), then a real away-from-kitchen broadcast.

## Find my phone ("where's my phone" rings it) — BUILT+DEPLOYED 2026-07-24 (live-verified)

**The missing Google Home feature Brad uses most.** Google's Find My
Device has NO public API (unofficial reverse-engineered projects exist —
rejected as fragile), but none is needed: the HA companion app is on both
phones, and a `notify.mobile_app_*` TTS message on `media_stream:
alarm_stream_max` rings at max alarm volume through silent mode. Piloted
live on the pixel 8 pro before building: first send without priority
flags waited for unlock; `ttl: 0` + `priority: high` in `data` delivered
instantly to the locked, dozing phone. Both flags are therefore always
sent.

- **find_phone.py** — broadcast-pattern module: hot-reloaded
  `phones.json` (config `PHONES_FILE`; brad=pixel_8_pro,
  adrienne=pixel_9_pro, aliases incl. dad/mom + "adrian" ASR variants),
  `resolve()` strips filler words then fuzz.ratio≥80, `ring()` posts the
  HA notify with a repeated "Here I am! This is Brad's phone." TTS.
- **Intent `find_phone`** (intent.py) with `phone_owner` field ("my",
  "brad", "mom"…). Person/business "where is X" stays ask/place_search.
- **"my phone" can't be attributed until speaker ID (item 9)** — handler
  returns needs_owner, app speaks "Whose phone — Brad's or Adrienne's?"
  and stashes a `find_phone` session pending op; the follow-up answer
  ("Brad's") is resolved by `resolve()` directly, NO second LLM parse
  ("no"/"never mind" cancels; anything else abandons + parses normally).
  home_control-style no-ask-fallback — never a web search.
- Tests: test_find_phone.py (7) + full suite 55 green (run inside the
  container image — host python has no httpx). E2E verified via
  /command: named ring, ask-whose→"brads" followup ring, never-mind.
- **Speaker ID upgrade path:** when item 9 lands, resolve "my" from the
  voice embedding and only fall back to ask-whose below confidence.
  Two-person closed set should easily beat Google's voice match (its
  constant "I can't identify your voice" to Adrienne was a top complaint).
- **Pending:** live-voice test from the kitchen mic; Adrienne's phone
  never actually rung (deliberately — only Brad's during tests); consider
  kid alias additions if kids' phones ever join HA.

**v2 rework same evening — Brad's two complaints after the first live ring:**
(a) the robotic TTS "here I am, this is Brad's phone" was grating vs
Google's musical alarm, and (b) there was NO WAY TO STOP IT — a TTS
notification produces audio only, no notification entry, nothing to
swipe, so it rang the full window while he hunted for an off switch.
- **Sound:** dropped `message: "TTS"` for a REGULAR notification on
  `channel: alarm_stream` (special channel name — still bypasses
  silent/DND). The SOUND is then a phone-side Android setting (HA app >
  Notifications > "Alarm Stream" channel > Sound), so each phone picks
  its own ringtone and we're out of the sound-choice business. Note this
  rings at the phone's ALARM volume, not forced max as alarm_stream_max
  did — revisit if it's ever too quiet to find.
- **Stopping, three ways:** `actions: [FIND_PHONE_FOUND "Found It"]` +
  tag `find_phone`; voice "found it" (new `phone_action: stop` field on
  the intent — "found it", "stop ringing the phone"); or swipe-dismiss.
  Node-RED tab **"Find Phone" 5888bdf58e18e8f8** bridges BOTH HA events
  (`mobile_app_notification_action` filtered to FIND_PHONE_FOUND, and
  `mobile_app_notification_cleared` filtered to tag find_phone) to
  `POST :8785/phone/found`, which cancels the ring task; cancellation
  also posts `clear_notification` so the alert leaves the phone.
- **Ring loop:** same-tagged re-post every FIND_PHONE_INTERVAL_S replays
  the sound (one notification entry, not a stack) x FIND_PHONE_REPEATS,
  plus a notification `timeout` so a stale alert can't linger if the
  orchestrator dies mid-window.
- **Live test (Brad, tap path):** bridge CONFIRMED — tap reached
  /phone/found. But it landed at +29s against a 30s window ("ring not
  active"): he couldn't cross the house in time. **Window raised 6->24
  repeats = 2 minutes**, which is only reasonable BECAUSE stopping is now
  easy. Redeployed; 60 tests green.
- **Cadence fix (Brad, immediately after):** a same-tag re-post RESTARTS
  the sound from the top, and at 5s against a ~20-30s alarm tone it "kept
  building then abruptly restarting" — every ring cut off mid-phrase,
  never once playing through. **INTERVAL must exceed the phone's ringtone
  length:** now 4 x 30s (same 2-min window, each ring completes). Rule for
  any future tweak: shortening the interval below the tone length
  re-creates this; lengthen the window with REPEATS, not a faster cadence.
- **Volume pegging (Brad: "peg it at 100 — the point is to FIND it").**
  Regular notifications IGNORE `alarm_stream_max` (verified in the android
  app source: media_stream is only read by the COMMAND_VOLUME_LEVEL
  handler; TTS-only otherwise). So volume takes
  `message: command_volume_level` + `data: {media_stream: alarm_stream,
  command: 0-255}` (app clamps to the stream's real step max) — and
  `setStreamVolume` PERSISTS: the app never reverts it. Pegging blind would
  leave their real morning alarms at full.
  **Therefore pegging is gated on being able to READ the level first** —
  `phones.json "volume_sensor"` (sensor.<phone>_volume_level_alarm):
  capture → peg to max → restore the captured step when the ring ends
  (natural end, voice stop, or tap — restore is shielded so a cancel
  mid-teardown can't strand it at max). No sensor = no peg, ring at
  whatever the phone is set to; a guessed restore is worse than a quiet
  ring. Plus a plausibility guard: a value >30 means the sensor reports a
  PERCENTAGE, and restoring e.g. 71 would clamp to max — refuse to peg.
  Config: FIND_PHONE_PEG_VOLUME, FIND_PHONE_MAX_VOLUME.
  ONE-TIME per phone to activate: HA app > Settings > Companion app >
  Manage sensors > Volume Levels > enable alarm volume. Entity IDs are
  already in phones.json, so pegging starts working the moment the sensor
  appears — no redeploy.
- **LIVE-VERIFIED 2026-07-24 (Brad's pixel 8 pro).** Brad flipped the
  sensor; it registered as `unknown` and needed `command_update_sensors`
  pushes to populate (useful trick: notify message `command_update_sensors`
  forces a companion sensor refresh; new sensors otherwise wait for the
  next cycle / app foreground). It reports **steps, not percent —
  min 1 / max 7, read 3** — so the plausibility guard passes and restore
  is exact. Full pegged ring: `pegged on brad (was 3)` -> ring -> Brad's
  in-window tap at +23s -> `phone found (ring stopped)` -> `restored to 3`,
  and the phone's OWN sensor independently read 3 afterwards. The v2 tap
  path is now confirmed INSIDE the window (the earlier +29s tap only
  missed because the window was 30s).
- **Two robustness fixes found while testing (both live-verified):**
  1. *Restart stranding.* A deploy/crash mid-ring skips the in-loop
     restore and would leave their REAL morning alarm at max. The pegged
     level is now journalled to `/data/find_phone_volume.json` (host
     volume, survives container recreation) before the ring window, and
     `restore_stranded()` runs on startup, pushes the restore, and deletes
     the journal (corrupt/unknown journals are dropped; a failed restore
     retries next startup). Verified by planting a journal and restarting:
     "alarm volume was left pegged on brad by a restart — restored to 3".
  2. *Last ring cut short.* Android applies a stream-volume change to
     audio already playing, so restoring immediately after the final
     re-post chopped that ring mid-tone. The loop now waits one INTERVAL
     after the last post before restoring (natural end only — a voice/tap
     stop still restores at once, since silence is the point).
  Also fixed a would-be import crash: the new config default referenced
  DB_PATH, which is defined LOWER in config.py — re-read the env default
  instead. 69 tests green.
- **DND-PERMISSION GATE (hit live, Brad 2026-07-24).** With the sensor
  enabled and the code correct, the pegged ring still came out QUIET and the
  phone showed "Please open the Home Assistant app and send the command
  again to grant the proper permissions". `command_volume_level` requires
  **Do Not Disturb access**; without it the app DROPS the command silently
  (HA's REST call still returns 200, so nothing appears in our logs) —
  which also means a failed peg is harmless, there's nothing to restore.
  Grant per phone: tap that notice, or Settings > Apps > Special app access
  > Do Not Disturb access > Home Assistant > Allow. So each phone needs
  THREE one-time grants: Volume Levels sensor, DND access, and (optional)
  a musical ringtone on the Alarm Stream channel.
  **Diagnostic added:** after pegging, a background task pushes
  command_update_sensors and reads the level back ~8s later; if it hasn't
  risen above the prior value it logs "alarm volume peg had NO effect on
  <phone> — grant Do Not Disturb access…". Best-effort, never blocks or
  fails a ring. 71 tests green.
  Also unexplained in that same run: the ring notification itself didn't
  sound until Brad unlocked the phone ~10s later, despite ttl 0 +
  priority high (the 02:55 ring was instant). Watch whether it recurs —
  possibly the permission-prompt notification taking precedence.
- **DND grant CONFIRMED working (Brad 2026-07-24): "loud and worked this
  time."** On current Pixels the Special-app-access entry is renamed
  **"Modes access"** (Android replaced Do Not Disturb with Modes) — same
  permission, that's the one to grant. That same ring ALSO closes the
  live-voice test: it came in by voice, the FAMILY-ROOM satellite winning
  arbitration ("Okay, computer." verified 100 while the kitchen mic heard
  "Okay, from here." at 66.7) -> find_phone, owner brad -> pegged (was 3)
  -> tap at +6s -> restored to 3.
- **Diagnostic false positive (found immediately, fixed).** That +6s tap
  made the new readback log "peg had NO effect (still 3)" — the early stop
  had ALREADY restored the level before the 8s readback, which looks
  identical to a dropped command. The readback now bails when the ring is
  no longer active, and `stop()` cancels the diagnostic task outright.
  Lesson for any future post-hoc verification here: a check that races the
  cleanup path will cry wolf on the FAST, HAPPY path. 72 tests green.
  Re-tested with a ring held past 10s: **"alarm volume peg confirmed on brad
  (now 7)"** — the phone's OWN sensor read 7 mid-ring (peg is real, not just
  audible), then stop -> "restored to 3". Peg/restore is now proven from
  both ends: our log AND the device sensor.
- **Pending after v2:** pick a musical ringtone on each phone's Alarm
  Stream channel (one-time, per phone — the channel only exists after the
  first notification arrives); enable the Volume Levels sensor to unlock
  pegging, then VERIFY the sensor's scale is steps-not-percent and that the
  restore lands (compare the sensor before/after a ring); re-test the
  tap/swipe stop INSIDE the window; voice "found it" while actually
  ringing; confirm the 30s cadence sounds right with the chosen tone;
  kitchen live-voice.

## Timer slot-fill: "set a timer for…" → "Sure, for how long?" — BUILT 2026-07-26 (not yet deployed)

**The live failure (Adrienne, 2026-07-26 23:55).** She said "okay computer,
set a timer for", paused to think about how long, and VAD endpointed on the
gap. Two things then went wrong, and the second is the one that mattered:

1. The classifier read the truncated `'set the timer for'` as **`unclear`** →
   "Sorry, I didn't catch that." The `set_timer`-with-null-duration branch
   that asks "how long?" already existed; the parse never reached it.
2. The follow-up window DID open and DID hear **"Eight minutes."** — and
   dropped it as `none` ("followup dropped as not-for-us"). A bare duration
   isn't a supported intent, and the follow-up note tells the parser to prefer
   `none` over talking back to the room. She re-said the whole command at
   23:56:44 and got her 8-minute timer.

Fixing only (1) would have asked "for how long?" and then thrown the answer
away. Both halves were required.

**What was built.**
- *Prompt rule* (`intent.py`): a timer command that stops before its duration
  is `set_timer` with `duration_seconds` null, never `unclear`.
- *Deterministic backstop* (`is_truncated_timer`): prompts are probabilistic,
  and `unclear` is an honest reading of "set the timer for". A narrow anchored
  regex forces the slot-fill path when the classifier says unclear/none. Safe
  here precisely because the phrase has exactly one meaning — this is NOT a
  pattern to reach for in general intent routing.
- *Clarify pending op* (`app.py`): the question arms
  `session_set_clarify(partial, question, label, theme)`; the next turn
  **stitches the reply onto the partial and re-parses the whole thing**
  (`parse_clarify`). That reuses the entire rule set instead of growing a
  grammar per slot, and generalises to any future incomplete command. The
  answer falls through the NORMAL dispatch, so it lands in the same
  `set_timer` branch a complete command would.
- *Duration fast path* (`spoken_duration`): "eight minutes" / "half an hour" /
  "an hour and a half" read locally, skipping a ~2s LLM round trip on the turn
  where the user is already waiting. Only has to be RIGHT, not complete —
  misses fall through to the parser. A bare number with no unit deliberately
  declines (the unit would be a guess; the parser has the original command).
- *Longer window* (`assistant.py`): `awaiting_slot` in the response makes the
  satellite hold the mic `CLARIFY_WINDOW_MS` (12s, was 7s). The whole reason
  we had to ask is that she pauses to think — she will pause again.

**Guards that matter.**
- Clarify stitching is **follow-up turns only**. A fresh wake word means she
  gave up and started over; stitching her new command onto the abandoned
  partial would produce nonsense.
- **One round only.** If the stitched re-parse still has no duration →
  "Okay, never mind." A second "for how long?" is a loop, not a conversation.
- An answer that abandons the request ("actually pause the music") is parsed
  on its own merits and the timer is dropped.
- "never mind" lands on `none` → silent drop, not a spoken reply. Silence is a
  fine answer to "never mind" and much safer than blurting into the room.

94 tests green (22 new: `test_intent_slots.py` for the two deterministic
pieces, `test_clarify.py` for the wiring, replaying the 23:55 sequence end to
end with the classifier stubbed). Test-isolation bug found and fixed while
writing them: `TimerEngine` is sqlite-backed, so the tests were sharing timers
and would have written into the REAL timer DB — each test now gets a temp
`DB_PATH`, and `clients.synthesize` is stubbed (timer creation pre-renders
announcement audio, so the suite was hitting the live TTS server; `--network
none` now proves it doesn't).

**Pending:** deploy (orchestrator rebuild + satellite push), then live-voice
test — say "okay computer, set a timer for", pause, answer "eight minutes".
Watch for: whether 12s is enough thinking time, and whether the classifier's
new rule fires without the regex backstop having to catch it (log line
"truncated timer rescued from intent=…" tells you the prompt lost).

**Phase 2 idea, NOT built:** the root cause is endpointing — Silero cut her off
after 700ms. A dangling-word check (capture ends on "for"/"to"/"and" → silently
reopen the mic ~2.5s and stitch, no spoken prompt) would fix it before she
hears a question at all. That is closer to what Google actually does. Left
until the spoken re-prompt is proven in the kitchen; it interacts with the
stitching path and is fiddlier.
