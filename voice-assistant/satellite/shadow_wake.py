"""Shadow wake-word bench for the kitchen voice assistant.

Runs livekit-wakeword (conv-attention, ONNX) against the TONOR mic with a
sliding 2s window, logs every trigger with a pre/post audio clip plus its
peak score, logs near-miss scores and ambient RMS, and serves a tiny mark
page so deliberate wake-word tests can be labeled true-positive /
false-negative. Shadow only: no sounds, no lights, no downstream actions.

Engine history: Porcupine was the original stage-1 pick, but Picovoice
killed its free tier on 2026-06-30 (existing keys disabled). livekit-
wakeword is Apache-2.0 with an open training pipeline for custom phrases.

Labeling model (see home_config/voice-assistant-plan.md):
- trigger with a /mark within MARK_WINDOW_S  -> true positive
- /mark with no recent trigger              -> false negative (clip saved
  from the rolling buffer so the miss is still inspectable)
- trigger with no mark                      -> false positive by definition
"""

import array
import http.server
import json
import math
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MODEL_PATH = os.getenv("MODEL_PATH", "/home/pi/wake-bench/hey_livekit.onnx")
WAKE_PHRASE = os.getenv("WAKE_PHRASE", "hey livekit")
TRIGGER_THRESHOLD = float(os.getenv("TRIGGER_THRESHOLD", "0.30"))
SCORE_FLOOR = float(os.getenv("SCORE_FLOOR", "0.12"))  # log near-misses above this
HOP_MS = int(os.getenv("HOP_MS", "352"))
MIC_DEVICE = os.getenv("MIC_DEVICE", "plughw:CARD=microphone")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8781"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/home/pi/wake-bench/data"))
SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # 32ms
WINDOW_SAMPLES = 32000  # 2s, what the classifier expects
RING_SECONDS = 60
PRE_ROLL_S = 5.0
POST_ROLL_S = 3.0
FN_CLIP_S = 12.0
MARK_WINDOW_S = 8.0
AMBIENT_EVERY_S = 300
AMBIENT_RMS_WINDOW_S = 5.0
RETRIGGER_GUARD_S = 1.0
NEARMISS_THROTTLE_S = 2.0

FPS = SAMPLE_RATE / FRAME_SAMPLES
HOP_FRAMES = max(1, int(HOP_MS / 1000 * FPS))

CLIP_DIR = DATA_DIR / "clips"
EVENTS_PATH = DATA_DIR / "events.jsonl"

state_lock = threading.Lock()
recent_triggers: deque = deque(maxlen=50)  # (epoch, clip_name, peak_score)
stats = {"started": time.time(), "frames": 0, "windows": 0, "triggers": 0, "marks": 0}
ring: deque = deque(maxlen=int(RING_SECONDS * FPS))  # raw frame bytes


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def append_event(event: dict) -> None:
    event = {"ts": now_iso(), **event}
    with state_lock:
        with EVENTS_PATH.open("a") as fh:
            fh.write(json.dumps(event) + "\n")


def rms_of(frames: list[bytes]) -> int:
    if not frames:
        return 0
    samples = array.array("h")
    for fr in frames:
        samples.frombytes(fr)
    if not samples:
        return 0
    return int(math.sqrt(sum(x * x for x in samples) / len(samples)))


