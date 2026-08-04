"""Arming eval for the stop model, v1 vs v2 vs v3, on real captured rings.

Runs ON THE KITCHEN SATELLITE (.251) so the onnxruntime build, thread caps and
CPU match the deployed path exactly.

Two hard-won rules from the 2026-07-25 post-mortem are baked in:

1. FAITHFUL REPLAY. The satellite scores a rolling 2s window every 224ms
   starting from a zeroed buffer at alarm start, so a naive full-window slide
   over the WAV is a different input distribution (see replay_stop_faithful.py
   for the whole story). This reproduces the live loop chunk-for-chunk.
2. STEADY STATE ONLY. Since 2026-07-25 the satellite's `stop_filled` counter
   suppresses scoring until the window holds WINDOW_SAMPLES of real audio, so
   the pre-fill transient that caused the 2026-07-24 self-dismissals can no
   longer fire. Scores before t=2.0s are recorded but excluded from every
   metric — including them would re-litigate a bug that is already fixed.

The arming criterion is the 2-consecutive-window rule from the 2026-07-25
second-look analysis: a real "stop" sustains across windows (people draw it
out), ring excursions mostly do not. So every metric below is computed on
min(s[i], s[i+1]) — the highest score a 2-consec rule would actually act on.

Bar agreed with Brad 2026-08-03 before the v3 run: ARM only if some threshold
T <= 0.9 clears the 2-consec ceiling across ALL held-out long unattended rings
by >= 0.15 AND keeps >= 60% 2-consec recall at the spoken stop.

Usage (on .251):
  .venv/bin/python eval_stop_v3.py <rings_dir> <manifest_0724> <manifest_0728> \
      <out.json> [--models v1=/path/stop.onnx v3=/path/stop_v3.onnx]

Takes ~20 min for 3 models over 27 clips on the mini PC. Write the report to a
FILE, not stdout: the first dry run lost everything when the run was killed
with buffered stdout still unflushed.
"""

from __future__ import annotations

import csv
import json
import sys
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort

_orig = ort.InferenceSession


def _capped(*a, **k):
    """Match the satellite's ORT thread caps (assistant.py) — scores are
    identical either way, but this keeps the eval off the other 6 cores."""
    if "sess_options" not in k:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        so.inter_op_num_threads = 2
        k["sess_options"] = so
    return _orig(*a, **k)


ort.InferenceSession = _capped

from livekit.wakeword import WakeWordModel  # noqa: E402

WINDOW_SAMPLES = 32000
CHUNK = 512          # SileroVad.CHUNK
HOP_CHUNKS = 7       # STOP_HOP_MS(224) // VAD_FRAME_MS(32)
SR = 16000
FULL_AT_S = WINDOW_SAMPLES / SR      # 2.0s — the stop_filled gate

# A stop spoken at T is only fully inside the trailing 2s window from about
# T+0.3 onward, and has slid out by T+2.0. Score the peak in a window a touch
# wider than that on both sides.
HIT_LO, HIT_HI = -0.5, 2.5

DEFAULT_MODELS = {
    "v1": "/home/pi/wake-bench/stop.onnx",
    "v2": "/home/pi/wake-bench/stop_v2.onnx",
    "v3": "/home/pi/wake-bench/stop_v3.onnx",
}
THRESHOLDS = [round(0.30 + 0.05 * i, 2) for i in range(14)]  # 0.30 .. 0.95
MARGIN_REQ = 0.15
RECALL_REQ = 0.60


def load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR, f"{path}: {w.getframerate()}Hz"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def score_clip(samples: np.ndarray, model, key: str) -> list[tuple[float, float]]:
    window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
    chunks = 0
    out: list[tuple[float, float]] = []
    for off in range(0, len(samples) - CHUNK + 1, CHUNK):
        window = np.concatenate([window[CHUNK:], samples[off:off + CHUNK]])
        chunks += 1
        if chunks >= HOP_CHUNKS:
            chunks = 0
            t = (off + CHUNK) / SR
            out.append((round(t, 3), round(float(model.predict(window)[key]), 4)))
    return out


