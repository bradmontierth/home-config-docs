"""Turn an agent reply into listenable audio.

Replies are markdown written for a screen. `speakable_paragraphs` flattens one
into plain paragraphs a TTS voice can read (no tables, fences, paths or
symbols), and `SpeechJob` sends those to a TTS backend one paragraph at a time
and joins the result into a single seekable MP3.

Two voices: the long-form one (Breeze on the GX10, natural but only about as
fast as real time) and the router's fast one as the fallback when the GX10
service is down. Because the long-form voice cannot be waited for, a render is
progressive: `SpeechJob` grows a `.part.mp3` paragraph by paragraph and the
gateway streams it while it grows.
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import time
import wave
from pathlib import Path

import httpx

TTS_URL = os.environ.get("HOME_AGENT_TTS_URL", "http://192.168.10.217:8891/v1/audio/speech")
TTS_VOICE = os.environ.get("HOME_AGENT_TTS_VOICE", "fast:doorbell")
FFMPEG_BIN = os.environ.get("HOME_AGENT_FFMPEG_BIN", "ffmpeg")
# Long-form voice; empty URL disables it and everything uses TTS_URL/TTS_VOICE.
LONGFORM_URL = os.environ.get("HOME_AGENT_LONGFORM_TTS_URL", "http://192.168.10.187:7861/v1/audio/speech")
LONGFORM_VOICE = os.environ.get("HOME_AGENT_LONGFORM_TTS_VOICE", "calm_explainer_f")
LONGFORM_PAUSE_S = 0.75  # it trims its own edges, so the paragraph gap is all ours
# The long-form voice renders slightly slower than real time, and each generation
# picks its own energy, so pieces are whole paragraphs: a shift of tone at a change
# of topic sounds natural, one in the middle of a thought sounds like a new narrator.
# The gateway buys the time for that by rendering as soon as a reply finishes (see
# app.prerender_speech). Only a render started by the Listen tap itself, with nobody
# ahead of it, cuts its first paragraph so the wait for the first sound stays short.
FIRST_PIECE_CHARS = int(os.environ.get("HOME_AGENT_LONGFORM_FIRST_PIECE_CHARS", "450"))
SPLICE_PAUSE_S = 0.42  # inside a paragraph that had to be cut
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


class Backend:
    def __init__(self, url: str, voice: str, pause_s: float, slow: bool, batch: bool = False,
                 engine: str = "", group_factor: dict[int, float] | None = None, group_overhead_s: float = 0.0) -> None:
        self.url, self.voice, self.pause_s, self.slow, self.batch = url, voice, pause_s, slow, batch
        # The long-form service reports its engine and speed (2026-11-12: Ming, ~10x real
        # time against Breeze's ~1.4x); the Breeze-measured GROUP_FACTOR is the default.
        self.engine = engine
        self.group_factor = group_factor or GROUP_FACTOR
        self.group_overhead_s = group_overhead_s
        # audio cache key: the same voice name rendered by another engine is different audio
        self.cache_voice = f"{voice}@{engine}" if engine and engine != "breeze" else voice


def fast_backend() -> Backend:
    return Backend(TTS_URL, TTS_VOICE, PAUSE_S, slow=False)


async def pick_backend() -> Backend:
    """The long-form voice when its service answers, else the router's fast voice."""
    if LONGFORM_URL:
        health = LONGFORM_URL.split("/v1/", 1)[0] + "/health"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(health)
                if response.status_code == 200:
                    info = response.json()
                    factor = {int(k): float(v) for k, v in (info.get("group_factor") or {}).items()} or None
                    return Backend(LONGFORM_URL, LONGFORM_VOICE, LONGFORM_PAUSE_S, slow=True, batch=bool(info.get("batch")),
                                   engine=str(info.get("backend") or ""), group_factor=factor,
                                   group_overhead_s=float(info.get("group_overhead_s") or 0))
        except httpx.HTTPError:
            pass
    return fast_backend()


