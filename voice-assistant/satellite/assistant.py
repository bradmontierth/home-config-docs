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

Modes (kill switch, HTTP POST /mode {active|shadow|off}, default active):
  active  full pipeline
  shadow  detect + log only; no chime, no action
  off     mic pipeline paused
"""

import difflib
import http.server
import io
import json
import os
import re
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
# MODEL_PATHS: comma-separated wake models scored every hop; any one clearing
# the threshold triggers (dual wake: okay_computer + okay_google). MODEL_PATH
# kept as single-model fallback for old .env files.
MODEL_PATHS = [p.strip() for p in os.getenv(
    "MODEL_PATHS",
    os.getenv("MODEL_PATH", "/home/pi/wake-bench/okay_computer.onnx"),
).split(",") if p.strip()]
WAKE_PHRASE = os.getenv("WAKE_PHRASE", "okay computer")
TRIGGER_THRESHOLD = float(os.getenv("TRIGGER_THRESHOLD", "0.5"))
HOP_MS = int(os.getenv("HOP_MS", "352"))
MIC_DEVICE = os.getenv("MIC_DEVICE", "plughw:CARD=microphone")
PLAYBACK_DEVICE = os.getenv("PLAYBACK_DEVICE", "plughw:CARD=Headphones")
ORCH_BASE = os.getenv("ORCH_BASE", "http://192.168.10.217:8785")
SOUNDS_DIR = Path(os.getenv("SOUNDS_DIR", "/home/pi/voice-pipeline/sounds"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/home/pi/voice-pipeline/data"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8781"))
START_MODE = os.getenv("MODE", "active")
# Which satellite this is, sent on /verify and /command/audio so the
# orchestrator can arbitrate between mics (first verified wake wins the turn;
# the other satellite is answered suppressed=true and shadow-captures).
SATELLITE_ID = os.getenv("SATELLITE_ID", "kitchen")
# Mic-only satellite: when set, ALL our own audio (chime/TTS/alarm/reply) POSTs
# to this satellite's /play instead of a local aplay — the kitchen box stays
# the house's one voice. Relay is synchronous: callers time capture and drains
# off "playback finished", and /play answers only when the sound has played.
PLAYBACK_RELAY_URL = os.getenv("PLAYBACK_RELAY_URL", "").rstrip("/")

PREROLL_S = float(os.getenv("PREROLL_S", "2.5"))
# Endpointing VAD. webrtcvad is energy/spectral and useless near steady noise
# (an AC next to the mic reads as 100% voiced at every aggressiveness, so the
# turn never endpoints and runs the full MAX_COMMAND_S). Silero is a tiny neural
# VAD (~2MB ONNX, ~1.7ms/32ms-chunk on a Pi 4) that ignores that noise (0% vs
# 100% on the same clip). Reuses the onnxruntime already loaded for wake.
# Model: github.com/snakers4/silero-vad raw src/silero_vad/data/silero_vad.onnx
# (~2.3MB, not in git — deploy alongside assistant.py like the wake .onnx).
SILERO_MODEL = os.getenv("SILERO_MODEL", "/home/pi/voice-pipeline/silero_vad.onnx")
SILERO_THRESHOLD = float(os.getenv("SILERO_THRESHOLD", "0.5"))
VAD_FRAME_MS = 32   # Silero requires exactly 512-sample (32ms @ 16k) chunks
# Command capture: keep grabbing the stream (like Echo/Nest — supports
# "okay computer set a timer" run together) for a minimum window, THEN endpoint
# on normal trailing silence. The min window is what stops the wake-chime bleed
# from ending capture before you speak.
MIN_CAPTURE_MS = int(os.getenv("MIN_CAPTURE_MS", "3000"))
SILENCE_MS = int(os.getenv("SILENCE_MS", "700"))
# Runaway guard, NOT a human speech budget (wall-clock from speech onset). With
# Silero endpointing working, the only way to hit this is speech that never
# stops — a TV/radio near the mic is genuine speech, so silence-endpointing
# correctly never fires. Keep it far above any real command or question; at 8
# it was cutting off long asks mid-word.
MAX_COMMAND_S = float(os.getenv("MAX_COMMAND_S", "20"))
MIN_VOICED_MS = int(os.getenv("MIN_VOICED_MS", "200"))
RETRIGGER_GUARD_S = float(os.getenv("RETRIGGER_GUARD_S", "1.5"))
# Spurious-onset guard (2026-07-09, the family-demo bug): the un-drained wake
# buffer holds ~1.5s of trigger→verify→chime bleed, and a short Silero blip in
# it (wake-word tail, chime edge, ducked-music vocals) counted as speech onset —
# then 704ms of BUFFERED silence endpointed the turn before the user could speak
# (log signature: silence_endpoint with wall≈100ms, voiced≤320ms). A silence
# endpoint is only trusted if we heard enough real speech OR the decision was
# made on live audio (lag = audio consumed minus wall elapsed). Otherwise the
# blip is discarded and we keep waiting within the wall-clock onset window.
# Follow-up turns drain first (lag≈0), so short live replies ("yes") still land.
MIN_COMMAND_VOICED_MS = int(os.getenv("MIN_COMMAND_VOICED_MS", "500"))
ENDPOINT_LAG_SPURIOUS_MS = int(os.getenv("ENDPOINT_LAG_SPURIOUS_MS", "400"))
# /command/audio can legitimately take a minute: a searched+reasoning ask
# measured 61s on 2026-07-09 (9 web searches) — the old 30s default hung up
# before the answer, so the dashboard showed it but the kitchen never spoke.
COMMAND_TIMEOUT_S = float(os.getenv("COMMAND_TIMEOUT_S", "120"))

# Follow-up / continued conversation: after a reply, reopen the mic (no wake
# word) for a bounded window; re-arm each time you actually speak. Default ON.
FOLLOWUP_ENABLED = os.getenv("FOLLOWUP_ENABLED", "1").lower() not in ("0", "false", "no", "")
FOLLOWUP_WINDOW_MS = int(os.getenv("FOLLOWUP_WINDOW_MS", "7000"))  # wait-for-speech window
FOLLOWUP_MIN_MS = int(os.getenv("FOLLOWUP_MIN_MS", "300"))        # min capture (no chime bleed)
FOLLOWUP_MAX_TURNS = int(os.getenv("FOLLOWUP_MAX_TURNS", "6"))    # runaway-session cap
# Wake-turn capture: after draining the chime/pre-roll bleed, wait up to
# WAKE_ONSET_MS for speech to start, then (Silero having endpointed cleanly)
# only a short min-capture is needed. No 3s floor — that was a webrtcvad crutch.
WAKE_ONSET_MS = int(os.getenv("WAKE_ONSET_MS", "4000"))
WAKE_MIN_CAPTURE_MS = int(os.getenv("WAKE_MIN_CAPTURE_MS", "400"))

# Live captions: during command capture, POST the ENTIRE buffer-so-far to the
# orchestrator every ~PARTIAL_INTERVAL_MS (a full-context batch re-decode, so
# partials carry zero accuracy penalty). Display-only — the final
# /command/audio decode is still the only thing intents run on.
PARTIALS_ENABLED = os.getenv("PARTIALS_ENABLED", "1").lower() not in ("0", "false", "no", "")
PARTIAL_INTERVAL_MS = int(os.getenv("PARTIAL_INTERVAL_MS", "400"))

# Playback volume (0-100) applied as software gain to OUR audio only (chimes,
# alarm, TTS) — music on the shared card is untouched. Driven by day-mode via
# Node-RED POST /volume (mirrors the /mode switch). Alarm never dips below the
# floor so a cooking timer stays audible even at night.
VOLUME_DEFAULT = int(os.getenv("VOLUME_DEFAULT", "50"))
ALARM_VOLUME_FLOOR = int(os.getenv("ALARM_VOLUME_FLOOR", "50"))

ALARM_MAX_LOOPS = int(os.getenv("ALARM_MAX_LOOPS", "14"))
ALARM_GAP_S = float(os.getenv("ALARM_GAP_S", "2.0"))         # space between beeps
# Ringing this long with no dismiss = nobody's in the kitchen -> tell the
# orchestrator, which pushes to the household phones. Deliberately well under
# the full ring (~45-90s); waiting for ring timeout would delay the phones by
# a minute. 0 disables.
UNATTENDED_ALERT_S = float(os.getenv("UNATTENDED_ALERT_S", "15"))
# Barge-in 'stop' listener: OVERLAPPING windows, not back-to-back chunks. The
# old 1s non-overlapping chunks dropped any "stop" straddling a boundary
# (~40% of them — that was the say-it-four-times bug, measured 13-16s to
# dismiss on 2026-07-09). A 2.5s window every 1s means every utterance is
# fully inside at least one window; the transcribe POST runs on a worker
# thread so the mic read cadence never blocks on the network.
ALARM_WINDOW_MS = int(os.getenv("ALARM_WINDOW_MS", "2500"))
ALARM_HOP_MS = int(os.getenv("ALARM_HOP_MS", "1000"))
DISMISS_WORDS = ("stop", "cancel", "okay computer", "ok computer",
                 "dismiss", "turn off", "enough", "quiet", " off")
# Fuzzy singles for when Parakeet mangles a word over the ringing ("stopp",
# "stahp"). Matched per-token at ≥0.8 SequenceMatcher ratio, len≥3.
DISMISS_FUZZY = ("stop", "cancel", "dismiss", "enough", "quiet")
# GX10 bias profile for ring-window transcribes: a stop-heavy phrase list so
# "stop" under the alarm masker decodes as stop, not "top"/"banned"/"stay".
ALARM_ASR_CLIENT = os.getenv("ALARM_ASR_CLIENT", "kitchen-alarm")
# Trained "stop" barge-in model (livekit-wakeword, augmented with the alarm
# themes as background noise). Scored ONLY inside the alarm branch of the main
# loop — zero cycles when nothing is ringing, and it replaces (not adds to)
# the idle wake scoring, so ring-time CPU ≈ idle CPU. Missing file = ASR-only
# dismiss, so deploy order stays flexible. Scores ≥ STOP_LOG_THRESHOLD are
# logged for threshold tuning against real rings.
STOP_MODEL_PATH = os.getenv("STOP_MODEL_PATH", "/home/pi/wake-bench/stop.onnx")
STOP_THRESHOLD = float(os.getenv("STOP_THRESHOLD", "0.5"))
STOP_HOP_MS = int(os.getenv("STOP_HOP_MS", "224"))
STOP_LOG_THRESHOLD = float(os.getenv("STOP_LOG_THRESHOLD", "0.2"))
# Every ring's mic audio is captured to WAV for offline model eval/retraining:
# the v1 stop model bench-passed on SYNTHETIC ring mixes (0.038) but false-
# fired at 0.7-0.9 on the REAL first ding through speakers+room+mic
# (2026-07-24, two self-dismissals). Train/eval on these, not synthetic.
ALARM_RING_DIR = DATA_DIR / "alarm_rings"
ALARM_RING_KEEP = int(os.getenv("ALARM_RING_KEEP", "40"))

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
WINDOW_SAMPLES = 32000
VAD_FRAME_SAMPLES = 320       # 20ms
RING_SECONDS = 20   # /mark grabs the whole ring — long enough to tap after a miss
FPS = SAMPLE_RATE / FRAME_SAMPLES
HOP_FRAMES = max(1, int(HOP_MS / 1000 * FPS))

CLIP_LOCK = threading.Lock()
PLAYBACK_LOCK = threading.Lock()
EVENTS_PATH = DATA_DIR / "events.jsonl"
# Every verify pre-roll is kept, verdict in the name: the rejects are the
# multi-voice-miss corpus we tune stage-2 against; the accepts are the
# regression set proving a tuning change didn't break the passing path.
# 2.5s mono 16k = ~80KB/clip at ~15 triggers/day — no rotation needed.
CLIP_DIR = DATA_DIR / "clips"


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
def sat_path(path: str) -> str:
    """Tag an orchestrator path with this satellite's identity."""
    return path + ("&" if "?" in path else "?") + "sat=" + SATELLITE_ID


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


