# Speaker ID (backlog item 9) — Brad vs Adrienne for person-dependent intents

Decided 2026-07-27 with Brad. Routes reminders/to-dos to the actual
speaker's lists and resolves "my phone" in find-phone without the
ask-whose detour. Two-adult closed set, lazy path per the original
backlog sketch: identification runs ONLY after intent parsing yields a
person-dependent item, over the command WAV the orchestrator already
holds (`/command/audio`).

## Model decision

- **NeMo `titanet_large`** speaker-verification embeddings (192-dim),
  resident on the GX10. Both parakeet/sortformer worker containers
  already ship NeMo 2.8.0, so the service reuses the existing
  `gx10-sortformer-worker:latest` image with a command override — no new
  image build. Load verified in-container 2026-07-27.
- **Rejected:** Tempo's `Qwen/Qwen3-Embedding-0.6B` (`~/local-llm` on the
  GX10) — TEXT embeddings, wrong modality (embeds what was said, not who
  said it). Sortformer — end-to-end diarizer, no reusable enrollment
  embeddings. pyannote-3.1 (legacy diarize path in gx10_worker.py) — has
  a usable embedding stage but it's buried in a batch job pipeline.
- Verification-not-classification: enrollment = average ~25 labeled
  clips per person into a centroid; identify = cosine vs centroids with
  an absolute threshold AND a margin-over-runner-up threshold; anything
  else is "unsure". No training, re-enrollment is minutes if the channel
  ever changes.

## Hardware context (Brad, 2026-07-27)

- Fridge mic relocation CANCELLED — the XVF3800 stays next to the big
  speakers (future music-AEC project wants the playback reference
  there). So the kitchen channel is stable; enrollments stay valid.
- Family-room satellite (pw_pi) out of commission — power cycle
  corrupted its boot flash drive; SSD purchased for the rebuild.
  Kitchen-only enrollment now; add family-room-mic clips to the
  centroids after the rebuild (its ReSpeaker is a different channel).

## Components

1. **GX10 embed service** — `gx10-parakeet-asr` repo,
   `scripts/resident_titanet_server.py`, compose service `speaker-embed`
   (container `gx10-speaker-embed`, host port **8096**, sortformer image
   + command override, `NEMO_CACHE_DIR=/data/nemo-cache` persisted under
   `state/`). API: `GET /health`, `POST /embed` (raw 16k mono WAV body →
   `{"embedding": [...192 floats, l2-normalized], "seconds": ...}`).
   Scoring logic deliberately lives orchestrator-side; the GPU service
   stays a dumb embedder.
2. **Labeling** — kitchen satellite `data/clips` (80 `cmd-*` full
   commands + 125 `verify-ok` wake clips, all current-channel since the
   XVF3800 flip predates them) rsync'd to beelink
   `~/voice-pipeline/data/speaker_clips/`. Review page
   `voice-assistant/tools/speaker_label_server.py` (beelink :8790,
   phone-friendly, alarm-rings review-page pattern): play clip → tap
   Brad / Adrienne / kid / other / unsure-skip → appends
   `~/voice-pipeline/data/speaker_labels.jsonl`. Brad labels (~15 min).
3. **Enrollment + calibration** — `voice-assistant/tools/speaker_enroll.py`
   (stdlib-only, runs on beelink host python): reads labels + clips,
   embeds via :8096, per-person centroid from a train split, then a
   held-out eval printing the score matrix / DET-ish sweep → pick
   `SPEAKER_THRESHOLD` (absolute cosine) + `SPEAKER_MARGIN`. Writes
   `~/voice-pipeline/data/speaker_profiles.json` (derived biometrics —
   data dir, NOT git; hot-reloaded like home_commands.json).
4. **Orchestrator wiring** (build after enrollment data exists) —
   `speaker.py` `identify(wav) -> (name, score, margin) | unsure`;
   called only for reminder/to-do adds and find-phone `phone_owner=my`.
   SHADOW first (house rule since alarm-stop): log guesses+scores on
   person-dependent turns without routing; arm via env once the logged
   separation confirms the offline calibration. Unsure ALWAYS falls back
   to today's behavior (LIST_OWNER / ask-whose) — never guess; TTS names
   the resolved owner ("Added to Adrienne's reminders") as the audible
   correction path; dashboard badge later.

## Status

- [x] Design + model verified (titanet loads in sortformer container)
- [x] GX10 speaker-embed service live + smoke-tested (:8096, 12-17ms)
- [x] Clips synced + labeling page up (beelink :8791, left running for
      relabels)
- [x] Brad labeled all 205 (143 brad / 11 adrienne / 50 skip / 1 other —
      mixed Brad+kid clips deliberately skipped, centroid purity rule)
- [x] Enrollment + calibration 2026-07-27: impostor max 0.248 vs
      same-speaker min 0.396, ZERO misIDs → SPEAKER_THRESHOLD=0.35,
      SPEAKER_MARGIN=0.15. Profiles: brad 108 clips, adrienne 9.
- [x] Orchestrator shadow wiring DEPLOYED (0ad679b): speaker.py,
      SPEAKER_MODE=shadow scores every /command/audio turn →
      /data/speaker_shadow.jsonl; identify() verified in-container on
      labeled clips. 101 tests green.
- [ ] Shadow soak: review speaker_shadow.jsonl after a few days of real
      turns — especially KID utterances (zero kid clips existed, so the
      calibration never tested kids-as-impostors; expectation is they
      land "unsure") and Adrienne's thin enrollment (9 clips; fatten
      from shadow-logged turns or a 2-min session of her speaking
      commands, then rerun speaker_enroll.py — hot-reloads, no restart).
- [x] ARMED 2026-07-27 (Brad's call after his first live shadow turn
      scored brad 0.63 / adrienne 0.09): SPEAKER_MODE=active in the
      beelink compose (1fa262a). identify() starts concurrent with
      intent parsing; reminder/to-do adds file under the voice-resolved
      owner with the TTS naming non-shopping routing ("On Adrienne's
      list."), find-phone "my" resolves by voice with ask-whose below
      confidence. Both modes keep logging every turn to
      speaker_shadow.jsonl. Disarm = SPEAKER_MODE=shadow + up -d.
- [ ] Live-voice tests: Brad "remind me" routing; Adrienne at the
      kitchen mic tonight (the "beat Google voice match" check); kid
      utterance lands unsure. Dashboard badge still unbuilt.
- [ ] Post-SSD-rebuild: add family-room-mic clips to centroids
