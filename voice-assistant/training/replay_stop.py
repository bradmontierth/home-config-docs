"""Replay real captured alarm rings through stop-model candidates.

The v1 stop model passed a SYNTHETIC bench (ring-only peak 0.038) and then
false-fired at 0.7-0.9 on the real first ding. This script is the eval that
replaces that bench: it replays the REAL captured rings
(data/alarm_rings/, labels in training/real-rings-manifest-20260724.csv)
through the satellite's exact scoring path — 2s window, STOP_HOP_MS stride,
same onnxruntime, same CPU — and reports the two numbers a runtime
threshold has to separate:

  ring_only : peak score over windows ending BEFORE the spoken stop
              (whole clip for negatives). This is the FALSE-FIRE risk.
  stop_peak : peak score over windows ending in [start+0.3, start+2.0],
              i.e. the windows that actually contain the spoken "stop".
              This is RECALL.

A threshold is only safe if max(ring_only) < min(stop_peak) with margin.

TWO TRAPS THIS SCRIPT AVOIDS (both bit the first attempt):

1. ONSETS. Parakeet merges "Your timer is done. Stop." into ONE segment, so
   the segment start is the ANNOUNCEMENT, not the stop — off by up to 11s on
   5 of 16 positives. `stop_start_s` in the manifest is the corrected value:
   parakeet onset where it agrees with the live journal dismiss timestamp
   (delta <= 2.5s), else journal_anchor - 1.2s (median ASR confirm lag).

2. CONTAMINATION. The 9 clips whose ring bodies were fed into v2's training
   backgrounds have TRAIN-CONTAMINATED ring_only scores — the model saw that
   exact audio. `contaminated` in the manifest marks them. The headline
   false-fire number MUST come from the uncontaminated set only.

Windows are also only counted when a region has >= MIN_WINDOWS of them; a
region with 0-2 windows reports None rather than a fake 0.000.

Run on the kitchen satellite (has livekit-wakeword + the clips + the real
deployment CPU). Takes ~5 min for 19 clips x 2 models — run it detached:
  scp replay_stop.py real-rings-manifest-20260724.csv pi@192.168.10.251:/tmp/
  ssh pi@192.168.10.251 "nohup /home/pi/voice-pipeline/.venv/bin/python \
      /tmp/replay_stop.py > /tmp/replay_stop_results.json 2>/tmp/replay_stop.log &"
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
    """Match the satellite's ORT_THREADS=2 so timings are representative."""
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
WINDOW = 32000            # 2.0s @ 16k, satellite WINDOW_SAMPLES
HOP = int(0.224 * 16000)  # satellite STOP_HOP_MS=224
SR = 16000
PRE_MARGIN = 0.3   # keep windows that might clip the word's leading edge out
STOP_LO, STOP_HI = 0.3, 2.0
MIN_WINDOWS = 3    # fewer than this -> report None, not a fake 0.0

MODELS = {"v1": "/home/pi/wake-bench/stop.onnx",
          "v2": "/home/pi/wake-bench/stop_v2.onnx"}
# Brad ear-verified an EARLIER stop than the manifest value on this clip
# (two stops at the end); use the earliest so no real stop can leak into the
# ring-only region and inflate the false-fire number.
EARLIEST_STOP = {"ring-20260724-174346.wav": 13.5}


def load(path):
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def windows(samples):
    """Yield (window_end_seconds, window) exactly as the satellite scores."""
    if len(samples) < WINDOW:
        samples = np.pad(samples, (WINDOW - len(samples), 0))
    for start in range(0, len(samples) - WINDOW + 1, HOP):
        yield (start + WINDOW) / SR, samples[start:start + WINDOW]


def main():
    models = {}
    for name, path in MODELS.items():
        if not Path(path).is_file():
            sys.exit(f"missing model: {path}")
        m = WakeWordModel(models=[path])
        key = next(iter(m.predict(np.zeros(WINDOW, dtype=np.int16)).keys()))
        models[name] = (m, key)

    out = []
    for r in csv.DictReader(open(MANIFEST)):
        name = r["file"]
        samples = load(RINGS / name)
        start = None
        if r["label"] == "positive" and r["stop_start_s"]:
            start = EARLIEST_STOP.get(name, float(r["stop_start_s"]))
        rec = {"file": name, "label": r["label"],
               "holdout": r["holdout"] == "True",
               "contaminated": r["contaminated"] == "True",
               "stop_start": start, "duration": float(r["duration_s"])}
        for mname, (m, key) in models.items():
            ring, stop = [], []
            for end_s, win in windows(samples):
                s = float(m.predict(win).get(key, 0.0))
                if start is None or end_s <= start - PRE_MARGIN:
                    ring.append(s)
                elif start + STOP_LO <= end_s <= start + STOP_HI:
                    stop.append(s)
            rec[f"{mname}_ring_only"] = (round(max(ring), 3)
                                         if len(ring) >= MIN_WINDOWS else None)
            rec[f"{mname}_stop_peak"] = round(max(stop), 3) if stop else None
            rec[f"{mname}_n_ring"] = len(ring)
        out.append(rec)
        fmt = lambda v: f"{v:.3f}" if isinstance(v, float) else "  -  "  # noqa: E731
        print(f"  {name} {r['label'][:3]}"
              f"{' CONTAM' if rec['contaminated'] else '       '}"
              f" ring v1={fmt(rec['v1_ring_only'])} v2={fmt(rec['v2_ring_only'])}"
              f" (n={rec['v1_n_ring']:2})"
              f" | stop v1={fmt(rec['v1_stop_peak'])} v2={fmt(rec['v2_stop_peak'])}",
              file=sys.stderr, flush=True)

    print(json.dumps(out, indent=1))

    def summarize(tag, sel):
        sub = [r for r in out if sel(r)]
        if not sub:
            return
        print(f"\n=== {tag} (n={len(sub)}) ===", file=sys.stderr)
        for mname in MODELS:
            ring = [r[f"{mname}_ring_only"] for r in sub
                    if r[f"{mname}_ring_only"] is not None]
            stops = [r[f"{mname}_stop_peak"] for r in sub
                     if r[f"{mname}_stop_peak"] is not None]
            line = f"  {mname}:"
            if ring:
                line += f" ring_only max={max(ring):.3f} (n={len(ring)})"
            if stops:
                line += (f"  stop_peak min={min(stops):.3f}"
                         f" median={sorted(stops)[len(stops)//2]:.3f} (n={len(stops)})")
            if ring and stops:
                line += f"  SEPARATION={min(stops) - max(ring):+.3f}"
            print(line, file=sys.stderr)

    summarize("UNCONTAMINATED — the number that counts",
              lambda r: not r["contaminated"])
    summarize("contaminated (model trained on this ring audio)",
              lambda r: r["contaminated"])
    summarize("negatives only (no stop anywhere)",
              lambda r: r["label"] == "negative")
    summarize("ALL", lambda r: True)


if __name__ == "__main__":
    main()
