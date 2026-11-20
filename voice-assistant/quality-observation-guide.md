# Kitchen / family-room microphone quality observation

Deployed 2026-09-08. Review: http://192.168.10.217:8785/quality/ui

**Automatic transcript selection enabled 2026-09-08 after the first labeled
review, at Brad's request.** The first verified wake retains reply/session
ownership, but command text and speaker-ID audio can now come from the better
paired Cohere recording. Both recordings, labels, and actual selection decisions
remain logged. See the active selection section below. Earlier observation-only
implementation notes describe the first rollout.

## Real-world test

Open the review page. Use a fresh “okay computer” wake for each trial, allowing
the previous follow-up listen to finish before starting the next one. Try the
same question a few times from each position:

- Kitchen, fan off.
- Kitchen, fan on (note speed and position, e.g. sink or stove).
- Family room, fan off/on.
- Either room with music playing.

Refresh after the answer. Each paired turn has the original winner, both Cohere
transcripts/scores, recordings, timing difference, and experimental score ranking.
The actual executed command/model is shown separately when available. Listen to
both recordings and save the expected winner with conditions in the notes.
“Either” and “neither” are available. If one mic never wakes/captures, the pair
will be incomplete; missing/invalid scores never produce a ranking.

A higher score is closer to zero. It is the mean log probability of decoded
non-special tokens, NOT a calibrated probability of correctness or a room
location estimate. We need labeled kitchen/family-room trials before deciding
on a switching margin. Transcript agreement is diagnostic only, particularly
for similar strings with different numbers or negations. No 300 ms selection
wait is enabled yet.

## Implementation

- `MIC_QUALITY_OBSERVE=1` in `/home/pi/voice-pipeline/docker-compose.yml`.
  Defaults off in source; scoped to kitchen/familyroom cold stitched wake turns.
- Suppressed `/verify` responses include the immutable primary `arb_turn_id`
  and the suppressed mic's own `turn_id`. Both satellites send these IDs on
  `/command/shadow`. The server validates the source rows, rooms and age
  (180 seconds), so late uploads cannot attach to a newer arbitration holder.
- The existing primary Cohere hedge requests `confidence=true`; its actual
  response supplies the observation, with no extra primary inference.
  Parakeet fallback/partials/verification retain their existing behavior.
- A paired shadow makes one Cohere request, capped at 1500 ms, with the same
  ASR profile/settings. Errors/timeouts are recorded without retries or
  command execution. Legacy/unpaired shadows retain their old Parakeet path.
- Cohere's running default is **greedy, no bias boost**, not the beam-4 decode
  assumed in the September 4 plan. Its optional score uses Transformers
  `output_scores` and `compute_transition_scores(normalize_logits=True)`,
  excluding tokenizer special IDs. Method: `mean_token_logprob_v1`.
  Empty, looped/collapsed or token-limit outputs are invalid for ranking.
  Reference: https://huggingface.co/docs/transformers/main/en/main_classes/text_generation
- Model, score method, decoder and bias settings must match for a comparison.
  Full hypothesis scores include any decoded wake phrase. Whether this masks
  command-specific errors is part of validation; no guarantee of correctness
  is inferred from a confident score.
- The kitchen stitches ch0 wake pre-roll to ch1 command audio. A ch0 floor
  subtracted from ch1 speech would be misleading. The optional acoustic
  diagnostic instead skips the 2.5-second pre-roll and reports command-channel
  p90-minus-p20 50 ms energy levels. The page calls this **level contrast**,
  not calibrated SNR. Music, continuous speech, and AGC can confound it.
- `mic_quality` and `mic_quality_labels` tables live in the existing
  `/home/pi/voice-pipeline/data/orchestrator.db`, keyed by primary turn ID.
  Audio is in `data/mic_quality_audio/`; recordings older than 14 days are
  removed on the next observation write. Text, scores and labels persist.
  SQLite/audio writes run in background threads off the response path.
- `/quality/recent`, `/quality/ui`, `/quality/audio/{name}` and
  `POST /quality/{turn_id}/label` provide review. Refresh is manual so playing
  audio or unsaved labels are not interrupted.

GX10 canonical server: `/home/pi/gx10-asr-candidates/app/candidate_asr_server.py`.
The source change is preserved here as `tools/cohere-confidence.patch` (apply
with `patch -p1` from the GX10 project directory on the pre-change version).
The server image was rebuilt and only the `cohere` compose service recreated.
Kitchen satellite uses the repository source. Family-room had an older mono
capture version; only its shadow-upload ID lines were patched, preserving its
existing capture code.

## Validation and limits

- 12 saved clips, paired plain/scored GPU inference: unchanged transcripts on
  all 12; valid scores on all 12; median 83.2 ms plain / 81.0 ms scored. Small
  sample, not evidence of a speedup.
- Six two-request pairs each, alternating scored/plain on the live HTTP service:
  both-ready median 163.2 ms plain / 170.5 ms scored; maximum scored 177.3 ms.
  The service serializes GPU inference. This was an idle-system check, not a
  local-LLM-under-load benchmark. Scores did not change the transcripts.
- 63 targeted tests passed (including eight quality tests). Full suite:
  546 tests, 544 passed, one skipped, one pre-existing home-control matcher
  failure (`turn on the ac` matches kitchen cans). The failure was reproduced
  in the pre-change deployed image and is outside this change.
- Live smoke: primary hedge + paired shadow, score persistence, both audio
  downloads, label save/readback, and review page all passed. Temporary smoke
  rows and recordings were removed; no command/chime/reply was executed.
  A first cold request took the existing Parakeet fallback and correctly
  recorded unavailable Cohere evidence; a subsequent run had both scores.
