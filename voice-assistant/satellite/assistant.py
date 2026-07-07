#!/usr/bin/env python3
"""Kitchen voice satellite — the last mile on the Pi.

Owns the mic. Runs livekit-wakeword stage-1 continuously; on a trigger it drives
the two-phase pipeline against the Beelink orchestrator and plays local audio
feedback. Alarms are serviced inline in the capture loop so there is always a
single mic reader.

Flow (active mode):
  stage-1 trigger  -> snapshot ~2.5s pre-roll -> POST /verify
  verified         -> play wake chime -> DRAIN mic (drop chime bleed)
                   -> capture command (webrtcvad endpoint) -> VAD chime
                   -> POST /command/audio -> play spoken reply
  not verified     -> nothing (stage-2 kills the permissive stage-1 FAs)

Alarm (timer expiry, orchestrator POSTs /alarm):
  queued -> main loop services it inline: play themed sound + announcement,
  then LISTEN in the gap (Parakeet) for a barge-in "stop"/"cancel"/"okay
  computer" — no wake word needed, like Echo/Nest. Loops until dismissed or
  timeout.

Modes (kill switch, HTTP POST /mode {active|shadow|off}, default shadow):
  active  full pipeline
  shadow  detect + log only; no chime, no action
  off     mic pipeline paused
"""

import http.server
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# --- config ----------------------------------------------------------------
MODEL_PATH = os.getenv("MODEL_PATH", "/home/pi/wake-bench/okay_computer.onnx")
WAKE_PHRASE = os.getenv("WAKE_PHRASE", "okay computer")
TRIGGER_THRESHOLD = float(os.getenv("TRIGGER_THRESHOLD", "0.5"))
HOP_MS = int(os.getenv("HOP_MS", "352"))
MIC_DEVICE = os.getenv("MIC_DEVICE", "plughw:CARD=microphone")
PLAYBACK_DEVICE = os.getenv("PLAYBACK_DEVICE", "plughw:CARD=Headphones")
ORCH_BASE = os.getenv("ORCH_BASE", "http://192.168.10.217:8785")
SOUNDS_DIR = Path(os.getenv("SOUNDS_DIR", "/home/pi/voice-pipeline/sounds"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/home/pi/voice-pipeline/data"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8781"))
START_MODE = os.getenv("MODE", "shadow")

PREROLL_S = float(os.getenv("PREROLL_S", "2.5"))
VAD_AGGRESSIVENESS = int(os.getenv("VAD_AGGRESSIVENESS", "2"))
# Command capture: keep grabbing the stream (like Echo/Nest — supports
# "okay computer set a timer" run together) for a minimum window, THEN endpoint
# on normal trailing silence. The min window is what stops the wake-chime bleed
# from ending capture before you speak.
MIN_CAPTURE_MS = int(os.getenv("MIN_CAPTURE_MS", "3000"))
SILENCE_MS = int(os.getenv("SILENCE_MS", "700"))
MAX_COMMAND_S = float(os.getenv("MAX_COMMAND_S", "8"))
MIN_VOICED_MS = int(os.getenv("MIN_VOICED_MS", "200"))
RETRIGGER_GUARD_S = float(os.getenv("RETRIGGER_GUARD_S", "1.5"))

# Follow-up / continued conversation: after a reply, reopen the mic (no wake
# word) for a bounded window; re-arm each time you actually speak. Default ON.
FOLLOWUP_ENABLED = os.getenv("FOLLOWUP_ENABLED", "1").lower() not in ("0", "false", "no", "")
FOLLOWUP_WINDOW_MS = int(os.getenv("FOLLOWUP_WINDOW_MS", "7000"))  # wait-for-speech window
FOLLOWUP_MIN_MS = int(os.getenv("FOLLOWUP_MIN_MS", "300"))        # min capture (no chime bleed)
FOLLOWUP_MAX_TURNS = int(os.getenv("FOLLOWUP_MAX_TURNS", "6"))    # runaway-session cap

