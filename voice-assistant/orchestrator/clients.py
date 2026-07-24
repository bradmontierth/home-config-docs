"""Thin async clients for the three downstream services.

Contracts lifted verbatim from the working doorbell shim
(doorbell_tts/shim/app.py) so behaviour matches production, including the
GX10 "Hypothesis(...)" silent-audio quirk.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from . import config

log = logging.getLogger("orchestrator.clients")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


async def transcribe(wav_bytes: bytes, client_name: str | None = None) -> str:
    """Parakeet batch transcription. Body is raw WAV; returns transcript text
    ("" on silence — GX10 returns a Hypothesis(...) repr for silent audio).
    `client_name` overrides the bias profile (e.g. the stop-heavy alarm
    profile for ring-window listening); default is the kitchen profile."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            config.ASR_URL,
            params={"chunk_seconds": 300, "context_seconds": 2,
                    "client": client_name or config.ASR_CLIENT},
            content=wav_bytes,
            headers={"Content-Type": "audio/wav"},
        )
    r.raise_for_status()
    text = str(r.json().get("transcript_text") or "").strip()
    if text.startswith("Hypothesis("):
        return ""
    return text


async def parse_intent_raw(messages: list[dict]) -> str:
    """Non-streaming chat completion, thinking disabled, temperature 0.
    Returns the assistant content with any stray <think> block stripped."""
    body = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 200,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(config.LLM_URL, json=body)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return _THINK_RE.sub("", content).strip()


async def synthesize(text: str, voice: str | None = None) -> bytes:
    """TTS router → WAV bytes."""
    payload = {
        "input": text,
        "voice": voice or config.TTS_VOICE,
        "response_format": "wav",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(config.TTS_URL, json=payload)
    r.raise_for_status()
    return r.content


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM reply. Tolerant of code fences
    or leading prose even though we ask for bare JSON at temperature 0."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in LLM reply: {text[:200]!r}")
    return json.loads(text[start : end + 1])
