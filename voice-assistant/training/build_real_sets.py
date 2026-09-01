#!/usr/bin/env python3
"""Turn the archived wake corpus into trainer-ready real-clip sets.

Plan: home_config/wake-retrain-plan.md §4. Stdlib only, so it runs on the
Beelink (which has the corpus, the turns snapshot and reaches the
orchestrator) and on the GX10 (inject step).

  build_real_sets.py build  [--corpus /home/pi/wake-corpus] [--out <corpus>/real_sets/<date>]
                            [--db <corpus>/orchestrator-snapshot.db] [--test-days 14]
                            [--no-probe] [--probe-limit N] [--labels <set>/ambiguous_labels.jsonl]
  build_real_sets.py inject --sets <out dir> --model-dir /work/output/okay_computer_v2
                            [--dup-positive 10] [--backgrounds-dir /work/data/backgrounds_real]

What `build` does, per clip in <corpus>/<sat>/clips/:
  verify-ok-*   -> positive            (Parakeet matched the phrase)
  verify-rej-*  -> background          if the turn's transcript is empty (stage-1 fired on non-speech)
                -> negative            if it is other speech
                -> ambiguous (held out) if the transcript starts "okay"/"ok" — a human decides
  near-*        -> POST to /verify/probe (silent stage 2, cached in near_probe_cache.jsonl)
                   verified -> positive (label missed_positive, the gold set); else as verify-rej
  mark-*        -> positive            (hand-tagged miss from /review)
Labels come from the turns table (joined by sat + filename timestamp ±3 s;
`turns.clip` is clobbered on confirmed wakes so the name is not the key).
Positives are trimmed to the phrase: trailing silence cut, then the last
<=1.6 s kept — the livekit augmentor end-aligns positives into its 2 s window.
Split is BY TIME: the newest --test-days go to *_test, the rest to *_train.
Output: {positive,negative,background}_{train,test}/<sat>-<clip>.wav,
ambiguous/, manifest.csv, summary.txt.

`--labels` is the human pass from tools/ambiguous_label_server.py (:8797),
one JSON line per clip {"key": "<sat>/<clip>", "label": "wake|not|unsure"},
last wins. It re-routes ambiguous clips — wake -> positive (label human_wake),
not -> negative (human_not) — and drops a short-flagged positive labelled not.
Anything unsure or unlabelled stays held out, exactly as before.

`inject` copies a built set into the trainer's model dir as clip_NNNNNN.wav
(numbering continues after the TTS clips, so run it AFTER `generate` and
BEFORE `augment`), repeating positive_train --dup-positive times, and drops
background_train into --backgrounds-dir for the augmentor's background mix.
"""
from __future__ import annotations

import argparse
import array
import csv
import datetime as dt
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.request
import wave
from collections import Counter, defaultdict
from pathlib import Path

SR = 16000
PI_SATS = ("kitchen", "familyroom", "master")
NAME_RE = re.compile(r"^(verify-ok|verify-rej|near|mark)-(\d{8})-(\d{6})\.wav$")
OKAY_RE = re.compile(r"^\W*(okay|ok)\b", re.I)
ORCH = os.environ.get("ORCHESTRATOR_URL", "http://192.168.10.217:8785")
JOIN_S = 3.0
KEEP_S = 1.6          # phrase length kept at the end of a positive
TAIL_PAD_S = 0.10     # silence left after the trimmed phrase
MIN_PHRASE_S = 0.5    # shorter than this after trimming is suspicious


