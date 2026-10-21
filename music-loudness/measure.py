#!/usr/bin/env python3
"""EBU R128 loudness of music files, one ffmpeg pass each at nice 19 (read-only).

Same measurement as Media Center's server/app/loudness.py: integrated loudness (LUFS),
loudness range (LU) and SAMPLE peak (dBFS). True peak costs ~6x on the Pi and reads at
most ~1 dB higher; a limiter covers the difference.

    measure.py <root> < songs.json > results.jsonl     (songs.json: [{"id", "path", ...}])
"""
import json
import os
import re
import subprocess
import sys
import time

SUMMARY = {
    "lufs": re.compile(r"Integrated loudness:\s+I:\s+(-?[0-9.]+|-inf) LUFS"),
    "lra": re.compile(r"Loudness range:\s+LRA:\s+(-?[0-9.]+) LU"),
    "peak": re.compile(r"Sample peak:\s+Peak:\s+(-?[0-9.]+|-inf) dBFS"),
}


def measure(path):
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-nostats", "-hide_banner", "-threads", "1", "-i", path,
         "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", "ebur128=peak=sample", "-f", "null", "-"],
        capture_output=True, text=True, timeout=3600, preexec_fn=lambda: os.nice(19))
    tail = proc.stderr[proc.stderr.rfind("Summary:"):] if "Summary:" in proc.stderr else ""
    vals = {}
    for k, rx in SUMMARY.items():
        m = rx.search(tail)
        vals[k] = None if not m or m.group(1) == "-inf" else float(m.group(1))
    if vals["lufs"] is None:
        raise RuntimeError(f"no loudness summary (rc={proc.returncode}): {proc.stderr[-300:]}")
    return vals


def main():
    root = sys.argv[1]
    for song in json.load(sys.stdin):
        t = time.time()
        row = dict(song)
        try:
            row.update(measure(os.path.join(root, song["path"])))
        except Exception as e:  # keep going; report the failure
            row["error"] = str(e)[:300]
        row["seconds"] = round(time.time() - t, 1)
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
