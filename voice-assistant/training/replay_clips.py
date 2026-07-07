"""Replay bench clips through the two trained candidates.

Slides each model's 2s window across every clip and records the peak score.
- mark-fn clips: real recordings of the user saying a wake phrase (the
  hey_livekit control missed them) -> real-world recall test.
- trigger clips: real household audio that tripped the control -> real-world
  false-positive carryover test.

Run on kitchen-speaker (has livekit-wakeword + onnxruntime + the clips).
"""

import csv
import glob
import os
import sys
import wave

import numpy as np

# cap ORT threads like the bench does
import onnxruntime as ort
_orig = ort.InferenceSession
def _capped(*a, **k):
    if "sess_options" not in k:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        k["sess_options"] = so
    return _orig(*a, **k)
ort.InferenceSession = _capped

from livekit.wakeword import WakeWordModel

CLIP_DIR = "/home/pi/wake-bench/data/clips"
WINDOW = 32000  # 2s @ 16k
HOP = 3200      # 200ms

MODELS = {
    "okay_computer": "/home/pi/wake-bench/okay_computer.onnx",
    "hey_computer": "/home/pi/wake-bench/hey_computer.onnx",
}


def peak_score(model, key, samples):
    if len(samples) < WINDOW:
        samples = np.pad(samples, (0, WINDOW - len(samples)))
    best = 0.0
    for start in range(0, len(samples) - WINDOW + 1, HOP):
        s = float(model.predict(samples[start:start + WINDOW]).get(key, 0.0))
        if s > best:
            best = s
    return round(best, 3)


def load(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def main():
    models = {}
    for name, path in MODELS.items():
        m = WakeWordModel(models=[path])
        key = next(iter(m.predict(np.zeros(WINDOW, dtype=np.int16)).keys()))
        models[name] = (m, key)

    writer = csv.writer(sys.stdout)
    writer.writerow(["clip", "kind", "okay_computer", "hey_computer"])
    for kind in ("mark-fn", "trigger"):
        for path in sorted(glob.glob(f"{CLIP_DIR}/{kind}-*.wav")):
            samples = load(path)
            row = [os.path.basename(path), kind]
            for name in MODELS:
                m, key = models[name]
                row.append(peak_score(m, key, samples))
            writer.writerow(row)
            sys.stdout.flush()


if __name__ == "__main__":
    main()