def write_wav(path: Path, frames: list[bytes]) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(frames))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # allow the kitchen dashboard (different origin) to call /mark
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            with state_lock:
                payload = {
                    "ok": True,
                    "mode": "shadow",
                    "engine": "livekit-wakeword",
                    "phrase": WAKE_PHRASE,
                    "trigger_threshold": TRIGGER_THRESHOLD,
                    "hop_ms": HOP_MS,
                    "uptime_s": int(time.time() - stats["started"]),
                    "frames": stats["frames"],
                    "windows": stats["windows"],
                    "triggers": stats["triggers"],
                    "marks": stats["marks"],
                }
            self._json(200, payload)
        elif self.path.startswith("/events"):
            limit = 50
            if "limit=" in self.path:
                try:
                    limit = min(500, int(self.path.split("limit=")[1].split("&")[0]))
                except ValueError:
                    pass
            lines = []
            if EVENTS_PATH.exists():
                with EVENTS_PATH.open() as fh:
                    lines = fh.readlines()[-limit:]
            self._json(200, {"events": [json.loads(l) for l in lines]})
        elif self.path.startswith("/clips/"):
            name = os.path.basename(self.path[len("/clips/"):])
            clip = CLIP_DIR / name
            if clip.is_file() and clip.suffix == ".wav":
                data = clip.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json(404, {"ok": False, "error": "no such clip"})
        elif self.path == "/" or self.path.startswith("/index"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"ok": False})

    def do_POST(self):
        if self.path != "/mark":
            self._json(404, {"ok": False})
            return
        now = time.time()
        with state_lock:
            match = next(
                (t for t in reversed(recent_triggers) if now - t[0] <= MARK_WINDOW_S),
                None,
            )
            stats["marks"] += 1
            ring_snapshot = list(ring)
        if match:
            append_event(
                {
                    "type": "mark",
                    "result": "true_positive",
                    "trigger_clip": match[1],
                    "trigger_peak_score": match[2],
                    "trigger_age_s": round(now - match[0], 1),
                }
            )
            self._json(200, {"ok": True, "result": "true_positive", "trigger_clip": match[1]})
        else:
            n = int(FN_CLIP_S * FPS)
            frames = ring_snapshot[-n:]
            clip_name = f"mark-fn-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
            write_wav(CLIP_DIR / clip_name, frames)
            append_event({"type": "mark", "result": "false_negative", "clip": clip_name})
            self._json(200, {"ok": True, "result": "false_negative", "clip": clip_name})


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wake Bench</title>
<style>
body{font-family:system-ui;margin:1rem;background:#111;color:#eee;max-width:40rem}
button{font-size:1.6rem;padding:1.2rem 2rem;border-radius:1rem;border:0;
background:#2b6;color:#fff;width:100%;cursor:pointer}
#out{margin:1rem 0;font-size:1.1rem;min-height:1.5rem}
table{width:100%;border-collapse:collapse;font-size:.85rem}
td,th{padding:.3rem;border-bottom:1px solid #333;text-align:left}
.fp{color:#f66}.tp{color:#6f6}.fn{color:#fa0}.nm{color:#888}
</style></head><body>
<h2>Wake-word bench (shadow)</h2>
<p>Phrase: <b>hey livekit</b></p>
<button onclick="mark()">I just said the wake word</button>
<div id="out"></div>
<h3>Recent events</h3>
<table id="ev"><tr><th>time</th><th>type</th><th>detail</th></tr></table>
<script>
async function mark(){
  const r = await fetch('/mark',{method:'POST'});
  const j = await r.json();
  document.getElementById('out').textContent =
    j.result === 'true_positive' ? 'Logged TRUE POSITIVE (trigger matched)' :
    'Logged FALSE NEGATIVE (no trigger in window; clip saved)';
  load();
}
async function load(){
  const r = await fetch('/events?limit=30'); const j = await r.json();
  const rows = j.events.reverse().map(e=>{
    let cls='', d='';
    if(e.type==='trigger'){cls='fp';d='peak='+e.peak_score+
      (e.clip?` <a style="color:#8cf" href="/clips/${e.clip}">clip</a>`:'');}
    if(e.type==='nearmiss'){cls='nm';d='score='+e.score;}
    if(e.type==='mark'){cls=e.result==='true_positive'?'tp':'fn';d=e.result;
      if(e.clip) d+=` <a style="color:#8cf" href="/clips/${e.clip}">clip</a>`;}
    if(e.type==='ambient'){cls='nm';d='rms='+e.rms;}
    if(e.type==='start'){d='bench started';}
    return `<tr class="${cls}"><td>${e.ts.slice(5,19)}</td><td>${e.type}</td><td>${d}</td></tr>`;
  }).join('');
  document.getElementById('ev').innerHTML =
    '<tr><th>time</th><th>type</th><th>detail</th></tr>'+rows;
}
load(); setInterval(load, 10000);
</script></body></html>"""


def main() -> int:
    # livekit-wakeword doesn't expose ORT session options; default sessions
    # spin a thread per core (~280% CPU on the Pi 4 for a 5% speedup).
    import onnxruntime as ort

    _orig_session = ort.InferenceSession

    def _capped_session(*args, **kwargs):
        if "sess_options" not in kwargs:
            so = ort.SessionOptions()
            so.intra_op_num_threads = int(os.getenv("ORT_THREADS", "1"))
            so.inter_op_num_threads = 1
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            kwargs["sess_options"] = so
        return _orig_session(*args, **kwargs)

    ort.InferenceSession = _capped_session

    from livekit.wakeword import WakeWordModel

    if not Path(MODEL_PATH).is_file():
        print(f"model missing: {MODEL_PATH}; retrying via systemd", flush=True)
        return 3
    model = WakeWordModel(models=[MODEL_PATH])
    model_key = next(iter(model.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16)).keys()))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    arecord = subprocess.Popen(
        [
            "arecord", "-D", MIC_DEVICE, "-f", "S16_LE",
            "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw", "-q",
        ],
        stdout=subprocess.PIPE,
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    append_event(
        {
            "type": "start",
            "engine": "livekit-wakeword",
            "model": os.path.basename(MODEL_PATH),
            "trigger_threshold": TRIGGER_THRESHOLD,
            "hop_ms": HOP_MS,
        }
    )
    print(
        f"shadow bench running: {model_key} threshold={TRIGGER_THRESHOLD} hop={HOP_MS}ms",
        flush=True,
    )

    frame_bytes = FRAME_SAMPLES * 2
    window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
    burst = None  # {"peak": float, "pre": [frames], "post": [frames], "remaining": int}
    guard_until = 0.0
    next_ambient = time.time() + AMBIENT_EVERY_S
    nearmiss_after = 0.0
    frames_since_hop = 0

    while True:
        chunk = arecord.stdout.read(frame_bytes)
        if not chunk or len(chunk) < frame_bytes:
            print("arecord stream ended; exiting for restart", flush=True)
            return 1
        with state_lock:
            ring.append(chunk)
            stats["frames"] += 1
        window = np.concatenate([window[FRAME_SAMPLES:], np.frombuffer(chunk, dtype=np.int16)])

        frames_since_hop += 1
        score = None
        if frames_since_hop >= HOP_FRAMES:
            frames_since_hop = 0
            score = float(model.predict(window).get(model_key, 0.0))
            with state_lock:
                stats["windows"] += 1

        now = time.time()
        if burst is not None:
            if score is not None:
                burst["peak"] = max(burst["peak"], score)
            burst["post"].append(chunk)
            burst["remaining"] -= 1
            if burst["remaining"] <= 0:
                clip_name = f"trigger-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
                frames = burst["pre"] + burst["post"]
                write_wav(CLIP_DIR / clip_name, frames)
                peak = round(burst["peak"], 3)
                append_event(
                    {
                        "type": "trigger",
                        "peak_score": peak,
                        "threshold": TRIGGER_THRESHOLD,
                        "clip": clip_name,
                        "rms": rms_of(frames),
                    }
                )
                with state_lock:
                    recent_triggers.append((now, clip_name, peak))
                    stats["triggers"] += 1
                print(f"trigger peak={peak} -> {clip_name}", flush=True)
                burst = None
                guard_until = now + RETRIGGER_GUARD_S
        elif score is not None and score >= TRIGGER_THRESHOLD and now >= guard_until:
            with state_lock:
                pre = list(ring)[-int(PRE_ROLL_S * FPS):]
            burst = {
                "peak": score,
                "pre": pre,
                "post": [],
                "remaining": int(POST_ROLL_S * FPS),
            }
        elif score is not None and score >= SCORE_FLOOR and now >= nearmiss_after:
            append_event({"type": "nearmiss", "score": round(score, 3)})
            nearmiss_after = now + NEARMISS_THROTTLE_S

        if now >= next_ambient:
            next_ambient = now + AMBIENT_EVERY_S
            with state_lock:
                w = list(ring)[-int(AMBIENT_RMS_WINDOW_S * FPS):]
            append_event({"type": "ambient", "rms": rms_of(w)})


if __name__ == "__main__":
    sys.exit(main())
