"""Kitchen-display camera views: "show me Simon" puts a kid's camera on the
touchscreen with its audio on the big speakers.

Everything downstream of this module already existed for the dashboard's touch
buttons and the doorbell pop. The display Pi runs a VLC helper that owns the
fullscreen window, the RTSP source (go2rtc on the Frigate Jetson), and the
audio hand-off to the kitchen squeezelite player. We drive that helper rather
than reinventing any of it, so a spoken "show me Claire" and a finger on the
dashboard button land in exactly the same state — including the on-screen Back
button, which closes a view we opened.

This is the only place the orchestrator talks to the display Pi directly. Every
other view is a dashboard card pushed over /api/assistant/event; a camera is a
fullscreen VLC window sitting *over* the kiosk, so it cannot ride that fan-out.

Two calls, not one: `/open` starts video only. The touch flow leaves audio to a
second tap on the overlay and the helper kept that split, so the voice path
makes the second call itself — deliberately late, because the camera audio and
our spoken confirmation come out of the same speakers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from . import config

log = logging.getLogger("orchestrator.camera")

# Spoken name -> helper stream key. The helper also serves "doorbell", which
# Node-RED already pops on a detection; it is deliberately not voice-reachable
# here because "show me the doorbell" is a different feature (it wants the
# event, not a live view).
STREAMS = ("simon", "claire")

SPOKEN = {"simon": "Simon", "claire": "Claire"}

# Whole words only, matched anywhere in the phrase, so "the camera in Simon's
# room" resolves as readily as "Simon". Possessives are stripped before lookup.
# "clare" is here because that is what the ASR returns for Claire often enough
# to matter; a wrong-kid camera is a worse failure than a missed phrase.
_NAMES = {
    "simon": "simon",
    "simons": "simon",
    "claire": "claire",
    "claires": "claire",
    "clare": "claire",
    "clares": "claire",
}

_PUNCT = re.compile(r"[^a-z0-9' ]+")

# Serialises open/close against the delayed audio start below.
_lock = asyncio.Lock()
_audio_task: asyncio.Task[Any] | None = None


def _clean(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", (text or "").lower()).split())


def resolve(text: str | None) -> str | None:
    """Which camera a spoken phrase names, or None.

    Forgiving about shape ("Simon", "Simon's room", "the camera in Simon's
    room") but strict about identity: only a real first name resolves. Nothing
    maps "the baby" to a kid — Simon's camera is the one named BabyCAMR
    upstream while Claire is the one with the nap-monitor pipeline, so that
    word points at both children and belongs to neither.
    """
    for word in _clean(text or "").split():
        key = _NAMES.get(word.replace("'", ""))
        if key:
            return key
    return None


async def _request(method: str, path: str) -> dict[str, Any]:
    url = f"{config.CAMERA_HELPER_URL.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=config.CAMERA_HTTP_TIMEOUT) as client:
        response = await client.request(method, url)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {}


async def status() -> dict[str, Any]:
    """What the display is showing right now, or {} if the helper is unreachable.

    Queried rather than remembered: the on-screen Back button closes the view
    through the helper without telling us, so a local flag would claim a camera
    is up long after it went away.
    """
    try:
        return await _request("GET", "/status")
    except Exception as exc:  # noqa: BLE001 — display Pi asleep/rebooting
        log.warning("camera status failed: %s", exc)
        return {}


async def is_open() -> bool:
    return bool((await status()).get("running"))


def _cancel_audio() -> None:
    global _audio_task
    if _audio_task is not None and not _audio_task.done():
        _audio_task.cancel()
    _audio_task = None


async def _play_audio_later(stream: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        await _request("POST", f"/audio/play/{stream}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — video is still up; audio is the extra
        log.warning("camera audio %s failed: %s", stream, exc)


async def show(stream: str, delay: float | None = None) -> None:
    """Open a camera fullscreen and bring its audio up behind the reply.

    The audio start is a scheduled task, not an await, for two reasons: the
    caller is still holding the turn open and must not block on it, and the
    delay exists precisely so the confirmation gets the speakers first. A view
    closed or switched inside the delay window cancels it — otherwise audio
    arrives for a camera that is no longer on screen.
    """
    global _audio_task
    if delay is None:
        delay = config.CAMERA_AUDIO_DELAY_S
    async with _lock:
        _cancel_audio()
        await _request("POST", f"/open/{stream}")
        _audio_task = asyncio.create_task(_play_audio_later(stream, delay))


async def close() -> None:
    """Close whatever is on screen. The helper stops the audio before killing
    VLC, so this is one call, not two."""
    async with _lock:
        _cancel_audio()
        await _request("POST", "/close")


async def handle(parsed: dict) -> dict[str, Any]:
    """show_camera: put a named camera up, or say what we could not resolve."""
    stream = resolve(parsed.get("camera_target"))
    if stream is None:
        return {"response": "I can show you Simon or Claire.", "ok": False}
    try:
        await show(stream)
    except Exception as exc:  # noqa: BLE001 — display Pi down / helper stopped
        log.warning("camera open %s failed: %s", stream, exc)
        return {"response": "Sorry, I couldn't reach the kitchen display.",
                "ok": False}
    return {"response": f"Showing {SPOKEN[stream]}.", "ok": True,
            "camera": stream}


async def handle_close() -> dict[str, Any]:
    """close_camera: dismiss the view. Honest when there was nothing to close —
    the phrase is cheap to say and the display state is not ours to assume."""
    if not await is_open():
        return {"response": "Nothing's on the display right now.", "ok": True}
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 — display Pi down / helper stopped
        log.warning("camera close failed: %s", exc)
        return {"response": "Sorry, I couldn't reach the kitchen display.",
                "ok": False}
    return {"response": "Okay.", "ok": True}