def consec(series: list[tuple[float, float]], n: int,
           lo: float | None = None, hi: float | None = None) -> float:
    """Peak of the n-consecutive-window rule over [lo, hi), steady state only.

    Value = max over i of min(s[i] .. s[i+n-1]) — i.e. the highest threshold
    at which n consecutive windows would all have fired.
    """
    vals = [s for t, s in series if t >= FULL_AT_S
            and (lo is None or t >= lo) and (hi is None or t < hi)]
    if len(vals) < n:
        return 0.0
    return max(min(vals[i:i + n]) for i in range(len(vals) - n + 1))


def read_manifests(m0724: Path, m0728: Path) -> list[dict]:
    clips: list[dict] = []
    for r in csv.DictReader(open(m0724)):
        clips.append({
            "file": r["file"],
            "set": "20260724",
            "label": r["label"],
            "stop_start": float(r["stop_start_s"]) if r["stop_start_s"] else None,
            "holdout": r["holdout"] == "True",
            # v2/v3 backgrounds were carved from these bodies where present
            "in_backgrounds": bool(r["background_clip"]),
            "note": r["transcript"][:120],
        })
    for r in csv.DictReader(open(m0728)):
        clips.append({
            "file": r["file"],
            "set": "20260728",
            "label": r["label"],
            "stop_start": None,
            "holdout": r["holdout"] == "True",
            "in_backgrounds": r["in_v3_backgrounds"] == "True",
            "sound": r["sound"],
            "v1_live_max": float(r["v1_max_score"]),
            "note": r["notes"][:120],
        })
    return clips


