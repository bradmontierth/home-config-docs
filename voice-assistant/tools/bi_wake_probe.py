#!/usr/bin/env python3
"""Pull a window of Blue Iris camera audio and run it through the wake
pipeline, so a miss can be blamed on the mic or on the model.

Built 2026-08-31 for the range-hood miss: the kitchen UnifiCam audio verified
"Okay computer" at stage 2 (Parakeet) at two attempts the ReSpeaker/stage 1
missed, and no amount of gain got stage 1 above 0.34 on it — the wake model,
not mic position, was the bottleneck. (Answer for that day: kitchen mic s1
<0.3 and 0.56; camera s1 0.17 and 0.31; stage 2 verified both.)

  bi_wake_probe.py export  --cam UnifiCam --at "2026-08-31 17:23:45" [--pre 8 --len 22] out.wav
  bi_wake_probe.py stage2  out.wav            # 2.5 s windows every 0.5 s → /verify/probe (silent, no turn row)
  bi_wake_probe.py stage1  out.wav            # run ON the kitchen box: .venv/bin/python bi_wake_probe.py stage1 /tmp/x.wav

Blue Iris creds are read from the Node-RED flow file (function "Build BI
status request"), never embedded or printed. Notes learned the hard way:
`clips` is Access denied even for admin — use `cliplist`; the export result
downloads from `/clips/@<id>?session=` (`/file/clips/...` answers 503).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import subprocess
import sys
import time
import urllib.request
import wave
from urllib.parse import parse_qs, urlparse

FLOWS = "/home/pi/nodered/data/projects/nodered_n100_mini/flows.json"
BI = "http://192.168.10.49:81"
ORCH = "http://192.168.10.217:8785"
SR = 16000


# --- Blue Iris -------------------------------------------------------------
def _creds() -> tuple[str, str]:
    for n in json.load(open(FLOWS)):
        if n.get("type") == "function" and "BI status" in (n.get("name") or ""):
            url = re.search(r'msg\.url\s*=\s*"([^"]+)"', n["func"]).group(1)
            pu = urlparse(url)
            if pu.username and pu.password:
                return pu.username, pu.password
            q = parse_qs(pu.query)
            return q["user"][0], q["pw"][0]
    sys.exit("no 'Build BI status request' function node in the flow file")


def _post(d: dict) -> dict:
    req = urllib.request.Request(BI + "/json", json.dumps(d).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def _login() -> str:
    user, pw = _creds()
    s = _post({"cmd": "login"})["session"]
    r = _post({"cmd": "login", "session": s,
               "response": hashlib.md5(f"{user}:{s}:{pw}".encode()).hexdigest()})
    if r.get("result") != "success":
        sys.exit(f"Blue Iris login failed: {r.get('result')}")
    return s


def export(cam: str, at: float, pre: float, length: float, out: str) -> None:
    s = _login()
    clips = _post({"cmd": "cliplist", "camera": cam, "session": s,
                   "startdate": int(at - 12 * 3600), "enddate": int(at + 600)})["data"]
    hit = next((c for c in clips if c["date"] <= at <= c["date"] + c["msec"] / 1000), None)
    if not hit:
        sys.exit(f"no {cam} recording covers {dt.datetime.fromtimestamp(at)}")
    startms = int((at - pre - hit["date"]) * 1000)
    r = _post({"cmd": "export", "path": hit["path"], "startms": startms,
               "msec": int(length * 1000), "audio": True, "profile": 0, "session": s})
    job = r["data"]["path"]
    print(f"export {job} queued: {r['data'].get('uri')}", file=sys.stderr)
    for _ in range(90):
        time.sleep(2)
        mine = [x for x in _post({"cmd": "export", "session": s}).get("data", [])
                if x.get("path") == job]
        if mine and mine[0].get("status") == "done":
            break
    else:
        sys.exit("export did not finish")
    mp4 = urllib.request.urlopen(f"{BI}/clips/{job}?session={s}", timeout=120).read()
    tmp = out + ".mp4"
    open(tmp, "wb").write(mp4)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", tmp,
                    "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", out], check=True)
    print(f"wrote {out} ({len(mp4) / 1e6:.1f} MB video kept at {tmp}); "
          f"t=0 is {dt.datetime.fromtimestamp(at - pre):%H:%M:%S} (BI may snap to a keyframe — "
          f"check the export uri above)", file=sys.stderr)


# --- stage 2 ---------------------------------------------------------------
def _read(path: str) -> bytes:
    with wave.open(path) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


def _wav(pcm: bytes) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR); o.writeframes(pcm)
    return b.getvalue()


def stage2(path: str, sat: str, win: float, step: float) -> None:
    raw = _read(path)
    total = len(raw) / 2 / SR
    end = win
    while end <= total + 1e-6:
        seg = raw[int((end - win) * SR) * 2:int(end * SR) * 2]
        req = urllib.request.Request(f"{ORCH}/verify/probe?sat={sat}", _wav(seg),
                                     {"Content-Type": "audio/wav"})
        r = json.load(urllib.request.urlopen(req, timeout=30))
        print(f"t_end={end:5.1f}s verified={str(r.get('verified')):5} "
              f"score={str(r.get('score')):6} decode={str(r.get('decode')):5} "
              f"{r.get('transcript')!r}")
        end += step


# --- stage 1 (runs on the satellite box, inside its venv) -------------------
def stage1(path: str, models: list[str], gains: list[float]) -> None:
    import numpy as np
    import onnxruntime as ort
    orig = ort.InferenceSession

    def capped(*a, **k):  # one thread: never contend with the live detector
        so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
        k.setdefault("sess_options", so); return orig(*a, **k)
    ort.InferenceSession = capped
    from livekit.wakeword import WakeWordModel
    WIN, HOP = 32000, int(0.096 * SR)   # the satellite's own _score_clip geometry
    s = np.frombuffer(_read(path), dtype=np.int16).astype(np.float32)
    m = WakeWordModel(models=models)
    for gain in gains:
        g = np.clip(s * 10 ** (gain / 20), -32768, 32767).astype(np.int16)
        rows = []
        for st in range(0, len(g) - WIN + 1, HOP):
            rows.append(((st + WIN) / SR, {k: float(v) for k, v in m.predict(g[st:st + WIN]).items()}))
        for key in rows[0][1]:
            t, p = max(rows, key=lambda r: r[1][key])
            print(f"gain {gain:+.0f} dB  {key}: peak {p[key]:.3f} at t_end={t:.2f}s")
        if gain == 0:
            for t, p in rows:
                hot = {k: round(v, 3) for k, v in p.items() if v >= 0.15}
                if hot:
                    print(f"    t_end={t:5.2f}s {hot}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--cam", default="UnifiCam")
    e.add_argument("--at", required=True, help='local time, "YYYY-MM-DD HH:MM:SS"')
    e.add_argument("--pre", type=float, default=8); e.add_argument("--len", type=float, default=22)
    e.add_argument("out")
    s2 = sub.add_parser("stage2"); s2.add_argument("wav"); s2.add_argument("--sat", default="kitchen")
    s2.add_argument("--win", type=float, default=2.5); s2.add_argument("--step", type=float, default=0.5)
    s1 = sub.add_parser("stage1"); s1.add_argument("wav")
    s1.add_argument("--model", action="append", default=None)
    s1.add_argument("--gains", default="0", help="comma list of dB, e.g. -6,0,6")
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.cam, dt.datetime.strptime(a.at, "%Y-%m-%d %H:%M:%S").timestamp(), a.pre, a.len, a.out)
    elif a.cmd == "stage2":
        stage2(a.wav, a.sat, a.win, a.step)
    else:
        stage1(a.wav, a.model or ["/home/pi/wake-bench/okay_computer.onnx"],
               [float(x) for x in a.gains.split(",")])


if __name__ == "__main__":
    main()
