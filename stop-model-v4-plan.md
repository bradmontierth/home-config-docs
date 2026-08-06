# Stop-model v4 plan — shorten the window, and fix the tempo mismatch

> **Status 2026-08-05 eve:** v4a/v4b both dead ends; the H1 "confirmed" verdict
> is RETRACTED (score-scale artifact, not better separation). **v2 is armed in
> production at 0.80.** The arm bar itself was the thing that was wrong — jump
> to *"The bar was wrong"* near the end for the current state.

Status: **PLAN ONLY, nothing built, nothing launched.** Written 2026-08-04 after
the v3 eval came back not-armable. Background and all prior numbers live in
`voice-assistant-backlog.md` item 3; this file covers only what comes next.

## Where v3 left us

Two rounds of background work (v2 ×10-duplicated attended bodies, v3
de-duplicated long unattended slices) landed in the same place: **not armable.**

- v3 did not beat v2 — 2-consec ceiling 0.784 vs 0.772, recall equal or worse
  above thr 0.45. The "×10 duplication overfit" premise is disproven.
- Real-ring backgrounds DID fix what they targeted: the periodic per-ding false
  fire (marimba 0.803 → 0.534, bubbling 0.416 → 0.278).
- **Recall is now the binding constraint, not the false-fire ceiling.** v2 and
  v3 never reach 60% recall at ANY threshold (both cap at 56%), and 5 of 16 real
  positives score <0.10 on all three models, capping recall at 69% before a
  threshold is even chosen.

So v4 must move RECALL. Another background run cannot.

## Two hypotheses, both measured, both cheap to test

### H1 — the 2s window is mostly ring (Brad, 2026-08-04)

The classifier answers "was 'stop' spoken in the last 2 seconds", but a real
spoken stop is nowhere near 2s long, so most of the window is masker.

Measured support:

