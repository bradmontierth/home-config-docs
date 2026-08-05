# Stop-model v4 plan — shorten the window, and fix the tempo mismatch

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

1. **Duration fit** (v4b): regenerate positives, measure p95 span, pick
   `n_timesteps` by the rule above. Abort if no value ≤8 fits ≥95%.
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

- **H1 false** if v4b's recall at the spoken stop does not improve over v3 at
  matched false-fire ceiling. Then window length was not the limiter and the
  masking is coincident after all.
- **H2 false** if v4a's recall does not improve. Then the synthetic/real tempo
  gap is not what is losing the 5 dead positives.
- If BOTH come back flat, stop retraining. The remaining lever is real
  multi-voice spoken-stop positives (below), and after that, accepting that the
  ASR + bias path is the dismissal mechanism and retiring the model.

## Still not in scope, still the other lever

Real spoken-stop positives from more than one voice — Adrienne and the kids were
never captured. The trainer builds positives from synthetic TTS, so real clips
are eval-only today; using them as training data is a separate piece of work.
This is a capture session, needs no code, and directly targets recall. v4a/v4b
do not replace it.

## Open questions for Brad

1. Run v4a alone first (safe, config-only, ~2 h), or v4a and v4b back-to-back
   overnight (~4 h) to get both readings at once?
2. Is dropping "stop the timer" from the target phrases acceptable? It becomes
   an ASR-path dismissal only, which is what it already is at the far mic.
3. v4b puts custom inference code in the alarm path, reaching into library
   privates. Worth the maintenance burden, or hold it until v4a reports?
