"""Turn an agent reply into listenable audio.

Replies are markdown written for a screen. `speakable_paragraphs` flattens one
into plain paragraphs a TTS voice can read (no tables, fences, paths or
symbols), and `render_speech` sends those to the TTS router one paragraph at a
time and joins the result into a single seekable MP3.
"""
import asyncio
import hashlib
import io
import json
import os
import re
import wave
from pathlib import Path

import httpx

TTS_URL = os.environ.get("HOME_AGENT_TTS_URL", "http://192.168.10.217:8891/v1/audio/speech")
TTS_VOICE = os.environ.get("HOME_AGENT_TTS_VOICE", "fast:doorbell")
FFMPEG_BIN = os.environ.get("HOME_AGENT_FFMPEG_BIN", "ffmpeg")
# Tables and code cannot be read aloud as written, so a local LLM turns just
# those blocks into sentences. Prose never goes through a model: it stays word
# for word, cleaned mechanically. Empty URL disables the LLM step.
LLM_URL = os.environ.get("HOME_AGENT_SPEECH_LLM_URL", "http://192.168.10.187:8095/v1/chat/completions")
LLM_MODEL = os.environ.get("HOME_AGENT_SPEECH_LLM_MODEL", "qwen3-next")
LLM_TABLE_SYSTEM = (
    "You convert one table from a technical message into sentences that a text-to-speech voice will read "
    "aloud. State every row and every cell: every number, every name, every parenthetical, in the original "
    "order, using the column headers as the labels and the headers' own words for units. Write numbers as "
    "digits exactly as given. Do not add units or facts that are not in the table, do not summarize, do not "
    "skip rows, do not comment. Output only the sentences."
)
LLM_CODE_SYSTEM = (
    "A technical message contains this code block, which cannot be read aloud. In one short sentence, say "
    "what the code or command does, starting with 'A code block that' or 'A command that'. Do not read the "
    "code itself. Output only that sentence."
)
PAUSE_S = 0.45  # silence between paragraphs
MAX_PARAGRAPH_CHARS = 900

SYMBOLS = {
    "→": " to ", "←": " from ", "⇒": " so ", "≈": " about ", "×": " times ", "≥": " at least ",
    "≤": " at most ", "—": ", ", "–": " to ", "&": " and ", "…": ".", "•": "", "✅": "", "❌": "",
    "`": "", "*": "", "#": "",
}


def latest_reply(log_path: Path, fallback_path: Path | None = None) -> str:
    """Final answer of the most recent finished turn in a session log."""
    reply = ""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result" and str(event.get("result") or "").strip():
            reply = str(event["result"])
        elif event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and str(item.get("text") or "").strip():
                reply = str(item["text"])
    if not reply and fallback_path and fallback_path.exists():
        reply = fallback_path.read_text(encoding="utf-8", errors="replace")
    return reply.strip()


def _speak_table_row(line: str) -> str:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return ", ".join(cell for cell in cells if cell) + "."