- Real human stops are **180 ms median, 390 ms max** (11 real stops the model
  can find, anchored label-free on the model's own peak time).
- Context carries **+7.1 dB median** more energy than the word itself, up to
  +44 dB (label-free). The label-anchored figure is +21.8 dB but is inflated by
  known-bad onsets, so use +7.1.
- Duty cycle is the sharpest argument. Ring periods are 2.55–3.53 s with a 2.0 s
  gap, so **a 2.0 s window can NEVER be ring-free for any theme** — there is
  always at least part of a ding inside it. Shrink the receptive field to ~1.0 s
  and the window is completely ring-free **28–39%** of the time:

  | theme | ding | period | ring-free @2.0s | @1.2s | @1.0s |
  |---|---|---|---|---|---|
  | marimba | 0.55s | 2.55s | 0% | 31% | 39% |
  | bubbling | 0.94s | 2.94s | 0% | 27% | 34% |
  | steam_whistle | 0.81s | 2.81s | 0% | 28% | 36% |
  | oven_ding | 1.53s | 3.53s | 0% | 23% | 28% |

### H2 — the synthetic positives are ~3× slower than real speech (new)

Measured on 4,000 round-0 positive clips from the v3 run: synthetic speech spans
**p50 700 ms, p95 1330 ms**, against **180 ms median** for real human stops. The
model is trained almost entirely on enunciated, drawn-out "stop"s and then asked
to find a clipped 180 ms one under ring masking. `length_scales: [0.75, 1.0,
1.25]` biases long, and `target_phrases` includes "stop the timer", which alone
accounts for most of the >1000 ms tail.

H2 needs **no code changes at all** — it is a config edit — and it attacks the
binding constraint directly. It should be tested first, or in parallel.

## The architecture facts that constrain any window change

Verified in the installed package (same version deployed on .251):

```
inference/model.py:   EMBEDDING_WINDOW = 76   # mel frames per embedding (~760 ms)
                      EMBEDDING_STRIDE = 8    # 80 ms between embeddings
                      MIN_EMBEDDINGS   = 16   # classifier input length -> ~1.97 s
data/features.py:     N_EMBEDDING_TIMESTEPS = 16   # module constant, NOT config
models/classifier.py: ConvAttentionClassifier(n_timesteps: int = 16)  # never plumbed
                      :191 factory calls it without n_timesteps
config.py:56          ModelConfig exposes only model_type + model_size
```

Consequences:

1. **The 2-second window is not a design choice, it is the minimum legal
   input.** 16 embeddings need 76 + 15×8 = 196 mel frames ≈ 1.97 s. Feed
   `predict()` less and it returns a hard 0.0 — it does not degrade.
2. **760 ms is the floor on the receptive field.** One embedder window is
   76 mel frames. There is no way to show this architecture only 400 ms; a
   "look back 400 ms" model cannot exist in this stack at either level.
3. **`clip_duration: 1.0` in the YAML would have silently produced garbage.**
   `_pad_or_truncate` LEFT-PADS WITH ZEROS to reach 16, so training would have
   completed clean on mostly-zero inputs while inference (which never pads)
   fed 16 real embeddings. Same train/serve mismatch family as the 2026-07-25
   startup-transient bug. **Do not set clip_duration below 2.0.**

### Why the change is small anyway

`augment.py:109 align_clip_to_end` places positives at the END of the window
with 0–200 ms jitter. So "take the last N embeddings" keeps the word by
construction — `clip_duration` stays 2.0, augmentation and backgrounds stay
exactly as they are, and only the number of timesteps the classifier reads
changes.

And `MelSpectrogramFrontend` applies a fixed affine (`mel/10 + 2`), no per-clip
statistics, so a mel frame has the same value whether it came from a short clip
or the tail of a 2 s buffer. That is what makes train/serve equivalence
achievable here at all.

## v4a — tempo fix (config only, no fork, do this first)

`configs/stop_v4a.yaml` from `stop_v3.yaml`, changing only:

```yaml
target_phrases: ["stop", "stop it"]        # drop "stop the timer"
length_scales: [0.5, 0.65, 0.8, 1.0]       # was [0.75, 1.0, 1.25]
output_dir: /work/output/stop_v4a
```

Rationale: "stop the timer" spoken at the far mic is a normal ASR turn, not a
barge-in the model needs to catch, and it is the phrase that drags the duration
distribution past 1 s. Everything else — backgrounds, negatives, model size,
target_fp_per_hour — stays identical to v3 so the comparison is clean.

Risk: faster TTS may be less intelligible and teach a sloppier target. The eval
catches it; that is what the held-out real clips are for.

## v4b — short receptive field (needs a patched trainer + our own predict)

Only if v4a alone does not clear the bar, or run it in parallel as a second
overnight job.

**Parameter choice is a gated pre-flight decision, not a guess.** After the
`setup` step regenerates positives with the v4a phrase list, measure the
speech-span distribution and pick the smallest `n_timesteps` whose receptive
field ≥ p95 span + 200 ms jitter:

| n_timesteps | receptive field | fits (v3 phrase mix) |
|---|---|---|
| 4 | 1000 ms | 62% |
| 6 | 1160 ms | 75% |
| 8 | 1320 ms | 86% |
| 16 | 1960 ms | 100% (today) |

With "stop the timer" dropped the distribution shifts left, so 6 is the expected
answer and 4 the aggressive variant. **Do not pick a value that truncates the
front of >5% of positives** — that trains the model on partial words.

### Train-side patch (inside the disposable NGC container only)

Applied by `run_stop_v4b.sh` after `pip install`, never committed to the image:

1. `data/features.py` — `N_EMBEDDING_TIMESTEPS = <N>`
2. `models/classifier.py:191` — pass `n_timesteps=<N>` to the factory
   (this also changes the baked `nn.LayerNorm([layer_dim, n_timesteps])` shapes
   and therefore the exported ONNX input signature)
3. Assert after patching that both values agree, and assert the model name is
   `stop` — so a future stage-1 retrain can never inherit this patch.

### Serve-side (satellite) — isolated from stage-1 by construction

`assistant.py` already builds separate instances: stage-1 at `:1097` and `:1376`
(`WakeWordModel(models=MODEL_PATHS)`), stop at `:1383`
(`WakeWordModel(models=[STOP_MODEL_PATH])`).

**Never patch site-packages on the satellite** — that would change
`MIN_EMBEDDINGS` for every model on the box, and both stage-1 heads are trained
at 16. Instead add a ~15-line `stop_predict()` in `assistant.py` that reuses the
library's own front-end objects and slices the sequence:

```python
n_ts = classifier_session.get_inputs()[0].shape[1]   # 16 today, 4 or 6 for v4b
emb_sequence = embeddings[-n_ts:]
```

Reading `n_ts` from the ONNX input shape rather than hardcoding it means the
same code path serves stage-1 and any stop model, and a mismatched pair fails
loudly at load instead of scoring silently wrong. The audio buffer
(`WINDOW_SAMPLES`), the hop, and stage-1 all stay unchanged.

This does reach into `WakeWordModel` internals (`_mel_frontend`,
`_speech_embedding`, `_classifiers`), which are private. Pin the package version
in the satellite venv and assert those attributes exist at startup.

## Pre-flight gates (before any GPU time)

0. **H2 mechanism pre-test** (no GPU, minutes, on .251): time-stretch the 5
   dead real positives 2–3× slower (pitch-preserving, ffmpeg atempo) and
   rescore with v1/v2/v3 via the faithful replay. If the dead clips come alive
   when slowed to synthetic tempo, H2's mechanism is confirmed before any
   training. A null result is inconclusive (stretch artifacts could mask it),
   but a positive result de-risks the v4a bet. Script:
   `wake-bench/h2_pretest/pretest_h2_stretch.py` on .251.

   **RESULT (run 2026-08-04, wake-bench/h2_pretest/results.txt): H2 confirmed
   for 2 of the 5 dead clips, refuted-or-inconclusive for 3.**
   - `132842` revives dramatically: 2-consec 0.05 → **0.72/0.70/0.59**
     (v1/v2/v3) at 0.7–0.5×. This stop was simply spoken too fast for the
     model. Strongest possible pre-training evidence for H2.
   - `132915` partially revives: c1 0.02 → 0.44, 2-consec up to 0.19–0.24 at
     0.5–0.4×.
   - `132818` and `132948` stay dead at every tempo — their killer is not
     tempo (masking/SNR/pronunciation; consistent with H1, since stretching
     does not remove ring overlap).
   - `131830` gets WORSE when stretched (c1 0.66 → 0.06): its word already
     peaks in one window but never sustains two — a boundary/sustain problem,
     not tempo.

   Read: v4a's mechanism is real but buys ~1–2 of the 5 dead clips at best;
   the remaining dead clips need the window change (v4b) and/or real capture
   data. Run v4a AND v4b, not v4a alone.
1. **Duration fit** (v4a AND v4b): regenerate positives, measure the span
   distribution. For v4b, pick `n_timesteps` by the rule above; abort if no
   value ≤8 fits ≥95%. For v4a, this same measurement gates the run: shifting
   mean length_scale from ~1.0 to ~0.74 lands p50 near ~500 ms, still ~3× the
   180 ms real median — if the regenerated p50 is still far above real tempo,
   push length_scales lower (or add post-TTS time-compression) before spending
   the night.

   **RESULT (2026-08-05): PASS at n_timesteps=4, but only after fixing the
   measuring instrument.** The first gate run aborted rc=3 on p95 = 1960 ms.
   That was not the tempo — `positive_train` holds each clean TTS clip plus
   three augmented variants (`clip_NNNNNN_rN.wav`) that are already aligned to
   2.0 s with room impulse and background noise mixed in, and the energy rule
   latched onto their noise floor (threshold pinned to the 5th-percentile floor
   in 35% of augmented clips vs 5% of clean ones), reporting the whole clip as
   "speech". `measure_spans.py` now excludes the `_rN` variants. Reverb tails
   are real energy too, and the model does not need to see them.

   Clean-clip spans, 4,000 sampled: p50 **310 ms**, p90 490, p95 **560**, p99
   710, max 930. Against v3's p50 of ~700 ms this is the tempo fix landing —
   and 310 ms is finally within reach of the 180 ms real median. Fit: n=4
   (rf 1000 ms) 99.8%, n=6 100%, n=8 100% → gate picks **n_timesteps = 4**,
   halving the receptive field from 1970 ms.
2. **Train/serve equivalence** (v4b): take ONE real ring clip, compute features
   the training path's way and the satellite's way, and assert the embedding
   arrays match to within float tolerance. This is the check that would have
   caught the zero-padding trap; do not skip it.
3. **ONNX shape**: confirm the exported v4b classifier input is `(1, N, 96)`,
   not `(1, 16, 96)`.
4. GX10 free disk (~15 G per run) and the `--runtime nvidia` flag — the two
   launch gotchas that have each cost a night already.

## Eval — unchanged, which is the point

`training/eval_stop_v3.py` on .251, extended with a `--n-timesteps` aware
predict. Same 27 clips, same faithful replay, same steady-state-only rule, same
bar agreed 2026-08-03:

> arm only if some threshold ≤0.90 clears the 2-consec ceiling across the four
> held-out long unattended rings by ≥0.15 AND keeps ≥60% 2-consec recall at the
> spoken stop.

Report v1/v2/v3/v4a/v4b side by side. **Watch for the counter-hypothesis:** less
context could make a bare ding onset MORE confusable, pushing the ceiling up.
If v4b's ceiling rises while recall also rises, that is a real trade to weigh,
not a failure.

## What would falsify each hypothesis

- **H1 false** if v4b's recall at the spoken stop does not improve over **v4a**
  at matched false-fire ceiling. v4b is built on v4a's phrase list and length
  scales, so v4a — not v3 — is the only baseline that isolates the window;
  comparing v4b to v3 confounds the window change with the tempo change.
- **H2 false** if v4a's recall does not improve over v3. Then the
  synthetic/real tempo gap is not what is losing the 5 dead positives.
- **Statistical caveat on both:** 16 real positives means recall moves in
  6.25% steps — the 56% vs 60% gap between today's ceiling and the arm bar is
  ONE clip, and "recall did not improve" is a ±1-clip coin flip. The capture
  session below fixes this and should land before conclusions are drawn.
- If BOTH come back flat, stop retraining. The remaining lever is real
  multi-voice spoken-stop positives (below), and after that, accepting that the
  ASR + bias path is the dismissal mechanism and retiring the model.

## RESULTS (2026-08-05 morning) — H2 falsified, H1 *apparently* confirmed (later retracted)

Both runs trained and were evaluated on .251 by the identical faithful-replay
harness over the same 27 clips (v1–v4a in `eval-v4a-20260805.json`, v4b in
`eval-v4b-20260805.json`).

| model | ring ceiling (c2) | recall above that ceiling | best margin @ thr | verdict |
|-------|------------------|---------------------------|-------------------|---------|
| v1    | 0.935 | 0/16  (0%)  | +0.015 @ 0.95 | not armable |
| v2    | 0.772 | 4/16  (25%) | +0.078 @ 0.85 | not armable |
| v3    | 0.784 | 2/16  (12%) | +0.066 @ 0.85 | not armable |
| v4a   | 0.911 | 1/16  (6%)  | +0.039 @ 0.95 | not armable |
| **v4b** | **0.387** | **5/16 (31%)** | **+0.163 @ 0.55** | not armable |

**H2 is FALSIFIED.** v4a's recall did not improve over v3 (1/16 vs 2/16 — a
±1-clip coin flip either way, so read it as flat, not as a regression). But the
ring ceiling moved decisively the WRONG way, 0.784 → 0.911. Matching real tempo
made the model *more* confusable with the alarm, which is mechanically sensible
in hindsight: a fast "stop" is a short energy burst, and so is a ding. The
H2 pre-test had capped the upside at 1–2 clips; it did not even deliver that.
Do not pursue tempo again.