def speech_path(session_dir: Path, text: str, voice: str = TTS_VOICE) -> Path:
    digest = hashlib.sha256(f"{voice}\n{text}".encode("utf-8")).hexdigest()[:16]
    return session_dir / f"speech-{digest}.mp3"


def _pieces(paragraphs: list[str], backend: Backend, fast_start: bool) -> list[tuple[str, float]]:
    """(text, silence after it): whole paragraphs, the first one cut short for a fast start on a slow backend."""
    pieces = [(paragraph, backend.pause_s) for paragraph in paragraphs]
    if fast_start and backend.slow and pieces and len(pieces[0][0]) > FIRST_PIECE_CHARS:
        head = ""
        sentences = re.split(r"(?<=[.!?])\s+", pieces[0][0])
        while sentences and (not head or len(head) + 1 + len(sentences[0]) <= FIRST_PIECE_CHARS):
            head = f"{head} {sentences.pop(0)}".strip()
        if sentences:
            pieces[0:1] = [(head, SPLICE_PAUSE_S), (" ".join(sentences), backend.pause_s)]
    return pieces


# The long-form service renders up to four pieces in one batch for little more time
# than one (memory-bandwidth-bound GPU). A group takes about its longest piece's
# audio length times this factor (measured 2026-10-30 incl. verification; 3 pads to 4).
GROUP_FACTOR = {1: 1.1, 2: 1.3, 3: 1.7, 4: 1.7}
ESTIMATE_MARGIN = 1.2  # a busy GPU or a retried take
STREAM_BYTES_PER_S = 8000  # the 64 kbps CBR the stream is encoded at
RETRY_DEADLINE_MARGIN_S = 5.0  # re-renders must land this far ahead of the listener
AUDIO_S_PER_CHAR = 0.068  # the long-form voice, until a job has measured its own


def _groups(pieces: list[tuple[str, float]], backend: Backend, fast_start: bool) -> list[list[tuple[str, float]]]:
    """Render order, then 4s: a Listen tap hears one piece soonest; a render started
    ahead of time banks two first, which carries playback over the first group of four."""
    if not backend.batch:
        return [[piece] for piece in pieces]
    groups, rest, sizes = [], list(pieces), [1, 2] if fast_start else [2]
    while rest:
        size = sizes.pop(0) if sizes else 4
        groups.append(rest[:size])
        rest = rest[size:]
    return groups


class _BreezeTurns:
    """One long-form group at a time across all jobs, the one someone is listening to first.

    Every finished reply pre-renders while Listen is in use, so replies from other
    sessions queue up; served in arrival order they made the reply being listened
    to wait behind them (2026-10-30).
    """

    def __init__(self) -> None:
        self.busy = False
        self.waiting: list[SpeechJob] = []
        self.cond: asyncio.Condition | None = None

    def _next(self) -> "SpeechJob":
        return max(self.waiting, key=lambda job: (job.listened, -job.started))

    async def acquire(self, job: "SpeechJob") -> None:
        self.cond = self.cond or asyncio.Condition()
        async with self.cond:
            self.waiting.append(job)
            try:
                await self.cond.wait_for(lambda: not self.busy and self._next() is job)
            finally:
                self.waiting.remove(job)
            self.busy = True

    async def release(self) -> None:
        async with self.cond:
            self.busy = False
            self.cond.notify_all()

    async def nudge(self) -> None:
        """A job's priority changed."""
        if self.cond:
            async with self.cond:
                self.cond.notify_all()


BREEZE_TURNS = _BreezeTurns()


