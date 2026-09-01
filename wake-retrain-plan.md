# okay_computer v2 — retrain on the real corpus

Drafted 2026-08-31 after the range-hood miss. Status: **PLAN, nothing trained.**
Companion notes: `voice-assistant-backlog.md` (stop-model retrain post-mortem),
`voice-assistant/tools/bi_wake_probe.py` (camera-audio probe),
`voice-assistant/tools/sync_wake_clips.sh` (nightly corpus sync).

## 1. Why the current model has to go

The deployed `okay_computer.onnx` (trained 2026-07 on the GX10) saw **zero real
audio**: 25,000 Piper-TTS positives, 2,000 synthetic backgrounds, generic
ACAV100M speech negatives. Both of its failure modes are domain mismatch:

| Failure | Evidence |
|---|---|
| **Misses real voices under real noise** | 2026-08-31 17:23:44: Adrienne, range hood on. Kitchen mic stage 1 <0.3 (not even a near miss); Blue Iris camera audio of the same second scored 0.17 — and **≤0.34 at every gain from −12 to +18 dB** — while Parakeet (stage 2) transcribed "Okay computer." cleanly. Level is not the problem; the model is. |
| **Fires on anything** | Kitchen stage-2 pass rate 4.2%: of ~6,300 kitchen fires, 3,980 had **no speech at all** (`empty`) and 2,087 were other speech (`low_score`). 96% of triggers are false. |
| Marginal on the people who use it most | Verified-wake stage-1 scores: Adrienne median 0.83 / p10 0.59, Brad 0.89 / 0.67. 41 of 396 verified wakes squeaked in under 0.6. Kitchen logs ~85 near misses/day (0.3–0.5). |

Interim mitigation, live since 2026-08-31 20:45: kitchen `TRIGGER_THRESHOLD`
0.50 → **0.40** (`~/voice-pipeline/.env` on .251, not git). Costs ~34 extra
stage-2 ASR calls/day; Parakeet gatekeeps false chimes. It cannot reach the
<0.3 misses — only a new model can.

## 2. What the corpus gives us (as of the 2026-08-31 03:45 sync)

`/home/pi/wake-corpus/<sat>/clips/` on the Beelink, mirrored nightly to
`dgx:/home/pi/wake-corpus`, with `orchestrator-snapshot.db` as the join key
(`stage1_score`, `wake_model`, `speaker`, `reject_reason` live only there).
Every clip is the 2.5 s stage-1 pre-roll at 16 kHz mono; the phrase sits at
the END of it (the pre-roll ends at the trigger).

| Set | Count | Label source | Use |
|---|---|---|---|
| **Real positives** `verify-ok-*` | 384 (kitchen 305 / familyroom 43 / master 36); 71 Brad + 31 Adrienne speaker-labelled in the last 30 d | Parakeet fuzzy-matched the phrase (label noise ≈ 0) | train (older) + held-out test (newest 14 d) |
| **Missed positives** `near-*` | 1,055 near-miss rows (kitchen 600 / familyroom 435 / master 20) — but the satellites keep only the last **300 per mic** and the sync did not archive them until today (fixed, see §3) | run each through `/verify/probe` (silent stage 2): verified ⇒ a real wake the model missed | **the gold set** — train + test |
| **Non-speech negatives** `verify-rej` + `empty` | 6,013 | Parakeet returned no words | `background_train` (they are stage-1 false fires = exactly the noise to learn) **and** replace generic `/work/data/backgrounds` — ~4 h of real hood/dishes/TV/kids |
| **Speech negatives** `verify-rej` + `low_score` | 2,940 | transcript is other speech | `negative_train`, **excluding** transcripts starting "okay"/"ok" (~250) |
| Ambiguous | ~250 (transcript "Okay, Laura…", "Okay." etc.) | needs an ear | human label (20 min) or drop |
| `cmd-*` | rotates at 80 | — | not wake data |
| Kids | Simon 63 + Claire 30 verified — **no audio** (bridges keep no clips) | — | synthetic only; see risk §6 |
| Camera audio | on demand via `bi_wake_probe.py export` | — | eval/curiosity only — AGC + AAC coloration, never train on it |

### Do we need human labels?
**Mostly no — stage 2 is the labeler.** Parakeet's verdict is what defines a
wake in production, so training to it is self-consistent. Human ears are
needed for exactly three small things:
1. The ~250 ambiguous rejects whose transcript starts with "okay" (a Parakeet
   mishear of a real wake would otherwise be trained as a negative).
2. A 50-clip spot check of the near-miss probe verdicts.
3. **Optional but highest value per minute:** 10 minutes of Adrienne (and the
   kids) saying "okay computer" in the kitchen with the hood running, captured
   with the satellite's `/mark` endpoint (saves + scores the clip). That is the
   one voice/noise combination the corpus is thinnest on.

## 3. Phase 0 — start the collection now (done 2026-08-31)
- `sync_wake_clips.sh` now archives `near-*.wav` too (they rotate at 300/mic;
  kitchen fills that in ~3.5 days). Nightly 03:45.