**H1 is CONFIRMED, and it is the most effective change across four model
generations.** Halving the receptive field (1970 ms → 1000 ms, n_timesteps
16 → 4) more than halved the ring ceiling, 0.784 → 0.387, while *also*
producing the best real-stop recall of any model. v4b clears the margin bar
(+0.163 at thr 0.55, vs a 0.15 requirement) — the first model ever to do so.
Every earlier model failed BOTH criteria; v4b fails only one.

> **RETRACTED the same evening — see "The bar was wrong" below.** This
> conclusion compares models at *fixed thresholds*. At matched false-positive
> rate v4b is **not better than v2**: v4b @ 0.15 and v2 @ 0.65 catch the exact
> same four clips, and v4b's median score on a true stop is 0.07 against v2's
> 0.52. `n_timesteps=4` compressed the score scale; it did not improve
> separation. The falling "ring ceiling" was the positives being squashed
> alongside the ring, which the ceiling metric cannot see because it only
> looks at negatives. **H1's real status is "not demonstrated" — the
> experiment could not distinguish a better separator from a rescaled one.**

**Still NOT ARMABLE, and the blocker is now purely recall.** The bar needs
margin ≥ 0.15 and recall ≥ 60% at the SAME threshold. v4b gives margin +0.163
at thr 0.55 with recall 19%, or recall 31% at thr 0.40 with margin +0.013.
Nothing reaches 60%. Note v4b's scores are compressed downward overall — it is
a less confident model in absolute terms, but a far better *separator*, which
is the only thing the arming rule cares about.

