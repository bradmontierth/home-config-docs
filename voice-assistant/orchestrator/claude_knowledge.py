"""Streaming client for the Claude knowledge service (ask backend, 2026-11-20).

The service (voice-assistant/knowledge-service/server.py) runs on the Beelink
host and answers with the `claude` CLI on the household subscription; this
container has neither the CLI nor a login. Same shape as openrouter.stream_chat:
an async generator of answer-text deltas with a `stats` out-param, so ask.py
can split at the ===MORE=== sentinel and hand the rest to a background task.

Raises before the first delta when the service is down or every model failed,
which is what lets ask.py fall back to another backend without the user
hearing half an answer twice.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from . import config

log = logging.getLogger("orchestrator.claude_knowledge")


async def stream_chat(system: str, prompt: str, *,
                      stats: dict | None = None) -> AsyncIterator[str]:
    """Yield answer deltas. `system` must be STATIC (the service keeps a
    process pre-warmed with it); per-request context belongs in `prompt`."""
    # No bytes arrive until the first answer text, and a searched answer can
    # take ~25 s (twice that if the service falls back to its second model).
    timeout = httpx.Timeout(connect=3.0, read=config.CLAUDE_KNOWLEDGE_READ_TIMEOUT_S,
                            write=10.0, pool=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{config.CLAUDE_KNOWLEDGE_URL}/ask",
                                 json={"system": system, "prompt": prompt}) as r:
            if r.status_code >= 400:
                detail = (await r.aread()).decode("utf-8", "ignore")[:300]
                raise RuntimeError(f"knowledge service {r.status_code}: {detail}")
            done = False
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("t")
                if kind == "delta":
                    if ev.get("text"):
                        yield ev["text"]
                elif kind == "meta":
                    log.info("claude answer: model=%s effort=%s warm=%s fallback=%s",
                             ev.get("model"), ev.get("effort"), ev.get("warm"),
                             ev.get("fallback"))
                elif kind == "done":
                    done = True
                    if stats is not None:
                        stats.update({
                            "generation_id": None,
                            "backend": "claude",
                            "model": ev.get("model"),
                            "searches": ev.get("searches"),
                            "tokens": ev.get("tokens"),
                            # Subscription-billed: no per-answer charge. The
                            # API-equivalent figure is kept for comparison only.
                            "cost": None,
                            "notional_cost": ev.get("notional_cost_usd"),
                        })
                    log.info("claude answer done: model=%s searches=%s first_text_ms=%s "
                             "ms=%s warm=%s fallback=%s notional=$%.4f",
                             ev.get("model"), ev.get("searches"), ev.get("first_text_ms"),
                             ev.get("ms"), ev.get("warm"), ev.get("fallback"),
                             ev.get("notional_cost_usd") or 0.0)
                elif kind == "error":
                    raise RuntimeError(f"knowledge service: {ev.get('error')}")
            if not done:
                raise RuntimeError("knowledge service closed the stream early")