class SpeechJob:
    """One progressive render: `part` grows as pieces finish, then becomes `out`."""

    def __init__(self, paragraphs: list[str], out: Path, backend: Backend, fast_start: bool) -> None:
        self.paragraphs, self.out, self.backend, self.fast_start = paragraphs, out, backend, fast_start
        # For a player that reconnects mid-stream (see app.get_session_speech).
        self.stream_started: float | None = None
        self.stream_sent = 0
        # where the listener's current request started, and when: estimates the play position
        self.stream_anchor_byte, self.stream_anchor_t = 0, 0.0
        self.part = out.with_name(out.stem + ".part.mp3")
        # Progress, for judging whether playback started now would catch up with rendering.
        self.started = time.monotonic()
        self.total_chars = sum(len(p) for p in paragraphs)
        self.done_chars = 0
        self.audio_s = 0.0  # rendered so far, pauses included
        self.speech_s = 0.0  # the same without the pauses
        self.groups = _groups(_pieces(paragraphs, backend, fast_start), backend, fast_start)
        self.group_index = 0  # the group being rendered
        self.group_started = self.started
        self.listened = False  # set by a Listen tap; puts this job first in BREEZE_TURNS
        self.first_audio = asyncio.Event()  # set on the first playable bytes, or on failure
        self.done = False
        self.error: str | None = None
        self.task = asyncio.create_task(self._run())

    def retry_deadline_s(self) -> float | None:
        """Seconds until the listener reaches the audio not rendered yet, less a margin.

        The long-form service skips a verification re-render that would land later
        (2026-11-12: re-renders took 2-6x the audio length and caused 20-30 s stalls).
        A job someone is waiting to hear counts as playing from now, because the group
        in flight when playback starts is the one a re-render stalls (same test). A
        pre-render nobody is listening to can take all the re-renders it wants.
        """
        if self.stream_started is None:
            return max(self.audio_s - RETRY_DEADLINE_MARGIN_S, 0.0) if self.listened else None
        playing_s = self.stream_anchor_byte / STREAM_BYTES_PER_S + (time.monotonic() - self.stream_anchor_t)
        return max(self.audio_s - playing_s - RETRY_DEADLINE_MARGIN_S, 0.0)

    async def _render(self, client: httpx.AsyncClient, group: list[tuple[str, float]]) -> list[tuple[bytes, int, int, int]]:
        """(pcm frames, rate, bytes per frame, channels) for each piece of a group."""
        deadline = {"deadline_s": round(d, 1)} if self.backend.slow and (d := self.retry_deadline_s()) is not None else {}
        if len(group) > 1:
            response = await client.post(
                self.backend.url.rstrip("/") + "/batch",
                json={"inputs": [text for text, _ in group], "voice": self.backend.voice} | deadline,
            )
            response.raise_for_status()
            body = response.json()
            return [(base64.b64decode(pcm), body["sample_rate"], 2, 1) for pcm in body["pcm16_b64"]]
        response = await client.post(
            self.backend.url, json={"input": group[0][0], "voice": self.backend.voice, "response_format": "wav"} | deadline
        )
        response.raise_for_status()
        with wave.open(io.BytesIO(response.content)) as chunk:
            # readframes(getnframes()) still works on Kokoro's streamed header (nframes 0xFFFFFFFF)
            width = chunk.getsampwidth() * chunk.getnchannels()
            return [(chunk.readframes(chunk.getnframes()), chunk.getframerate(), width, chunk.getnchannels())]

    async def _run(self) -> None:
        ffmpeg = None
        try:
            async with httpx.AsyncClient(timeout=1800.0) as client:
                holding = False  # a job being listened to keeps its turn between groups
                try:
                    for index, group in enumerate(self.groups):
                        if self.backend.slow and not holding:
                            await BREEZE_TURNS.acquire(self)
                            holding = True
                        self.group_index, self.group_started = index, time.monotonic()
                        rendered = await self._render(client, group)
                        if holding and not self.listened:
                            await BREEZE_TURNS.release()
                            holding = False
                        for (text, pause_s), (frames, rate, width, channels) in zip(group, rendered):
                            if ffmpeg is None:
                                self.part.unlink(missing_ok=True)
                                # No Xing header: it is rewritten at the end, which a stream reader never sees.
                                ffmpeg = await asyncio.create_subprocess_exec(
                                    FFMPEG_BIN, "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(rate),
                                    "-ac", str(channels), "-i", "pipe:0", "-b:a", "64k",
                                    "-write_xing", "0", "-flush_packets", "1", "-f", "mp3", str(self.part),
                                    stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                )
                            ffmpeg.stdin.write(frames + b"\x00" * int(pause_s * rate) * width)
                            await ffmpeg.stdin.drain()
                            self.done_chars += len(text)
                            self.speech_s += len(frames) / (rate * width)
                            self.audio_s += len(frames) / (rate * width) + pause_s
                        if not self.first_audio.is_set():
                            for _ in range(50):  # let the encoder put the first bytes on disk
                                if self.part.exists() and self.part.stat().st_size > 4096:
                                    break
                                await asyncio.sleep(0.1)
                            self.first_audio.set()
                finally:
                    if holding:
                        await BREEZE_TURNS.release()
            self.group_index = len(self.groups)
            if ffmpeg is None:
                raise RuntimeError("nothing to speak")
            ffmpeg.stdin.close()
            stderr = await ffmpeg.stderr.read()
            if await ffmpeg.wait() != 0:
                raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')[:300]}")
            self.part.rename(self.out)  # open stream readers keep their handle
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__
            if ffmpeg is not None and ffmpeg.returncode is None:
                ffmpeg.kill()
            self.part.unlink(missing_ok=True)
        finally:
            self.done = True
            self.first_audio.set()

    def _group_render_s(self, group: list[tuple[str, float]]) -> float:
        per_char = self.speech_s / self.done_chars if self.done_chars else AUDIO_S_PER_CHAR
        factor = self.backend.group_factor.get(len(group), max(self.backend.group_factor.values()))
        return (max(len(text) for text, _ in group) * per_char * factor + self.backend.group_overhead_s) * ESTIMATE_MARGIN

    def group_eta_s(self) -> float | None:
        """Seconds until the group being rendered is expected to land, None when nothing is rendering."""
        if self.done or self.group_index >= len(self.groups):
            return None
        return self.group_started + self._group_render_s(self.groups[self.group_index]) - time.monotonic()

    def can_play_through(self, slack_s: float = 1.0) -> bool:
        """Would playback started now reach the end without catching up with rendering?

        Walks the remaining groups: each must land, by estimate (GROUP_FACTOR, with
        ESTIMATE_MARGIN for a busy GPU or a retried take), before playback reaches it.
        """
        if self.done:
            return not self.error
        if self.done_chars == 0:
            return False
        per_char = self.speech_s / self.done_chars
        now = time.monotonic()
        playable_until = now + self.audio_s
        landed = self.group_started
        for group in self.groups[self.group_index:]:
            landed += self._group_render_s(group)
            if landed > playable_until - slack_s:
                return False
            playable_until = max(playable_until, landed) + sum(len(text) * per_char + pause for text, pause in group)
        return True


def mp3_first_frame(path: Path) -> int:
    """Byte offset of the first audio frame: after the ID3 tag ffmpeg writes, if any."""
    with open(path, "rb") as handle:
        head = handle.read(10)
    if len(head) == 10 and head[:3] == b"ID3":  # tag size is four 7-bit bytes
        return 10 + ((head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 | (head[9] & 0x7F))
    return 0


def mp3_frame_offset(path: Path, approx: int) -> int:
    """The first MP3 frame boundary at or after `approx`, so a stream can start mid-file."""
    first = mp3_first_frame(path)
    with open(path, "rb") as handle:
        if approx <= first:
            return 0
        handle.seek(first)
        sync = handle.read(3)
        handle.seek(approx)
        window = handle.read(8192)
    same = lambda i: window[i:i + 2] == sync[:2] and (window[i + 2] & 0xFC) == (sync[2] & 0xFC)
    for i in range(len(window) - 400):
        # CBR: the next frame starts one frame length on (192 bytes at 64 kbps / 24 kHz, +1 if padded)
        if same(i) and any(same(i + n) for n in range(96, 400)):
            return approx + i
    return 0