- Both satellite services healthy/active after deployment; volumes retained
  (kitchen 60, family room 50). First five labeled pairs reviewed on September 8:
  [review findings](quality-review-2026-09-08.md). Broader room/noise validation
  and threshold calibration remain pending.

## Rollback

Set `MIC_QUALITY_OBSERVE=0` in the operational compose and run
`docker compose up -d --no-deps voice-orchestrator` from `/home/pi/voice-pipeline`.
This restores the old shadow decode path immediately while leaving existing
observations reviewable. Extra satellite response/upload fields are harmless.

Pre-change images were tagged `voice-pipeline-voice-orchestrator:before-quality-20260908`
and (on GX10) `gx10-asr-candidates:before-quality-20260908`. Satellite source
backups are `~/voice-pipeline/assistant.py.before-quality-20260908` on each box;
GX10 server backup is `app/candidate_asr_server.py.before-quality-20260908`.

## Kitchen chirp/upload overlap — deployed 2026-09-08

The first labeled tests exposed a separate delay: the kitchen completed capture,
played `vad.wav` synchronously, then uploaded the command. The WAV is 395 ms;
capture-end to server receipt was 598/601 ms in the two family-room tests.
Family-room shadows, which play no chirp, uploaded in 10/12 ms.

Kitchen wake and dashboard-button commands now use `post_command_with_chirp`:
reserve the local playback lock, start the existing HTTP upload in a worker,
play the chirp on the normal playback path, release the lock, then await the
HTTP result. ASR and command handling can overlap the chirp; immediate local
or server-pushed replies still wait behind it. The lock is reentrant so the
ordinary playback helper can run under the reservation. It must be released
before waiting on HTTP, because the server can await a `/speak` callback.

Empty captures retain their chirp; follow-ups have no completion chirp and
remain unchanged. Relayed playback retains its existing serial behavior since
this process cannot reserve the remote speaker lock. Only the kitchen satellite
was deployed for this latency change. Quality selection remains observation-only.

Five concurrency tests pass: overlapping upload, early reply ordering,
server-pushed reply without HTTP deadlock, HTTP failure/lock release, missing
sound, and relay behavior (early reply/overlap share one test). Run
`python3 voice-assistant/satellite/test_vad_overlap.py` from the repository root.
Source compilation and post-restart health/mode/volume checks passed. Actual
end-to-end response savings await the next live turns; the expected removed
serial delay is roughly 0.6 s, not a guaranteed 0.6 s off every reply.

The satellite logs `command upload starts with vad chirp (dispatch_delay=...ms)`.
Compare that timestamp and the server's observation `received_at` with the
preceding capture-end log. Earlier shadow-ready advantages were measured BEFORE
this fix and must not be used as the expected post-fix timing distribution.

Rollback on the kitchen mini PC: restore
`~/voice-pipeline/assistant.py.before-vad-overlap-20260908` as `assistant.py` and
restart `voice-assistant.service`. The backup includes quality observation IDs.

## Active selection — deployed 2026-09-08

`MIC_QUALITY_SELECT=1`, `MIC_QUALITY_WAIT_MS=150`, `MIC_QUALITY_MARGIN=0.01`
in the operational compose. Observation remains enabled. Only kitchen/familyroom
cold stitched wakes participate. After the original final ASR completes:

1. Keep Parakeet fallbacks and rejected/missing primary Cohere evidence.
2. If no validated suppressed peer joined this turn, continue immediately.
3. Use an already-completed shadow immediately; otherwise wait up to 150 ms.
4. Compare only valid scores from matching model/decoder/bias configurations.
5. Exact normalized command agreement retains the original. Fuzzy similarity
   does not skip comparisons: numbers and negations may differ by one token.
6. On disagreement, select shadow only when its mean token log-prob exceeds
   primary by more than 0.01 and the existing repetition/length sanity checks
   pass. This initial household tuning catches today's 0.0238-gap mishear;
   it is not a calibrated guarantee of accuracy. Level contrast is logged only.
7. The primary handler executes once using the selected transcript/audio for
   command handling and speaker ID. Reply routing, room defaults and follow-up
   session ownership remain with the original satellite. The shadow handler
   never dispatches. Late evidence is logged but cannot revise the decision.

Selection uses an event-loop cache keyed by immutable turn ID, with 32 entries
and 180-second expiry. Disk writes are off the command path. Completed decisions
seal their cache entry; only small markers remain until expiry. Decisions live
in `mic_quality_decisions` and `/quality/recent` as `selection`: original owner,
selected mic/text, reason, rule/margin, wait duration/cap and score comparison.
The review page distinguishes the actual selection from retrospective ranking,
which may include a shadow that arrived too late to use. Existing primary ASR
latency/fallback fields keep their original meaning; selected shadow ASR latency
is in the decision record.

78 targeted tests passed, covering corrected dispatch and chosen speaker audio,
observation mode, existing hedge/wake behavior, late shadows, timeout, no peer,
small margins, agreement and fallback. The replay fixture reproduces the saved
“What gains it?” / “What day is it?” score pair. No extra ASR inference or LLM
judge is introduced. The first post-chirp-fix real pair's shadow finished 88 ms
after primary; the 150 ms cap covers that observed case without restoring the
old roughly 600 ms serial chirp delay. Real-world decisions remain monitorable.

Rollback to observation: set `MIC_QUALITY_SELECT=0`, then
`docker compose up -d --no-deps voice-orchestrator` from `/home/pi/voice-pipeline`.
Pre-picker image: `voice-pipeline-voice-orchestrator:before-picker-20260908`.
