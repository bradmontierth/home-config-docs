"""Long-form speech service in front of Breeze TTS 2 (OpenAI-style /v1/audio/speech -> WAV).

What it adds over the raw Breeze API:
  * named voices: a frozen reference clip + transcript per voice under VOICES_DIR
    (Breeze voice design is a lottery, so a voice IS its clip, never its prompt)
  * whole-paragraph generations, split on sentence boundaries only above MAX_CHARS,
    so the model sets its own sentence-to-sentence timing
  * every piece is transcribed (Parakeet) and compared with the text; a restarted
    clause or dropped passage is re-rendered with a new seed before anyone hears it
  * POST /v1/audio/speech/batch renders up to four inputs together through the Breeze
    batch endpoint (pure-clone voices only): ~2.2x real time for four against 1.0x for one
  * every piece's median pitch is logged next to the voice's own (`pitch_hz` +-
    `pitch_tol_hz` in <voice>.json). LOG ONLY since 2026-11-12: pitch was half of
    all re-renders (17 of 33 since 10-31) and a re-render stalls real-time playback
  * callers can pass `deadline_s`: a re-render that would land after it is skipped
    and the first take is used (a repeated phrase beats 30 s of silence)
"""
import array
import asyncio
import base64
import io
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

import torch
import torchaudio

