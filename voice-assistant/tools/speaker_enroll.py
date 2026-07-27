#!/usr/bin/env python3
"""Build speaker-ID profiles from labeled clips (item 9 enrollment).

Run on the beelink AFTER labeling at :8791. Stdlib only.

  python3 speaker_enroll.py            # report + write profiles
  python3 speaker_enroll.py --dry-run  # report only

Reads  ~/voice-pipeline/data/speaker_clips/ + speaker_labels.jsonl
Embeds via the GX10 titanet service (http://192.168.10.187:8096/embed).
Writes ~/voice-pipeline/data/speaker_profiles.json:
  {"model": "titanet_large", "dim": 192, "built": "...",
   "profiles": {"brad": {"centroid": [...], "clips": N}, ...}}

Calibration: per-person holdout (every 4th clip). For each holdout clip,
cosine to every centroid -> reports the same-speaker score range, the
impostor range, and the best/runner-up margin distribution. Pick
SPEAKER_THRESHOLD / SPEAKER_MARGIN for the orchestrator from this output.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

DATA = Path.home() / "voice-pipeline" / "data"
CLIPS_DIR = DATA / "speaker_clips"
LABELS_PATH = DATA / "speaker_labels.jsonl"
PROFILES_PATH = DATA / "speaker_profiles.json"
EMBED_URL = "http://192.168.10.187:8096/embed"
ENROLL_LABELS = {"brad", "adrienne", "kid"}
HOLDOUT_EVERY = 4  # every 4th clip per person goes to eval, not the centroid
MIN_CLIPS = 8


def last_labels() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            out[rec["clip"]] = rec["label"]
        except Exception:
            continue
    return out


def embed(path: Path) -> list[float]:
    req = urllib.request.Request(EMBED_URL, data=path.read_bytes(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["embedding"]


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def centroid(vecs: list[list[float]]) -> list[float]:
    n = len(vecs)
    mean = [sum(col) / n for col in zip(*vecs)]
    norm = sum(v * v for v in mean) ** 0.5 or 1.0
    return [round(v / norm, 6) for v in mean]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    by_person: dict[str, list[Path]] = {}
    for clip, label in sorted(last_labels().items()):
        if label in ENROLL_LABELS and (CLIPS_DIR / clip).exists():
            by_person.setdefault(label, []).append(CLIPS_DIR / clip)

    train: dict[str, list[list[float]]] = {}
    hold: dict[str, list[tuple[str, list[float]]]] = {}
    for person, paths in sorted(by_person.items()):
        if len(paths) < MIN_CLIPS:
            print(f"[skip] {person}: only {len(paths)} clips (<{MIN_CLIPS})")
            continue
        print(f"[embed] {person}: {len(paths)} clips", flush=True)
        for i, p in enumerate(paths):
            vec = embed(p)
            if HOLDOUT_EVERY and i % HOLDOUT_EVERY == HOLDOUT_EVERY - 1:
                hold.setdefault(person, []).append((p.name, vec))
            else:
                train.setdefault(person, []).append(vec)

    if len(train) < 2:
        raise SystemExit("need >=2 enrolled people for identification; label more clips")

    cents = {p: centroid(vs) for p, vs in train.items()}

    print("\n=== holdout calibration ===")
    margins_ok: list[float] = []
    top_wrong: list[tuple[str, str, float]] = []
    for person, items in sorted(hold.items()):
        same, imp = [], []
        for name, vec in items:
            scores = sorted(((cos(vec, c), q) for q, c in cents.items()), reverse=True)
            (s1, p1), (s2, _p2) = scores[0], scores[1]
            same.append(cos(vec, cents[person]))
            imp.extend(s for s, q in scores if q != person)
            if p1 == person:
                margins_ok.append(s1 - s2)
            else:
                top_wrong.append((name, p1, s1))
        fmt = lambda xs: f"min {min(xs):.3f} / med {sorted(xs)[len(xs)//2]:.3f} / max {max(xs):.3f}"
        print(f"{person:9s} n={len(items)}  same-spk {fmt(same)}   impostor {fmt(imp)}")
    if margins_ok:
        print(f"correct-top1 margins: min {min(margins_ok):.3f} / med {sorted(margins_ok)[len(margins_ok)//2]:.3f}")
    for name, who, s in top_wrong:
        print(f"  MISID {name}: top1={who} ({s:.3f})")
    print("suggest: SPEAKER_THRESHOLD ~ midway between impostor max and same-spk min;")
    print("         SPEAKER_MARGIN   ~ half the min correct margin. Verify by eye above.")

    if args.dry_run:
        return
    payload = {
        "model": "titanet_large",
        "dim": len(next(iter(cents.values()))),
        "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profiles": {p: {"centroid": c, "clips": len(train[p])} for p, c in cents.items()},
    }
    PROFILES_PATH.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    print(f"\nwrote {PROFILES_PATH} ({', '.join(f'{p}:{v['clips']}' for p, v in payload['profiles'].items())})")


if __name__ == "__main__":
    main()
