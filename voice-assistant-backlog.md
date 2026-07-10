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
- **Kid ages in captions (2026-07-10, immich-slideshow 77b769b).**
  Sweep syncs name→birthDate from Immich /api/people (only people with
  a birthDate set get ages — kids yes, parents no, nothing hardcoded;
  set/change birthdays in Immich and it flows through within the hour).
  Feed decorates names with age at photo time: newborn / "8 mo" /
  half-year steps to 3 ("1", "1½", "2", "2½") / whole years after.
  Server-side only; viewer untouched.

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
