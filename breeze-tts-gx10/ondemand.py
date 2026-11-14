#!/usr/bin/env python3
"""Breeze on demand (GX10 :7862, systemd --user unit breeze-ondemand.service).

Breeze (container breeze-tts-eval, :7860) is stopped by default since Home Agent Listen moved
to Ming (2026-11-12); storybook renders still need it. A client calls POST /ensure before
each generation: if Breeze is down this starts it and blocks until /health answers (~2 min
warmup), otherwise it returns at once. The helper stops Breeze again after IDLE_S without an
/ensure, but ONLY if the helper started it: a Breeze someone started by hand is left alone.

Memory guard: Breeze holds ~12-14 GB of unified memory, and the GX10 rule is >= 20 GB
MemAvailable left over, so it refuses to start below MIN_AVAIL_GB (503 + reason).
Never touches breeze-longform (port 7861 belongs to ming-longform now).

GET /status -> {"running", "healthy", "owned", "idle_s", "mem_available_gb"}
"""
import json, os, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONTAINER = os.environ.get("BREEZE_CONTAINER", "breeze-tts-eval")
HEALTH = os.environ.get("BREEZE_HEALTH", "http://127.0.0.1:7860/health")
PORT = int(os.environ.get("PORT", "7862"))
IDLE_S = int(os.environ.get("IDLE_S", "1200"))
MIN_AVAIL_GB = float(os.environ.get("MIN_AVAIL_GB", "34"))
START_TIMEOUT_S = int(os.environ.get("START_TIMEOUT_S", "420"))
STATE = Path(os.environ.get("STATE_FILE", Path.home() / ".local/state/breeze-ondemand.json"))

lock = threading.Lock()  # one start at a time; concurrent /ensure calls wait on it
state = {"owned": False, "last_used": 0.0}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def save():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def running():
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                       capture_output=True, text=True)
    return r.stdout.strip() == "true"


def healthy():
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def mem_available_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 / 1024
    return 0.0


def ensure():
    """-> (http status, body)"""
    with lock:
        state["last_used"] = time.time(); save()
        if healthy():
            return 200, {"ready": True, "started": False}
        if not running():
            avail = mem_available_gb()
            if avail < MIN_AVAIL_GB:
                log(f"refused start: MemAvailable {avail:.1f} GB < {MIN_AVAIL_GB} GB")
                return 503, {"ready": False, "error": f"GX10 has {avail:.1f} GB available, Breeze needs {MIN_AVAIL_GB:.0f} GB free to start"}
            log(f"starting {CONTAINER} (MemAvailable {avail:.1f} GB)")
            subprocess.run(["docker", "start", CONTAINER], check=True, capture_output=True)
            state["owned"] = True; save()
        t0 = time.time()
        while time.time() - t0 < START_TIMEOUT_S:
            if healthy():
                log(f"{CONTAINER} healthy after {time.time() - t0:.0f} s")
                state["last_used"] = time.time(); save()
                return 200, {"ready": True, "started": True, "warmup_s": round(time.time() - t0)}
            if not running():
                return 500, {"ready": False, "error": f"{CONTAINER} exited during warmup, see docker logs"}
            time.sleep(3)
        return 504, {"ready": False, "error": f"{CONTAINER} not healthy after {START_TIMEOUT_S} s"}


def reaper():
    while True:
        time.sleep(60)
        with lock:
            if not state["owned"]:
                continue
            if not running():
                log(f"{CONTAINER} no longer running; dropping ownership")
                state["owned"] = False; save(); continue
            idle = time.time() - state["last_used"]
            if idle > IDLE_S:
                log(f"stopping {CONTAINER} after {idle / 60:.0f} min idle")
                subprocess.run(["docker", "stop", CONTAINER], capture_output=True)
                state["owned"] = False; save()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.rstrip("/") == "/ensure":
            return self._send(*ensure())
        self._send(404, {"error": "not found"})

    def do_GET(self):
        if self.path.rstrip("/") == "/status":
            return self._send(200, {"running": running(), "healthy": healthy(), "owned": state["owned"],
                                    "idle_s": round(time.time() - state["last_used"]) if state["last_used"] else None,
                                    "idle_stop_s": IDLE_S, "mem_available_gb": round(mem_available_gb(), 1)})
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # /ensure is called per line; keep the journal to starts and stops


if __name__ == "__main__":
    if STATE.exists():
        state.update(json.loads(STATE.read_text()))
    threading.Thread(target=reaper, daemon=True).start()
    log(f"breeze-ondemand on :{PORT}, container {CONTAINER}, idle stop {IDLE_S} s, owned={state['owned']}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
