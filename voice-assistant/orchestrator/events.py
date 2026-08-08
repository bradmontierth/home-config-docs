"""Fan-out of assistant events to the kitchen dashboard and alarm dispatch to
the satellites. Both are best-effort: a dead dashboard or satellite must never
break the timer engine.

Alarm dispatch is addressed per satellite via the zones table, so a timer
rings in the room it was set in and a stop reaches only that room."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config, zones

log = logging.getLogger("orchestrator.events")


async def emit(event_type: str, **fields: Any) -> None:
    """POST one assistant event to the dashboard, which re-broadcasts it over
    /api/live to the kiosk(s) (jukebox pattern). Never raises."""
    payload = {"type": event_type, **fields}
    headers = {}
    if config.DASHBOARD_EVENT_TOKEN:
        headers["Authorization"] = f"Bearer {config.DASHBOARD_EVENT_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            await client.post(config.DASHBOARD_EVENT_URL, json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001
        log.warning("dashboard emit %s failed: %s", event_type, exc)


async def alarm(timer: dict[str, Any], announce_url: str | None) -> None:
    """Ring an expired timer in the room it was set in.

    The satellite is addressed from the timer's own `sat`, not from a single
    global env var — otherwise every alarm in the house rings on whichever box
    that variable happens to name, which is what a bathroom timer ringing in
    the kitchen looked like."""
    host = zones.host_for(timer.get("sat"))
    if not host:
        return
    body = {
        "timer_id": timer["id"],
        "label": timer.get("label"),
        "sound_theme": timer.get("sound_theme"),
        "announce_url": announce_url,
    }
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            await client.post(f"{host}/alarm", json=body)
    except Exception as exc:  # noqa: BLE001
        log.info("alarm dispatch to %s failed: %s", host, exc)


async def alarm_stop(sat: str | None = None) -> None:
    """Silence a ringing satellite alarm. Fired when a ringing timer is
    cancelled/dismissed through the orchestrator, so every client (kiosk,
    phone, curl) stops the sound and not just the card. Safe to blind-fire:
    the satellite clears its dismiss flag before each new alarm.

    Scoped to one satellite. A stop is always about a sound somebody can hear,
    so it goes to that room and nowhere else; the caller decides which room by
    passing the timer's `sat`.

    Callers on the satellite's own end-of-ring POST path must await this
    BEFORE responding — the satellite starts its next queued alarm only after
    that response, which orders this dismiss ahead of the next ring."""
    host = zones.host_for(sat)
    if not host:
        return
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            await client.post(f"{host}/alarm/dismiss")
    except Exception as exc:  # noqa: BLE001
        log.info("alarm stop to %s failed: %s", host, exc)


async def satellite_chime(path: str) -> None:
    """Play a short sound on the kitchen satellite by URL — it fetches the WAV
    back from us and plays it through its own volume/ducking path, the same
    route the ask filler uses.

    Used for the reminder pop, where the chime is the whole audio cue: a card
    on the screen is glanceable and stays in the kitchen, whereas SPEAKING the
    reminder is the part that carries to everyone else in the house. Best
    effort — a silent pop still shows."""
    if not config.SATELLITE_SPEAK_URL or not path:
        return
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            await client.post(config.SATELLITE_SPEAK_URL, json={"url": path})
    except Exception as exc:  # noqa: BLE001
        log.warning("satellite chime failed: %s", exc)


async def phone_alert(title: str, body: str, **data: str) -> None:
    """Fan a push notification to every registered household phone via the
    Voice Notes companion. Best-effort."""
    if not config.COMPANION_ALERT_URL:
        return
    payload = {"title": title, "body": body, "data": data}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(config.COMPANION_ALERT_URL, json=payload)
            log.info("phone alert %r -> %s %s", title, resp.status_code, resp.text[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("phone alert failed: %s", exc)
