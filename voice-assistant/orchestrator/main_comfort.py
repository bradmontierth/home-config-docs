"""Adaptive main-house comfort: Node-RED plans, HA submits, Node-RED acknowledges.

The GET endpoints only preview policy/read results. All device-affecting requests
go through HA mqtt.publish; the voice layer never sets the thermostat directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid

import httpx

from . import config
from .weather import _token

log = logging.getLogger("orchestrator.main_comfort")
URL = os.getenv("NODE_RED_COMFORT_URL", "http://127.0.0.1:1880").rstrip("/")


def affirmation(text: str) -> str | None:
    """Whole replies only: 'yes but don't do that' is never an approval."""
    t = " ".join(re.sub(r"[^a-z' ]", "", text.lower()).split())
    if t in {"yes", "yeah", "yep", "yup", "yes please", "sure", "okay", "ok",
             "go ahead", "do it", "please do", "sounds good", "confirm", "confirmed"}:
        return "yes"
    if t in {"no", "nope", "no thanks", "no thank you", "cancel", "stop",
             "never mind", "nevermind", "leave it", "don't", "dont"}:
        return "no"
    return None


def describe(p: dict, question: bool = False) -> str:
    if p["hvac"] == "heat":
        text = f"set the heating target to {p['heatTarget']:g} degrees"
    elif p["kind"] == "hot":
        text = f"cool toward {p['stopAt']:g} degrees"
    else:
        text = f"let it warm toward {p['stopAt']:g} degrees"
    if question:
        return f"I can {text} for one hour. That's a larger adjustment than usual. Go ahead?"
    return f"I'll {text} for one hour."


async def request(kind: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{URL}/main-comfort/preview",
                                 params={"kind": kind, "request_id": uuid.uuid4().hex})
            r.raise_for_status()
            plan = r.json()
        if not plan.get("ok"):
            return {"ok": False, "response": plan.get("response") or "I couldn't check the thermostat."}
        proposal = plan["proposal"]
        if plan["requires_confirmation"]:
            return {"ok": True, "response": describe(proposal, question=True),
                    "awaiting_slot": True, "_comfort_proposal": proposal}
        return await apply(proposal, confirmed=False)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("comfort preview failed: %s", exc)
        return {"ok": False, "response": "I couldn't check the thermostat. Please try again."}


async def apply(proposal: dict, confirmed: bool) -> dict:
    """Submit the exact frozen proposal; never recompute a larger request on yes."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{config.HA_URL}/api/services/mqtt/publish",
                                  headers={"Authorization": f"Bearer {_token()}"},
                                  json={"topic": "voice/main_comfort/apply", "qos": 1,
                                        "retain": False, "payload": json.dumps({
                                            "proposal": proposal, "confirmed": confirmed})})
            r.raise_for_status()
            until = time.monotonic() + 4
            while time.monotonic() < until:
                r = await client.get(f"{URL}/main-comfort/result/{proposal['requestId']}")
                r.raise_for_status()
                result = r.json()
                if not result.get("pending"):
                    if result.get("ok"):
                        return {"ok": True, "response": describe(result["proposal"]),
                                "comfort_expires_at": result["expiresAt"]}
                    return {"ok": False, "response": result.get("response") or "I couldn't apply that temperature."}
                await asyncio.sleep(0.15)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("comfort apply failed: %s", exc)
    # Delivery might have succeeded even if the acknowledgement was lost.
    return {"ok": False, "response": "I couldn't confirm whether that temperature hold was applied."}