- Kitchen trigger 0.40: more marginal wakes now reach stage 2, so the
  verify-ok/rej sets grow faster in the 0.40–0.50 band that matters.
- Let it run **~1 week** before cutting the sets: ≈ +70 positives, +600 near
  misses (≈ tens of verified misses), +2,000 negatives.

## 4. Phase 1 — build the real-clip sets (`voice-assistant/training/build_real_sets.py`, to write)

Runs on the Beelink (it has the corpus + the DB snapshot; the GX10 has neither
satellite keys nor the probe). Output is a directory tree the trainer already
understands: the livekit pipeline is `generate → augment (+ feature extraction)
→ train → export → eval`, and `augment` reads every `clip_NNNNNN.wav` in
`<output>/okay_computer_v2/{positive,negative,background}_{train,test}/`,
end-aligns positives into a 2.0 s window with 0–200 ms jitter, mixes
backgrounds/RIRs, and writes `_rN.wav` rounds. **Injection = copy real wavs in
as `clip_NNNNNN.wav` (numbering continues after the TTS clips) between
`generate` and `augment`.** No trainer changes.

1. **Join** clips to turns (filename timestamp ↔ `turns.at` ±3 s; `turns.clip`
   is clobbered on confirmed wakes, the backlog notes the fix).
2. **Label near misses**: POST each `near-*.wav` to
   `:8785/verify/probe?sat=<sat>` → verified ⇒ `missed_positive`, else
   `near_negative`. Write a manifest CSV (clip, sat, kind, label, speaker,
   stage1, s2 score, transcript, at).
3. **Trim positives** to the phrase: energy/VAD-trim trailing silence, keep the
   last ≤1.6 s (phrase ends within one hop of the trigger; the augmentor
   end-aligns anyway). Near-miss positives: the pre-roll ends at the peak
   window, same trim.
4. **Split by time**, not at random: newest 14 days ⇒ `*_test`, older ⇒
   `*_train`. Never let the same turn land in both.
