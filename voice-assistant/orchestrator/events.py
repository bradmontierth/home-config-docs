"""Fan-out of assistant events to the kitchen dashboard and alarm dispatch to
the satellite. Both are best-effort: a dead dashboard or satellite must never
break the timer engine."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

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
    """Tell the satellite to start the themed alarm for an expired timer.
    Best-effort; satellite service is not built yet in this slice."""
    if not config.SATELLITE_ALARM_URL:
        return
    body = {
        "timer_id": timer["id"],
        "label": timer.get("label"),
        "sound_theme": timer.get("sound_theme"),
        "announce_url": announce_url,
    }
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            await client.post(config.SATELLITE_ALARM_URL, json=body)
    except Exception as exc:  # noqa: BLE001
        log.info("satellite alarm dispatch failed (expected until satellite built): %s", exc)