BREEZE_URL = os.environ.get("BREEZE_URL", "http://127.0.0.1:7860")
PARAKEET_URL = os.environ.get("PARAKEET_URL", "http://127.0.0.1:8090/parakeet/transcribe?chunk_seconds=300&context_seconds=2")
VOICES_DIR = Path(os.environ.get("VOICES_DIR", "/voices"))
DEFAULT_VOICE = os.environ.get("DEFAULT_VOICE", "calm_explainer_f")
MAX_CHARS = int(os.environ.get("MAX_CHARS", "1200"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
SPLICE_PAUSE_S = 0.42
RATE = 24000
SILENCE = 300  # |sample| below this counts as silence when trimming edges

app = FastAPI(title="Breeze long-form speech")
breeze_lock = asyncio.Lock()  # Breeze serves one request at a time (409 otherwise)


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _breeze(text, ref_wav, ref_text, seed, instruction, cfg):
    boundary = uuid.uuid4().hex
    fields = {"text": text, "ref_text": ref_text, "seed": seed}
    if instruction:
        fields |= {"instruction": instruction, "cfg_scale": cfg}
    body = b""
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="ref_audio"; filename="ref.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + ref_wav + f"\r\n--{boundary}--\r\n".encode()
    return _post(f"{BREEZE_URL}/v1/audio/speech", body,
                 {"Content-Type": f"multipart/form-data; boundary={boundary}"}, 900)


def _breeze_batch(texts, ref_wav, ref_text, seed):
    body = json.dumps({"texts": texts, "ref_text": ref_text, "seed": seed,
                       "ref_audio_b64": base64.b64encode(ref_wav).decode()}).encode()
    out = json.loads(_post(f"{BREEZE_URL}/v1/audio/speech/batch", body, {"Content-Type": "application/json"}, 1800))
    return [base64.b64decode(b) for b in out["pcm16_b64"]]


def trim(pcm):
    """Strip leading/trailing silence, keeping 30 ms so onsets are not clipped."""
    a = array.array("h", pcm)
    keep = int(0.03 * RATE)
    first = next((i for i, s in enumerate(a) if abs(s) > SILENCE), 0)
    last = next((i for i in range(len(a) - 1, -1, -1) if abs(a[i]) > SILENCE), len(a) - 1)
    return a[max(first - keep, 0): last + keep].tobytes()


def wav_bytes(pcm, rate=RATE):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def chunks(text, max_chars):
    """Whole paragraph if it fits, else whole sentences grouped up to max_chars."""
    out = []
    for para in (p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()):
        if len(para) <= max_chars:
            out.append(para)
            continue
        cur = ""
        for s in re.split(r"(?<=[.!?])\s+", para):
            if cur and len(cur) + 1 + len(s) > max_chars:
                out.append(cur)
                cur = s
            else:
                cur = f"{cur} {s}".strip()
        if cur:
            out.append(cur)
    return out


def _words(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).split()


def problems(text, heard):
    """Why this take should be redone, or [] if it sounds like the text.

    Parakeet writes numbers as digits, so exact word matching is useless; instead
    look for the two failure shapes seen in practice: a clause said twice, and a
    passage missing or invented (length far off).
    """
    ref, hyp = _words(text), _words(heard)
    found = []
    if len(ref) >= 8:
        ratio = len(hyp) / len(ref)
        if ratio < 0.70 or ratio > 1.30:
            found.append(f"length {len(hyp)} words vs {len(ref)}")
    n = 4
    ref_grams = Counter(tuple(ref[i:i + n]) for i in range(len(ref) - n + 1))
    hyp_grams = Counter(tuple(hyp[i:i + n]) for i in range(len(hyp) - n + 1))
    repeats = [" ".join(g) for g, c in hyp_grams.items() if c >= 2 and c > ref_grams.get(g, 0)]
    if repeats:
        found.append(f"repeated: {repeats[0]!r}" + (f" (+{len(repeats) - 1} more)" if len(repeats) > 1 else ""))
    return found


def median_pitch(pcm):
    """Median F0 (Hz) over voiced frames; None when there is too little speech to say."""
    x = torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0
    if len(x) < RATE * 2:
        return None
    f0 = torchaudio.functional.detect_pitch_frequency(x.unsqueeze(0), RATE, frame_time=0.02, freq_low=80, freq_high=400)[0]
    hop = int(0.02 * RATE)
    rms = x.unfold(0, hop, hop).pow(2).mean(1).sqrt()
    n = min(len(f0), len(rms))
    voiced = f0[:n][rms[:n] > 0.02]
    return float(voiced.median()) if len(voiced) >= 50 else None


def _transcribe(pcm):
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
         "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
        input=pcm, capture_output=True, check=True)
    out = _post(PARAKEET_URL, proc.stdout, {"Content-Type": "audio/wav"}, 300)
    return json.loads(out).get("transcript_text", "")


def load_voice(name):
    name = re.sub(r"[^A-Za-z0-9_-]", "", name or "") or DEFAULT_VOICE
    wav, txt = VOICES_DIR / f"{name}.wav", VOICES_DIR / f"{name}.txt"
    if not wav.exists() or not txt.exists():
        raise HTTPException(status_code=404, detail=f"unknown voice {name!r}; have {sorted(p.stem for p in VOICES_DIR.glob('*.wav'))}")
    meta_path = VOICES_DIR / f"{name}.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return name, wav.read_bytes(), txt.read_text().strip(), meta


def check(text, pcm, meta):
    """(problems, pitch) for one take; problems is None when verification could not run."""
    try:
        found = problems(text, _transcribe(pcm))
    except Exception as exc:  # verification is a safety net, never a reason to fail the render
        print(f"verify skipped: {exc}", flush=True)
        return None, None
    pitch = median_pitch(pcm)  # logged only, see the module docstring
    return found, pitch


def retry_fits(pcm, deadline):
    """Would one more take of this piece land before `deadline` (time.time())? None = no deadline."""
    if deadline is None:
        return True
    # a batch of one renders at ~1.0-1.1x real time, plus Parakeet; measured retries ran longer
    return time.time() + 1.5 * len(pcm) / 2 / RATE + 5 <= deadline


def render_piece(text, voice, first_attempt=0, best=None, pitches=None, deadline=None):
    """One generation, verified; retried with a new seed while it has problems. Returns (pcm, log).

    A batch take that failed its check comes in as `best` with first_attempt=1.
    """
    _, ref_wav, ref_text, meta = voice
    seed = int(meta.get("seed", 21))
    pitches = pitches or []
    attempt = first_attempt
    for attempt in range(first_attempt, MAX_ATTEMPTS):
        if best is not None and not retry_fits(best[0], deadline):
            return best[0], {"attempts": attempt, "problems": best[1], "pitch_hz": pitches, "retry": "skipped, deadline"}
        for _ in range(60):  # Breeze answers 409 while another request is decoding
            try:
                if meta.get("instruction"):
                    pcm = trim(_breeze(text, ref_wav, ref_text, seed + attempt, meta["instruction"], meta.get("cfg_scale", 4)))
                else:  # a batch of one: same speed, and a take that never ends is cut at ~1.75x
                    pcm = trim(_breeze_batch([text], ref_wav, ref_text, seed + attempt)[0])
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                time.sleep(2)
        else:
            raise RuntimeError("Breeze stayed busy")
        found, pitch = check(text, pcm, meta)
        if found is None:
            return pcm, {"attempts": attempt + 1, "verify": "skipped"}
        pitches.append(round(pitch) if pitch else None)
        if best is None or len(found) < len(best[1]):
            best = (pcm, found)
        if not found:
            break
    return best[0], {"attempts": attempt + 1, "problems": best[1], "pitch_hz": pitches}


def render_batch(texts, voice, deadline=None):
    """Several pieces in one Breeze batch, each verified; a failed take is redone on its own."""
    _, ref_wav, ref_text, meta = voice
    takes = [trim(pcm) for pcm in _breeze_batch(texts, ref_wav, ref_text, int(meta.get("seed", 21)))]
    results = []
    for text, pcm in zip(texts, takes):
        found, pitch = check(text, pcm, meta)
        if found is None:
            results.append((pcm, {"attempts": 1, "batch": len(texts), "verify": "skipped"}))
        elif not found:
            results.append((pcm, {"attempts": 1, "batch": len(texts), "problems": [], "pitch_hz": [round(pitch) if pitch else None]}))
        else:
            pcm, log = render_piece(text, voice, first_attempt=1, best=(pcm, found),
                                    pitches=[round(pitch) if pitch else None], deadline=deadline)
            results.append((pcm, log | {"batch": len(texts)}))
    return results


@app.get("/health")
def health():
    try:
        with urllib.request.urlopen(f"{BREEZE_URL}/health", timeout=5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=503, detail="Breeze backend is not ready")
    return {"status": "ok", "batch": True, "voices": sorted(p.stem for p in VOICES_DIR.glob("*.wav")), "default_voice": DEFAULT_VOICE}


@app.post("/v1/audio/speech")
async def speech(request: Request):
    body = await request.json()
    text = str(body.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is empty")
    if str(body.get("response_format") or "wav") != "wav":
        raise HTTPException(status_code=400, detail="only response_format=wav is supported")
    voice = load_voice(str(body.get("voice") or ""))
    t0 = time.time()
    deadline = t0 + float(body["deadline_s"]) if body.get("deadline_s") is not None else None
    audio, logs = bytearray(), []
    async with breeze_lock:
        for i, piece in enumerate(chunks(text, MAX_CHARS)):
            try:
                pcm, log = await asyncio.to_thread(render_piece, piece, voice, deadline=deadline)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"render failed: {exc}") from exc
            if i:
                audio += b"\x00" * (int(SPLICE_PAUSE_S * RATE) * 2)
            audio += pcm
            logs.append(log)
    seconds = len(audio) / 2 / RATE
    print(json.dumps({"voice": voice[0], "chars": len(text), "audio_s": round(seconds, 1),
                      "wall_s": round(time.time() - t0, 1), "pieces": logs}), flush=True)
    return Response(content=wav_bytes(bytes(audio)), media_type="audio/wav",
                    headers={"X-Audio-Seconds": f"{seconds:.2f}",
                             "X-Attempts": ",".join(str(l["attempts"]) for l in logs)})


@app.post("/v1/audio/speech/batch")
async def speech_batch(request: Request):
    """Up to four inputs rendered together; JSON with one 24 kHz mono PCM16 (base64) per input.

    Each input should fit one piece (MAX_CHARS); longer ones are split and rendered in turn.
    Voices with a direction (instruction) cannot batch and render one by one.
    """
    body = await request.json()
    inputs = [str(t or "").strip() for t in body.get("inputs") or []]
    if not inputs or not all(inputs) or len(inputs) > 4:
        raise HTTPException(status_code=400, detail="inputs must be 1-4 non-empty strings")
    voice = load_voice(str(body.get("voice") or ""))
    t0 = time.time()
    deadline = t0 + float(body["deadline_s"]) if body.get("deadline_s") is not None else None
    batchable = not voice[3].get("instruction") and all(len(chunks(t, MAX_CHARS)) == 1 for t in inputs)
    async with breeze_lock:
        try:
            if batchable:
                results = await asyncio.to_thread(render_batch, inputs, voice, deadline)
            else:
                results = []
                for text in inputs:
                    audio, logs = bytearray(), []
                    for i, piece in enumerate(chunks(text, MAX_CHARS)):
                        pcm, log = await asyncio.to_thread(render_piece, piece, voice, deadline=deadline)
                        if i:
                            audio += b"\x00" * (int(SPLICE_PAUSE_S * RATE) * 2)
                        audio += pcm
                        logs.append(log)
                    results.append((bytes(audio), {"pieces": logs}))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"render failed: {exc}") from exc
    seconds = [len(pcm) / 2 / RATE for pcm, _ in results]
    print(json.dumps({"voice": voice[0], "batch": len(inputs), "deadline_s": body.get("deadline_s"), "chars": [len(t) for t in inputs],
                      "audio_s": [round(s, 1) for s in seconds], "wall_s": round(time.time() - t0, 1),
                      "pieces": [log for _, log in results]}), flush=True)
    return {"sample_rate": RATE, "audio_s": [round(s, 2) for s in seconds],
            "pcm16_b64": [base64.b64encode(pcm).decode() for pcm, _ in results]}