# --- audio -----------------------------------------------------------------
def read_wav(path: Path) -> array.array:
    with wave.open(str(path)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16 kHz mono s16")
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        return a


def write_wav(path: Path, samples: array.array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(samples.tobytes())


def frame_rms_db(samples: array.array, frame: int = 320) -> list[float]:
    out = []
    for i in range(0, len(samples) - frame + 1, frame):
        acc = 0
        for v in samples[i:i + frame]:
            acc += v * v
        out.append(10 * math.log10(acc / frame / (32768 ** 2) + 1e-12))
    return out


def trim_positive(samples: array.array) -> tuple[array.array, float]:
    """Cut trailing silence, keep the last KEEP_S. Returns (audio, voiced_s)
    where voiced_s is how much of the kept audio is above the floor — the
    manifest flags anything under MIN_PHRASE_S for a human ear."""
    frame = 320  # 20 ms
    db = frame_rms_db(samples, frame)
    if not db:
        return samples, 0.0
    peak = max(db)
    floor = max(peak - 25.0, -55.0)
    last = max((i for i, v in enumerate(db) if v >= floor), default=len(db) - 1)
    end = min(len(samples), (last + 1) * frame + int(TAIL_PAD_S * SR))
    start = max(0, end - int(KEEP_S * SR))
    kept = samples[start:end]
    voiced = sum(1 for v in db[start // frame:(end // frame)] if v >= floor) * frame / SR
    return kept, round(voiced, 2)


# --- turns -----------------------------------------------------------------
def load_turns(db: Path) -> dict[str, list[dict]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    by_sat: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(
            "SELECT turn_id, at, sat, kind, verified, reject_reason, transcript,"
            "       wake_score, stage1_score, speaker, decode, clip"
            "  FROM turns WHERE kind IN ('wake','near_miss') AND sat IN (?,?,?)"
            " ORDER BY at", PI_SATS):
        by_sat[r["sat"]].append(dict(r))
    return by_sat


def match_turn(turns: list[dict], kind: str, ts: float, clip: str) -> dict | None:
    """Nearest turn of the right kind within JOIN_S; exact clip name wins."""
    want = "near_miss" if kind == "near" else "wake"
    best, best_d = None, JOIN_S + 1
    for t in turns:
        if t["kind"] != want:
            continue
        if t.get("clip") == clip:
            return t
        d = abs(t["at"] - ts)
        if d < best_d:
            if kind == "verify-ok" and t["verified"] != 1:
                continue
            if kind == "verify-rej" and t["verified"] != 0:
                continue
            best, best_d = t, d
    return best if best_d <= JOIN_S else None


# --- probe -----------------------------------------------------------------
def probe(sat: str, path: Path) -> dict:
    req = urllib.request.Request(f"{ORCH}/verify/probe?sat={sat}", path.read_bytes(),
                                 {"Content-Type": "audio/wav"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def load_cache(p: Path) -> dict[str, dict]:
    out = {}
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                d = json.loads(line); out[d["key"]] = d
            except (ValueError, KeyError):
                pass
    return out


# --- build -----------------------------------------------------------------
def classify(kind: str, turn: dict | None, verdict: dict | None) -> tuple[str, str]:
    """(set, label). set in positive/negative/background/ambiguous/unjoined."""
    if kind == "mark":
        return "positive", "mark"
    if kind == "verify-ok":
        return "positive", "verified"
    if kind == "verify-rej":
        if turn is None:
            return "unjoined", "verify-rej"
        text = (turn.get("transcript") or "").strip()
        if turn.get("reject_reason") == "suppressed":
            return "unjoined", "suppressed"
        if not text:
            return "background", "empty"
        if OKAY_RE.match(text):
            return "ambiguous", "okay-prefix"
        return "negative", "low_score"
    # near
    if verdict is None:
        return "unjoined", "near-unprobed"
    if verdict.get("verified"):
        return "positive", "missed_positive"
    text = (verdict.get("transcript") or "").strip()
    if not text:
        return "background", "near-empty"
    if OKAY_RE.match(text):
        return "ambiguous", "near-okay-prefix"
    return "negative", "near-speech"


def load_human_labels(p: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if p and p.exists():
        for line in p.read_text().splitlines():
            try:
                d = json.loads(line)
                if d.get("label") in ("wake", "not", "unsure"):
                    out[d["key"]] = d["label"]
            except (ValueError, KeyError):
                pass
    return out


def build(a: argparse.Namespace) -> None:
    corpus = Path(a.corpus)
    human = load_human_labels(Path(a.labels) if a.labels else None)
    out = Path(a.out or corpus / "real_sets" / dt.date.today().isoformat())
    out.mkdir(parents=True, exist_ok=True)
    turns = load_turns(Path(a.db))
    cache_path = corpus / "near_probe_cache.jsonl"
    cache = load_cache(cache_path)
    cutoff = time.time() - a.test_days * 86400
    rows, counts = [], Counter()
    probed = 0
    for sat in PI_SATS:
        clip_dir = corpus / sat / "clips"
        if not clip_dir.is_dir():
            continue
        for path in sorted(clip_dir.glob("*.wav")):
            m = NAME_RE.match(path.name)
            if not m:
                continue
            kind, d, t = m.groups()
            ts = dt.datetime.strptime(d + t, "%Y%m%d%H%M%S").timestamp()
            turn = match_turn(turns[sat], kind, ts, path.name)
            verdict = None
            if kind == "near":
                key = f"{sat}/{path.name}"
                if key in cache:
                    verdict = cache[key]["verdict"]
                elif not a.no_probe and (a.probe_limit is None or probed < a.probe_limit):
                    try:
                        verdict = probe(sat, path); probed += 1
                        with cache_path.open("a") as fh:
                            fh.write(json.dumps({"key": key, "verdict": verdict,
                                                 "at": time.time()}) + "\n")
                        time.sleep(0.05)
                    except Exception as exc:  # noqa: BLE001
                        print(f"probe failed {key}: {exc}", file=sys.stderr)
            set_, label = classify(kind, turn, verdict)
            heard = human.get(f"{sat}/{path.name}")
            if set_ == "ambiguous" and heard == "wake":
                set_, label = "positive", "human_wake"
            elif set_ == "ambiguous" and heard == "not":
                set_, label = "negative", "human_not"
            split = "test" if ts >= cutoff else "train"
            voiced = None
            dest = None
            if set_ == "positive":
                kept, voiced = trim_positive(read_wav(path))
                if voiced < MIN_PHRASE_S and heard == "not":
                    set_, label = "dropped", "short-not"
            if set_ in ("positive", "negative", "background"):
                dest = out / f"{set_}_{split}" / f"{sat}-{path.name}"
                if set_ == "positive":
                    write_wav(dest, kept)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, dest)
            elif set_ == "ambiguous":
                dest = out / "ambiguous" / f"{sat}-{path.name}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
            counts[(set_, split, sat)] += 1
            rows.append({
                "sat": sat, "clip": path.name, "kind": kind, "set": set_, "label": label,
                "split": split if set_ in ("positive", "negative", "background") else "",
                "at": dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
                "speaker": (turn or {}).get("speaker") or "",
                "stage1": (turn or {}).get("stage1_score"),
                "stage2_score": (verdict or {}).get("score", (turn or {}).get("wake_score")),
                "transcript": (verdict or {}).get("transcript", (turn or {}).get("transcript")) or "",
                "decode": (verdict or {}).get("decode", (turn or {}).get("decode")) or "",
                "voiced_s": voiced,
                "flag": "short" if voiced is not None and voiced < MIN_PHRASE_S else "",
                "out": str(dest.relative_to(out)) if dest else "",
            })
    with (out / "manifest.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    lines = [f"real sets built {dt.datetime.now():%Y-%m-%d %H:%M} from {corpus} "
             f"(db {a.db}, test = newest {a.test_days} d, probed {probed} new near clips)", ""]
    for set_ in ("positive", "negative", "background", "ambiguous", "dropped", "unjoined"):
        for split in ("train", "test"):
            per = {s: counts[(set_, split, s)] for s in PI_SATS if counts[(set_, split, s)]}
            if per:
                lines.append(f"{set_:10} {split:5} {sum(per.values()):5}  {per}")
    pos = [r for r in rows if r["set"] == "positive"]
    by_label = Counter(r["label"] for r in pos)
    by_spk = Counter(r["speaker"] or "?" for r in pos)
    short = sum(1 for r in pos if r["flag"] == "short")
    lines += ["", f"positives by label: {dict(by_label)}",
              f"positives by speaker: {dict(by_spk)}",
              f"positives flagged short (<{MIN_PHRASE_S}s voiced): {short} — listen before training",
              f"ambiguous clips for a human ear: {sum(1 for r in rows if r['set'] == 'ambiguous')}"
              f" (human labels applied: {sum(1 for r in rows if r['label'] in ('human_wake', 'human_not', 'short-not'))})"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n-> {out}")


# --- inject ----------------------------------------------------------------
def inject(a: argparse.Namespace) -> None:
    sets, model_dir = Path(a.sets), Path(a.model_dir)
    for split in ("positive_train", "positive_test", "negative_train", "negative_test",
                  "background_train", "background_test"):
        src = sets / split
        if not src.is_dir():
            continue
        dst = model_dir / split
        dst.mkdir(parents=True, exist_ok=True)
        existing = [int(m.group(1)) for p in dst.glob("clip_*.wav")
                    if (m := re.match(r"^clip_(\d{6})\.wav$", p.name))]
        n = max(existing, default=-1) + 1
        reps = a.dup_positive if split == "positive_train" else 1
        files = sorted(src.glob("*.wav"))
        for _ in range(reps):
            for f in files:
                shutil.copyfile(f, dst / f"clip_{n:06d}.wav"); n += 1
        print(f"{split}: +{len(files) * reps} real clips ({len(files)} unique x{reps}) "
              f"-> {dst} now clip_{n - 1:06d}")
    if a.backgrounds_dir:
        bg = Path(a.backgrounds_dir); bg.mkdir(parents=True, exist_ok=True)
        files = sorted((sets / "background_train").glob("*.wav"))
        for f in files:
            shutil.copyfile(f, bg / f.name)
        print(f"backgrounds: {len(files)} real-room clips -> {bg} (add to augmentation.background_paths)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--corpus", default="/home/pi/wake-corpus")
    b.add_argument("--out")
    b.add_argument("--db", default="/home/pi/wake-corpus/orchestrator-snapshot.db")
    b.add_argument("--test-days", type=int, default=14)
    b.add_argument("--no-probe", action="store_true")
    b.add_argument("--probe-limit", type=int)
    b.add_argument("--labels", help="ambiguous_labels.jsonl from tools/ambiguous_label_server.py")
    i = sub.add_parser("inject")
    i.add_argument("--sets", required=True); i.add_argument("--model-dir", required=True)
    i.add_argument("--dup-positive", type=int, default=10)
    i.add_argument("--backgrounds-dir")
    a = ap.parse_args()
    (build if a.cmd == "build" else inject)(a)


if __name__ == "__main__":
    main()