# Playback volume (0-100) applied as software gain to OUR audio only (chimes,
# alarm, TTS) — music on the shared card is untouched. Driven by day-mode via
# Node-RED POST /volume (mirrors the /mode switch). Alarm never dips below the
# floor so a cooking timer stays audible even at night.
VOLUME_DEFAULT = int(os.getenv("VOLUME_DEFAULT", "50"))
ALARM_VOLUME_FLOOR = int(os.getenv("ALARM_VOLUME_FLOOR", "50"))

ALARM_MAX_LOOPS = int(os.getenv("ALARM_MAX_LOOPS", "14"))
ALARM_GAP_S = float(os.getenv("ALARM_GAP_S", "2.0"))         # space between beeps
STOP_CHUNK_MS = int(os.getenv("STOP_CHUNK_MS", "1000"))      # continuous-listen cadence
DISMISS_WORDS = ("stop", "cancel", "okay computer", "ok computer",
                 "dismiss", "turn off", "enough", "quiet", " off")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
WINDOW_SAMPLES = 32000
VAD_FRAME_SAMPLES = 320       # 20ms
RING_SECONDS = 8
FPS = SAMPLE_RATE / FRAME_SAMPLES
HOP_FRAMES = max(1, int(HOP_MS / 1000 * FPS))

CLIP_LOCK = threading.Lock()
PLAYBACK_LOCK = threading.Lock()
EVENTS_PATH = DATA_DIR / "events.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


def append_event(event: dict) -> None:
    event = {"ts": now_iso(), **event}
    with CLIP_LOCK:
        with EVENTS_PATH.open("a") as fh:
            fh.write(json.dumps(event) + "\n")


# --- shared state ----------------------------------------------------------
VOLUME_FILE = DATA_DIR / "volume"


class State:
    def __init__(self, mode: str):
        self.lock = threading.Lock()
        self.mode = mode
        self.volume = VOLUME_DEFAULT
        try:
            self.volume = max(0, min(100, int(VOLUME_FILE.read_text().strip())))
        except Exception:  # noqa: BLE001 — no persisted volume yet
            pass
        self.alarm_queue: list[dict] = []
        self.current_alarm: str | None = None
        self.dismiss = threading.Event()
        self.stats = {"started": time.time(), "triggers": 0, "turns": 0}

    def get_mode(self) -> str:
        with self.lock:
            return self.mode

    def set_mode(self, mode: str) -> None:
        with self.lock:
            self.mode = mode

    def set_volume(self, level: int) -> int:
        level = max(0, min(100, int(level)))
        with self.lock:
            self.volume = level
        try:
            VOLUME_FILE.write_text(str(level))
        except Exception:  # noqa: BLE001
            pass
        return level

    def volume_factor(self, is_alarm: bool) -> float:
        with self.lock:
            vol = self.volume
        if is_alarm:
            vol = max(vol, ALARM_VOLUME_FLOOR)
        return max(0.0, min(1.0, vol / 100.0))

    def enqueue_alarm(self, req: dict) -> None:
        with self.lock:
            self.alarm_queue.append(req)


STATE = State(START_MODE)