5. **Weight**: ~300 real positives against 25,000 synthetic is ~1%; duplicate
   real train positives ×10 (the stop-v3 run disproved the "duplication
   overfits" worry). Real negatives/backgrounds are already thousands — no
   duplication.
6. **Backgrounds**: the 6,000 `empty` clips as `background_train/test` AND
   dropped into `/work/data/backgrounds` so synthetic positives get mixed
   with *our* rooms, not only generic noise.
7. Push the tree to `dgx:/home/pi/wake-train/output/okay_computer_v2/`.

Config `training/okay_computer_v2.yaml` = a copy of `okay_computer.yaml` with
`model_name: okay_computer_v2`, `output_dir` bumped, `background_paths`
including the real-room dir. Two optional levers for voice diversity, since
Piper's voices are the reason it never learned Adrienne: `tts_backend: voxcpm`
with female-leaning `voice_design_prompts`, and `max_speakers` unset. Run
`generate` → inject → `augment` → `train` → `export` (not `run`, which would
regenerate over the injected clips). GX10 launch gotchas from the stop runs
still apply: `--runtime nvidia`, the 0.12 CUDA memory-fraction cap, pin
torchaudio 2.9.0 `--no-deps`.

## 5. Phase 2 — evaluate on the satellite, faithfully

Run on **.251** inside `~/voice-pipeline/.venv` (same onnxruntime, one thread,
2 s window / 96 ms hop as `_score_clip`; `bi_wake_probe.py stage1` or
`training/replay_clips.py`). Never arm on the synthetic bench — the stop model
passed it at 0.038 and self-dismissed on the first real ding.

Report, for v1 and v2 side by side, at T = 0.40 and 0.50:
- recall on held-out real positives, **per speaker** (adrienne / brad / unsure);
- recall on the **missed-positive set** (v1 is 0% there by construction);
- false-fire rate = share of held-out `empty` and `low_score` rejects scoring
  ≥ T (v1 is 100% by construction — they all fired);
- peak on the 22 s camera clip (`data/camera_probe/…`) as a sanity story.

**Arm bar:** v2 recall ≥ v1 on held-out positives for *every* speaker, ≥ 50% on
missed positives, and ≤ 50% of v1's reject false-fire rate at the same T.

## 6. Phase 3 — live A/B, then cutover
Add v2 to the kitchen `MODEL_PATHS` **alongside** v1 (the satellite already
takes the max over models; `turns.wake_model` records which one fired). Stage 2
gatekeeps, so the only risk is extra ASR calls. After a week, compare per-model
verified vs rejected in the turns table (`voice-ops` funnel, filter by
`wake_model`), then drop v1 from `MODEL_PATHS`. Family room second, master
closet (Dot bridge) last.

**Risks.** (1) Over-fitting to Brad + Adrienne and regressing the kids: the
kids have verified turns but no audio (bridges), so keep synthetic positives
the majority, hold out by time, and watch Simon/Claire pass rates in
voice-ops after cutover. (2) Alignment: the augmentor end-aligns; a positive
with the phrase mid-clip trains a wrong target — hence the trim. (3) Label
leakage: the ~250 "okay…" rejects must be labelled or dropped, never
auto-negatives.

**Effort.** Set builder + manifest ~1 day; GX10 training ~2 h (same recipe);
eval 1 h; one week each of collection and live A/B.

## Phase 1 status — 2026-08-31 21:05, first sets BUILT
Early sync pulled the 600 surviving near-miss clips (300/mic) + 12,323-row
turns snapshot; `training/build_real_sets.py build` (home_config c4e6b71)
probe-labelled all 600 and wrote `/home/pi/wake-corpus/real_sets/2026-08-31`
(14-day test split) and `…-t7` (7-day split). Every clip joined to a turn.

| set | train | test | notes |
|---|---|---|---|
| positive | 276 (t7: 320) | 121 (t7: 77) | 391 verified + 2 mark + **4 missed_positive**; trimmed to 1.6 s, median 1.2 s voiced; 4 flagged short — drop or listen |
| negative | 2,090 | 816 | low_score speech, "okay…" excluded |
| background | 4,940 | 1,472 | empty rejects + near-empty (≈ 4.4 h of real room noise) |
| ambiguous | 231 | | "Okay.", "Okay, Peter." … held out for a human ear |

**Findings.** (1) Near misses are almost never real wakes: 600 probed → 399
no-speech, 181 other speech, 16 "okay…", **4 verified** (all family room,
s1 0.30–0.45). Adrienne's misses sit *below* 0.3, so the near-miss band is
not where the recall is hiding — the retrain has to move the model, not the
threshold. (2) Speaker labels only exist since speaker-ID shipped (07-27),
so a 14-day hold-out takes 30 of Adrienne's 43 clips out of training; **use
the 7-day split** (adrienne 22 train / 21 test, brad 48 / 40). (3) 256
positives are pre-speaker-ID and unlabelled by voice — fine for training,
excluded from per-speaker eval.

**Next:** rsync `real_sets/2026-08-31-t7` to `dgx:/home/pi/wake-train/real_sets/`,
write `configs/okay_computer_v2.yaml`, run `generate` → `inject
--dup-positive 10 --backgrounds-dir /work/data/backgrounds_real` → `augment`
→ `train` → `export`, then Phase 2 eval on .251.

## GX10 run LAUNCHED 2026-08-31 21:24 MDT
Brad labelled all 235 (24 wake / 211 not). Rebuilt →
`real_sets/2026-09-01-labelled` (positives 331 train / 86 test incl. 21
`human_wake`; negatives 2,547 / 569 incl. 211 `human_not`; backgrounds 5,436 /
976). Brad's ear note: many ambiguous clips were *very* quiet — the family-room
mic hearing someone in the kitchen — which is the paired-mic loudness story,
not a labelling problem.

Container `wake-train-okay-computer-v2` on dgx (`launch_okay_computer_v2.sh`
→ `run_okay_computer_v2.sh`, log `~/wake-train/train_okay_computer_v2.log`,
memwatch fail-safe). v1's 24,936 synthetic originals reused (no TTS), real
positives ×10 → positive_train 28,246 originals; augment 3 rounds → train
100k steps → export → synthetic eval. Output:
`~/wake-train/output/okay_computer_v2/okay_computer_v2/okay_computer_v2.onnx`.
Then Phase 2: copy to `.251:~/wake-bench/okay_computer_v2.onnx`, eval on the
held-out real test split with `bi_wake_probe.py stage1` geometry.

## v2 TRAINED 2026-08-31 23:02 MDT — eval in progress
Container exited 0: augment 69 min, train 28 min (100k steps), export OK.
Model: `dgx:~/wake-train/output/okay_computer_v2/okay_computer_v2/okay_computer_v2.onnx`
(952 KB, same size as v1) — copies at `beelink:/home/pi/wake-corpus/models/` and
`.251:~/wake-bench/okay_computer_v2.onnx` (staged, NOT in MODEL_PATHS).
Synthetic eval @0.5: v2 recall 86.9% / FPPH 0.14 (opt. T 0.58 → 85.5% / 0.07)
vs v1 90.0% / 0.39 (opt. 0.66 → 86.6% / 0.08). Sanity only.

**v1 baseline on the real held-out bundle (.251, `eval_wake_v2.py`, 86 pos /
569 neg / 976 bg original pre-rolls):** recall 86% @0.40 (adrienne 19/21,
brad 37/40; the 4 real misses 0/4), 80% @0.50; negatives still fire 60% /
45%, backgrounds 54% / 41%; camera 0.17 / 0.31. Offline replay is not the
live loop (live-caught positives replay 14–20% under threshold; rejects
re-fire ~50%), so read v1-vs-v2 as RELATIVE, same geometry both.
v2 eval running on .251 → `~/wake-bench/eval_v2.{log,json}`.