# --- music ducking -----------------------------------------------------------
# Music at speech volume on the same speakers defeats command capture AND the
# alarm 'stop' barge-in listener (the mic can't AEC audio it didn't play).
# Duck on a CONFIRMED wake (stage-2 verify pass), manual trigger, and alarm
# start; unduck when the turn (incl. follow-ups) or alarm ends. NOT on stage-1
# triggers: verify decodes pre-trigger audio so ducking can't help it, and
# music false-fires made every dip audible as stutter. The orchestrator
# refcounts nested pairs and has a watchdog, so these are safe to fire blind.
def _music_post(path: str) -> None:
    """Fire-and-forget on its own thread — must never delay /verify or capture."""
    def _post():
        try:
            post_json(path, {}, timeout=3)
        except Exception:  # noqa: BLE001 — no music / orch down: nothing to duck
            pass
    threading.Thread(target=_post, daemon=True).start()


def duck_music() -> None:
    _music_post("/music/duck")


def unduck_music() -> None:
    _music_post("/music/unduck")


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


def _play_scaled(wav_bytes: bytes) -> None:
    with PLAYBACK_LOCK:
        try:
            subprocess.run(["aplay", "-q", "-D", PLAYBACK_DEVICE, "-"],
                           input=wav_bytes, timeout=30, check=False)
        except Exception as exc:  # noqa: BLE001
            log(f"aplay bytes failed: {exc}")


def _play_local(wav_bytes: bytes, is_alarm: bool = False) -> None:
    """Scale by OUR volume and play on OUR card. The /play endpoint lands here
    directly so a relayed clip can never bounce back out (no relay loops)."""
    _play_scaled(_scale_wav(wav_bytes, STATE.volume_factor(is_alarm)))


