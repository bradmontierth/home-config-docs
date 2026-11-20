"""Curated animal-video intent and the kitchen dashboard playback client."""
import re
import json
import os
from pathlib import Path
import httpx
from . import config

CATALOG_PATH = Path(os.getenv("ANIMAL_CATALOG_PATH", "/home/pi/dashboard_webapp/data/animals/catalog.json"))
_cache_signature = None
_cache_catalog = {}


def catalog():
    """Read an atomically replaced shared catalog; retain last good data on I/O failure."""
    global _cache_signature, _cache_catalog
    try:
        stat = CATALOG_PATH.stat()
        signature = (str(CATALOG_PATH), stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if signature != _cache_signature:
            data = json.loads(CATALOG_PATH.read_text())
            if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
                raise ValueError("Invalid animal catalog")
            _cache_catalog, _cache_signature = data, signature
    except (OSError, ValueError):
        pass
    return _cache_catalog


def names():
    return tuple(catalog())


def aliases():
    result = {}
    for key, clip in catalog().items():
        name = clip.get("name", key.replace("-", " ")).lower()
        values = {name, key.replace("-", " "), *clip.get("aliases", [])}
        if name not in {"sheep", "deer", "fish", "moose"}:
            values.add(name + ("es" if name.endswith(("s", "x", "ch", "sh")) else "s"))
        result.update({str(alias).lower(): key for alias in values})
    return result


def resolve(value):
    if not isinstance(value, str):
        return None
    if value in catalog():
        return value
    return aliases().get(clean(value))


REPLAY = {"again", "play it again", "do it again", "one more time", "show it again"}
STOP = {"stop", "stop it", "close", "close it", "go back", "back",
        "close the video", "stop the video", "close the animal", "stop the animal"}


def clean(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip().removesuffix(" please")


def fast_parse(text):
    text = clean(text)
    patterns = (
        r"what (?:sound|noise) (?:does|do) (?:a |an |the )?([a-z]+(?: [a-z]+){0,4}) make",
        r"what (?:does|do) (?:a |an |the )?([a-z]+(?: [a-z]+){0,4}) (?:say|sound like)",
        r"(?:play|make|show me) (?:a |an |the )?([a-z]+(?: [a-z]+){0,4}) (?:sound|sounds|noise)",
        r"(?:show me|let me hear) (?:a |an |the )?([a-z]+(?: [a-z]+){0,4})",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, text)
        if match and (animal := resolve(match[1])):
            return {"intent": "animal_sound", "animal": animal}
    return None


async def request(action, body=None):
    base = config.DASHBOARD_EVENT_URL.rsplit("/api/", 1)[0]
    async with httpx.AsyncClient(timeout=7) as client:
        if body is None:
            response = await client.get(f"{base}/api/animals/{action}")
        else:
            response = await client.post(f"{base}/api/animals/{action}", json=body)
        response.raise_for_status()
        return response.json()


async def contextual(text):
    text = clean(text)
    if text not in REPLAY | STOP:
        return None
    try:
        state = await request("state")
    except Exception:
        return None
    # Wake has already stopped the picture. Keep a short recent context so
    # "okay computer, again/stop" still refers to that animal after the wake.
    if not (state.get("current") or state.get("recent")):
        return None
    return {"intent": "animal_sound", "animal": "again" if text in REPLAY else "stop"}


async def handle(animal):
    animal = animal if animal in ("again", "stop") else resolve(animal)
    if not animal:
        return {"ok": False, "response": "I don't have that animal video yet."}
    try:
        result = await request("stop" if animal == "stop" else "play",
                               {} if animal == "stop" else {"animal": animal})
        return {"ok": True, "response": "", "skip_tts": True,
                "end_session": True, "animal": result.get("animal", animal)}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 503:
            response = "The kitchen display isn't connected right now."
        elif exc.response.status_code == 404:
            response = "I don't have that animal video yet."
        else:
            response = "Sorry, I couldn't play the animal video."
    except Exception:
        response = "Sorry, I couldn't reach the kitchen display."
    return {"ok": False, "response": response}
