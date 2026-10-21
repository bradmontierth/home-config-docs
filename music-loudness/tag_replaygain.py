#!/usr/bin/env python3
"""Write ReplayGain 2.0 tags from measure.py's EBU R128 results (no re-measuring).

    tag_replaygain.py <root> <results.jsonl> [--only PATH ...] [--dry-run] > log.jsonl

Reference -18 LUFS. Track gain = -18 - integrated loudness. Album loudness is the
duration-weighted energy mean of the album's tracks (a close approximation of measuring
the album as one piece, which is what rsgain does; gating makes them differ by a few
tenths of a dB at most). Peaks are SAMPLE peaks (measure.py), written as linear ratios.
Gains are written unclamped, as the standard expects; players cap boosts.

Songs that decode as silence (<= -60 LUFS or no peak) get no tags: a +50 dB "gain" would
be dangerous in any player that doesn't cap.
"""
import argparse
import json
import math
import os
import sys

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TXXX, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4FreeForm

REF = -18.0
KEYS = ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK", "REPLAYGAIN_ALBUM_GAIN",
        "REPLAYGAIN_ALBUM_PEAK", "REPLAYGAIN_REFERENCE_LOUDNESS")


def silent(x):
    return x.get("error") or x.get("lufs") is None or x["lufs"] <= -60 or x.get("peak") is None


def album_values(rows):
    albums = {}
    for x in rows:
        if not silent(x):
            albums.setdefault(x.get("album_id") or x["path"].rsplit("/", 1)[0], []).append(x)
    out = {}
    for key, xs in albums.items():
        weight = sum(max(x.get("duration") or 0, 1) for x in xs)
        energy = sum(max(x.get("duration") or 0, 1) * 10 ** (x["lufs"] / 10) for x in xs) / weight
        out[key] = (10 * math.log10(energy), max(x["peak"] for x in xs))
    return out


def values(x, album):
    a_lufs, a_peak = album
    return {
        "REPLAYGAIN_TRACK_GAIN": f"{REF - x['lufs']:.2f} dB",
        "REPLAYGAIN_TRACK_PEAK": f"{10 ** (x['peak'] / 20):.6f}",
        "REPLAYGAIN_ALBUM_GAIN": f"{REF - a_lufs:.2f} dB",
        "REPLAYGAIN_ALBUM_PEAK": f"{10 ** (a_peak / 20):.6f}",
        "REPLAYGAIN_REFERENCE_LOUDNESS": f"{REF:.2f} LUFS",
    }


def write(path, tags):
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "flac":
        f = FLAC(path)
        for k, v in tags.items():
            f[k] = v
        f.save()
    elif ext == "mp3":
        try:
            f = ID3(path)
        except ID3NoHeaderError:
            f = ID3()
        for k, v in tags.items():
            f.delall(f"TXXX:{k}")
            f.add(TXXX(encoding=3, desc=k, text=[v]))
        f.save(path)
    elif ext in ("m4a", "mp4", "aac"):
        f = MP4(path)
        for k, v in tags.items():
            f[f"----:com.apple.iTunes:{k}"] = [MP4FreeForm(v.encode())]
        f.save()
    else:
        raise ValueError(f"unsupported format: {ext}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("results")
    ap.add_argument("--only", nargs="*", help="library-relative paths to tag (default: all)")
    ap.add_argument("--only-file", help="file with one library-relative path per line")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.results)]
    albums = album_values(rows)
    only = set(a.only or [])
    if a.only_file:
        only |= {l.rstrip("\n") for l in open(a.only_file) if l.strip()}
    done = skipped = failed = 0
    for x in rows:
        if only and x["path"] not in only:
            continue
        rec = {"path": x["path"], "id": x.get("id")}
        if silent(x):
            rec["skipped"] = "silent/unmeasured"
            skipped += 1
        else:
            tags = values(x, albums[x.get("album_id") or x["path"].rsplit("/", 1)[0]])
            rec["tags"] = tags
            if not a.dry_run:
                try:
                    write(os.path.join(a.root, x["path"]), tags)
                    done += 1
                except Exception as e:  # keep going, report
                    rec["error"] = f"{type(e).__name__}: {e}"[:300]
                    failed += 1
        print(json.dumps(rec), flush=True)
    print(json.dumps({"summary": {"tagged": done, "skipped": skipped, "failed": failed, "dry_run": a.dry_run}}), file=sys.stderr)


if __name__ == "__main__":
    main()