def main() -> int:
    argv = sys.argv[1:]
    models_spec = dict(DEFAULT_MODELS)
    if "--models" in argv:
        i = argv.index("--models")
        models_spec = dict(s.split("=", 1) for s in argv[i + 1:])
        argv = argv[:i]
    rings, m0724, m0728, out_path = (Path(a) for a in argv[:4])
    partial = out_path.with_suffix(".jsonl")   # survives a kill mid-run
    partial.write_text("")

    models = {}
    for name, path in models_spec.items():
        if not Path(path).exists():
            print(f"!! missing model {name}: {path}", file=sys.stderr)
            continue
        m = WakeWordModel(models=[path])
        key = next(iter(m.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16)).keys()))
        models[name] = (m, key)
    print(f"models: {list(models)}", file=sys.stderr)

    clips = read_manifests(m0724, m0728)
    out = []
    for c in clips:
        p = rings / c["file"]
        if not p.exists():
            print(f"  SKIP (missing) {c['file']}", file=sys.stderr)
            continue
        samples = load(p)
        rec = dict(c, duration=round(len(samples) / SR, 2), models={})
        for name, (m, key) in models.items():
            series = score_clip(samples, m, key)
            lo = hi = None
            if c["stop_start"] is not None:
                lo, hi = c["stop_start"] + HIT_LO, c["stop_start"] + HIT_HI
            rec["models"][name] = {
                "max": max((s for t, s in series if t >= FULL_AT_S), default=0.0),
                "c2": consec(series, 2),
                "c3": consec(series, 3),
                # at the spoken stop only (positives)
                "c2_hit": consec(series, 2, lo, hi) if lo is not None else None,
                "c1_hit": max((s for t, s in series
                               if t >= FULL_AT_S and lo <= t < hi), default=0.0)
                          if lo is not None else None,
                "series": [{"t": t, "s": s} for t, s in series],
            }
        out.append(rec)
        with partial.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        line = "  ".join(f"{n}: max={rec['models'][n]['max']:.3f} "
                         f"c2={rec['models'][n]['c2']:.3f}" +
                         (f" hit_c2={rec['models'][n]['c2_hit']:.3f}"
                          if rec['models'][n]['c2_hit'] is not None else "")
                         for n in models)
        print(f"  {c['file']} {c['label'][:3]:3} {line}", file=sys.stderr)

    # ---- the arming table --------------------------------------------------
    # PRIMARY negatives: held-out long unattended rings (2026-07-28). Nothing
    # in this set contributed audio to any training background pool, so their
    # ceiling is the honest false-fire number.
    neg_primary = [r for r in out if r["set"] == "20260728"
                   and r["label"] == "negative" and r["holdout"]]
    neg_contam = [r for r in out if r["set"] == "20260728"
                  and r["label"] == "negative" and not r["holdout"]]
    neg_short = [r for r in out if r["set"] == "20260724" and r["label"] == "negative"]
    pos = [r for r in out if r["label"] == "positive" and r["stop_start"] is not None]
    pos_clean = [r for r in pos if not r["in_backgrounds"]]

    summary = {}
    for name in models:
        ceiling = max((r["models"][name]["c2"] for r in neg_primary), default=0.0)
        worst = max(neg_primary, key=lambda r: r["models"][name]["c2"], default=None)
        rows = []
        for t in THRESHOLDS:
            hits = [r for r in pos if r["models"][name]["c2_hit"] >= t]
            hits_clean = [r for r in pos_clean if r["models"][name]["c2_hit"] >= t]
            # Same rule, but scored anywhere in the clip instead of inside the
            # labeled stop window. Some 2026-07-24 onsets are known to be early
            # (parakeet merged the announcement and the stop into one segment;
            # 131830 peaks 0.935 with 0.026 in its labeled window), so this is
            # the optimistic bound on recall and the strict number is the bar.
            hits_any = [r for r in pos if r["models"][name]["c2"] >= t]
            rows.append({
                "threshold": t,
                "margin": round(t - ceiling, 3),
                "recall": round(len(hits) / len(pos), 3) if pos else None,
                "recall_clean": (round(len(hits_clean) / len(pos_clean), 3)
                                 if pos_clean else None),
                "recall_anywhere": round(len(hits_any) / len(pos), 3) if pos else None,
                "n_pos_hit": len(hits),
                "arms": t - ceiling >= MARGIN_REQ and t <= 0.90
                        and bool(pos) and len(hits) / len(pos) >= RECALL_REQ,
            })
        armable = [r for r in rows if r["arms"]]
        summary[name] = {
            "ceiling_c2_primary": round(ceiling, 3),
            "ceiling_clip": worst["file"] if worst else None,
            "ceiling_c2_contaminated": round(
                max((r["models"][name]["c2"] for r in neg_contam), default=0.0), 3),
            "ceiling_c2_short_negatives": round(
                max((r["models"][name]["c2"] for r in neg_short), default=0.0), 3),
            "n_positives": len(pos),
            "n_positives_clean": len(pos_clean),
            "sweep": rows,
            "ARMS": bool(armable),
            "best_threshold": min(armable, key=lambda r: r["threshold"])["threshold"]
                              if armable else None,
        }

    print("\n=== 2-CONSECUTIVE-WINDOW ARMING TABLE (steady state, t>=2.0s) ===",
          file=sys.stderr)
    for name, s in summary.items():
        print(f"\n{name}: ceiling(c2) on held-out long rings = {s['ceiling_c2_primary']:.3f}"
              f"  [{s['ceiling_clip']}]", file=sys.stderr)
        print(f"    contaminated long rings {s['ceiling_c2_contaminated']:.3f} | "
              f"short 0724 negatives {s['ceiling_c2_short_negatives']:.3f}", file=sys.stderr)
        for r in s["sweep"]:
            if r["margin"] < 0:
                continue
            flag = "  <== ARMS" if r["arms"] else ""
            print(f"    thr {r['threshold']:.2f}  margin {r['margin']:+.3f}  "
                  f"recall {r['recall']:.0%} ({r['n_pos_hit']}/{s['n_positives']})  "
                  f"clean {r['recall_clean']:.0%}  anywhere {r['recall_anywhere']:.0%}"
                  f"{flag}", file=sys.stderr)
        print(f"    VERDICT: {'ARMABLE at ' + str(s['best_threshold']) if s['ARMS'] else 'NOT ARMABLE'}"
              f"  (bar: margin >= {MARGIN_REQ}, recall >= {RECALL_REQ:.0%}, thr <= 0.90)",
              file=sys.stderr)

    # Faithfulness check: v1 replay should reproduce the live-logged peaks.
    if "v1" in models:
        print("\n=== faithfulness: v1 replay vs live-logged max (2026-07-28) ===",
              file=sys.stderr)
        for r in out:
            if r["set"] == "20260728":
                print(f"    {r['file']}  replay {r['models']['v1']['max']:.3f}  "
                      f"live {r['v1_live_max']:.3f}", file=sys.stderr)

    out_path.write_text(json.dumps({"summary": summary, "clips": out}))
    print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