**Recall is a data problem, not a modeling problem.** Across four generations
and three orthogonal interventions (background composition v3, tempo v4a,
receptive field v4b), no model has exceeded 31% recall on real spoken stops.
The one lever never pulled is real spoken-stop positives — every model here
learned "stop" from TTS and is asked to find it under a loud ring at room
distance. The capture pre-step below is now the critical path; a v5 without it
would be a fifth guess.

### Engineering note: `n_timesteps` is baked into SIX places

The architecture section above understated this. Changing it required patching
all of the following (container-only, disposable):

1. `data/features.py` — `N_EMBEDDING_TIMESTEPS = 16`
2. `models/classifier.py` — factory never passes `n_timesteps`
3. `data/dataset.py` — the shipped 16-timestep feature banks
   (`openwakeword_features_ACAV100M_2000_hrs_16bit.npy`, 17 GB, 1024 of ~1124
   examples per batch) are frozen artifacts that regeneration never touches;
   sliced to the last N at the mmap load site (a lazy view, no RAM cost)
4. `training/trainer.py` — `_load_validation_data`, which is DUPLICATED in (5);
   this one fires only in the final training quarter, so missing it costs a
   full 13-minute train before it crashes
5. `eval/evaluate.py` — the duplicate of (4)
6. `export/onnx.py` — `dummy_input = torch.randn(2, 16, 96)`

