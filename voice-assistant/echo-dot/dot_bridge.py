#!/usr/bin/env python3
"""EchoMuse transport adapter for the existing two-stage voice bridge.

The Dot is deliberately a thin audio appliance. Its outbound WebSockets carry
continuous 16 kHz mono PCM to the Beelink; all wake, verification, VAD, intent,
and TTS work remains in the existing local assistant pipeline.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import signal
import socket
import time
import wave
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
import numpy as np
from aiohttp import WSMsgType, web
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from base_bridge import (
    ACTIVE_ARM_FILE,
    DATA_DIR,
    ORCH_BASE,
    RETRIGGER_GUARD_S,
    SATELLITE_ID,
    TZ,
    Bridge,
)


log = logging.getLogger("echo-dot-bridge")
DOT_PORT = int(os.getenv("DOT_PORT", "8770"))
CONTROLLER_IP = os.getenv("CONTROLLER_IP", "192.168.10.217")
MDNS_NAME = os.getenv("MDNS_NAME", "Beelink Echo Dot Bridge")
SOUNDS_DIR = Path(os.getenv("SOUNDS_DIR", "/sounds"))
DOT_STARTUP_VOLUME = int(os.getenv("DOT_STARTUP_VOLUME", "65"))
DOT_AEC_ENABLED = os.getenv("DOT_AEC_ENABLED", "true").lower() in {
    "1", "true", "yes", "on",
}
SPEAKER_RATE = 48_000
SPEAKER_PERIOD_BYTES = 4096


class EchoDotBridge(Bridge):
    def __init__(self) -> None:
        super().__init__()
        self.control_ws: web.WebSocketResponse | None = None
        self.data_ws: web.WebSocketResponse | None = None
        self.device_id: str | None = None
        self.device_version: str | None = None
        self.capabilities: list[str] = []
        self.control_connected = False
        self.data_connected = False
        self.play_lock = asyncio.Lock()
        self.last_device_message: dict | None = None
        self.last_playback_stats: dict | None = None

    def _refresh_connected(self) -> None:
        self.connected = self.control_connected and self.data_connected

    def health(self) -> dict:
        result = super().health()
        result.update({
            "transport": "echomuse-websocket",
            "device_id": self.device_id,
            "device_version": self.device_version,
            "device_ip": "192.168.10.47",
            "control_connected": self.control_connected,
            "data_connected": self.data_connected,
            "capabilities": self.capabilities,
            "last_device_message": self.last_device_message,
            "last_playback_stats": self.last_playback_stats,
        })
        return result

    async def send_control(self, message: dict) -> bool:
        ws = self.control_ws
        if ws is None or ws.closed:
            log.warning("Dot control unavailable for %s", message.get("type"))
            return False
        await ws.send_json(message)
        return True

    async def set_anim(self, pattern: str, colors: list[list[int]] | None = None,
                       ttl_s: int = 30, listening: bool = False) -> None:
        anim: dict = {"pattern": pattern}
        if colors is not None:
            anim["colors"] = colors
        if pattern != "off":
            anim["ttlSec"] = ttl_s
        if listening:
            anim["listening"] = True
        if pattern == "spin":
            anim["periodMs"] = 80
        await self.send_control({"type": "led_anim", "anim": anim})

    @staticmethod
    def wav_to_mono_48k(blob: bytes) -> tuple[bytes, float]:
        with wave.open(io.BytesIO(blob), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        if width != 2:
            raise ValueError(f"unsupported WAV sample width: {width}")
        samples = np.frombuffer(frames, dtype="<i2")
        if channels > 1:
            samples = samples.reshape(-1, channels).astype(np.int32).mean(axis=1)
        else:
            samples = samples.astype(np.int32)
        if rate != SPEAKER_RATE and samples.size:
            out_count = max(1, round(samples.size * SPEAKER_RATE / rate))
            old_pos = np.arange(samples.size, dtype=np.float64)
            new_pos = np.arange(out_count, dtype=np.float64) * rate / SPEAKER_RATE
            samples = np.interp(new_pos, old_pos, samples).round()
        pcm = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
        return pcm, len(pcm) / 2 / SPEAKER_RATE

    async def play_wav(self, blob: bytes) -> float:
        pcm, duration = self.wav_to_mono_48k(blob)
        ws = self.data_ws
        if ws is None or ws.closed:
            raise RuntimeError("Dot data connection unavailable")
        # Local playback is not a new wake source. The turn path already
        # diverts mic frames into turn_queue, but /speak can run while idle;
        # cover that case so a filler/reminder cannot wake on its own echo.
        self.guard_until = max(
            self.guard_until, time.time() + duration + RETRIGGER_GUARD_S
        )
        async with self.play_lock:
            for offset in range(0, len(pcm), SPEAKER_PERIOD_BYTES):
                period = pcm[offset:offset + SPEAKER_PERIOD_BYTES]
                if len(period) < SPEAKER_PERIOD_BYTES:
                    period += bytes(SPEAKER_PERIOD_BYTES - len(period))
                await ws.send_bytes(bytes([0x02]) + period)
            await ws.send_bytes(bytes([0x03]))
            # The Dot buffers playback. Keep chimes synchronous so command
            # capture starts at the same audible point as the Pi satellites.
            await asyncio.sleep(duration + 0.08)
        return duration

    async def play_file(self, name: str) -> None:
        path = SOUNDS_DIR / name
        await self.play_wav(path.read_bytes())

    async def play_url(self, url: str) -> None:
        target = url if url.startswith(("http://", "https://")) else urljoin(
            ORCH_BASE + "/", url.lstrip("/")
        )
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(target) as response:
                response.raise_for_status()
                blob = await response.read()
        duration = await self.play_wav(blob)
        log.info("played Dot response duration=%.2fs", duration)

    async def call_service(self, name: str) -> None:
        if name == "bridge_wake_confirmed":
            await self.send_control({"type": "beam_lock"})
            await self.set_anim("solid", [[0, 150, 80]], ttl_s=30, listening=True)
            await self.play_file("wake.wav")
        elif name == "bridge_vad_complete":
            await self.set_anim("spin", [[0, 180, 100], [0, 35, 20]], ttl_s=45)
            await self.play_file("vad.wav")
        elif name == "bridge_error":
            await self.set_anim("solid", [[180, 0, 0]], ttl_s=3)
        elif name == "bridge_alarm_dismissed":
            await self.play_file("dismiss.wav")
        elif name == "bridge_idle":
            await self.send_control({"type": "beam_unlock"})
            await self.set_anim("off")
        else:
            log.debug("unmapped Dot service %s", name)

    async def run_turn(self, preroll: bytes) -> None:
        try:
            verify = await self.post_wav(
                self.verify_path("/verify"), preroll, timeout_s=20
            )
            if not verify.get("verified"):
                log.info("stage-2 rejected transcript=%r", verify.get("transcript"))
                return
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
            path = f"/command/audio?stitched=1&sat={SATELLITE_ID}"
            if verify.get("turn_id"):
                path += f"&turn_id={verify['turn_id']}"
            response = await self.post_wav(path, preroll + command, timeout_s=120)
            log.info(
                "command intent=%s transcript=%r response=%r",
                response.get("intent"), response.get("transcript"), response.get("response"),
            )
            if response.get("audio_url"):
                await self.play_url(str(response["audio_url"]))
        except Exception as exc:  # noqa: BLE001
            log.exception("Dot turn failed: %s", exc)
            await self.call_service("bridge_error")
        finally:
            await self.call_service("bridge_idle")
            self.turn_queue = None
            self.window.fill(0)
            self.wake_filled = 0
            self.hop_frames = 0
            self.guard_until = time.time() + RETRIGGER_GUARD_S

    async def control_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        peer = request.remote
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=10)
            if first.type != WSMsgType.TEXT:
                raise ValueError("first control message was not JSON")
            register = json.loads(first.data)
            if register.get("type") != "register":
                raise ValueError("first control message was not register")
            old = self.control_ws
            if old is not None and not old.closed:
                await old.close(code=1001, message=b"superseded")
            self.control_ws = ws
            self.device_id = str(register.get("device_id") or "unknown")
            self.device_version = str(register.get("version") or "unknown")
            self.capabilities = list(register.get("capabilities") or [])
            self.control_connected = True
            self._refresh_connected()
            await ws.send_json({"type": "ack", "device_id": self.device_id})
            await ws.send_json({
                "type": "config",
                "owwOnDevice": "off",
                "adcDigitalGain": 88,
                "adcMicpga": 40,
                "micGainDb": 24,
                "startupVolume": DOT_STARTUP_VOLUME,
                "beamformingEnabled": True,
                "beamAngle": -1,
                "agcEnabled": False,
                "aecEnabled": DOT_AEC_ENABLED,
                "aecDelayMs": 0,
                "aecTailMs": 300,
                "aecRefSource": "auto",
                "bleProxyEnabled": False,
            })
            await self.set_anim("off")
            await ws.send_json({"type": "mic_start"})
            self.audio_started = True
            self.settings = "EchoMuse center/omni ch6, +24dB, 16kHz mono S16LE"
            log.info(
                "Dot control connected peer=%s id=%s version=%s caps=%s",
                peer, self.device_id, self.device_version, self.capabilities,
            )
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                body = json.loads(msg.data)
                kind = str(body.get("type") or "")
                self.last_device_message = {"type": kind, "at": time.time()}
                if kind == "ping":
                    await ws.send_json({"type": "pong"})
                elif kind == "playback_stats":
                    self.last_playback_stats = body
                    log.info("Dot playback stats=%s", body)
                elif kind == "button":
                    log.info("Dot button event=%s", body)
                    if not body.get("down") and int(body.get("clickType", -1)) == 138:
                        asyncio.create_task(self.button_stop())
                elif kind in {"log", "mute_state", "stats", "direction"}:
                    log.debug("Dot %s=%s", kind, body)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Dot control ended peer=%s: %s", peer, exc)
        finally:
            if self.control_ws is ws:
                self.control_ws = None
                self.control_connected = False
                self.audio_started = False
                self._refresh_connected()
        return ws

    async def data_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2 * 1024 * 1024)
        await ws.prepare(request)
        peer = request.remote
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=10)
            if first.type != WSMsgType.TEXT:
                raise ValueError("first data message was not identify JSON")
            identify = json.loads(first.data)
            if identify.get("type") != "identify":
                raise ValueError("first data message was not identify")
            old = self.data_ws
            if old is not None and not old.closed:
                await old.close(code=1001, message=b"superseded")
            self.data_ws = ws
            self.data_connected = True
            self._refresh_connected()
            log.info("Dot data connected peer=%s id=%s", peer, identify.get("device_id"))
            async for msg in ws:
                if msg.type != WSMsgType.BINARY:
                    continue
                packet = bytes(msg.data)
                if len(packet) > 3 and packet[0] == 0x01:
                    await self.on_audio(packet[3:], None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Dot data ended peer=%s: %s", peer, exc)
        finally:
            if self.data_ws is ws:
                self.data_ws = None
                self.data_connected = False
                self._refresh_connected()
        return ws


async def dot_server(bridge: EchoDotBridge) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/control", bridge.control_handler)
    app.router.add_get("/data", bridge.data_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", DOT_PORT).start()
    return runner


async def dot_http_server(bridge: EchoDotBridge) -> web.AppRunner:
    """Health/control surface plus local playback used by ask fillers."""

    async def health(_request: web.Request) -> web.Response:
        return web.json_response(bridge.health())

    async def mode(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        ok, detail = await bridge.set_mode(str(body.get("mode", "")))
        return web.json_response(
            {"ok": ok, "mode": bridge.mode, "detail": detail},
            status=200 if ok else 409,
        )

    async def alarm(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        bridge.start_alarm(
            str(body.get("timer_id") or "") or None,
            bool(body.get("dismiss_armed", True)),
        )
        return web.json_response({"ok": True, "alarm_active": True})

    async def alarm_arm(_request: web.Request) -> web.Response:
        bridge.arm_alarm()
        return web.json_response({"ok": True, "alarm_armed": bridge.alarm_armed})

    async def alarm_dismiss(_request: web.Request) -> web.Response:
        bridge.clear_alarm()
        return web.json_response({"ok": True, "alarm_active": False})

    async def speak(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        url = str(body.get("url") or "").strip()
        if not url:
            return web.json_response({"ok": False, "error": "url required"}, status=400)
        try:
            await bridge.play_url(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Dot /speak failed url=%s: %s", url, exc)
            return web.json_response(
                {"ok": False, "error": type(exc).__name__}, status=502
            )
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/mode", mode)
    app.router.add_post("/alarm", alarm)
    app.router.add_post("/alarm/arm", alarm_arm)
    app.router.add_post("/alarm/dismiss", alarm_dismiss)
    app.router.add_post("/speak", speak)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("HTTP_PORT", "8796"))).start()
    return runner


async def advertise() -> tuple[AsyncZeroconf, ServiceInfo]:
    info = ServiceInfo(
        "_emcontroller._tcp.local.",
        f"{MDNS_NAME}._emcontroller._tcp.local.",
        addresses=[socket.inet_aton(CONTROLLER_IP)],
        port=DOT_PORT,
        properties={"version": "1", "server": MDNS_NAME},
        server=f"{socket.gethostname()}.local.",
    )
    # This host has many Docker bridges. Joining mDNS on all of them exhausts
    # the kernel multicast membership limit and can omit the one LAN interface
    # the Dot actually needs, so bind discovery to the real controller IP.
    zeroconf = AsyncZeroconf(interfaces=[CONTROLLER_IP])
    await zeroconf.async_register_service(info, allow_name_change=True)
    log.info("mDNS advertising %s:%d", CONTROLLER_IP, DOT_PORT)
    return zeroconf, info


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bridge = EchoDotBridge()
    health_runner = await dot_http_server(bridge)
    transport_runner = await dot_server(bridge)
    zeroconf, service = await advertise()
    processor = asyncio.create_task(bridge.process_audio())
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, done.set)
    log.info(
        "Echo Dot bridge up mode=%s active_armed=%s dot_port=%d health_port=%s",
        bridge.mode, ACTIVE_ARM_FILE.exists(), DOT_PORT, os.getenv("HTTP_PORT", "8796"),
    )
    await done.wait()
    processor.cancel()
    await asyncio.gather(processor, return_exceptions=True)
    await zeroconf.async_unregister_service(service)
    await zeroconf.async_close()
    await transport_runner.cleanup()
    await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
