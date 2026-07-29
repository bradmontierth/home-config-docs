#!/usr/bin/env python3
"""Carve long unattended alarm rings into background clips for stop-model v3.

Why slicing at all (vs handing the trainer the whole 40-52s file): the
livekit-wakeword augmenter draws a RANDOM background FILE per sample and
crops clip_duration (2.0s) out of it, so draw weight is per-FILE, not
per-second — a 52s ring and a 3s clip are equally likely. v2 worked around
that by duplicating 9 short bodies x10, which the 2026-07-25 second-look
analysis suspected of overfitting (v2 beat v1 on contaminated clips and lost
on unseen ones). Slicing gives the same draw weight with ZERO duplicated
audio: every output file is a distinct, non-overlapping span.

Unlike the 2026-07-24 carves, these rings need no stop-onset labeling — they
were rung out in an empty kitchen (2026-07-28), so the only speech in them is
the TTS announcement, which is correctly background: the model must not fire
on it either.

Slice length is the per-theme weighting lever (a shorter slice on the same
audio = more files = more draws, still with no repeated audio). The 2026-07-28
capture showed v1 false-fires are periodic at the ding cadence and strongly
theme-dependent — marimba fired on essentially EVERY ding (peaks 0.837/0.849)
while steam_whistle never cleared 0.24 — so marimba is sliced finer.

Usage: slice_ring_backgrounds.py [--seg SECONDS] <out_dir> <in.wav> [in.wav ...]
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

SEG_S = 3.0          # >= the 2.0s model window, so a crop always fits
MIN_TAIL_S = 2.5     # drop a trailing remainder shorter than this


def slice_wav(src: Path, out_dir: Path, seg_s: float = SEG_S) -> int:
    with wave.open(str(src)) as w:
        rate, chans, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        pcm = w.readframes(w.getnframes())
    frame = chans * width
    seg = int(seg_s * rate) * frame
    keep = int(min(MIN_TAIL_S, seg_s) * rate) * frame
    stem = src.stem.replace("ring-", "ringseg-")
    n = 0
    for i in range(0, len(pcm), seg):
        chunk = pcm[i:i + seg]
        if len(chunk) < keep:
            break
        with wave.open(str(out_dir / f"{stem}-s{n:02d}.wav"), "wb") as o:
            o.setnchannels(chans)
            o.setsampwidth(width)
            o.setframerate(rate)
            o.writeframes(chunk)
        n += 1
    return n


def main() -> int:
    argv = sys.argv[1:]
    seg_s = SEG_S
    if argv and argv[0] == "--seg":
        seg_s = float(argv[1])
        argv = argv[2:]
    if len(argv) < 2:
        print(__doc__)
        return 2
    out_dir = Path(argv[0])
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for path in argv[1:]:
        n = slice_wav(Path(path), out_dir, seg_s)
        print(f"{Path(path).name}: {n} segments ({seg_s}s)")
        total += n
    print(f"total: {total} segments in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