# --- HTTP helpers ----------------------------------------------------------
def post_wav(path: str, wav_bytes: bytes, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        ORCH_BASE + path, data=wav_bytes,
        headers={"Content-Type": "audio/wav"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def post_json(path: str, obj: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        ORCH_BASE + path, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_bytes(url: str, timeout: float = 30) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def wrap_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


# --- audio in/out ----------------------------------------------------------
def drain_input(stdout) -> None:
    """Discard whatever the mic buffered during our own playback (chime/alarm
    bleed) so the next capture starts on live audio, not our own sound."""
    fd = stdout.fileno()
    os.set_blocking(fd, False)
    try:
        while True:
            b = stdout.read(65536)
            if not b:
                break
    except (BlockingIOError, TypeError, OSError):
        pass
    finally:
        os.set_blocking(fd, True)


def _scale_wav(wav_bytes: bytes, factor: float) -> bytes:
    """Apply a software gain to 16-bit PCM WAV bytes (our audio only — the mixer
    stays put so music on the shared card is unaffected)."""
    if factor >= 0.999:
        return wav_bytes
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        s = np.frombuffer(frames, dtype=np.int16).astype(np.float32) * factor
        out = np.clip(s, -32768, 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setparams(params)
            w.writeframes(out)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — on any parse issue, play unscaled
        return wav_bytes


def play_wav_bytes(wav_bytes: bytes, is_alarm: bool = False) -> None:
    wav_bytes = _scale_wav(wav_bytes, STATE.volume_factor(is_alarm))
    with PLAYBACK_LOCK:
        try:
            subprocess.run(["aplay", "-q", "-D", PLAYBACK_DEVICE, "-"],
                           input=wav_bytes, timeout=30, check=False)
        except Exception as exc:  # noqa: BLE001
            log(f"aplay bytes failed: {exc}")


def play_file(path: Path, is_alarm: bool = False) -> None:
    if not path.exists():
        log(f"missing sound {path}")
        return
    try:
        data = path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        log(f"read sound failed ({path}): {exc}")
        return
    play_wav_bytes(data, is_alarm=is_alarm)


def theme_sound(theme: str) -> Path:
    p = SOUNDS_DIR / "themes" / f"{theme}.wav"
    return p if p.exists() else SOUNDS_DIR / "themes" / "marimba.wav"


# --- command capture (min window, then webrtcvad endpointing) --------------
def capture_command(stdout, vad, min_capture_ms: int = MIN_CAPTURE_MS,
                    onset_ms: int | None = None) -> bytes:
    """Capture one utterance. Two independent windows:
      - `onset_ms`: how long to wait for speech to START before giving up (the
        wake turn = min_capture_ms; a follow-up = the longer FOLLOWUP window).
      - `min_capture_ms`: once capturing, don't endpoint before this (covers the
        wake-chime bleed + run-together commands; ~0 for follow-ups, no chime).
    After speech, endpoint on SILENCE_MS trailing silence."""
    if onset_ms is None:
        onset_ms = min_capture_ms
    frame_bytes = VAD_FRAME_SAMPLES * 2
    frames: list[bytes] = []
    speech = False
    silence_ms = voiced_ms = total_ms = 0
    while total_ms < MAX_COMMAND_S * 1000:
        b = stdout.read(frame_bytes)
        if len(b) < frame_bytes:
            break
        total_ms += 20
        frames.append(b)
        try:
            is_speech = vad.is_speech(b, SAMPLE_RATE)
        except Exception:  # noqa: BLE001
            is_speech = False
        if is_speech:
            speech = True
            silence_ms = 0
            voiced_ms += 20
        elif speech:
            silence_ms += 20
        if not speech:
            if total_ms >= onset_ms:
                break                       # no speech onset in window -> abort
            continue                        # keep waiting for speech to start
        if total_ms < min_capture_ms:
            continue                        # never endpoint inside the min window
        if silence_ms >= SILENCE_MS:
            break                           # normal endpoint after speech
    return b"".join(frames) if voiced_ms >= MIN_VOICED_MS else b""


# --- one wake turn ---------------------------------------------------------
def run_turn(preroll_pcm: bytes, stdout, vad) -> None:
    STATE.stats["turns"] += 1
    try:
        v = post_wav("/verify", wrap_wav(preroll_pcm))
    except Exception as exc:  # noqa: BLE001
        log(f"/verify failed: {exc}")
        return
    append_event({"type": "verify", "verified": v.get("verified"),
                  "score": v.get("score"), "transcript": v.get("transcript")})
    if not v.get("verified"):
        log(f"stage-2 REJECT score={v.get('score')} transcript={v.get('transcript')!r}")
        return

    log(f"wake CONFIRMED score={v.get('score')}")
    play_file(SOUNDS_DIR / "wake.wav")
    # NB: no drain — the chime bleed is harmless in the audio (Parakeet ignores
    # the tone); the min capture window keeps it from ending capture early, and
    # not draining preserves a run-together command spoken over the chime.
    cmd_pcm = capture_command(stdout, vad)
    play_file(SOUNDS_DIR / "vad.wav")
    if not cmd_pcm:
        log("no command captured")
        return
    try:
        resp = post_wav("/command/audio", wrap_wav(cmd_pcm))
    except Exception as exc:  # noqa: BLE001
        log(f"/command/audio failed: {exc}")
        return
    log(f"command -> {resp.get('intent')}: {resp.get('response')}")
    append_event({"type": "command", "intent": resp.get("intent"),
                  "transcript": resp.get("transcript"), "response": resp.get("response")})
    url = resp.get("audio_url")
    if url:
        try:
            play_wav_bytes(get_bytes(ORCH_BASE + url))
        except Exception as exc:  # noqa: BLE001
            log(f"reply playback failed: {exc}")
    run_followups(stdout, vad)


def run_followups(stdout, vad) -> None:
    """Continued conversation: after the reply, reopen the mic (no wake word) and
    listen for another command; re-arm on each actionable turn. Ends silently on
    a quiet window, on a not-for-us reply (orchestrator intent 'none'), if a timer
    starts ringing, or at the safety cap. Echo/Google 'Follow-Up Mode'."""
    if not FOLLOWUP_ENABLED:
        return
    for _ in range(FOLLOWUP_MAX_TURNS):
        if STATE.current_alarm is not None or STATE.alarm_queue:
            return                          # a timer needs the mic — don't hold it
        # Drop the reply that just played (it bled into the mic) so we don't
        # transcribe our own voice as a follow-up. No AEC on this mic.
        drain_input(stdout)
        # Cue the dashboard "Listening…" badge so it's clear the mic is open for
        # a follow-up (no wake word) — fire-and-forget, never block the capture.
        try:
            post_json("/session/listening", {}, timeout=2)
        except Exception:  # noqa: BLE001
            pass
        cmd = capture_command(stdout, vad, min_capture_ms=FOLLOWUP_MIN_MS,
                              onset_ms=FOLLOWUP_WINDOW_MS)
        if not cmd:
            return                          # quiet window -> conversation over
        try:
            resp = post_wav("/command/audio?followup=1", wrap_wav(cmd))
        except Exception as exc:  # noqa: BLE001
            log(f"followup /command/audio failed: {exc}")
            return
        intent = resp.get("intent")
        reply = resp.get("response") or ""
        if resp.get("silent") or intent in (None, "none") or not reply:
            log(f"followup not for us (intent={intent}) -> ending session")
            return
        log(f"followup -> {intent}: {reply}")
        append_event({"type": "followup", "intent": intent,
                      "transcript": resp.get("transcript"), "response": reply})
        url = resp.get("audio_url")
        if url:
            try:
                play_wav_bytes(get_bytes(ORCH_BASE + url))
            except Exception as exc:  # noqa: BLE001
                log(f"followup reply playback failed: {exc}")
    log("followup max turns reached -> ending session")


# --- alarm: playback in a thread, main loop listens continuously -----------
def check_dismiss(pcm: bytes) -> bool:
    """Transcribe a chunk of mic audio and look for a barge-in dismiss word.
    The alarm sound / announcement never contain these, so no false stops."""
    try:
        t = post_wav("/transcribe", wrap_wav(pcm), timeout=10).get("transcript", "").lower()
    except Exception:  # noqa: BLE001
        return False
    if t.strip():
        log(f"alarm-listen heard: {t!r}")
    return any(w.strip() in t for w in DISMISS_WORDS)


def alarm_listen_chunk(stdout) -> None:
    """Read ~STOP_CHUNK_MS of mic and dismiss the alarm if a stop word is heard.
    Runs continuously (concurrent with alarm playback) — say 'stop' any time."""
    frame_bytes = VAD_FRAME_SAMPLES * 2
    n = max(1, STOP_CHUNK_MS // 20)
    buf: list[bytes] = []
    for _ in range(n):
        if STATE.current_alarm is None:
            break
        b = stdout.read(frame_bytes)
        if len(b) < frame_bytes:
            break
        buf.append(b)
    if buf and check_dismiss(b"".join(buf)):
        log("dismiss word -> stopping alarm")
        STATE.dismiss.set()


def alarm_playback(req: dict) -> None:
    """Thread: loop the themed sound + periodic announcement until dismissed or
    timeout. Does NOT touch the mic — the main loop listens for 'stop'."""
    tid = STATE.current_alarm
    theme = req.get("sound_theme") or "marimba"
    log(f"ALARM start timer={tid} label={req.get('label')} theme={theme}")
    announce_wav = None
    if req.get("announce_url"):
        try:
            announce_wav = get_bytes(ORCH_BASE + req["announce_url"])
        except Exception as exc:  # noqa: BLE001
            log(f"announce fetch failed: {exc}")
    sound = theme_sound(theme)
    dismissed = False
    for i in range(ALARM_MAX_LOOPS):
        if STATE.dismiss.is_set():
            dismissed = True
            break
        play_file(sound, is_alarm=True)
        # announce only on the first loop — replaying it masks the mic and makes
        # 'stop' harder to catch (seen in the 2026-07-06 live test).
        if announce_wav and i == 0 and not STATE.dismiss.is_set():
            play_wav_bytes(announce_wav, is_alarm=True)
        if STATE.dismiss.wait(ALARM_GAP_S):   # woken early if main loop hears stop
            dismissed = True
            break
    if dismissed:
        play_file(SOUNDS_DIR / "dismiss.wav")  # confirmation chirp: "it took"
    with STATE.lock:
        STATE.current_alarm = None
    log(f"ALARM end timer={tid} ({'dismissed' if dismissed else 'timeout'})")
    try:
        post_json(f"/timers/{tid}/dismiss", {})   # sync orchestrator ringing->done
    except Exception:  # noqa: BLE001
        pass
    start_next_alarm()


def start_next_alarm() -> None:
    with STATE.lock:
        if STATE.current_alarm is not None or not STATE.alarm_queue:
            return
        req = STATE.alarm_queue.pop(0)
        STATE.current_alarm = req.get("timer_id") or f"anon-{int(time.time())}"
    STATE.dismiss.clear()
    threading.Thread(target=alarm_playback, args=(req,), daemon=True).start()


# --- HTTP control surface --------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:  # noqa: BLE001
            return {}

    def do_GET(self):
        if self.path == "/health":
            with STATE.lock:
                self._json(200, {
                    "ok": True, "mode": STATE.mode, "phrase": WAKE_PHRASE,
                    "threshold": TRIGGER_THRESHOLD,
                    "uptime_s": int(time.time() - STATE.stats["started"]),
                    "triggers": STATE.stats["triggers"], "turns": STATE.stats["turns"],
                    "alarm": STATE.current_alarm, "alarm_queue": len(STATE.alarm_queue),
                    "volume": STATE.volume,
                })
        elif self.path == "/mode":
            self._json(200, {"mode": STATE.get_mode()})
        else:
            self._json(404, {"ok": False})

    def do_POST(self):
        if self.path == "/mode":
            mode = str(self._read_json().get("mode", "")).lower()
            if mode not in ("active", "shadow", "off"):
                self._json(400, {"ok": False, "error": "mode must be active|shadow|off"})
                return
            STATE.set_mode(mode)
            log(f"mode -> {mode}")
            self._json(200, {"ok": True, "mode": mode})
        elif self.path == "/volume":
            level = self._read_json().get("level")
            if not isinstance(level, (int, float)):
                self._json(400, {"ok": False, "error": "level (0-100) required"})
                return
            lvl = STATE.set_volume(level)
            log(f"volume -> {lvl}")
            self._json(200, {"ok": True, "volume": lvl})
        elif self.path == "/alarm":
            body = self._read_json()
            STATE.enqueue_alarm(body)
            start_next_alarm()
            self._json(200, {"ok": True, "timer_id": body.get("timer_id")})
        elif self.path == "/alarm/dismiss":
            STATE.dismiss.set()
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False})


# --- main capture loop -----------------------------------------------------
def main() -> int:
    import onnxruntime as ort
    _orig = ort.InferenceSession

    def _capped(*a, **k):
        if "sess_options" not in k:
            so = ort.SessionOptions()
            so.intra_op_num_threads = int(os.getenv("ORT_THREADS", "1"))
            so.inter_op_num_threads = 1
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            k["sess_options"] = so
        return _orig(*a, **k)

    ort.InferenceSession = _capped

    import webrtcvad
    from livekit.wakeword import WakeWordModel

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not Path(MODEL_PATH).is_file():
        log(f"model missing: {MODEL_PATH}")
        return 3

    model = WakeWordModel(models=[MODEL_PATH])
    model_key = next(iter(model.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16)).keys()))
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    arecord = subprocess.Popen(
        ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE)
    log(f"satellite up: phrase={WAKE_PHRASE!r} threshold={TRIGGER_THRESHOLD} "
        f"mode={STATE.get_mode()} orch={ORCH_BASE}")
    append_event({"type": "start", "mode": STATE.get_mode(),
                  "model": os.path.basename(MODEL_PATH)})

    frame_bytes = FRAME_SAMPLES * 2
    ring: deque = deque(maxlen=int(RING_SECONDS * FPS))
    window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
    frames_since_hop = 0
    guard_until = 0.0

    in_alarm = False

    def resync():
        nonlocal window, frames_since_hop, guard_until
        drain_input(arecord.stdout)
        window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
        frames_since_hop = 0
        guard_until = time.time() + RETRIGGER_GUARD_S

    while True:
        # while an alarm is ringing (playback runs in its own thread), the main
        # loop listens continuously for a 'stop' barge-in — no wake word needed.
        if STATE.current_alarm is not None:
            in_alarm = True
            alarm_listen_chunk(arecord.stdout)
            continue
        if in_alarm:
            in_alarm = False
            resync()

        chunk = arecord.stdout.read(frame_bytes)
        if not chunk or len(chunk) < frame_bytes:
            log("arecord stream ended; exiting for restart")
            return 1
        ring.append(chunk)
        window = np.concatenate([window[FRAME_SAMPLES:], np.frombuffer(chunk, dtype=np.int16)])

        mode = STATE.get_mode()
        if mode == "off":
            continue

        frames_since_hop += 1
        if frames_since_hop < HOP_FRAMES:
            continue
        frames_since_hop = 0

        score = float(model.predict(window).get(model_key, 0.0))
        now = time.time()
        if score < TRIGGER_THRESHOLD or now < guard_until:
            continue

        STATE.stats["triggers"] += 1
        peak = round(score, 3)
        append_event({"type": "trigger", "peak_score": peak, "mode": mode})
        log(f"stage-1 trigger peak={peak} mode={mode}")
        guard_until = now + RETRIGGER_GUARD_S
        if mode != "active":
            continue

        preroll = b"".join(list(ring)[-int(PREROLL_S * FPS):])
        run_turn(preroll, arecord.stdout, vad)
        resync()


if __name__ == "__main__":
    sys.exit(main())
