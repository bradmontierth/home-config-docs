"""Ringing a timer through a whole-home audio zone instead of a satellite.

The master closet satellite is mic-only: its speaker is a small USB thing in a
closet, inaudible from the shower. Its alarms belong on the master bath
speakers, which are Music Assistant players behind the whole-home amp.

Everything below is shaped by what MA 2.6 actually does, measured on the loft
player 2026-08-07:

- `players/cmd/play_announcement` **blocks for the full duration** of the
  clip. A 60.0s file returned after 60.6s. That is convenient: we get control
  back at each chunk boundary without polling anything.
- `players/cmd/stop` **does not interrupt an announcement.** Issued 5s into a
  60s clip, the clip still ran to completion 55s later. So a single 40-second
  ring track would be a 40-second alarm that ignores "stop" — worse than what
  the satellites do today.
- A second announcement **queues behind** the first rather than preempting it,
  so it is not an interrupt either.
- `players/cmd/volume_set` **does** take effect mid-announcement, instantly,
  and holds. That is our stop: mute the player and the room goes quiet at
  once, whatever the announcement thinks it is still doing.
- MA does **not** restore the volume afterwards once we have set it by hand
  (the loft ended a test at 0 having started at 50), so restoring it is ours
  to do. Getting this wrong leaves a room silent forever.

Hence: the ring is a handful of pre-rendered chunks played back to back, a
dismiss mutes immediately and stops queueing more, and the volume is put back
when the last in-flight chunk returns. Chunking is not about the audible stop
— the mute already made that instant — it bounds how long a dismissed alarm
keeps the player's announce slot busy, so a doorbell ten seconds later is not
stuck behind a corpse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import wave

import httpx

from . import config, zones

log = logging.getLogger("orchestrator.zone_alarm")

_THEMES_DIR = os.path.join(os.path.dirname(__file__), "sounds", "themes")

# Mirrors the satellite's own ring: a short beep, then a gap, up to a total
# ring length after which we give up and let the phones escalation carry it.
GAP_S = float(os.getenv("ZONE_RING_GAP_S", "2.0"))
CYCLES = int(os.getenv("ZONE_RING_CYCLES", "14"))
CYCLES_PER_CHUNK = int(os.getenv("ZONE_RING_CYCLES_PER_CHUNK", "3"))
# MA reports an announcement finished before the sound has actually left the
# room: snapcast buffers, and the amp path adds its own tail padding. Arming
# the satellite's dismiss listener at MA's word was ~1.5s too early, and the
# mic caught the end of "Your timer is done" as "You're turn it off"
# (2026-08-08). Wait this out before handing the room back to the listener.
ARM_SETTLE_S = float(os.getenv("ZONE_RING_ARM_SETTLE_S", "1.5"))

# steam_whistle is deliberately absent: it is a recording of a person
# whistling and is unsettling coming out of the walls in a dark bathroom.
# A timer set with that theme rings in the zone as marimba.
FALLBACK_THEME = "marimba"


def _resolve_theme(theme: str | None) -> str:
    """The theme we will actually ring with. Names the cache file too, so a
    fallback is never cached under the name of the theme it stood in for."""
    if theme and os.path.exists(os.path.join(_THEMES_DIR, f"{theme}.wav")):
        return theme
    return FALLBACK_THEME


def build_chunk(theme: str | None) -> str:
    """Render (and cache) one chunk of ring for a theme: N beeps with gaps.

    Written into the announcement cache so the existing /audio/{name} route
    serves it — Music Assistant fetches the URL itself, so it has to be
    reachable from the MA container, which that route already is."""
    resolved = _resolve_theme(theme)
    name = f"ring-{resolved}-{CYCLES_PER_CHUNK}-{int(GAP_S * 10)}.wav"
    out = os.path.join(config.ANNOUNCE_CACHE_DIR, name)
    if os.path.exists(out):
        return name
    with wave.open(os.path.join(_THEMES_DIR, f"{resolved}.wav")) as src:
        params = src.getparams()
        beep = src.readframes(src.getnframes())
    silence = b"\0" * (params.sampwidth * params.nchannels
                       * int(GAP_S * params.framerate))
    os.makedirs(config.ANNOUNCE_CACHE_DIR, exist_ok=True)
    with wave.open(out, "wb") as dst:
        dst.setparams(params)
        for _ in range(CYCLES_PER_CHUNK):
            dst.writeframes(beep)
            dst.writeframes(silence)
    log.info("built ring chunk %s", name)
    return name


async def _ma(command: str, args: dict, timeout: float) -> None:
    """One Music Assistant command. Announcements block for their duration, so
    the timeout is the caller's business."""
    body = {"message_id": f"orch-{os.urandom(4).hex()}",
            "command": command, "args": args}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(config.MA_API_URL, json=body)
        r.raise_for_status()