Slice the shared banks to the last N rather than `reshape(-1, N, 96)`: the
reshape would keep all the audio and yield 4× the negatives, but it would also
change the negative pool size between v4a and v4b and confound the very
comparison the run exists to make. Last-N holds the pool at 46,584 (verified
identical to v4a) and is the exact slice the satellite takes at serve time.

Scripts: `wake-train/resume_stop_v4b.sh` on the GX10 carries all six patches
plus a pre-flight that calls `_load_validation_data()` directly, so (4) now
fails in seconds instead of 13 minutes.

### Serve-side work v4b would still need

`inference/model.py` hardcodes `MIN_EMBEDDINGS = 16` and returns 0.0 below it,
so the satellite CANNOT run a 4-timestep model as shipped. `eval_stop_v4.py`'s
`NTsModel` reimplements the correct predict (read N from the ONNX input shape,
slice the embedding sequence to the last N) and is verified equivalent to the
library path for 16-timestep models — that wrapper is the reference for the
satellite patch, if v4b's successor ever earns deployment.

## Capture session — promoted to a pre-step (2026-08-04 review)

> **Partly done 2026-08-05.** 23 clips labelled via a purpose-built multi-mark
> labeller (`ring-labeler/labeler.py`, :8792) → 46 marked stops across 35
> positive clips; `ALARM_RING_KEEP=200` is live so nothing evicts. The
> positional half was then superseded by `stop_trial.py`, which labels position
> and theme at capture time. **Still outstanding: Adrienne and the kids** —
> every clip to date is Brad, and they are the actual use case.
>
> Offline re-transcription (`transcribe_rings.py`, `window_asr.py`) settled the
> slicing question that motivated part of this: full-clip batch ASR finds 41%
> of marked stops, windowed decode 49%, union 62%, live 65%. Long-form decoding
> DROPS short utterances, so batch is not an upper bound and **slicing helps**.
> There is no headroom in the slicing.

Real spoken-stop clips from more than one voice — Adrienne and the kids were
never captured. Originally deferred as a training-data lever; promoted because
its **eval value alone** justifies doing it first: at 16 positives the arm bar
has one-clip resolution, and every GPU-night comparison after the eval set is
doubled becomes a measurement instead of noise. (Training on real positives
remains separate future work.)

