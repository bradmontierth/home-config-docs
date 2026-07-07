"""Streaming client for the smart 'ask' model (GPT-5.4 via OpenRouter).

OpenAI-compatible SSE. We stream token deltas so the ask handler can split the
reply at the ===MORE=== sentinel: speak the short part the instant it's complete
while the full part keeps arriving for the dashboard.

The key is read from a file (raw key, or a dotenv line OPENROUTER_KEY=... /
OPENROUTER_API_KEY=...), matching the voice-notes companion convention.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from . import config

log = logging.getLogger("orchestrator.openrouter")

_KEY_CACHE: str | None = None


def _read_key() -> str:
    global _KEY_CACHE
    if _KEY_CACHE:
        return _KEY_CACHE
    if not config.OPENROUTER_KEY_FILE:
        raise RuntimeError("OPENROUTER_API_KEY_FILE not configured")
    with open(config.OPENROUTER_KEY_FILE, encoding="utf-8") as fh:
        raw = fh.read()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" in line:
            name, _, val = line.partition("=")
            if name.strip() in ("OPENROUTER_KEY", "OPENROUTER_API_KEY"):
                val = val.strip().strip('"').strip("'")
                if val:
                    _KEY_CACHE = val
                    return val
    text = raw.strip()
    if text and "\n" not in text and "=" not in text:  # plain key file
        _KEY_CACHE = text
        return text
    raise RuntimeError("OpenRouter key file missing OPENROUTER_KEY")


async def stream_chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.3,
) -> AsyncIterator[str]:
    """Yield assistant content deltas from a streaming chat completion.

    Owns its own httpx client for its whole lifetime, so a partially-consumed
    generator can be handed to a background task and finished there. Always
    aclose() it when done."""
    body = {
        "model": model or config.OPENROUTER_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": config.OPENROUTER_MAX_TOKENS,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {_read_key()}",
        "Content-Type": "application/json",
        "X-Title": "Kitchen Voice Assistant",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST", f"{config.OPENROUTER_BASE_URL}/chat/completions",
            json=body, headers=headers,
        ) as r:
            if r.status_code >= 400:
                detail = (await r.aread()).decode("utf-8", "ignore")[:300]
                raise RuntimeError(f"OpenRouter {r.status_code}: {detail}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta
