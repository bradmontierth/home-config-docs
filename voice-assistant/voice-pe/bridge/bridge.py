#!/usr/bin/env python3
"""ESPHome API audio bridge for Simon's Voice PE.

The device continuously streams its processed microphone channel over the
native API. This process runs the same two-stage wake path as the existing
satellites: livekit-wakeword locally, then Parakeet verification through the
orchestrator. It starts in shadow mode and has a separate file interlock before
active mode can produce any feedback or routed room audio.
"""

from __future__ import annotations

import asyncio
import difflib
import io
import json
import logging
import os
import re
import signal
import time
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import numpy as np
import yaml
from aioesphomeapi import APIClient
from aioesphomeapi.model import Event
from livekit.wakeword import WakeWordModel


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("simon-voice-bridge")

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
WINDOW_SAMPLES = 32_000
FRAME_BYTES = FRAME_SAMPLES * 2
FPS = SAMPLE_RATE / FRAME_SAMPLES

DEVICE_HOST = os.getenv("DEVICE_HOST", "192.168.30.62")
DEVICE_PORT = int(os.getenv("DEVICE_PORT", "6053"))
DEVICE_NAME = os.getenv("DEVICE_NAME", "simon-voice-pe")
SATELLITE_ID = os.getenv("SATELLITE_ID", "simon")
ORCH_BASE = os.getenv("ORCH_BASE", "http://127.0.0.1:8785").rstrip("/")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8793"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
ACTIVE_ARM_FILE = Path(os.getenv("ACTIVE_ARM_FILE", "/data/routed-audio-armed"))
START_MODE = os.getenv("MODE", "shadow").lower()
AUDIO_CHANNEL = os.getenv("AUDIO_CHANNEL", "processed").lower()
MODEL_PATHS = [p for p in os.getenv("MODEL_PATHS", "").split(",") if p]
SILERO_MODEL = os.getenv("SILERO_MODEL", "/models/silero_vad.onnx")
STOP_MODEL_PATH = os.getenv("STOP_MODEL_PATH", "/models/stop.onnx")
STOP_MODEL_ENABLED = os.getenv("STOP_MODEL_ENABLED", "false").lower() in {
    "1", "true", "yes", "on",
}
TRIGGER_THRESHOLD = float(os.getenv("TRIGGER_THRESHOLD", "0.5"))
HOP_FRAMES = max(1, int(int(os.getenv("HOP_MS", "320")) / 1000 * FPS))
STOP_THRESHOLD = float(os.getenv("STOP_THRESHOLD", "0.5"))
STOP_LOG_THRESHOLD = float(os.getenv("STOP_LOG_THRESHOLD", "0.2"))
STOP_REQUIRED_HITS = max(1, int(os.getenv("STOP_REQUIRED_HITS", "2")))
STOP_DISMISS_ENABLED = os.getenv("STOP_DISMISS_ENABLED", "false").lower() in {
    "1", "true", "yes", "on",
}
STOP_HOP_FRAMES = max(1, int(int(os.getenv("STOP_HOP_MS", "224")) / 1000 * FPS))
ALARM_ASR_CLIENT = os.getenv("ALARM_ASR_CLIENT", "kitchen-alarm")
ALARM_ASR_TIMEOUT_S = float(os.getenv("ALARM_ASR_TIMEOUT_S", "10"))
ALARM_WINDOW_FRAMES = max(
    1, int(int(os.getenv("ALARM_WINDOW_MS", "2500")) / 1000 * FPS)
)
ALARM_HOP_FRAMES = max(
    1, int(int(os.getenv("ALARM_HOP_MS", "1000")) / 1000 * FPS)
)
DISMISS_WORDS = (
    "stop", "cancel", "okay computer", "ok computer", "dismiss",
    "turn off", "enough", "quiet", "off",
)
DISMISS_FUZZY = ("stop", "cancel", "dismiss", "enough", "quiet")
PREROLL_FRAMES = max(1, int(float(os.getenv("PREROLL_S", "2.5")) * FPS))
RETRIGGER_GUARD_S = float(os.getenv("RETRIGGER_GUARD_S", "1.5"))
SILERO_THRESHOLD = float(os.getenv("SILERO_THRESHOLD", "0.5"))
SILENCE_MS = int(os.getenv("SILENCE_MS", "700"))
MIN_VOICED_MS = int(os.getenv("MIN_VOICED_MS", "200"))
MIN_COMMAND_VOICED_MS = int(os.getenv("MIN_COMMAND_VOICED_MS", "500"))
WAKE_ONSET_MS = int(os.getenv("WAKE_ONSET_MS", "4000"))
MAX_COMMAND_S = float(os.getenv("MAX_COMMAND_S", "20"))
TZ = ZoneInfo(os.getenv("TZ", "America/Denver"))
QUIET_START = os.getenv("QUIET_START", "20:00")
QUIET_END = os.getenv("QUIET_END", "07:00")


def _clock_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


QUIET_START_MIN = _clock_minutes(QUIET_START)
QUIET_END_MIN = _clock_minutes(QUIET_END)


def _local_quiet() -> bool:
    now = datetime.now(TZ)
    minute = now.hour * 60 + now.minute
    if QUIET_START_MIN > QUIET_END_MIN:
        return minute >= QUIET_START_MIN or minute < QUIET_END_MIN
    return QUIET_START_MIN <= minute < QUIET_END_MIN


def _api_key() -> str:
    path = Path(os.environ["ESPhOME_SECRETS_FILE"])
    values = yaml.safe_load(path.read_text()) or {}
    key = str(values.get(os.getenv("API_KEY_NAME", "voice_pe_api_key"), "")).strip()
    if not key:
        raise RuntimeError(f"API encryption key missing from {path}")
    return key


def wrap_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return out.getvalue()


def dismiss_in(transcript: str) -> bool:
    """Recognize the kitchen alarm listener's stop words and close variants."""
    normalized = " ".join(re.sub(r"[^\w\s]", " ", transcript.lower()).split())
    for word in DISMISS_WORDS:
        # "off" must be a whole word so office/offer cannot dismiss an alarm.
        pattern = (
            rf"\b{re.escape(word)}\b"
            if word == "off"
            else rf"\b{re.escape(word)}"
        )
        if re.search(pattern, normalized):
            return True
    for token in re.findall(r"[a-z]+", normalized):
        if len(token) < 3:
            continue
        for word in DISMISS_FUZZY:
            if (
                token[0] == word[0]
                and difflib.SequenceMatcher(None, token, word).ratio() >= 0.8
            ):
                return True
    return False


def _bound_onnx_sessions() -> None:
    """Make the small always-on models use one non-spinning worker thread.

    LiveKit constructs its internal sessions without accepting SessionOptions.
    Install this process-local constructor wrapper before those sessions are
    created; it does not affect any other container or ESPHome workload.
    """
    import onnxruntime as ort

    original = ort.InferenceSession
    if getattr(original, "_simon_bounded", False):
        return

    def bounded_session(path_or_bytes, sess_options=None, *args, **kwargs):
        options = sess_options or ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        return original(path_or_bytes, options, *args, **kwargs)

    bounded_session._simon_bounded = True
    ort.InferenceSession = bounded_session


_bound_onnx_sessions()


class BatchedWakeWordModel(WakeWordModel):
    """Equivalent LiveKit inference with one batched embedding-model call.

    The upstream stateless predictor invokes the embedding ONNX session once
    for each of the 16 overlapping mel windows. Its feature extractor already
    supports batching those windows, which materially reduces continuous CPU.
    """

    EMBEDDING_WINDOW = 76
    EMBEDDING_STRIDE = 8
    MIN_EMBEDDINGS = 16

    def predict(self, audio_chunk: np.ndarray) -> dict[str, float]:
        if not self._classifiers:
            return {}
        if audio_chunk.dtype == np.int16:
            audio_chunk = audio_chunk.astype(np.float32) / 32768.0

        all_mel = self._mel_frontend(audio_chunk.flatten())
        if all_mel.ndim == 2:
            all_mel = all_mel[np.newaxis, :, :]
        embeddings = self._speech_embedding.extract_embeddings(
            all_mel,
            window_size=self.EMBEDDING_WINDOW,
            stride=self.EMBEDDING_STRIDE,
        )
        if embeddings.shape[1] < self.MIN_EMBEDDINGS:
            return {name: 0.0 for name in self._classifiers}

        emb_input = embeddings[:, -self.MIN_EMBEDDINGS :, :].astype(np.float32)
        predictions = {}
        for name, (session, input_name) in self._classifiers.items():
            output = session.run(None, {input_name: emb_input})
            predictions[name] = float(output[0][0, 0])
        return predictions


class SileroVad:
    CHUNK = 512
    CONTEXT = 64

    def __init__(self, model_path: str, threshold: float):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(model_path, sess_options=options)
        self.threshold = threshold
        self.sample_rate = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def is_speech(self, frame: bytes) -> bool:
        chunk = (np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0).reshape(1, -1)
        samples = np.concatenate([self.context, chunk], axis=1)
        output = self.session.run(
            None,
            {"input": samples, "state": self.state, "sr": self.sample_rate},
        )
        self.state = output[1]
        self.context = samples[:, -self.CONTEXT :]
        return float(output[0].ravel()[0]) >= self.threshold


class Bridge:
    def __init__(self) -> None:
        required_paths = [*MODEL_PATHS, SILERO_MODEL]
        if STOP_MODEL_ENABLED:
            required_paths.append(STOP_MODEL_PATH)
        for path in required_paths:
            if not Path(path).is_file():
                raise RuntimeError(f"required model missing: {path}")
        self.model = BatchedWakeWordModel(models=MODEL_PATHS)
        self.model_keys = list(
            self.model.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16)).keys()
        )
        self.vad = SileroVad(SILERO_MODEL, SILERO_THRESHOLD)
        self.stop_model: BatchedWakeWordModel | None = None
        self.stop_key: str | None = None
        if STOP_MODEL_ENABLED:
            self.stop_model = BatchedWakeWordModel(models=[STOP_MODEL_PATH])
            self.stop_key = next(iter(
                self.stop_model.predict(np.zeros(WINDOW_SAMPLES, dtype=np.int16))
            ))
        self.mode = START_MODE if START_MODE in {"shadow", "probe", "off"} else "shadow"
        if START_MODE == "active" and ACTIVE_ARM_FILE.exists():
            self.mode = "active"
        self.client: APIClient | None = None
        self.services: dict[str, object] = {}
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=512)
        self.turn_queue: asyncio.Queue[bytes] | None = None
        self.audio_buffer = bytearray()
        self.ring: deque[bytes] = deque(maxlen=max(PREROLL_FRAMES, int(20 * FPS)))
        self.window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
        self.wake_filled = 0
        self.hop_frames = 0
        self.guard_until = 0.0
        self.alarm_active = False
        self.alarm_armed = False
        self.alarm_timer_id: str | None = None
        self.stop_window = np.zeros(WINDOW_SAMPLES, dtype=np.int16)
        self.stop_filled = 0
        self.stop_hop_frames = 0
        self.stop_consecutive = 0
        self.alarm_dismiss_task: asyncio.Task | None = None
        self.last_stop_score: float | None = None
        self.alarm_asr_window: deque[bytes] = deque(maxlen=ALARM_WINDOW_FRAMES)
        self.alarm_asr_hop_frames = 0
        self.alarm_asr_latest: tuple[int, str | None, bytes] | None = None
        self.alarm_asr_task: asyncio.Task | None = None
        self.alarm_generation = 0
        self.last_alarm_transcript: str | None = None
        self.turn_task: asyncio.Task | None = None
        self.connected = False
        self.audio_started = False
        self.settings = "unknown"
        self.last_audio_at = 0.0
        self.last_rms = 0
        self.peak_10s = 0
        self.peak_reset_at = time.time() + 10
        self.last_trigger: dict | None = None
        self.last_probe: dict | None = None
        self.stats = {
            "audio_bytes": 0,
            "triggers": 0,
            "verified": 0,
            "dropped": 0,
            "alarm_asr_transcribes": 0,
            "alarm_asr_dismissals": 0,
        }

    def health(self) -> dict:
        return {
            "ok": self.connected,
            "mode": self.mode,
            "active_armed": ACTIVE_ARM_FILE.exists(),
            "audio_started": self.audio_started,
            "last_audio_age_s": (
                round(time.time() - self.last_audio_at, 1) if self.last_audio_at else None
            ),
            "audio_settings": self.settings,
            "audio_rms": self.last_rms,
            "audio_peak_10s": self.peak_10s,
            "quiet_local": _local_quiet(),
            "turn_active": bool(self.turn_task and not self.turn_task.done()),
            "alarm_active": self.alarm_active,
            "alarm_armed": self.alarm_armed,
            "alarm_timer_id": self.alarm_timer_id,
            "last_stop_score": self.last_stop_score,
            "stop_model_enabled": STOP_MODEL_ENABLED,
            "stop_dismiss_enabled": STOP_DISMISS_ENABLED,
            "alarm_asr_active": bool(
                self.alarm_asr_task and not self.alarm_asr_task.done()
            ),
            "last_alarm_transcript": self.last_alarm_transcript,
            "last_trigger": self.last_trigger,
            "last_probe": self.last_probe,
            "stats": self.stats,
        }

    async def set_mode(self, mode: str) -> tuple[bool, str]:
        if mode not in {"active", "shadow", "probe", "off"}:
            return False, "mode must be active, shadow, probe, or off"
        if mode == "active" and not ACTIVE_ARM_FILE.exists():
            return False, f"active mode is locked; {ACTIVE_ARM_FILE} does not exist"
        self.mode = mode
        log.info("mode -> %s", mode)
        return True, mode

    async def call_service(self, name: str) -> None:
        service = self.services.get(name)
        if not service or not self.client:
            log.warning("ESPHome service unavailable: %s", name)
            return
        await self.client.execute_service(service, {})

    async def policy(self) -> dict:
        if _local_quiet():
            return {"allowed": False, "reason": "local_quiet_hours"}
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{ORCH_BASE}/satellite/policy", params={"sat": SATELLITE_ID}
                ) as response:
                    if response.status != 200:
                        return {"allowed": False, "reason": f"policy_http_{response.status}"}
                    return await response.json()
        except Exception as exc:  # noqa: BLE001 - policy must fail closed
            return {"allowed": False, "reason": f"policy_unavailable:{type(exc).__name__}"}

    async def post_wav(self, path: str, pcm: bytes, timeout_s: float) -> dict:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{ORCH_BASE}{path}", data=wrap_wav(pcm), headers={"Content-Type": "audio/wav"}
            ) as response:
                response.raise_for_status()
                return await response.json()

    async def post_json(self, path: str, body: dict, timeout_s: float) -> dict:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{ORCH_BASE}{path}", json=body) as response:
                response.raise_for_status()
                return await response.json()

    def _reset_stop_window(self) -> None:
        self.stop_window.fill(0)
        self.stop_filled = 0
        self.stop_hop_frames = 0
        self.stop_consecutive = 0

    def _reset_alarm_asr_window(self) -> None:
        self.alarm_asr_window.clear()
        self.alarm_asr_hop_frames = 0
        self.alarm_asr_latest = None

    def offer_alarm_asr(self) -> None:
        """Offer the newest rolling window without ever blocking mic intake.

        A slow Parakeet decode drops intermediate snapshots and consumes only
        the newest one next, matching the proven kitchen alarm listener.
        """
        self.alarm_asr_latest = (
            self.alarm_generation,
            self.alarm_timer_id,
            b"".join(self.alarm_asr_window),
        )
        if not self.alarm_asr_task or self.alarm_asr_task.done():
            self.alarm_asr_task = asyncio.create_task(self.alarm_asr_worker())

    async def alarm_asr_worker(self) -> None:
        try:
            while self.alarm_asr_latest is not None:
                generation, timer_id, pcm = self.alarm_asr_latest
                self.alarm_asr_latest = None
                try:
                    result = await self.post_wav(
                        f"/transcribe?client={ALARM_ASR_CLIENT}",
                        pcm,
                        timeout_s=ALARM_ASR_TIMEOUT_S,
                    )
                    transcript = str(result.get("transcript") or "").strip()
                    self.stats["alarm_asr_transcribes"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("alarm ASR failed: %s", exc)
                    continue
                if transcript:
                    self.last_alarm_transcript = transcript
                    log.info("alarm-listen heard: %r", transcript)
                if not dismiss_in(transcript):
                    continue
                if not (
                    self.alarm_active
                    and self.alarm_armed
                    and generation == self.alarm_generation
                    and timer_id == self.alarm_timer_id
                ):
                    log.info("stale/disarmed alarm dismiss transcript ignored")
                    continue

                # Disarm synchronously before the POST so another overlapping
                # window cannot submit a duplicate dismissal.
                self.alarm_armed = False
                self.alarm_asr_latest = None
                self.stats["alarm_asr_dismissals"] += 1
                log.info("alarm ASR dismiss transcript=%r timer=%s", transcript, timer_id)
                try:
                    await self.post_json("/alarm/stop", {"sat": SATELLITE_ID}, 15)
                except Exception as exc:  # noqa: BLE001
                    log.warning("alarm ASR dismiss failed: %s", exc)
                    if self.alarm_active and timer_id == self.alarm_timer_id:
                        self.arm_alarm()
                else:
                    # Match the Pi satellites: confirmation is local to the
                    # listening appliance and cannot leak through Snap groups.
                    await self.call_service("bridge_alarm_dismissed")
                return
        finally:
            self.alarm_asr_task = None

    def stop_score_fires(self, score: float) -> bool:
        """Apply the model's trained two-consecutive-window arming rule."""
        self.last_stop_score = score
        if score >= STOP_THRESHOLD:
            self.stop_consecutive += 1
        else:
            self.stop_consecutive = 0
        return self.stop_consecutive >= STOP_REQUIRED_HITS

    def start_alarm(self, timer_id: str | None, armed: bool) -> None:
        self.alarm_generation += 1
        self.alarm_timer_id = timer_id
        self.alarm_active = True
        self.alarm_armed = armed
        self.last_stop_score = None
        self.last_alarm_transcript = None
        self._reset_stop_window()
        self._reset_alarm_asr_window()
        # Do not let the announcement/ring tail become wake-word pre-roll when
        # the alarm eventually stops.
        self.window.fill(0)
        self.wake_filled = 0
        self.hop_frames = 0
        log.info("alarm start timer=%s armed=%s", timer_id, armed)

    def arm_alarm(self) -> None:
        if not self.alarm_active:
            return
        # Invalidate any in-flight pre-arm decode and flush the spoken
        # announcement from both rolling windows. It has previously decoded as
        # "turn it off" on the kitchen pipeline.
        self.alarm_generation += 1
        self._reset_stop_window()
        self._reset_alarm_asr_window()
        self.alarm_armed = True
        log.info("alarm ASR stop listener armed timer=%s", self.alarm_timer_id)

    def clear_alarm(self) -> None:
        if self.alarm_active:
            log.info("alarm cleared timer=%s", self.alarm_timer_id)
        self.alarm_generation += 1
        self.alarm_active = False
        self.alarm_armed = False
        self.alarm_timer_id = None
        self._reset_stop_window()
        self._reset_alarm_asr_window()
        self.window.fill(0)
        self.wake_filled = 0
        self.hop_frames = 0

    async def dismiss_alarm(self, score: float) -> None:
        try:
            log.info("stop model fired score=%.3f -> dismiss Simon alarm", score)
            await self.post_json("/alarm/stop", {"sat": SATELLITE_ID}, 15)
        except Exception as exc:  # noqa: BLE001
            log.warning("stop-model alarm dismiss failed: %s", exc)
            if self.alarm_active:
                self.alarm_armed = True

    async def button_stop(self) -> None:
        try:
            timeout = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{ORCH_BASE}/music/button-stop", params={"sat": SATELLITE_ID}
                ) as response:
                    if response.status != 200:
                        log.warning("button stop returned HTTP %s", response.status)
        except Exception as exc:  # noqa: BLE001
            log.warning("button stop failed: %s", exc)

    def on_state(self, state: object) -> None:
        if not isinstance(state, Event):
            return
        event_type = getattr(state, "event_type", "")
        if event_type == "press":
            log.info("center button press -> stop Simon music")
            asyncio.create_task(self.button_stop())

    async def on_start(self, conversation_id, flags, settings, wake_word_phrase):
        self.audio_started = True
        self.settings = repr(settings)
        log.info(
            "audio stream requested conversation=%s flags=%s settings=%s wake=%r channel=%s",
            conversation_id,
            flags,
            settings,
            wake_word_phrase,
            AUDIO_CHANNEL,
        )
        return 0

    async def on_stop(self, abort: bool) -> None:
        self.audio_started = False
        log.warning("audio stream stopped abort=%s", abort)

    async def on_audio(self, raw: bytes, processed: bytes | None) -> None:
        if AUDIO_CHANNEL == "raw":
            chunk = raw
        else:
            chunk = processed if processed is not None else raw
        if not chunk:
            return
        self.last_audio_at = time.time()
        self.stats["audio_bytes"] += len(chunk)
        try:
            self.audio_queue.put_nowait(bytes(chunk))
        except asyncio.QueueFull:
            self.stats["dropped"] += len(chunk)

    async def process_audio(self) -> None:
        while True:
            chunk = await self.audio_queue.get()
            self.audio_buffer.extend(chunk)
            while len(self.audio_buffer) >= FRAME_BYTES:
                frame = bytes(self.audio_buffer[:FRAME_BYTES])
                del self.audio_buffer[:FRAME_BYTES]
                samples = np.frombuffer(frame, dtype=np.int16)
                self.last_rms = round(float(np.sqrt(np.mean(
                    samples.astype(np.float32) ** 2))))
                now = time.time()
                if now >= self.peak_reset_at:
                    self.peak_10s = 0
                    self.peak_reset_at = now + 10
                self.peak_10s = max(self.peak_10s, int(np.max(np.abs(
                    samples.astype(np.int32)))))
                self.ring.append(frame)
                if self.turn_queue is not None:
                    self.turn_queue.put_nowait(frame)
                    continue
                if self.alarm_active:
                    if self.alarm_armed:
                        self.alarm_asr_window.append(frame)
                        self.alarm_asr_hop_frames += 1
                        if (
                            self.alarm_asr_hop_frames >= ALARM_HOP_FRAMES
                            and len(self.alarm_asr_window) >= ALARM_HOP_FRAMES
                        ):
                            self.alarm_asr_hop_frames = 0
                            self.offer_alarm_asr()
                    if STOP_MODEL_ENABLED and self.stop_model and self.stop_key:
                        stop_samples = np.frombuffer(frame, dtype=np.int16)
                        self.stop_window = np.concatenate([
                            self.stop_window[FRAME_SAMPLES:], stop_samples
                        ])
                        self.stop_filled = min(
                            WINDOW_SAMPLES, self.stop_filled + FRAME_SAMPLES
                        )
                        self.stop_hop_frames += 1
                    if (
                        STOP_MODEL_ENABLED
                        and self.stop_model is not None
                        and self.stop_key is not None
                        and self.alarm_armed
                        and self.stop_filled >= WINDOW_SAMPLES
                        and self.stop_hop_frames >= STOP_HOP_FRAMES
                    ):
                        self.stop_hop_frames = 0
                        score = float(self.stop_model.predict(self.stop_window)[self.stop_key])
                        fires = self.stop_score_fires(score)
                        if score >= STOP_LOG_THRESHOLD:
                            log.info(
                                "alarm stop-model score=%.3f hits=%d/%d",
                                score,
                                self.stop_consecutive,
                                STOP_REQUIRED_HITS,
                            )
                        if fires and STOP_DISMISS_ENABLED:
                            # Disarm before scheduling so adjacent overlapping
                            # windows cannot send duplicate dismiss requests.
                            self.alarm_armed = False
                            self.alarm_dismiss_task = asyncio.create_task(
                                self.dismiss_alarm(score)
                            )
                        elif fires:
                            log.warning(
                                "stop model would dismiss at score=%.3f; monitor-only",
                                score,
                            )
                    # While ringing, the dedicated stop head replaces normal
                    # wake scoring. Requiring "Okay computer" over the alarm
                    # is exactly the masking failure this path avoids.
                    continue
                if self.mode == "off":
                    continue
                self.window = np.concatenate([self.window[FRAME_SAMPLES:], samples])
                self.wake_filled = min(WINDOW_SAMPLES, self.wake_filled + FRAME_SAMPLES)
                self.hop_frames += 1
                if self.wake_filled < WINDOW_SAMPLES or self.hop_frames < HOP_FRAMES:
                    continue
                self.hop_frames = 0
                scores = self.model.predict(self.window)
                key = max(self.model_keys, key=lambda item: scores.get(item, 0.0))
                score = float(scores.get(key, 0.0))
                now = time.time()
                if score < TRIGGER_THRESHOLD or now < self.guard_until:
                    continue
                self.guard_until = now + RETRIGGER_GUARD_S
                self.stats["triggers"] += 1
                self.last_trigger = {
                    "at": datetime.now(TZ).isoformat(timespec="seconds"),
                    "model": key,
                    "score": round(score, 3),
                    "mode": self.mode,
                }
                log.info("stage-1 trigger model=%s score=%.3f mode=%s", key, score, self.mode)
                if self.mode == "shadow":
                    continue
                gate = await self.policy()
                if not gate.get("allowed", False):
                    log.info("wake no-op: %s", gate.get("reason", "policy"))
                    continue
                preroll = b"".join(list(self.ring)[-PREROLL_FRAMES:])
                self.turn_queue = asyncio.Queue(maxsize=1024)
                if self.mode == "probe":
                    self.turn_task = asyncio.create_task(self.run_probe(preroll))
                    continue
                self.turn_task = asyncio.create_task(self.run_turn(preroll))

    async def run_probe(self, preroll: bytes) -> None:
        """Stage-2 validation with no feedback, turn claim, amp wake, or action."""
        try:
            result = await self.post_wav(
                f"/verify/probe?sat={SATELLITE_ID}", preroll, timeout_s=20
            )
            self.last_probe = {
                "at": datetime.now(TZ).isoformat(timespec="seconds"),
                "verified": bool(result.get("verified")),
                "transcript": result.get("transcript"),
                "score": result.get("score"),
                "decode": result.get("decode"),
                "latency_ms": result.get("latency_ms"),
            }
            log.info("stage-2 probe result=%s", self.last_probe)
        except Exception as exc:  # noqa: BLE001
            log.exception("stage-2 probe failed: %s", exc)
        finally:
            self.turn_queue = None
            self.window.fill(0)
            self.wake_filled = 0
            self.hop_frames = 0
            self.guard_until = time.time() + RETRIGGER_GUARD_S

    async def capture_command(self) -> bytes:
        assert self.turn_queue is not None
        self.vad.reset()
        frames: list[bytes] = []
        speech = False
        voiced_ms = silence_ms = 0
        started = time.monotonic()
        speech_started = 0.0
        while True:
            if not speech and (time.monotonic() - started) * 1000 >= WAKE_ONSET_MS:
                break
            if speech and time.monotonic() - speech_started >= MAX_COMMAND_S:
                break
            try:
                frame = await asyncio.wait_for(self.turn_queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            frames.append(frame)
            is_speech = self.vad.is_speech(frame)
            if is_speech:
                if not speech:
                    speech_started = time.monotonic()
                speech = True
                voiced_ms += 32
                silence_ms = 0
            elif speech:
                silence_ms += 32
                if silence_ms >= SILENCE_MS and voiced_ms >= MIN_COMMAND_VOICED_MS:
                    break
        log.info(
            "capture frames=%s voiced_ms=%s tail_silence_ms=%s",
            len(frames),
            voiced_ms,
            silence_ms,
        )
        return b"".join(frames) if voiced_ms >= MIN_VOICED_MS else b""

    async def run_turn(self, preroll: bytes) -> None:
        try:
            verify = await self.post_wav(
                f"/verify?sat={SATELLITE_ID}", preroll, timeout_s=20
            )
            if not verify.get("verified"):
                log.info("stage-2 rejected transcript=%r", verify.get("transcript"))
                return
            # Re-check after ASR: a quiet/alarm state change during verification
            # still results in a silent no-op.
            gate = await self.policy()
            if not gate.get("allowed", False):
                log.info("verified wake no-op after policy recheck: %s", gate.get("reason"))
                return
            self.stats["verified"] += 1
            await self.call_service("bridge_wake_confirmed")
            command = await self.capture_command()
            if not command:
                return
            await self.call_service("bridge_vad_complete")
            # Continue the turn row /verify opened, so this turn is one row and
            # not two. No /telemetry back-post from here: the Voice PE plays its
            # own chime in ESPHome, so this process never sees a trigger→chime
            # number to report.
            path = f"/command/audio?stitched=1&sat={SATELLITE_ID}"
            if verify.get("turn_id"):
                path += f"&turn_id={verify['turn_id']}"
            response = await self.post_wav(
                path,
                preroll + command,
                timeout_s=120,
            )
            log.info(
                "command intent=%s transcript=%r response=%r",
                response.get("intent"),
                response.get("transcript"),
                response.get("response"),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed: %s", exc)
            await self.call_service("bridge_error")
        finally:
            await self.call_service("bridge_idle")
            self.turn_queue = None
            self.window.fill(0)
            self.wake_filled = 0
            self.hop_frames = 0
            self.guard_until = time.time() + RETRIGGER_GUARD_S

    async def connect_once(self) -> None:
        stopped = asyncio.Event()

        async def on_disconnect(expected: bool) -> None:
            log.warning("ESPHome disconnected expected=%s", expected)
            self.connected = False
            stopped.set()

        client = APIClient(
            DEVICE_HOST,
            DEVICE_PORT,
            None,
            client_info="simon-voice-bridge/1",
            noise_psk=_api_key(),
            expected_name=DEVICE_NAME,
        )
        self.client = client
        await client.connect(on_stop=on_disconnect, login=True)
        self.connected = True
        entities, services = await client.list_entities_services()
        self.services = {service.name: service for service in services}
        log.info(
            "connected to %s; %d entities; services=%s",
            DEVICE_NAME,
            len(entities),
            sorted(self.services),
        )
        client.subscribe_states(self.on_state)
        client.subscribe_voice_assistant(
            handle_start=self.on_start,
            handle_stop=self.on_stop,
            handle_audio=self.on_audio,
        )
        await self.call_service("bridge_start_stream")
        await stopped.wait()
        await client.disconnect(force=True)

    async def reconnect_loop(self) -> None:
        while True:
            try:
                await self.connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.connected = False
                log.warning("ESPHome connection failed: %s", exc)
            await asyncio.sleep(5)


async def http_server(bridge: Bridge) -> aiohttp.web.AppRunner:
    from aiohttp import web

    async def health(_request):
        return web.json_response(bridge.health())

    async def mode(request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        ok, detail = await bridge.set_mode(str(body.get("mode", "")))
        return web.json_response(
            {"ok": ok, "mode": bridge.mode, "detail": detail}, status=200 if ok else 409
        )

    async def alarm(request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        bridge.start_alarm(
            str(body.get("timer_id") or "") or None,
            bool(body.get("dismiss_armed", True)),
        )
        return web.json_response({"ok": True, "alarm_active": True})

    async def alarm_arm(_request):
        bridge.arm_alarm()
        return web.json_response({"ok": True, "alarm_armed": bridge.alarm_armed})

    async def alarm_dismiss(_request):
        bridge.clear_alarm()
        return web.json_response({"ok": True, "alarm_active": False})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/mode", mode)
    app.router.add_post("/alarm", alarm)
    app.router.add_post("/alarm/arm", alarm_arm)
    app.router.add_post("/alarm/dismiss", alarm_dismiss)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HTTP_PORT).start()
    return runner


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bridge = Bridge()
    runner = await http_server(bridge)
    processor = asyncio.create_task(bridge.process_audio())
    connector = asyncio.create_task(bridge.reconnect_loop())
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, done.set)
    log.info(
        "bridge up mode=%s active_armed=%s quiet=%s-%s",
        bridge.mode,
        ACTIVE_ARM_FILE.exists(),
        QUIET_START,
        QUIET_END,
    )
    await done.wait()
    connector.cancel()
    processor.cancel()
    await asyncio.gather(connector, processor, return_exceptions=True)
    if bridge.client:
        await bridge.client.disconnect(force=True)
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
