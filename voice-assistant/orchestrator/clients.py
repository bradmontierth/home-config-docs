"""Thin async clients for the three downstream services.

Contracts lifted verbatim from the working doorbell shim
(doorbell_tts/shim/app.py) so behaviour matches production, including the
GX10 "Hypothesis(...)" silent-audio quirk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from . import config, timing

log = logging.getLogger("orchestrator.clients")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _clean_transcript(payload: dict) -> str:
    text = str(payload.get("transcript_text") or "").strip()
    if text.startswith("Hypothesis("):
        return ""
    return text


async def _asr_request(url: str, wav_bytes: bytes,
                       client_name: str | None) -> tuple[str, str | None, int]:
    """One ASR call. Returns (text, model label from the response or None,
    wall ms). Raises on HTTP/connection failure."""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            url,
            params={"chunk_seconds": 300, "context_seconds": 2,
                    "client": client_name or config.ASR_CLIENT},
            content=wav_bytes,
            headers={"Content-Type": "audio/wav"},
        )
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(f"asr {url} refused: {payload.get('error')}")
    model = payload.get("model") if isinstance(payload, dict) else None
    return (_clean_transcript(payload if isinstance(payload, dict) else {}),
            str(model) if model else None,
            round((time.perf_counter() - t0) * 1000))


async def _asr_request_retry_429(url: str, wav_bytes: bytes,
                                 client_name: str | None) -> tuple[str, str | None, int]:
    """_asr_request with one retry on 429. The Parakeet API rejects (not
    queues) at two in-flight interactive requests, and hedging means the
    fallback leg can land on top of a partial still decoding."""
    try:
        return await _asr_request(url, wav_bytes, client_name)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 429:
            raise
    await asyncio.sleep(config.FINAL_ASR_RETRY_429_S)
    return await _asr_request(url, wav_bytes, client_name)


async def transcribe(wav_bytes: bytes, client_name: str | None = None) -> str:
    """Parakeet batch transcription. Body is raw WAV; returns transcript text
    ("" on silence — GX10 returns a Hypothesis(...) repr for silent audio).
    `client_name` overrides the bias profile (e.g. the stop-heavy alarm
    profile for ring-window listening); default is the kitchen profile."""
    with timing.stage("asr"):
        text, _, _ = await _asr_request(config.ASR_URL, wav_bytes, client_name)
    return text


# --- hedged final decode ---------------------------------------------------

@dataclass
class AsrResult:
    """Outcome of transcribe_final. `fallback_reason` is None when the primary
    answer was used; otherwise why Parakeet's was taken instead."""
    text: str
    model: str | None = None
    primary_ms: int | None = None
    fallback_ms: int | None = None
    fallback_reason: str | None = None

    def turn_fields(self) -> dict:
        """Columns for the turn row. Empty when the hedge is off, so rows from
        before/without the feature stay NULL rather than reading 'parakeet'."""
        if self.model is None:
            return {}
        return {"asr_model": self.model,
                "asr_primary_ms": self.primary_ms,
                "asr_fallback_ms": self.fallback_ms,
                "asr_fallback_reason": self.fallback_reason or "none"}


_WORD_RE = re.compile(r"[a-z0-9']+")
_breaker_until = 0.0     # monotonic time until which the primary is skipped


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _repeats(text: str, max_repeat: int) -> bool:
    """True when any word occurs more than `max_repeat` times consecutively."""
    run = 0
    prev = None
    for w in _words(text):
        run = run + 1 if w == prev else 1
        prev = w
        if run > max_repeat:
            return True
    return False


def _sanity_reason(primary_text: str, fallback_text: str | None) -> str | None:
    """Why an in-time primary result should be discarded, or None to keep it."""
    if _repeats(primary_text, config.FINAL_ASR_MAX_REPEAT):
        return "repeat"
    if fallback_text is not None:
        n_p, n_f = len(_words(primary_text)), len(_words(fallback_text))
        if (n_f > 0 and n_p > 3 * n_f
                and n_p >= config.FINAL_ASR_LENGTH_MIN_WORDS):
            return "length"
    return None


def _swallow(task: asyncio.Task) -> None:
    """Retrieve a background task's exception so asyncio doesn't log it."""
    if not task.cancelled():
        task.exception()


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return "error"