async def _snap_volume(client_id: str) -> int | None:
    """The true resting volume, from the snapserver that actually owns these
    players.

    MA caches this and the cache goes stale: on 2026-08-08 ma_shower reported
    0 for hours while the snapclient was really at 20, and MA kept reporting 0
    even as its own volume_set correctly moved the client from 20 to 35.
    Restoring MA's number would have left the bathroom permanently silent, so
    MA is not trusted for this one value."""
    try:
        reader, writer = await asyncio.open_connection(
            config.SNAPSERVER_HOST, config.SNAPSERVER_PORT, limit=1 << 20)
        try:
            writer.write(json.dumps({"id": 1, "jsonrpc": "2.0",
                                     "method": "Server.GetStatus"}).encode() + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=6)
        finally:
            writer.close()
        for group in json.loads(line)["result"]["server"]["groups"]:
            for client in group["clients"]:
                if client["id"] == client_id:
                    return client["config"]["volume"]["percent"]
    except Exception as exc:  # noqa: BLE001
        log.warning("snapserver volume read for %s failed: %s", client_id, exc)
    return None


async def _resting_volume(route: dict) -> int:
    """What to put the room back to. Snapserver first, then MA, then the
    configured level — and never zero, whatever any of them claim. A room we
    hand back at zero is a room that never speaks again, which is a far worse
    failure than restoring a slightly wrong number."""
    fallback = route.get("volume") or 20
    if route.get("snap_client"):
        level = await _snap_volume(route["snap_client"])
        if level:
            return level
    level = await _player_volume(route.get("ma_player"))
    return level or fallback


async def _player_volume(player: str) -> int | None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(config.MA_API_URL, json={
                "message_id": f"orch-{os.urandom(4).hex()}",
                "command": "players/all", "args": {}})
            for p in r.json():
                if p.get("player_id") == player:
                    return p.get("volume_level")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read %s volume: %s", player, exc)
    return None


