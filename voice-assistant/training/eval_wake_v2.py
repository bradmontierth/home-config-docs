#!/usr/bin/env python3
"""Phase-2 eval for a retrained wake model, on the satellite, on real clips.

Runs ON .251 inside ~/voice-pipeline/.venv (same onnxruntime, one thread,
2 s window / 96 ms hop as assistant._score_clip). Scores every clip of a
held-out test bundle with each model and reports, at T = 0.40 and 0.50:

  positives   recall (share scoring >= T), per speaker and per label
  negatives   false-fire share (these all fired on v1 by construction, so
  backgrounds  v1 reads ~100% here and the number to watch is v2's drop)
  camera clip peak score, if present

Honest caveat printed with the results: every corpus positive is one the
OLD model already fired on, so recall here measures "no regression", not
the misses below 0.3 that started this. Those need live A/B or mined audio.

Bundle layout (built by stage_eval_bundle on the Beelink):
  <bundle>/{positive,negative,background}/<sat>-<clip>.wav   ORIGINAL pre-rolls
  <bundle>/manifest.csv                                       subset of the set manifest

  .venv/bin/python eval_wake_v2.py <bundle> --model v1=/home/pi/wake-bench/okay_computer.onnx \
      --model v2=/home/pi/wake-bench/okay_computer_v2.onnx [--camera /tmp/kitchen_cam.wav] [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np
import onnxruntime as ort

_orig = ort.InferenceSession


def _capped(*a, **k):
    so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
    k.setdefault("sess_options", so); return _orig(*a, **k)


ort.InferenceSession = _capped
from livekit.wakeword import WakeWordModel  # noqa: E402

SR, WIN, HOP = 16000, 32000, int(0.096 * 16000)
THRESHOLDS = (0.40, 0.50)


def load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def peak(model: WakeWordModel, key: str, s: np.ndarray) -> float:
    if len(s) < WIN:
        s = np.pad(s, (WIN - len(s), 0))
    best = 0.0
    for st in range(0, len(s) - WIN + 1, HOP):
        best = max(best, float(model.predict(s[st:st + WIN]).get(key, 0.0)))
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--model", action="append", required=True, help="name=path.onnx")
    ap.add_argument("--camera")
    ap.add_argument("--json")
    a = ap.parse_args()
    bundle = Path(a.bundle)
    meta = {f'{r["sat"]}-{r["clip"]}': r for r in csv.DictReader((bundle / "manifest.csv").open())}
    models = {}
    for spec in a.model:
        name, path = spec.split("=", 1)
        m = WakeWordModel(models=[path])
        key = next(iter(m.predict(np.zeros(WIN, dtype=np.int16)).keys()))
        models[name] = (m, key)

    scores: dict[str, dict[str, float]] = defaultdict(dict)   # clip -> model -> peak
    sets: dict[str, list[str]] = {}
    for set_ in ("positive", "negative", "background"):
        files = sorted((bundle / set_).glob("*.wav"))
        sets[set_] = [f.name for f in files]
        for f in files:
            s = load(f)
            for name, (m, key) in models.items():
                scores[f.name][name] = peak(m, key, s)
        print(f"scored {set_}: {len(files)} clips", flush=True)

    out = {"thresholds": THRESHOLDS, "models": list(models), "results": {}}
    lines = []
    for name in models:
        lines.append(f"\n=== {name} ===")
        res = out["results"][name] = {}
        pos = [scores[c][name] for c in sets["positive"]]
        res["positive"] = {"n": len(pos), "median": statistics.median(pos) if pos else None,
                           "p10": sorted(pos)[len(pos) // 10] if pos else None}
        lines.append(f"positives n={len(pos)} median={res['positive']['median']:.3f} p10={res['positive']['p10']:.3f}")
        for T in THRESHOLDS:
            r = sum(1 for v in pos if v >= T) / len(pos) if pos else 0
            res["positive"][f"recall@{T}"] = r
            by_spk = defaultdict(list); by_lab = defaultdict(list)
            for c in sets["positive"]:
                by_spk[meta.get(c, {}).get("speaker") or "?"].append(scores[c][name] >= T)
                by_lab[meta.get(c, {}).get("label") or "?"].append(scores[c][name] >= T)
            spk = ", ".join(f"{k} {sum(v)}/{len(v)}" for k, v in sorted(by_spk.items()))
            lab = ", ".join(f"{k} {sum(v)}/{len(v)}" for k, v in sorted(by_lab.items()))
            lines.append(f"  T={T}: recall {r:.1%}   by speaker: {spk}   by label: {lab}")
            res["positive"][f"by_speaker@{T}"] = {k: [sum(v), len(v)] for k, v in by_spk.items()}
        for set_ in ("negative", "background"):
            vals = [scores[c][name] for c in sets[set_]]
            res[set_] = {"n": len(vals), "median": statistics.median(vals) if vals else None}
            ff = {T: (sum(1 for v in vals if v >= T) / len(vals) if vals else 0) for T in THRESHOLDS}
            for T in THRESHOLDS:
                res[set_][f"fire@{T}"] = ff[T]
            lines.append(f"{set_:10} n={len(vals)} median={res[set_]['median']:.3f}  still fires: "
                         + "  ".join(f"T={T} {ff[T]:.1%}" for T in THRESHOLDS))
        if a.camera:
            cam = load(Path(a.camera)).astype(np.float32)
            m, key = models[name]
            best = {}
            for st in range(0, len(cam) - WIN + 1, HOP):
                v = float(m.predict(cam[st:st + WIN].astype(np.int16)).get(key, 0.0)); t = (st + WIN) / SR
                zone = "17:23:45" if 1 <= t <= 6 else "17:23:56" if 12 <= t <= 17 else "elsewhere"
                best[zone] = max(best.get(zone, 0.0), v)
            res["camera"] = best
            lines.append("camera clip peaks: " + ", ".join(f"{k} {v:.3f}" for k, v in best.items()))
    lines.append("\nCAVEAT: corpus positives are ones the OLD model already fired on (>= its threshold), "
                 "so recall here is a no-regression check; negatives/backgrounds all fired on v1, "
                 "so v1 reads ~100% and v2's drop is the specificity gain.")
    print("\n".join(lines))
    if a.json:
        out["per_clip"] = scores
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