async def transcribe_final(wav_bytes: bytes,
                           client_name: str | None = None) -> AsrResult:
    """The /command/audio decode: FINAL_ASR_URL hedged with Parakeet.

    Both requests go out together. The primary's answer is used if it lands
    inside FINAL_ASR_DEADLINE_MS, or before Parakeet's when that is later
    (never past FINAL_ASR_HARD_CAP_MS), and passes the loop/length checks;
    otherwise Parakeet's, which is usually already in. The fallback is about
    availability, not speed -- the cases it covers are a container that is
    down, wedged or looping, and those need a fallback already in flight, not
    one started after the deadline. Worst case stays flat at about the
    deadline plus Parakeet's remaining time. A primary error or hard-cap miss
    trips a FINAL_ASR_BREAKER_S Parakeet-only window; a lost race and the
    quality rejections don't.

    With FINAL_ASR_URL unset this is exactly transcribe()."""
    global _breaker_until
    if not config.FINAL_ASR_URL:
        return AsrResult(await transcribe(wav_bytes, client_name))
    with timing.stage("asr"):
        if time.monotonic() < _breaker_until:
            text, model, ms = await _asr_request(config.ASR_URL, wav_bytes,
                                                 client_name)
            return AsrResult(text, model or config.ASR_FALLBACK_LABEL,
                             None, ms, "breaker")
        primary = asyncio.create_task(
            _asr_request(config.FINAL_ASR_URL, wav_bytes, client_name))
        fallback = asyncio.create_task(
            _asr_request_retry_429(config.ASR_URL, wav_bytes, client_name))
        p_text = p_model = None
        p_ms = None
        reason: str | None = None
        t0 = time.monotonic()
        await asyncio.wait({primary}, timeout=config.FINAL_ASR_DEADLINE_MS / 1000)
        if not primary.done():
            # Past the deadline. The primary is still accepted if it beats
            # Parakeet to the line (the GX10 slows ~3x for BOTH models while
            # the local LLM is generating, so an absolute deadline alone would
            # fall back on every busy turn); the hard cap is where a
            # still-missing primary is treated as wedged.
            remaining = config.FINAL_ASR_HARD_CAP_MS / 1000 - (time.monotonic() - t0)
            await asyncio.wait({primary, fallback}, timeout=max(remaining, 0),
                               return_when=asyncio.FIRST_COMPLETED)
        if primary.done():
            exc = primary.exception()
            if exc is None:
                p_text, p_model, p_ms = primary.result()
            else:
                reason = _failure_reason(exc)
        elif fallback.done():
            reason = "timeout"      # lost the race: no breaker, it cost nothing
            primary.cancel()
        else:
            reason = "hard_cap"     # nobody home for FINAL_ASR_HARD_CAP_MS
            primary.cancel()
        if reason is None:
            f_text = None
            if fallback.done() and not fallback.cancelled() \
                    and fallback.exception() is None:
                f_text = fallback.result()[0]
            reason = _sanity_reason(p_text or "", f_text)
        elif reason != "timeout":
            _breaker_until = time.monotonic() + config.FINAL_ASR_BREAKER_S
            log.warning("final asr primary %s; parakeet-only for %.0fs",
                        reason, config.FINAL_ASR_BREAKER_S)
        if reason is None and not p_text:
            # Primary heard nothing. Parakeet is already in flight; waiting
            # for it costs at most its remaining ~100 ms and turns a would-be
            # "I didn't catch that" into whatever Parakeet heard.
            try:
                f_text, f_model, f_ms = await fallback
            except Exception:  # noqa: BLE001
                f_text = ""
            if f_text:
                return AsrResult(f_text, f_model or config.ASR_FALLBACK_LABEL,
                                 p_ms, f_ms, "empty")
        if reason is None:
            f_ms = None
            if fallback.done() and not fallback.cancelled() \
                    and fallback.exception() is None:
                f_ms = fallback.result()[2]
            if not fallback.done():
                fallback.add_done_callback(_swallow)
            return AsrResult(p_text or "", p_model or "primary", p_ms, f_ms, None)
        try:
            f_text, f_model, f_ms = await fallback
        except Exception as exc:  # noqa: BLE001
            if p_text is None:
                raise            # both failed: same outcome as today
            log.warning("final asr: parakeet failed (%s) after primary %s; "
                        "using primary anyway", exc, reason)
            return AsrResult(p_text, p_model or "primary", p_ms, None,
                             f"{reason}+fallback_error")
        if reason in ("repeat", "length"):
            log.info("final asr %s: primary=%r parakeet=%r", reason, p_text, f_text)
        return AsrResult(f_text, f_model or config.ASR_FALLBACK_LABEL,
                         p_ms, f_ms, reason)


async def parse_intent_raw(messages: list[dict]) -> str:
    """Non-streaming chat completion, thinking disabled, temperature 0.
    Returns the assistant content with any stray <think> block stripped."""
    body = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": config.LLM_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with timing.stage("classify"):
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
    with timing.stage("tts"):
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