class ZoneRinger:
    """One ring in progress per satellite. Re-ringing the same room replaces
    the previous ring rather than layering a second one over it."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def ringing(self, sat: str) -> bool:
        task = self._tasks.get(sat)
        return bool(task and not task.done())

    async def start(self, sat: str, timer: dict, announce_url: str | None) -> None:
        await self.stop(sat)
        self._tasks[sat] = asyncio.create_task(
            self._ring(sat, timer, announce_url), name=f"zone-ring-{sat}")

    async def stop(self, sat: str) -> None:
        """Silence the room NOW, then let the ring unwind.

        The mute is what the person in the room experiences as "stop"; the
        cancelled task is what keeps the next chunk from being queued. Both
        matter, and the mute has to come first because the chunk already
        playing cannot be interrupted."""
        task = self._tasks.get(sat)
        if not task or task.done():
            return
        route = zones.route_for(sat) or {}
        player = route.get("ma_player")
        if player:
            try:
                await _ma("players/cmd/volume_set",
                          {"player_id": player, "volume_level": 0}, timeout=8)
            except Exception as exc:  # noqa: BLE001
                log.warning("mute of %s failed: %s", player, exc)
        task.cancel()

    async def _ring(self, sat: str, timer: dict, announce_url: str | None) -> None:
        route = zones.route_for(sat) or {}
        player = route.get("ma_player")
        if not player:
            log.warning("zone alarm for %s has no ma_player; not ringing", sat)
            return
        volume = route.get("alarm_volume", route.get("volume"))
        restore_to = await _resting_volume(route)
        chunk = build_chunk(timer.get("sound_theme"))
        base = config.PUBLIC_BASE.rstrip("/")
        chunks = max(1, -(-CYCLES // CYCLES_PER_CHUNK))
        log.info("zone alarm sat=%s player=%s timer=%s chunks=%d vol=%s",
                 sat, player, timer.get("id"), chunks, volume)
        try:
            # The amp sleeps in ~11 minutes; a cold one swallows the opening.
            # Same pre-wake the spoken replies use.
            from . import broadcast as broadcast_mod
            await broadcast_mod.amp_wake(route.get("rooms") or [], volume)

            if announce_url:
                # Blocks for the real length of the announcement, which is
                # exactly what makes the next line precise: we know when our
                # own voice stopped coming out of the walls.
                await self._play(player, f"{base}{announce_url}", volume, 60)
                await asyncio.sleep(ARM_SETTLE_S)
            await self._arm_dismiss(sat)
            for _ in range(chunks):
                await self._play(player, f"{base}/audio/{chunk}", volume, 90)
        except asyncio.CancelledError:
            log.info("zone alarm sat=%s dismissed", sat)
            raise
        except Exception as exc:  # noqa: BLE001 — a failed ring must not
            log.warning("zone alarm sat=%s failed: %s", sat, exc)
        finally:
            # Unconditional: MA does not put the volume back once we have
            # touched it, and a room left at 0 is a room that never speaks
            # again. Shielded so a cancel cannot skip it.
            await asyncio.shield(self._settle(player, restore_to))

    async def _arm_dismiss(self, sat: str) -> None:
        """Tell the satellite it may now believe its own ears. Until this, its
        dismiss listener is disarmed so the announcement cannot kill the ring
        it just introduced (the satellite also self-arms on a timeout, so a
        failure here costs a little self-dismiss risk, never a stuck alarm)."""
        host = zones.host_for(sat)
        if not host:
            return
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                await client.post(f"{host}/alarm/arm")
        except Exception as exc:  # noqa: BLE001
            log.warning("arming dismiss on %s failed: %s", sat, exc)

    async def _play(self, player: str, url: str,
                    volume: int | None, timeout: float) -> None:
        args = {"player_id": player, "url": url, "use_pre_announce": False}
        if volume is not None:
            args["volume_level"] = volume
        await _ma("players/cmd/play_announcement", args, timeout)

    async def _settle(self, player: str, level: int) -> None:
        """Put the volume back — but only once MA has actually stopped.

        Restoring immediately after a dismiss undoes our own mute: the chunk
        that was already playing cannot be interrupted, so the room came back
        up to full volume and kept beeping for the rest of it (measured on the
        loft, ~8 seconds of exactly the sound the user just asked to stop).
        The mute has to outlive the announcement, so wait for it, then restore.
        """
        deadline = asyncio.get_running_loop().time() + 90
        clear = 0
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    r = await client.post(config.MA_API_URL, json={
                        "message_id": f"orch-{os.urandom(4).hex()}",
                        "command": "players/all", "args": {}})
                    busy = next((p.get("announcement_in_progress")
                                 for p in r.json()
                                 if p.get("player_id") == player), False)
            except Exception as exc:  # noqa: BLE001
                log.warning("settle poll for %s failed: %s", player, exc)
                break
            # Two clear polls, not one: the gap between chunks reads as quiet,
            # and restoring there put the room back to full volume while the
            # next chunk was already on its way (observed 2026-08-08 -- the
            # restore landed 1.0s after a dismiss instead of 8.2s, and MA then
            # re-asserted a volume it had captured during our mute).
            clear = 0 if busy else clear + 1
            if clear >= 2:
                break
            await asyncio.sleep(1)
        await self._restore(player, level)

    async def _restore(self, player: str, level: int) -> None:
        try:
            await _ma("players/cmd/volume_set",
                      {"player_id": player, "volume_level": level}, timeout=8)
            log.info("restored %s volume to %s", player, level)
        except Exception as exc:  # noqa: BLE001
            log.error("FAILED to restore %s volume to %s: %s", player, level, exc)


RINGER = ZoneRinger()
