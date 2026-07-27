"""Speaker ID (backlog item 9): who said this command — brad or adrienne?

Embeds the command WAV via the resident titanet service on the GX10 and
scores it against enrolled centroids (tools/speaker_enroll.py →
SPEAKER_PROFILES_FILE, hot-reloaded on mtime). Identification is
verification-style: cosine to every centroid, accept the winner only when
it clears an absolute threshold AND a margin over the runner-up; anything
else is "unsure". Unsure ALWAYS falls back to today's behavior
(LIST_OWNER / find-phone ask-whose) — a misroute is worse than the status
quo, so this module never guesses.

Modes (config.SPEAKER_MODE):
  shadow — score every command turn, append to SPEAKER_SHADOW_LOG, route
           nothing. The live DET data that decides when to arm.
  active — person-dependent handlers use identify() to route.
  off    — never touch the GX10.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("orchestrator.speaker")

_profiles_cache: tuple[float, dict[str, list[float]]] | None = None  # (mtime, centroids)


def _profiles() -> dict[str, list[float]]:
    """name -> l2-normalized centroid. {} when no enrollment exists yet."""
    global _profiles_cache
    path = Path(config.SPEAKER_PROFILES_FILE)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        _profiles_cache = None
        return {}
    if _profiles_cache is None or _profiles_cache[0] != mtime:
        try:
            raw = json.loads(path.read_text())
            cents = {name: p["centroid"] for name, p in raw["profiles"].items()}
        except Exception as exc:  # noqa: BLE001 — a bad profiles file must not kill turns
            log.warning("speaker profiles unreadable (%s): %s", path, exc)
            return {}
        _profiles_cache = (mtime, cents)
        log.info("speaker profiles loaded: %s", ", ".join(sorted(cents)))
    return _profiles_cache[1]


def decide(scores: dict[str, float]) -> dict:
    """Threshold + margin decision over {name: cosine}. Pure, unit-tested."""
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
    margin = top - runner_up
    ok = top >= config.SPEAKER_THRESHOLD and margin >= config.SPEAKER_MARGIN
    return {
        "speaker": top_name if ok else "unsure",
        "top": top_name,
        "score": round(top, 4),
        "margin": round(margin, 4),
        "scores": {k: round(v, 4) for k, v in scores.items()},
    }


async def identify(wav: bytes) -> dict | None:
    """Score a command WAV. None when disabled, unenrolled, or the embed
    service is unreachable — callers treat None exactly like "unsure"."""
    if config.SPEAKER_MODE == "off":
        return None
    cents = _profiles()
    if len(cents) < 2:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                config.SPEAKER_EMBED_URL, content=wav,
                headers={"Content-Type": "audio/wav"})
        r.raise_for_status()
        emb = r.json()["embedding"]
    except Exception as exc:  # noqa: BLE001 — GX10 down must not break the turn
        log.warning("speaker embed failed: %s", exc)
        return None
    result = decide({n: sum(x * y for x, y in zip(emb, c)) for n, c in cents.items()})
    result["ms"] = r.json().get("ms")
    return result


async def shadow(wav: bytes, transcript: str, intent_name: str | None = None,
                 sat: str = "unknown", followup: bool = False) -> None:
    """Fire-and-forget per-turn scoring in shadow mode. Never raises."""
    if config.SPEAKER_MODE != "shadow":
        return
    try:
        result = await identify(wav)
        if result is None:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "sat": sat,
            "followup": followup,
            "transcript": transcript,
            "intent": intent_name,
            **result,
        }
        with open(config.SPEAKER_SHADOW_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info("speaker shadow: %s score=%s margin=%s %r",
                 record["speaker"], record["score"], record["margin"], transcript)
    except Exception as exc:  # noqa: BLE001 — shadow must be invisible to the turn
        log.warning("speaker shadow failed: %s", exc)
