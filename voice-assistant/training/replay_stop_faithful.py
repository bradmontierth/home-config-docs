"""Faithful bit-for-bit replay of the satellite's alarm-time stop scoring.

WHY THIS EXISTS (the trap that invalidated two earlier eval attempts):

The satellite RESETS its scoring window to ZEROS at every alarm start
(assistant.py: `stop_window = np.zeros(WINDOW_SAMPLES)` in the `not in_alarm`
branch) and begins predicting immediately, every 7 chunks (224ms). So the
first ~2s of every ring is scored against a window that is mostly SILENCE
with a sliver of ring at the end. That startup transient is a completely
different input distribution from ring steady-state — and it is exactly when
v1 false-fired in the live incident (0.833/0.729 within 650ms of ring start,
and 0.92 at +0.75s on ring-20260724-174346).

A naive replay that slides a full 2s window over the WAV NEVER REPRODUCES
THAT STATE — its first window is already full of real audio at t=2.0s. Both
earlier eval scripts had this blind spot and therefore could not see the
actual bug.

This script reproduces the live loop exactly:
  stop_window = zeros(32000)
  per 512-sample chunk: window = concat(window[512:], chunk); every 7th -> predict

VALIDATION: the per-window scores it produces for ring-20260724-174346.wav
should match the scores the live satellite logged for that ring
(0.241/0.785/0.92/0.455/0.529 over the first ~1.2s). If they match, the
simulation is faithful and its verdicts can be trusted.

Output: JSON with every (t, v1, v2) plus a startup-vs-steady-state split.
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
WINDOW_SAMPLES = 32000
CHUNK = 512          # SileroVad.CHUNK
HOP_CHUNKS = 7       # STOP_HOP_MS(224) // VAD_FRAME_MS(32)
SR = 16000
# The window holds real audio only after WINDOW_SAMPLES have been shifted in.
FULL_AT_S = WINDOW_SAMPLES / SR   # 2.0s
MODELS = {"v1": "/home/pi/wake-bench/stop.onnx",
          "v2": "/home/pi/wake-bench/stop_v2.onnx"}


def load(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def score_clip(samples, model, key):
    """Replay the satellite loop; yield (t_seconds, score) per prediction."""
    window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
    chunks = 0
    out = []
    for off in range(0, len(samples) - CHUNK + 1, CHUNK):
        window = np.concatenate([window[CHUNK:], samples[off:off + CHUNK]])
        chunks += 1
        if chunks >= HOP_CHUNKS:
            chunks = 0
            t = (off + CHUNK) / SR
            out.append((round(t, 3), round(float(model.predict(window)[key]), 4)))
    return out


def main():
    models = {}
    for name, path in MODELS.items():
        m = WakeWordModel(models=[path])
        key = next(iter(m.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16)).keys()))
        models[name] = (m, key)

    out = []
    for r in csv.DictReader(open(MANIFEST)):
        samples = load(RINGS / r["file"])
        series = {n: score_clip(samples, m, k) for n, (m, k) in models.items()}
        rec = {"file": r["file"], "label": r["label"],
               "holdout": r["holdout"] == "True",
               "contaminated": r["contaminated"] == "True",
               "stop_start": float(r["stop_start_s"]) if r["stop_start_s"] else None,
               "duration": float(r["duration_s"]),
               "scores": {n: [{"t": t, "s": s} for t, s in v]
                          for n, v in series.items()}}
        for n, v in series.items():
            startup = [s for t, s in v if t < FULL_AT_S]
            steady = [s for t, s in v if t >= FULL_AT_S]
            rec[f"{n}_startup_max"] = max(startup) if startup else None
            rec[f"{n}_steady_max"] = max(steady) if steady else None
        out.append(rec)
        print(f"  {r['file']} {r['label'][:3]:3} "
              f"STARTUP(<2s) v1={rec['v1_startup_max']} v2={rec['v2_startup_max']}"
              f"  | STEADY v1={rec['v1_steady_max']} v2={rec['v2_steady_max']}",
              file=sys.stderr, flush=True)

    print(json.dumps(out))

    print("\n=== STARTUP TRANSIENT (window still zero-padded, t<2.0s) ===",
          file=sys.stderr)
    print("   no human has spoken yet here — ANY high score is a false fire",
          file=sys.stderr)
    for n in MODELS:
        vals = [r[f"{n}_startup_max"] for r in out if r[f"{n}_startup_max"] is not None]
        over = [r["file"] for r in out if (r[f"{n}_startup_max"] or 0) >= 0.5]
        print(f"  {n}: max={max(vals):.3f} median={sorted(vals)[len(vals)//2]:.3f}"
              f"  clips>=0.5: {len(over)}/{len(vals)}", file=sys.stderr)
        for f in over:
            print(f"       FALSE FIRE: {f}", file=sys.stderr)


if __name__ == "__main__":
    main()