Logistics — the pipeline already does the capturing:

- `assistant.py` records EVERY ring to `data/alarm_rings/ring-*.wav` on .251,
  from alarm start until dismissal — so a voice-dismissed ring contains the
  barge-in "stop" by construction. **Dismiss by voice, not by tap/phone**, or
  the clip has no stop in it.
- Retention was `ALARM_RING_KEEP=40` and the directory was full at exactly 40,
  so every new ring evicted the oldest capture. Raised to 200 in the satellite
  `.env` 2026-08-04 (applies at next restart; restart resets mode→shadow) and
  the existing 40 archived to the Beelink at `/home/pi/alarm-rings-archive/`.
- ~15 captures from Jul 26–Aug 4 are already on disk and UNLABELED (the eval
  manifests stop at 2026-07-28) — including several days of real-world stop
  use with kid noise. Label these before recording more.
- Labeling = one manifest row per clip (file, label, stop_start, notes), same
  format as `real-rings-manifest-20260728.csv`, then pass the new manifest to
  `eval_stop_v3.py`.
- Session shape: set timers, let them ring into steady state, say "stop" from
  varied positions/distances; get Adrienne and each kid to do several; include
  some with music/TV/kid-chaos running.

## The bar was wrong (2026-08-05 evening) — v2 ARMED in production

Brad, after four generations of not-armable verdicts: *"we're going in circles
and chasing our tails. In my mind it's really not that hard — all we need is a
wakeword model. It doesn't even have to be very good. It can have false
positives. I just need it to cancel the timer without having to repeat myself
three or four times."*

He was right, and three separate errors were driving the circling.

**Error 1 — the arm bar was a wake-word bar.** Margin ≥ 0.15 over the ring
ceiling AND recall ≥ 60% is the standard for a model that listens all day and
must never fire on the house. This model is scored *only* inside
`if STATE.current_alarm is not None` — it is deaf unless something is ringing.
The two false-positive kinds are not equally bad and the single bar conflated
them:

- **Self-dismiss** — the ring scores high before anyone speaks, the alarm
  cancels itself, nobody learns the timer went off. Genuinely bad, must be rare.
- **Trigger-happy** — fires on other speech while ringing. Someone was in the
  room talking; an alarm they already know about stops early. Mild, and Brad
  explicitly accepted it.

The ceiling metric is also a MAX over a handful of holdout rings, so one
unlucky window in one clip vetoes every threshold beneath it.

**Error 2 — comparing at fixed thresholds instead of matched FP rate.** See the
retraction above. v3 and v4b never beat v2; three GPU nights went into
rediscovering v2's separation with a different score scale.

**Error 3 — hunting an acoustic mechanism for a problem 5× smaller than
assumed.** Ring capture opens at alarm start and closes at dismissal, so the
mark count in a clip IS the number of times a human had to speak:

| stops needed | clips | |
|---|---|---|
| 1 | 28 | 80% — already one-and-done, ASR only |
| 2 | 4 | 11% |
| 3 | 2 | 6% |
| 4 | 1 | 3% |

A model's entire addressable gap is the **11 spoken-and-ignored stops**;
everything else it "catches" is redundant with ASR. Before this was measured, a
mic-relocation was proposed off an uncontrolled comparison with n=6 in the
deciding bucket. Brad killed it with one observation: *"okay computer" works
fine from every corner of the room*, clearing a two-window bar. Far-field
pickup was never the problem. Geometry, AEC and ducking are all closed.

### Eval/serve mismatch found while re-scoring

`assistant.py:1510` fires on a **single** window over threshold. Every eval in
this project used c2 (two consecutive). c2 is strictly harder, so every recall
number here understated what production would do — it suppressed recall far
more than it suppressed false fires:

| v2 @ 0.80 | self-dismiss | repeats fixed |
|---|---|---|
| shipping rule (1 window) | 2/43 alarms | 5/11 |
| 2-consecutive rule | 1/43 | 3/11 |

### Live positional harness (`ring-labeler/stop_trial.py`)

