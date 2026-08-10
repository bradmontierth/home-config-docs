"""The snapserver that actually owns the whole-home zone players.

Music Assistant is the thing we *command*, but it is not the thing that knows.
The zone players (`ma_shower`, `ma_loft`, …) are snapclients on an external
snapserver, and MA's cached view of their volume goes stale and stays stale.

Measured 2026-08-08: `ma_shower` reported 0 for hours while the snapclient was
really at 20, and MA kept reporting 0 even as its own `volume_set` correctly
moved that client from 20 to 35. Writes propagate; the read-back does not. So
anything that reads a volume in order to act on it later — restore it after an
alarm, duck it under a wake word, add ten to it — has to read it from here.

Reading MA instead is not a small bug. It restores rooms to zero (silent
forever), and it makes ducking a no-op: `max(MUSIC_DUCK_MIN, 0 * 0.25)` is 5,
5 >= 0, so the duck decides there is nothing to do and returns.
"""

from __future__ import annotations

import asyncio
import json
import logging

from . import config

log = logging.getLogger("orchestrator.snapcast")


async def _rpc(method: str, params: dict | None = None) -> dict | None:
    """One newline-delimited JSON-RPC round trip, or None on any trouble."""
    try:
        reader, writer = await asyncio.open_connection(
            config.SNAPSERVER_HOST, config.SNAPSERVER_PORT, limit=1 << 20)
        try:
            body: dict = {"id": 1, "jsonrpc": "2.0", "method": method}
            if params is not None:
                body["params"] = params
            writer.write(json.dumps(body).encode() + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=6)
        finally:
            writer.close()
        return json.loads(line).get("result")
    except Exception as exc:  # noqa: BLE001
        log.warning("snapserver %s failed: %s", method, exc)
    return None


async def volume(client_id: str) -> int | None:
    """Current volume percent of a snapclient, or None if it cannot be read.

    None means "no answer", never "zero" — every caller has to be able to tell
    those apart, because acting on a false zero is how a room goes silent.
    """
    result = await _rpc("Server.GetStatus")
    if not result:
        return None
    try:
        for group in result["server"]["groups"]:
            for client in group["clients"]:
                if client["id"] == client_id:
                    return client["config"]["volume"]["percent"]
    except (KeyError, TypeError) as exc:
        log.warning("unexpected snapserver status shape: %s", exc)
    return None


async def set_volume(client_id: str, percent: int) -> bool:
    """Set a zone's volume AT THE SNAPSERVER rather than through MA.

    Not a style preference. Snapserver does not echo a change notification
    back to the connection that made the change, and MA's player model is fed
    by those notifications — so MA's own volume_set moves the room and leaves
    MA's model stale forever. Measured 2026-08-09: MA wrote 30, the snapserver
    read 30, and MA still reported 20 a minute later.

    That matters because MA brackets every announcement with a save/restore of
    that model. Anything set through MA is therefore undone by the next thing
    the room says, and in a room where every command is answered out loud,
    that is *every* change: three "turn it up"s in a row left the bath at
    exactly 20, with the reply politely saying "Okay, louder" each time.

    Written from here, MA sees the notification, its model is right, and its
    restore afterwards puts back the level we chose.
    """
    result = await _rpc("Client.SetVolume",
                        {"id": client_id, "volume": {"muted": False,
                                                     "percent": max(0, min(100, percent))}})
    return result is not None