def _relay_play(wav_bytes: bytes, is_alarm: bool) -> None:
    """POST raw (unscaled) WAV to the speaker satellite's /play. It scales with
    its own volume — the day-mode /volume flow only drives the kitchen box.
    Synchronous by contract: returns when the audio has finished playing there,
    so our capture/drain timing works the same as local aplay."""
    try:
        req = urllib.request.Request(
            f"{PLAYBACK_RELAY_URL}/play?alarm={1 if is_alarm else 0}",
            data=wav_bytes, headers={"Content-Type": "audio/wav"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
    except Exception as exc:  # noqa: BLE001 — a mute turn beats a crashed one
        log(f"playback relay failed: {exc}")


def play_wav_bytes(wav_bytes: bytes, is_alarm: bool = False) -> None:
    if PLAYBACK_RELAY_URL:
        _relay_play(wav_bytes, is_alarm)
        return
    _play_local(wav_bytes, is_alarm)


def speak_url(url: str) -> None:
    """Fetch a WAV from the orchestrator and play it. Used for the ask filler
    ('Let me look that up…') pushed while /command/audio is still in flight;
    PLAYBACK_LOCK makes the real reply queue behind it instead of colliding."""
    try:
        play_wav_bytes(get_bytes(url))
    except Exception as exc:  # noqa: BLE001
        log(f"speak playback failed ({url}): {exc}")


# Chimes replay constantly at a volume that changes a few times a day (the
# day-mode /volume flow), yet the disk read + numpy rescale ran per play —
# on the wake path, between /verify returning and the chime. Cache the scaled
# bytes per (file, volume); a handful of chimes/themes × a few levels is tiny.
_SOUND_CACHE: dict[tuple[str, int], bytes] = {}


def play_file(path: Path, is_alarm: bool = False) -> None:
    if PLAYBACK_RELAY_URL:
        try:
            _relay_play(path.read_bytes(), is_alarm)
        except OSError as exc:
            log(f"read sound failed ({path}): {exc}")
        return
    factor = STATE.volume_factor(is_alarm)
    key = (str(path), round(factor * 1000))
    data = _SOUND_CACHE.get(key)
    if data is None:
        if not path.exists():
            log(f"missing sound {path}")
            return
        try:
            data = _scale_wav(path.read_bytes(), factor)
        except Exception as exc:  # noqa: BLE001
            log(f"read sound failed ({path}): {exc}")
            return
        _SOUND_CACHE[key] = data
    _play_scaled(data)


def theme_sound(theme: str) -> Path:
    p = SOUNDS_DIR / "themes" / f"{theme}.wav"
    return p if p.exists() else SOUNDS_DIR / "themes" / "marimba.wav"


# --- slideshow media audio ---------------------------------------------------
# The kitchen display plays home videos muted (no speaker on the display); the
# slideshow service relays each clip's audio track here. Unlike /speak this
# must NOT hold PLAYBACK_LOCK — a 45s clip would queue assistant replies and
# alarms behind it. It gets its own killable aplay process instead, and is
# stopped by /media/stop, any stage-1 wake trigger, and alarm start. Music is
# ducked for exactly the playback lifetime (the watcher thread owns unduck).
MEDIA_LOCK = threading.Lock()
MEDIA_PROC: subprocess.Popen | None = None


def media_stop() -> None:
    with MEDIA_LOCK:
        proc = MEDIA_PROC
    if proc and proc.poll() is None:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def media_play(url: str) -> bool:
    """Fetch a WAV and play it interruptibly. Returns once aplay is running so
    the caller (the kiosk viewer, transitively) can start the picture in step."""
    global MEDIA_PROC
    try:
        data = _scale_wav(get_bytes(url), STATE.volume_factor(False))
    except Exception as exc:  # noqa: BLE001
        log(f"media fetch failed ({url}): {exc}")
        return False
    media_stop()
    duck_music()
    try:
        proc = subprocess.Popen(["aplay", "-q", "-D", PLAYBACK_DEVICE, "-"],
                                stdin=subprocess.PIPE)
    except Exception as exc:  # noqa: BLE001
        log(f"media aplay spawn failed: {exc}")
        unduck_music()
        return False
    with MEDIA_LOCK:
        MEDIA_PROC = proc

    def run() -> None:
        global MEDIA_PROC
        try:
            proc.stdin.write(data)   # blocks at the pipe as aplay consumes
            proc.stdin.close()
        except Exception:  # noqa: BLE001 — killed mid-write (BrokenPipe) is normal
            pass
        proc.wait()
        with MEDIA_LOCK:
            if MEDIA_PROC is proc:
                MEDIA_PROC = None
        unduck_music()

    threading.Thread(target=run, daemon=True).start()
    log(f"media play {len(data) / 1e6:.1f}MB from {url}")
    return True


# --- live-caption partials ---------------------------------------------------
class PartialStreamer:
    """Feeds the dashboard's live captions. capture_command offers the entire
    buffer-so-far every ~PARTIAL_INTERVAL_MS; a single daemon worker POSTs it
    to the orchestrator's /partial. Strictly decoupled from capture: the worker
    holds at most the LATEST snapshot (an unsent older one is overwritten, never
    queued), so a slow decode or dead orchestrator can never back-pressure the
    mic loop — worst case captions skip forward. seq increases monotonically;
    the dashboard drops anything <= the newest it has rendered, so out-of-order
    arrivals are harmless. seq is seeded from the CLOCK, not 0: the kiosk's
    lastSeq survives a satellite restart, and a counter that reset to 1 made it
    silently drop every caption until seq caught back up (live regression
    2026-07-09 — earlier restarts always came with a kiosk reload that masked
    it). Offers accrue ~2.5/s only while someone speaks, so the counter can
    never outrun the next restart's clock seed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: tuple[int, bytes] | None = None
        self._wake = threading.Event()
        self._seq = int(time.time())
        threading.Thread(target=self._worker, daemon=True).start()

    def offer(self, pcm: bytes) -> None:
        with self._lock:
            self._seq += 1
            self._latest = (self._seq, pcm)
        self._wake.set()

    def _worker(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                item, self._latest = self._latest, None
                self._wake.clear()
            if item is None:
                continue
            seq, pcm = item
            try:
                post_wav(f"/partial?seq={seq}", wrap_wav(pcm), timeout=4)
            except Exception:  # noqa: BLE001 — captions are cosmetic, never log-spam
                pass


PARTIALS = PartialStreamer() if PARTIALS_ENABLED else None
PARTIAL_FRAMES = max(1, PARTIAL_INTERVAL_MS // VAD_FRAME_MS)


# --- Silero neural VAD -----------------------------------------------------
class SileroVad:
    """Streaming speech detector. Feed it consecutive 512-sample (32ms @ 16k)
    int16 chunks; it carries recurrent state AND a 64-sample context across a
    turn (reset() between turns). is_speech() -> bool via a probability threshold.

    CRITICAL: the model is fed 64 samples of the previous window PREPENDED to the
    512 new ones (576 total) — matching the reference silero-vad OnnxWrapper.
    Feeding a bare 512 makes it output ~0 on everything (that was the bug)."""

    CHUNK = 512
    CONTEXT = 64

    def __init__(self, model_path: str, threshold: float = SILERO_THRESHOLD):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(model_path, sess_options=so)
        self.threshold = threshold
        self._sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def is_speech(self, frame_bytes: bytes) -> bool:
        chunk = (np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float32)
                 / 32768.0).reshape(1, -1)
        x = np.concatenate([self._context, chunk], axis=1)   # 64 context + 512
        out = self.sess.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._state = out[1]
        self._context = x[:, -self.CONTEXT:]
        return float(out[0].ravel()[0]) >= self.threshold


# --- command capture (min window, then Silero endpointing) -----------------
def capture_command(stdout, vad, min_capture_ms: int = MIN_CAPTURE_MS,
                    onset_ms: int | None = None, partials: bool = False) -> bytes:
    """Capture one utterance. Two independent windows:
      - `onset_ms`: how long to wait for speech to START before giving up (the
        wake turn = min_capture_ms; a follow-up = the longer FOLLOWUP window).
      - `min_capture_ms`: once capturing, don't endpoint before this (covers the
        wake-chime bleed + run-together commands; ~0 for follow-ups, no chime).
    After speech, endpoint on SILENCE_MS trailing silence.
    `partials`: stream live-caption snapshots to the orchestrator while
    capturing. On for wake AND follow-up turns (user call 2026-07-08): captions
    are silent and only visible if you're looking at the dashboard, so they
    don't violate the follow-ups-are-unobtrusive rule the way audio would —
    and it's a local pipeline, so seeing chatter transcribed isn't creepy.
    Dropped-chatter captions just fade out (dashboard re-arms a short hide
    timer per partial)."""
    if onset_ms is None:
        onset_ms = min_capture_ms
    frame_bytes = SileroVad.CHUNK * 2   # 512 samples * int16
    vad.reset()                         # fresh recurrent state per utterance
    frames: list[bytes] = []
    speech = False
    speech_t0 = 0.0
    silence_ms = voiced_ms = total_ms = 0
    last_partial_at = 0
    reason = "max_command"
    t0 = time.time()
    while True:
        # Max-command cap is WALL-CLOCK FROM SPEECH ONSET, not audio duration
        # (same bug class as the onset timeout, fixed 2026-07-08): the capture
        # buffer opens with ~1.7s of chime/verify bleed that reads in ~0ms of
        # real time, and counting it as audio silently ate a quarter of the
        # speaking budget — live turns hit "8s" at ~6.3s and cut mid-word.
        if speech and time.time() - speech_t0 >= MAX_COMMAND_S:
            break
        # A timer starting to ring mid-capture needs the mic back: the 'stop'
        # barge-in listener runs in the main loop, which this capture blocks.
        # Bail with whatever we have — the alarm just started, so at most one
        # frame of it bled in and the command (if any) is intact.
        if STATE.current_alarm is not None or STATE.alarm_queue:
            reason = "alarm_bail"
            break
        b = stdout.read(frame_bytes)
        if len(b) < frame_bytes:
            reason = "stream_end"
            break
        total_ms += VAD_FRAME_MS
        frames.append(b)
        try:
            is_speech = vad.is_speech(b)
        except Exception:  # noqa: BLE001
            is_speech = False
        if is_speech:
            if not speech:
                speech_t0 = time.time()
            speech = True
            silence_ms = 0
            voiced_ms += VAD_FRAME_MS
        elif speech:
            silence_ms += VAD_FRAME_MS
        # Live captions: once speech has started, offer the WHOLE buffer-so-far
        # every ~PARTIAL_INTERVAL_MS. offer() is a lock+event set (microseconds);
        # the POST happens on the streamer's own thread, so the capture loop's
        # timing is untouched even if the orchestrator is slow or down.
        if PARTIALS and partials and speech and len(frames) - last_partial_at >= PARTIAL_FRAMES:
            last_partial_at = len(frames)
            PARTIALS.offer(b"".join(frames))
        if not speech:
            # Onset timeout is WALL-CLOCK, not audio-duration: the buffered
            # chime/pre-roll bleed (read in ~0ms of real time) must NOT count
            # against the time we give you to start speaking. Run-together speech
            # in that buffer still registers above; this only bounds real waiting.
            if (time.time() - t0) * 1000 >= onset_ms:
                reason = "no_speech_onset"
                break                       # no speech onset in window -> abort
            continue                        # keep waiting for speech to start
        if total_ms < min_capture_ms:
            continue                        # never endpoint inside the min window
        if silence_ms >= SILENCE_MS:
            # A short voiced blip whose trailing "silence" was mostly BUFFERED
            # audio is bleed, not the user (see MIN_COMMAND_VOICED_MS above) —
            # discard it and re-arm onset. A real run-together command carries
            # ≥1s of voice, so it still endpoints straight from the buffer.
            lag_ms = total_ms - (time.time() - t0) * 1000
            if voiced_ms < MIN_COMMAND_VOICED_MS and lag_ms > ENDPOINT_LAG_SPURIOUS_MS:
                log(f"spurious onset discarded voiced={voiced_ms}ms "
                    f"lag={round(lag_ms)}ms total={total_ms}ms")
                frames.clear()
                last_partial_at = 0
                speech = False
                voiced_ms = silence_ms = 0
                continue
            reason = "silence_endpoint"
            break                           # normal endpoint after speech
    # Diagnostics for endpoint tuning: how long capture ran, how much of it
    # webrtcvad called "voiced" (a high ratio during quiet = noise fooling the
    # VAD), the trailing silence at cutoff, and any inference overhead vs frames.
    wall_ms = round((time.time() - t0) * 1000)
    voiced_pct = round(100 * voiced_ms / total_ms) if total_ms else 0
    log(f"capture reason={reason} total={total_ms}ms voiced={voiced_ms}ms "
        f"({voiced_pct}%) tail_silence={silence_ms}ms wall={wall_ms}ms "
        f"min={min_capture_ms} onset={onset_ms} thr={SILERO_THRESHOLD}")
    return b"".join(frames) if voiced_ms >= MIN_VOICED_MS else b""


# --- one wake turn ---------------------------------------------------------
def _persist_verify(preroll_pcm: bytes, event: dict) -> None:
    """Clip write + event append for a verify. On a CONFIRMED wake this runs on
    a worker thread: the ~80KB SD write used to sit between /verify returning
    and the chime, so any SD stall was pure added chime latency."""
    clip_name = event.get("clip")
    if clip_name:
        try:
            (CLIP_DIR / clip_name).write_bytes(wrap_wav(preroll_pcm))
        except Exception as exc:  # noqa: BLE001 — clip is diagnostics, never fatal
            log(f"clip save failed: {exc}")
            event = {**event, "clip": None}
    append_event(event)


def run_turn(preroll_pcm: bytes, stdout, vad, trigger_t0: float) -> None:
    STATE.stats["turns"] += 1
    t_post = time.time()
    try:
        v = post_wav(sat_path("/verify"), wrap_wav(preroll_pcm))
    except Exception as exc:  # noqa: BLE001
        log(f"/verify failed: {exc}")
        return
    if v.get("suppressed"):
        # The other satellite verified this same utterance first and owns the
        # turn. Stay silent (no chime — the winner already chimed) but capture
        # the command anyway and post it as a shadow: the logs then show when
        # THIS mic had the cleaner take (the v2 dual-transcribe decision data).
        # partials=False — never fight the winner's live captions.
        log(f"arbitration: suppressed (winner={v.get('winner')}) -> shadow capture")
        append_event({"type": "verify", "suppressed": True,
                      "winner": v.get("winner")})
        cmd_pcm = capture_command(stdout, vad, min_capture_ms=WAKE_MIN_CAPTURE_MS,
                                  onset_ms=WAKE_ONSET_MS, partials=False)
        if cmd_pcm:
            try:
                post_wav(sat_path("/command/shadow"),
                         wrap_wav(preroll_pcm + cmd_pcm), timeout=30)
            except Exception as exc:  # noqa: BLE001
                log(f"/command/shadow failed: {exc}")
        return
    # Chime-latency instrumentation (speech-end→trigger lives in the clip's
    # trailing silence; these cover trigger→chime): rtt_ms = full /verify round
    # trip as seen here, server_ms = the orchestrator's own ASR+verify time
    # (the gap between them is WiFi + HTTP), chime_ms = trigger→chime-start
    # (excludes the aplay spawn, ~50ms of constant cost after this).
    rtt_ms = round((time.time() - t_post) * 1000)
    verdict = "ok" if v.get("verified") else "rej"
    clip_name = f"verify-{verdict}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
    event = {"type": "verify", "verified": v.get("verified"),
             "score": v.get("score"), "transcript": v.get("transcript"),
             "decode": v.get("decode"), "clip": clip_name,
             "rtt_ms": rtt_ms, "server_ms": v.get("latency_ms")}
    if not v.get("verified"):
        _persist_verify(preroll_pcm, event)
        log(f"stage-2 REJECT score={v.get('score')} transcript={v.get('transcript')!r}")
        return

    chime_ms = round((time.time() - trigger_t0) * 1000)
    event["chime_ms"] = chime_ms
    log(f"wake CONFIRMED score={v.get('score')} rtt={rtt_ms}ms "
        f"server={v.get('latency_ms')}ms trigger_to_chime={chime_ms}ms")
    threading.Thread(target=_persist_verify, args=(preroll_pcm, event),
                     daemon=True).start()
    # Duck only on a CONFIRMED wake. Ducking used to fire on every stage-1
    # trigger, but /verify decodes the pre-roll — audio captured BEFORE any
    # duck could land — so early ducking never helped verification, and music
    # false-fires turned into ~400ms volume dips on the big speakers that read
    # as playback stutter (live diagnosis 2026-07-24: ~17 dips in a 21-min
    # jukebox session, both satellites ducking the same kitchen queue).
    duck_music()
    try:
        play_file(SOUNDS_DIR / "wake.wav")
        # Deliberately NO drain: the buffered audio holds a run-together command
        # ("okay computer, download a car") spoken without waiting for the chime.
        # capture_command still processes it (Silero catches the speech in it); the
        # onset timeout is wall-clock, so the buffer doesn't steal the wait budget.
        cmd_pcm = capture_command(stdout, vad, min_capture_ms=WAKE_MIN_CAPTURE_MS,
                                  onset_ms=WAKE_ONSET_MS, partials=True)
        play_file(SOUNDS_DIR / "vad.wav")
        if not cmd_pcm:
            log("no command captured")
            return
        try:
            # Stitch the pre-roll in front of the command. Capture starts AT the
            # stage-1 trigger, but a run-together command ("okay computer set a
            # timer…") begins DURING the ~0.5s detect lag, so its first word(s)
            # exist only in the pre-roll (live bug 2026-07-12: "set a" showed in
            # /verify's transcript, command decoded as just "timer for 30
            # seconds"). The buffers are gapless — the pre-roll ends on the last
            # frame the main loop read, capture reads the pipe from the next — so
            # prepending reassembles the utterance; the orchestrator strips the
            # leading wake phrase from the stitched transcript (?stitched=1).
            # Long timeout on purpose: a searched ask can run ~60s and the reply
            # audio only exists in this response. Wake detection is paused while we
            # wait (single mic reader) — acceptable; alarms still fire (HTTP thread).
            resp = post_wav(sat_path("/command/audio?stitched=1"),
                            wrap_wav(preroll_pcm + cmd_pcm),
                            timeout=COMMAND_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log(f"/command/audio failed: {exc}")
            return
        log(f"command -> {resp.get('intent')}: {resp.get('response')}")
        cmd_clip = f"cmd-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
        threading.Thread(target=_persist_cmd, args=(preroll_pcm + cmd_pcm, cmd_clip),
                         daemon=True).start()
        append_event({"type": "command", "intent": resp.get("intent"),
                      "transcript": resp.get("transcript"), "response": resp.get("response"),
                      "clip": cmd_clip})
        url = resp.get("audio_url")
        if url:
            try:
                play_wav_bytes(get_bytes(ORCH_BASE + url))
            except Exception as exc:  # noqa: BLE001
                log(f"reply playback failed: {exc}")
        run_followups(stdout, vad)
    finally:
        unduck_music()


def run_manual_turn(stdout, vad) -> None:
    """Button-initiated turn (dashboard mic tap): no wake word, no verify —
    chime, capture, act. For when conditions beat the wake word (music, TV)
    and you're near the display anyway. The pre-roll is deliberately NOT
    stitched in: the seconds before a tap are room chatter, not command."""
    STATE.stats["turns"] += 1
    append_event({"type": "trigger", "mode": "active", "model": "manual"})
    log("manual trigger (button)")
    drain_input(stdout)             # start on live audio, not pre-tap backlog
    play_file(SOUNDS_DIR / "wake.wav")
    try:
        post_json("/session/listening", {}, timeout=2)
    except Exception:  # noqa: BLE001
        pass
    cmd_pcm = capture_command(stdout, vad, min_capture_ms=WAKE_MIN_CAPTURE_MS,
                              onset_ms=WAKE_ONSET_MS, partials=True)
    play_file(SOUNDS_DIR / "vad.wav")
    if not cmd_pcm:
        log("manual turn: no command captured")
        return
    try:
        resp = post_wav(sat_path("/command/audio"), wrap_wav(cmd_pcm),
                        timeout=COMMAND_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        log(f"manual /command/audio failed: {exc}")
        return
    log(f"manual command -> {resp.get('intent')}: {resp.get('response')}")
    cmd_clip = f"cmd-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
    threading.Thread(target=_persist_cmd, args=(cmd_pcm, cmd_clip),
                     daemon=True).start()
    append_event({"type": "command", "intent": resp.get("intent"),
                  "transcript": resp.get("transcript"), "response": resp.get("response"),
                  "clip": cmd_clip})
    url = resp.get("audio_url")
    if url:
        try:
            play_wav_bytes(get_bytes(ORCH_BASE + url))
        except Exception as exc:  # noqa: BLE001
            log(f"manual reply playback failed: {exc}")
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
                              onset_ms=FOLLOWUP_WINDOW_MS, partials=True)
        if not cmd:
            return                          # quiet window -> conversation over
        try:
            resp = post_wav(sat_path("/command/audio?followup=1"), wrap_wav(cmd),
                            timeout=COMMAND_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log(f"followup /command/audio failed: {exc}")
            return
        intent = resp.get("intent")
        reply = resp.get("response") or ""
        if resp.get("silent") or intent in (None, "none") or not reply:
            log(f"followup not for us (intent={intent}) -> ending session")
            return
        log(f"followup -> {intent}: {reply}")
        fup_clip = f"cmd-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
        threading.Thread(target=_persist_cmd, args=(cmd, fup_clip),
                         daemon=True).start()
        append_event({"type": "followup", "intent": intent,
                      "transcript": resp.get("transcript"), "response": reply,
                      "clip": fup_clip})
        url = resp.get("audio_url")
        if url:
            try:
                play_wav_bytes(get_bytes(ORCH_BASE + url))
            except Exception as exc:  # noqa: BLE001
                log(f"followup reply playback failed: {exc}")
    log("followup max turns reached -> ending session")


# --- alarm: playback in a thread, main loop listens continuously -----------
def _dismiss_in(transcript: str) -> bool:
    """Word-start match first (matches 'stopped', 'turn it off'), then
    per-token fuzzy for ringing-mangled words. Word-START boundary, not plain
    substring: bare `in` let "off" match inside "cOFFee" and the announcement
    'Your coffee timer is done' self-dismissed the alarm (2026-07-24). Labels
    that themselves start with a dismiss word would still self-dismiss —
    accepted; the announcement never otherwise contains these words.
    Punctuation is normalized away first ('Okay, computer.' used to miss)."""
    t = " ".join(re.sub(r"[^\w\s]", " ", transcript.lower()).split())
    for w in DISMISS_WORDS:
        w = w.strip()
        # "off" must match the exact word: as a prefix it hits office/offer.
        # The rest keep prefix matching (stopped, cancelled, dismissed).
        pat = rf"\b{re.escape(w)}\b" if w == "off" else rf"\b{re.escape(w)}"
        if re.search(pat, t):
            return True
    for tok in re.findall(r"[a-z]+", t):
        if len(tok) < 3:
            continue
        for w in DISMISS_FUZZY:
            # First letter must match: ASR mangles preserve the onset consonant
            # ("stopp"/"stob" for stop) — without this, "top" (shelf, it off)
            # fuzzy-matched "stop" and could dismiss an alarm from cooking talk.
            if tok[0] == w[0] and difflib.SequenceMatcher(None, tok, w).ratio() >= 0.8:
                return True
    return False


class DismissChecker:
    """Transcribes rolling mic windows during an alarm and fires STATE.dismiss
    on a barge-in stop word. Same latest-snapshot-only worker shape as
    PartialStreamer: the main loop's offer() never blocks, a slow decode just
    skips a window (the next one overlaps it anyway)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: bytes | None = None
        self._wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    def offer(self, pcm: bytes) -> None:
        with self._lock:
            self._latest = pcm
        self._wake.set()

    def _worker(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                pcm, self._latest = self._latest, None
                self._wake.clear()
            if pcm is None or STATE.current_alarm is None:
                continue
            try:
                t = post_wav(f"/transcribe?client={ALARM_ASR_CLIENT}",
                             wrap_wav(pcm), timeout=10).get("transcript", "")
            except Exception:  # noqa: BLE001
                continue
            if t.strip():
                log(f"alarm-listen heard: {t!r}")
            if STATE.current_alarm is not None and _dismiss_in(t):
                log("dismiss word -> stopping alarm")
                STATE.dismiss.set()


DISMISS_CHECKER = DismissChecker()


def _unattended_watch(tid: str) -> None:
    """One-shot watchdog: alarm rang UNATTENDED_ALERT_S with no dismiss ->
    POST the orchestrator, which escalates to the household phones. Waits on
    the same Event the ring loop does, so any dismiss cancels it for free."""
    if STATE.dismiss.wait(UNATTENDED_ALERT_S):
        return
    if STATE.current_alarm != tid:
        return
    try:
        post_json(f"/timers/{tid}/unattended", {}, timeout=20)
        log(f"unattended alert sent timer={tid}")
    except Exception as exc:  # noqa: BLE001
        log(f"unattended alert failed: {exc}")


def alarm_playback(req: dict) -> None:
    """Thread: loop the themed sound + periodic announcement until dismissed or
    timeout. Does NOT touch the mic — the main loop listens for 'stop'."""
    tid = STATE.current_alarm
    theme = req.get("sound_theme") or "marimba"
    log(f"ALARM start timer={tid} label={req.get('label')} theme={theme}")
    if UNATTENDED_ALERT_S > 0 and req.get("timer_id"):
        threading.Thread(
            target=_unattended_watch, args=(req["timer_id"],), daemon=True
        ).start()
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
    unduck_music()
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
    media_stop()   # a ringing timer outranks slideshow video audio
    duck_music()   # the ringing must beat the music, and 'stop' must be hearable
    threading.Thread(target=alarm_playback, args=(req,), daemon=True).start()


# --- wake review: ring capture, offline scoring, review page ---------------
# The ring only advances in the main wake loop, so a /mark during a turn or a
# ringing alarm captures pre-turn audio — fine for its purpose (stage-1 misses
# happen precisely when no turn started).
RING: deque = deque(maxlen=int(RING_SECONDS * FPS))

# Manual trigger (dashboard mic button): the HTTP thread flags it, the main
# loop starts a no-wake-word turn on the next frame. Stale taps (queued while
# a turn held the loop) expire rather than reopening the mic out of nowhere.
MANUAL_TRIGGER = threading.Event()
MANUAL_TRIGGER_AT = [0.0]
MANUAL_TRIGGER_TTL_S = 3.0

CMD_CLIPS_KEEP = int(os.getenv("CMD_CLIPS_KEEP", "80"))


def _persist_cmd(pcm: bytes, clip_name: str) -> None:
    """Keep the audio behind every command decode (~320KB per 10s turn) so a
    mis-transcription is auditable by ear on /review; prune oldest past cap."""
    try:
        (CLIP_DIR / clip_name).write_bytes(wrap_wav(pcm))
        cmds = sorted(CLIP_DIR.glob("cmd-*.wav"))
        for old in cmds[:-CMD_CLIPS_KEEP]:
            old.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        log(f"cmd clip persist failed: {exc}")


_SCORER_LOCK = threading.Lock()
_SCORER = None


def _score_clip(pcm: bytes) -> dict:
    """Peak stage-1 score per model over a sliding window. Uses a dedicated
    model instance — the hot-loop model must never be touched off-thread."""
    global _SCORER
    from livekit.wakeword import WakeWordModel
    with _SCORER_LOCK:
        if _SCORER is None:
            _SCORER = WakeWordModel(models=MODEL_PATHS)
        samples = np.frombuffer(pcm, dtype=np.int16)
        hop = int(0.096 * SAMPLE_RATE)
        peaks: dict = {}
        for start in range(0, max(1, len(samples) - WINDOW_SAMPLES + 1), hop):
            win = samples[start:start + WINDOW_SAMPLES]
            if len(win) < WINDOW_SAMPLES:
                win = np.pad(win, (WINDOW_SAMPLES - len(win), 0))
            for k, v in _SCORER.predict(win).items():
                if float(v) > peaks.get(k, 0.0):
                    peaks[k] = float(v)
        return {k: round(v, 3) for k, v in peaks.items()}


def _events_tail(limit: int) -> list:
    try:
        with EVENTS_PATH.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 256 * 1024))
            lines = fh.read().decode(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


REVIEW_HTML = """<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kitchen wake review</title><style>
body{font-family:system-ui,sans-serif;max-width:680px;margin:0 auto;padding:1em;background:#111;color:#ddd}
h2{margin:.2em 0 .6em}
#mark{width:100%;padding:1em;font-size:1.3em;border-radius:12px;border:0;background:#c0392b;color:#fff}
#mark:active{background:#e74c3c}
#stat{display:block;margin:.5em 0;color:#8f8;min-height:1.2em}
.ev{border-bottom:1px solid #333;padding:.5em 0;font-size:.92em}
.t{color:#888;margin-right:.5em}
.b{display:inline-block;padding:.05em .5em;border-radius:8px;font-size:.85em;margin-right:.4em}
.trigger{background:#444}.ok{background:#1e7e34}.rej{background:#7a2a20}
.command{background:#1a5276}.mark{background:#b7770d}.other{background:#333}
audio{width:100%;margin-top:.3em;height:32px}
.d{color:#aaa}
</style></head><body>
<h2>Kitchen wake review</h2>
<button id="mark">Mark — save last 20s</button><span id="stat"></span>
<div id="list">loading…</div>
<script>
const fmt = ts => new Date(ts).toLocaleTimeString([], {hour12:false});
function row(e, scores){
  let b = "other", label = e.type, d = "";
  if (e.type === "trigger"){ b = "trigger"; d = e.model + (e.peak_score != null ? ` peak ${e.peak_score}` : ""); }
  else if (e.type === "verify"){ b = e.verified ? "ok" : "rej";
    label = e.verified ? "verified" : "rejected";
    d = `score ${e.score} — “${e.transcript||""}” (${e.rtt_ms||"?"}ms)`; }
  else if (e.type === "command" || e.type === "followup"){ b = "command";
    d = `${e.intent}: “${e.transcript}” → ${e.response||""}`; }
  else if (e.type === "mark"){ b = "mark";
    const p = scores[e.clip];
    d = `${e.seconds}s saved` + (p ? ` — peak scores: ${Object.entries(p).map(([k,v])=>`${k} ${v}`).join(", ")}` : " — scoring…"); }
  else { d = JSON.stringify(Object.assign({}, e, {ts: undefined, type: undefined})); }
  const audio = e.clip ? `<audio controls preload="none" src="/clips/${e.clip}"></audio>` : "";
  return `<div class="ev"><span class="t">${fmt(e.ts)}</span><span class="b ${b}">${label}</span><span class="d">${d}</span>${audio}</div>`;
}
async function refresh(){
  if ([...document.querySelectorAll("audio")].some(a => !a.paused)) return;
  const evs = await (await fetch("/events?limit=60")).json();
  const scores = {};
  evs.forEach(e => { if (e.type === "mark_scores") scores[e.clip] = e.peaks; });
  document.getElementById("list").innerHTML =
    evs.filter(e => e.type !== "mark_scores").reverse().map(e => row(e, scores)).join("") || "no events";
}
document.getElementById("mark").onclick = async () => {
  const s = document.getElementById("stat");
  s.textContent = "saving…";
  try {
    const r = await (await fetch("/mark", {method:"POST"})).json();
    s.textContent = r.ok ? `saved ${r.clip} — scoring in background` : (r.error||"failed");
  } catch(e){ s.textContent = "error: " + e; }
  setTimeout(refresh, 500); setTimeout(refresh, 6000); setTimeout(refresh, 15000);
};
refresh(); setInterval(refresh, 10000);
</script></body></html>"""


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

    def _serve_bytes(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/review":
            self._serve_bytes(REVIEW_HTML.encode(), "text/html; charset=utf-8")
            return
        if path == "/events":
            limit = 60
            for part in query.split("&"):
                if part.startswith("limit="):
                    try:
                        limit = max(1, min(500, int(part[6:])))
                    except ValueError:
                        pass
            self._json(200, _events_tail(limit))
            return
        if path.startswith("/clips/"):
            name = os.path.basename(path[len("/clips/"):])
            target = CLIP_DIR / name
            if not re.fullmatch(r"[\w.-]+\.wav", name) or not target.is_file():
                self._json(404, {"ok": False})
                return
            self._serve_bytes(target.read_bytes(), "audio/wav")
            return
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
        elif self.path == "/speak":
            url = str(self._read_json().get("url", ""))
            if not url:
                self._json(400, {"ok": False, "error": "url required"})
                return
            if url.startswith("/"):
                url = ORCH_BASE + url
            threading.Thread(target=speak_url, args=(url,), daemon=True).start()
            self._json(200, {"ok": True})
        elif self.path == "/media/play":
            url = str(self._read_json().get("url", ""))
            if not url:
                self._json(400, {"ok": False, "error": "url required"})
                return
            # Synchronous through aplay spawn: the response is the kiosk's cue
            # to start the picture, so audio and video begin close together.
            self._json(200, {"ok": media_play(url)})
        elif self.path == "/media/stop":
            media_stop()
            self._json(200, {"ok": True})
        elif self.path.partition("?")[0] == "/play":
            # Playback relay target: a mic-only satellite (family room) POSTs
            # its chime/TTS/reply WAV here so this box stays the house's one
            # voice. Scaled by OUR volume; responds only after playback ends
            # (the relayer's turn timing depends on it). Each request occupies
            # one ThreadingHTTPServer thread — fine at this call rate.
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n) if n else b""
            if not body:
                self._json(400, {"ok": False, "error": "wav body required"})
                return
            is_alarm = "alarm=1" in self.path.partition("?")[2]
            _play_local(body, is_alarm)
            self._json(200, {"ok": True})
        elif self.path == "/trigger":
            if STATE.get_mode() != "active":
                self._json(409, {"ok": False, "error": "satellite not active"})
                return
            if STATE.current_alarm is not None:
                self._json(409, {"ok": False, "error": "alarm ringing"})
                return
            MANUAL_TRIGGER_AT[0] = time.time()
            MANUAL_TRIGGER.set()
            self._json(200, {"ok": True})
        elif self.path == "/mark":
            pcm = b"".join(RING)
            if not pcm:
                self._json(409, {"ok": False, "error": "ring empty"})
                return
            name = f"mark-{time.strftime('%Y%m%d-%H%M%S')}.wav"
            (CLIP_DIR / name).write_bytes(wrap_wav(pcm))
            append_event({"type": "mark", "clip": name,
                          "seconds": round(len(pcm) / 2 / SAMPLE_RATE, 1)})
            log(f"mark saved {name} ({len(pcm)//2//SAMPLE_RATE}s)")

            def _score():
                try:
                    peaks = _score_clip(pcm)
                    append_event({"type": "mark_scores", "clip": name, "peaks": peaks})
                    log(f"mark scores {name}: {peaks}")
                except Exception as exc:  # noqa: BLE001
                    log(f"mark scoring failed: {exc}")

            threading.Thread(target=_score, daemon=True).start()
            self._json(200, {"ok": True, "clip": name})
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

    from livekit.wakeword import WakeWordModel

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    for _mp in MODEL_PATHS:
        if not Path(_mp).is_file():
            log(f"model missing: {_mp}")
            return 3
    if not Path(SILERO_MODEL).is_file():
        log(f"silero VAD model missing: {SILERO_MODEL}")
        return 3

    model = WakeWordModel(models=MODEL_PATHS)
    model_keys = list(model.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16)).keys())
    vad = SileroVad(SILERO_MODEL)
    log(f"Silero VAD loaded ({SILERO_MODEL}, threshold {SILERO_THRESHOLD})")

    stop_model = stop_key = None
    if Path(STOP_MODEL_PATH).is_file():
        stop_model = WakeWordModel(models=[STOP_MODEL_PATH])
        stop_key = list(stop_model.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16)).keys())[0]
        log(f"stop barge-in model loaded ({STOP_MODEL_PATH}, threshold {STOP_THRESHOLD})")
    else:
        log(f"no stop model at {STOP_MODEL_PATH}; alarm dismiss is ASR-only")

    server = http.server.ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    arecord = subprocess.Popen(
        ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE)
    # The default 64KB pipe holds only ~2.0s of 16k mono audio. Reply playback
    # blocks the reader for 5-8s and the pre-capture seam (verify+chime) for up
    # to ~2s — both overflowed it, making arecord overrun and silently DROP mic
    # audio (7 overruns logged 2026-07-19). 1MB ≈ 32s of headroom; drain_input
    # discards the backlog where it isn't wanted.
    try:
        import fcntl
        fcntl.fcntl(arecord.stdout.fileno(),
                    getattr(fcntl, "F_SETPIPE_SZ", 1031), 1 << 20)
    except OSError as exc:
        log(f"pipe resize failed (staying at 64KB): {exc}")
    log(f"satellite up: phrase={WAKE_PHRASE!r} threshold={TRIGGER_THRESHOLD} "
        f"mode={STATE.get_mode()} orch={ORCH_BASE}")
    append_event({"type": "start", "mode": STATE.get_mode(),
                  "model": ",".join(os.path.basename(p) for p in MODEL_PATHS)})

    frame_bytes = FRAME_SAMPLES * 2
    ring = RING
    window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
    frames_since_hop = 0
    guard_until = 0.0

    in_alarm = False
    ring_wav = None

    def resync():
        nonlocal window, frames_since_hop, guard_until
        drain_input(arecord.stdout)
        window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
        frames_since_hop = 0
        guard_until = time.time() + RETRIGGER_GUARD_S

    alarm_window: deque = deque(maxlen=max(1, ALARM_WINDOW_MS // VAD_FRAME_MS))
    alarm_hop_frames = max(1, ALARM_HOP_MS // VAD_FRAME_MS)
    alarm_frames = 0
    stop_hop_chunks = max(1, STOP_HOP_MS // VAD_FRAME_MS)
    stop_window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
    stop_chunks = 0
    # Real samples shifted into stop_window since the alarm started. Until it
    # reaches WINDOW_SAMPLES the window is still part digital silence from the
    # alarm-start reset, and the model must NOT be asked to score it — see the
    # startup-transient note at the scoring site below.
    stop_filled = 0

    while True:
        # while an alarm is ringing (playback runs in its own thread), the main
        # loop listens continuously for a 'stop' barge-in — no wake word needed.
        # Overlapping windows via a rolling buffer; transcription on the
        # DismissChecker worker so this read cadence never blocks. The trained
        # stop model scores the same audio every ~224ms and dismisses mid-beep;
        # the ASR path stays as fallback for "cancel"/"turn it off".
        if STATE.current_alarm is not None:
            if not in_alarm:
                in_alarm = True
                alarm_window.clear()
                alarm_frames = 0
                stop_window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
                stop_chunks = 0
                stop_filled = 0
                try:
                    ALARM_RING_DIR.mkdir(parents=True, exist_ok=True)
                    ring_wav = wave.open(str(
                        ALARM_RING_DIR / time.strftime("ring-%Y%m%d-%H%M%S.wav")), "wb")
                    ring_wav.setnchannels(1)
                    ring_wav.setsampwidth(2)
                    ring_wav.setframerate(SAMPLE_RATE)
                except Exception as exc:  # noqa: BLE001
                    log(f"ring capture open failed: {exc}")
                    ring_wav = None
            b = arecord.stdout.read(SileroVad.CHUNK * 2)
            if not b or len(b) < SileroVad.CHUNK * 2:
                log("arecord stream ended; exiting for restart")
                return 1
            if ring_wav is not None:
                ring_wav.writeframes(b)
            alarm_window.append(b)
            alarm_frames += 1
            if alarm_frames >= alarm_hop_frames and len(alarm_window) >= alarm_hop_frames:
                alarm_frames = 0
                DISMISS_CHECKER.offer(b"".join(alarm_window))
            if stop_model is not None:
                stop_window = np.concatenate(
                    [stop_window[SileroVad.CHUNK:], np.frombuffer(b, dtype=np.int16)])
                stop_filled += SileroVad.CHUNK
                stop_chunks += 1
                # STARTUP TRANSIENT GUARD (2026-07-25). stop_window is reset to
                # ZEROS at alarm start, so for the first 2s it is mostly digital
                # silence with a sliver of ring at the end — an input the model
                # never saw in training (every training clip is a full 2s of
                # audio). Scoring it is meaningless and it is what actually
                # caused the 2026-07-24 self-dismissals: replaying the 19 real
                # captured rings through this exact loop, v1 peaks 0.5-0.92 in
                # that pre-fill window on 18/19 clips — including clips with no
                # spoken "stop" anywhere — while the same clips score <=0.26 in
                # steady state. (v2, trained with real ring backgrounds, was no
                # better: 17/19.) The replay reproduced the live incident's
                # logged scores to 3 decimals, so this is the mechanism, not a
                # guess. Waiting for a full window costs no real recall: the
                # announcement is still playing and nobody has barged in yet,
                # and the ASR dismiss path covers the gap either way.
                # See training/replay_stop_faithful.py.
                if stop_chunks >= stop_hop_chunks and stop_filled >= WINDOW_SAMPLES:
                    stop_chunks = 0
                    s = float(stop_model.predict(stop_window)[stop_key])
                    if s >= STOP_LOG_THRESHOLD:
                        log(f"stop-model score={round(s, 3)}")
                    if (s >= STOP_THRESHOLD and STATE.current_alarm is not None
                            and not STATE.dismiss.is_set()):
                        log(f"stop model fired score={round(s, 3)} -> stopping alarm")
                        append_event({"type": "alarm_stop_model", "score": round(s, 3)})
                        STATE.dismiss.set()
            continue
        if in_alarm:
            in_alarm = False
            if ring_wav is not None:
                try:
                    ring_wav.close()
                    keep = sorted(ALARM_RING_DIR.glob("ring-*.wav"))
                    for old in keep[:-ALARM_RING_KEEP]:
                        old.unlink()
                except Exception as exc:  # noqa: BLE001
                    log(f"ring capture close failed: {exc}")
                ring_wav = None
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

        if MANUAL_TRIGGER.is_set():
            MANUAL_TRIGGER.clear()
            if mode == "active" and time.time() - MANUAL_TRIGGER_AT[0] < MANUAL_TRIGGER_TTL_S:
                media_stop()
                duck_music()
                try:
                    run_manual_turn(arecord.stdout, vad)
                finally:
                    unduck_music()
                resync()
            continue

        frames_since_hop += 1
        if frames_since_hop < HOP_FRAMES:
            continue
        frames_since_hop = 0

        scores = model.predict(window)
        top_key = max(model_keys, key=lambda k: scores.get(k, 0.0))
        score = float(scores.get(top_key, 0.0))
        now = time.time()
        if score < TRIGGER_THRESHOLD or now < guard_until:
            continue

        STATE.stats["triggers"] += 1
        peak = round(score, 3)
        append_event({"type": "trigger", "peak_score": peak, "mode": mode,
                      "model": top_key})
        log(f"stage-1 trigger peak={peak} model={top_key} mode={mode}")
        guard_until = now + RETRIGGER_GUARD_S
        if mode != "active":
            continue

        preroll = b"".join(list(ring)[-int(PREROLL_S * FPS):])
        media_stop()   # slideshow video audio would talk over the turn
        run_turn(preroll, arecord.stdout, vad, now)
        resync()


if __name__ == "__main__":
    sys.exit(main())