Fires alarms straight at the satellite's `/alarm`, records position + theme +
outcome at capture time, so the corpus is labelled by construction instead of
by a human afterwards. `timer_id` is deliberately omitted — the unattended
watchdog only starts `if req.get("timer_id")`, so a 20-alarm run doesn't push
20 escalations to the household phones. Silent trials (nobody speaks) are the
only way to measure precision.

Two findings the harness produced about itself:

- **Only 4 ring sounds exist.** The orchestrator advertises 7 themes but the
  satellite ships 4 WAVs; `theme_sound()` silently falls back to marimba for
  cluck/moo/sizzle. marimba is therefore the most common sound AND the one
  closest to the threshold (peaks 0.672, 0.712). `steam_whistle` was dropped
  entirely per Brad — the sample is a human whistling, not a kettle.
- **A prompt bug invalidated the first run.** A spoken trial landing right
  after a silent one still got the terse "Again." prompt, which reads as *be
  silent again*; Brad stayed quiet and four spoken trials recorded as detection
  failures that were nothing of the kind. All four timeouts correlated exactly
  with following a silent trial. A theme effect had been narrated on top of
  that artifact before the correlation was checked. Fixed by building the whole
  running order up front and never phrasing a prompt relative to the one before
  it; a plan-printer asserts zero ambiguous prompts before any alarm fires.

### Results — 18 trials, 5 positions × 3 themes

| | |
|---|---|
| pure-ring peaks (silent, nobody in room) | marimba 0.672, oven_ding 0.318, bubbling 0.165 |
| **ASR alone** | **15/15** — every position, every theme |
| **model alone @ 0.80** | **13/15** |
| self-dismiss | 0 (but only 45s of pure ring) |

**v2 armed at 0.80 on .251 2026-08-05 21:22** (`STOP_MODEL_PATH=stop_v2.onnx`,
`STOP_THRESHOLD=0.80`, `STOP_LOG_THRESHOLD=0.05`; original at
`.env.bak-20260805-stopv2`). The best model we owned had been in the drawer
since 2026-07-25 while three worse ones were trained against a metric that
punished it for having a wide score range.

### The harness measures the easy case

First three real timers, live:

| | model | outcome |
|---|---|---|
| 1 | peak 0.539 | **miss** — `'Have any issues? Stop.'` |
| 2 | 0.919 | crossed, 82 ms behind ASR |
| 3 | 0.941 | **fired first**, 178 ms ahead |

The miss is the important one. *"Say stop when it rings"* reliably elicits an
**isolated** utterance, which scores 0.85–0.98. Real dismissals are the tail of
a sentence, and a 2s window shared with ~1.6s of other speech collapses the
score to 0.539. **13/15 measures a case that doesn't happen much.** Same class
of error as the prompt bug: a harness built to produce the clean case.

Also corrected: the harness's "2.1s average saved" compared the model's journal
timestamp against ASR's HTTP-poll timestamp — different clocks, ~250ms of poll
lag baked in. On one clock they are within ~200ms. The model's value is not
per-utterance speed; it is the multi-repeat cases.

### Verdict and next

Run `ring-labeler/stop_report.py` after a day or two of real use. **SELF-DISMISS
> 0 is the only number that forces a rollback** (raise to 0.90, still fixes the
fridge/family repeat cases). Everything else is upside.

- ASR misheard "stop" as `'So'`, `'So'`, `'So'`, `'Stuff.'` — the final plosive
  dropping is the same signature seen in the July corpus. During a ringing
  alarm the dismiss vocabulary can afford to be loose. **Unqueued.**
- The family-room satellite **cannot participate**: `SATELLITE_ALARM_URL` is
  singular (kitchen only), so `current_alarm` is never set there and the stop
  branch never runs. Arming it is a no-op. Not worth the plumbing — the kitchen
  mic went 3/3 on the family-room position.
- Real-positive training (below) is still the only untried lever, and now has
  18 position-labelled clips plus 9 salvageable from the aborted run.
- `parakeet` leaks `Hypothesis(score=0.0, y_sequence=tensor([]...` into
  `transcript_text` on empty decodes instead of `""`. Anything doing
  `if transcript:` treats silence as speech. Reported, unfixed.
- ASR hallucinates on unattended ring audio (`'Where are you?'`, `'Really?'`,
  `'Seriously.'`). If it ever hallucinates a dismiss word the alarm self-cancels
  through a path with no threshold to tune.
