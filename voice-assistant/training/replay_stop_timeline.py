"""Dump the full per-window score timeline for every real ring clip.

Companion to replay_stop.py. That script reduces each clip to region peaks,
which turned out to be fragile: a "ring-only" region is only stop-free if the
labels are perfect, and clip 131830 proved they aren't — the live ASR heard
"So" at +7.25s, i.e. a real spoken "stop" mangled by ring masking (exactly
the failure this model exists to fix). Scoring that region as false-fire risk
would have been wrong.

So: score every window once, dump (time, v1, v2) for every clip, and do all
region analysis offline where it can be re-cut as labels improve.

The assumption-free false-fire metric this enables is the EARLY window —
the first ~3s of a ring is ring tone + TTS announcement with no human speech
in it — which is also exactly when the v1 incident fired (0.833/0.729 within
650ms of ring start).

Run detached on the kitchen satellite; ~5 min for 19 clips x 2 models.
"""

import csv
import json
import sys
import wave
from pathlib import Path

import numpy as np

import onnxruntime as ort

_orig = ort.InferenceSession


def _capped(*a, **k):
    if "sess_options" not in k:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        so.inter_op_num_threads = 2
        k["sess_options"] = so
    return _orig(*a, **k)


ort.InferenceSession = _capped

from livekit.wakeword import WakeWordModel  # noqa: E402

RINGS = Path("/home/pi/voice-pipeline/data/alarm_rings")
MANIFEST = Path("/tmp/real-rings-manifest.csv")
WINDOW = 32000
HOP = int(0.224 * 16000)
SR = 16000
MODELS = {"v1": "/home/pi/wake-bench/stop.onnx",
          "v2": "/home/pi/wake-bench/stop_v2.onnx"}


def load(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def main():
    models = {}
    for name, path in MODELS.items():
        m = WakeWordModel(models=[path])
        key = next(iter(m.predict(np.zeros(WINDOW, dtype=np.int16)).keys()))
        models[name] = (m, key)

    out = []
    for r in csv.DictReader(open(MANIFEST)):
        samples = load(RINGS / r["file"])
        if len(samples) < WINDOW:
            samples = np.pad(samples, (WINDOW - len(samples), 0))
        tl = []
        for start in range(0, len(samples) - WINDOW + 1, HOP):
            win = samples[start:start + WINDOW]
            end_s = round((start + WINDOW) / SR, 3)
            row = {"t": end_s}
            for mname, (m, key) in models.items():
                row[mname] = round(float(m.predict(win).get(key, 0.0)), 4)
            tl.append(row)
        out.append({"file": r["file"], "label": r["label"],
                    "holdout": r["holdout"] == "True",
                    "contaminated": r["contaminated"] == "True",
                    "stop_start": (float(r["stop_start_s"])
                                   if r["stop_start_s"] else None),
                    "duration": float(r["duration_s"]), "timeline": tl})
        early = [w for w in tl if w["t"] <= 3.0]
        print(f"  {r['file']} n={len(tl):3} "
              f"early3s v1={max((w['v1'] for w in early), default=0):.3f} "
              f"v2={max((w['v2'] for w in early), default=0):.3f} "
              f"| clipmax v1={max(w['v1'] for w in tl):.3f} "
              f"v2={max(w['v2'] for w in tl):.3f}", file=sys.stderr, flush=True)

    print(json.dumps(out))


if __name__ == "__main__":
    main()