def _clean_inline(text: str) -> str:
    text = re.sub(r"!?\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links -> their text
    text = re.sub(r"https?://[^\s)]+?(?=[.,;:!?)]*(?:\s|$))", "a link", text)
    text = re.sub(r"(\.\w{1,5}):\d+\b", r"\1", text)  # file.py:51 -> file.py
    # long paths -> last segment, without a :line suffix
    text = re.sub(r"(?<![\w.])~?(?:/[\w.\-@+]+){2,}/?(?::\d+)?", lambda m: m.group(0).rstrip("/").split("/")[-1].split(":")[0], text)
    text = re.sub(r"~(?=\d)", "about ", text)
    for symbol, spoken in SYMBOLS.items():
        text = text.replace(symbol, spoken)
    text = re.sub(r"\b(\d+)-(\d+)\b", r"\1 to \2", text)
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def speakable_paragraphs(markdown: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            if not in_fence:
                flush()
                paragraphs.append("There is a code block here, which I will skip.")
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("AWAITING_PHONE_APPROVAL:"):
            flush()
            paragraphs.append("I am waiting for your approval: " + stripped.split(":", 1)[1].strip().rstrip(".") + ".")
            continue
        if re.fullmatch(r"\|?[\s:|\-]+\|?", stripped) and "-" in stripped:
            continue  # table separator row
        if stripped.startswith("|"):
            flush()
            paragraphs.append(_speak_table_row(stripped))
            continue
        if stripped.startswith("#"):
            flush()
            paragraphs.append(stripped.lstrip("#").strip().rstrip(".:") + ".")
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush()
            item = re.sub(r"^[-*+]\s+", "", stripped)
            current.append(item if item.endswith((".", "?", "!", ":")) else item + ".")
            flush()
            continue
        if re.fullmatch(r"[-*_]{3,}", stripped):
            flush()
            continue
        current.append(stripped)
    flush()

    spoken: list[str] = []
    for paragraph in paragraphs:
        text = _clean_inline(paragraph)
        if not re.search(r"[A-Za-z0-9]", text):
            continue
        while len(text) > MAX_PARAGRAPH_CHARS:
            cut = max(text.rfind(". ", 0, MAX_PARAGRAPH_CHARS), text.rfind("? ", 0, MAX_PARAGRAPH_CHARS))
            cut = cut + 1 if cut > 200 else MAX_PARAGRAPH_CHARS
            spoken.append(text[:cut].strip())
            text = text[cut:].strip()
        spoken.append(text)
    # Bullets become one-liners; glue short neighbours so the voice doesn't
    # restart its intonation (and pause) on every fragment.
    merged: list[str] = []
    for text in spoken:
        if merged and len(merged[-1]) + len(text) < 400:
            merged[-1] = f"{merged[-1]} {text}"
        else:
            merged.append(text)
    return merged


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", text)]


def _segments(markdown: str) -> list[tuple[str, str]]:
    """Ordered ('prose' | 'table' | 'code', text) pieces of a reply."""
    segments: list[tuple[str, list[str]]] = []

    def add(kind: str, line: str) -> None:
        if segments and segments[-1][0] == kind:
            segments[-1][1].append(line)
        else:
            segments.append((kind, [line]))

    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            add("code", line)
            in_fence = not in_fence
        elif in_fence:
            add("code", line)
        elif line.strip().startswith("|"):
            add("table", line)
        else:
            add("prose", line)
    return [(kind, "\n".join(lines)) for kind, lines in segments]


async def _ask_llm(client: httpx.AsyncClient, system: str, user: str) -> str:
    response = await client.post(
        LLM_URL,
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": 3000,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    response.raise_for_status()
    text = str(response.json()["choices"][0]["message"]["content"] or "")
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


async def _speak_table(client: httpx.AsyncClient, table: str, context: str) -> str:
    """Sentences for a table, accepted only if every number in it survived."""
    wanted = _numbers(table)
    for _ in range(2):
        try:
            spoken = await _ask_llm(
                client, LLM_TABLE_SYSTEM, f"Text just before the table (do not repeat it): {context}\n\nTable:\n{table}"
            )
        except Exception:
            break
        have = _numbers(spoken)
        if spoken and all(n in have for n in wanted):
            return spoken
    return table  # falls through to the mechanical row-by-row reading


async def _speak_code(client: httpx.AsyncClient, code: str) -> str:
    try:
        spoken = await _ask_llm(client, LLM_CODE_SYSTEM, code[:4000])
    except Exception:
        return code
    if not spoken or len(spoken) > 300 or "\n" in spoken:
        return code  # mechanical pass says "there is a code block here"
    return f"{spoken.rstrip('.')}, which I will not read out."


async def speakable_reply(markdown: str, cache_dir: Path) -> list[str]:
    """Paragraphs ready for TTS; LLM output is cached per reply so the wording,
    and with it the audio cache key, stays stable between presses."""
    segments = _segments(markdown)
    if not LLM_URL or all(kind == "prose" for kind, _ in segments):
        return speakable_paragraphs(markdown)
    digest = hashlib.sha256(f"v2\n{LLM_MODEL}\n{markdown}".encode("utf-8")).hexdigest()[:16]
    cache = cache_dir / f"speech-text-{digest}.json"
    try:
        return list(json.loads(cache.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    async with httpx.AsyncClient(timeout=180.0) as client:
        jobs = []
        for index, (kind, text) in enumerate(segments):
            if kind == "table" and len(text.splitlines()) >= 3:
                context = segments[index - 1][1].strip()[-300:] if index else ""
                jobs.append(_speak_table(client, text, context))
            elif kind == "code":
                jobs.append(_speak_code(client, text))
            else:
                jobs.append(asyncio.sleep(0, result=text))
        pieces = await asyncio.gather(*jobs)
    paragraphs = speakable_paragraphs("\n\n".join(pieces))
    cache.write_text(json.dumps(paragraphs), encoding="utf-8")
    return paragraphs


def speech_path(session_dir: Path, text: str) -> Path:
    digest = hashlib.sha256(f"{TTS_VOICE}\n{text}".encode("utf-8")).hexdigest()[:16]
    return session_dir / f"speech-{digest}.mp3"


async def render_speech(paragraphs: list[str], out: Path) -> float:
    """Render to `out` (mp3); returns the duration in seconds."""
    joined = out.with_suffix(".wav")
    writer = None
    frames_total, rate = 0, 24000
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            for paragraph in paragraphs:
                response = await client.post(
                    TTS_URL, json={"input": paragraph, "voice": TTS_VOICE, "response_format": "wav"}
                )
                response.raise_for_status()
                with wave.open(io.BytesIO(response.content)) as chunk:
                    if writer is None:
                        writer = wave.open(str(joined), "wb")
                        # not setparams(): Kokoro streams, so its header nframes is 0xFFFFFFFF
                        writer.setnchannels(chunk.getnchannels())
                        writer.setsampwidth(chunk.getsampwidth())
                        writer.setframerate(chunk.getframerate())
                        rate = chunk.getframerate()
                    frames = chunk.readframes(chunk.getnframes())
                    width = chunk.getsampwidth() * chunk.getnchannels()
                    silence = b"\x00" * int(PAUSE_S * rate) * width
                    writer.writeframes(frames)
                    writer.writeframes(silence)
                    frames_total += (len(frames) + len(silence)) // width
        if writer is None:
            raise RuntimeError("nothing to speak")
        writer.close()
        writer = None
        partial = out.with_name(out.stem + ".part.mp3")
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN, "-y", "-loglevel", "error", "-i", str(joined), "-b:a", "64k", str(partial),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')[:300]}")
        partial.rename(out)
    finally:
        if writer is not None:
            writer.close()
        joined.unlink(missing_ok=True)
    return frames_total / float(rate)
