import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


APP_NAME = "home-agent-gateway"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
HOME_CONFIG_DIR = Path(os.environ.get("HOME_AGENT_HOME_CONFIG", "/home/pi/home_config"))
SESSION_ROOT = Path(
    os.environ.get("HOME_AGENT_SESSION_ROOT", str(HOME_CONFIG_DIR / "home-agent" / "sessions"))
)
RUNNER_URL = os.environ.get("HOME_AGENT_RUNNER_URL", "http://127.0.0.1:8766")
PARAKEET_URL = os.environ.get("HOME_AGENT_PARAKEET_URL", "http://jetson-tts:8090")
GATEWAY_TOKEN = os.environ.get("HOME_AGENT_TOKEN", "")
FFMPEG_BIN = os.environ.get("HOME_AGENT_FFMPEG_BIN", "ffmpeg")


app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def require_token(request: Request) -> None:
    if not GATEWAY_TOKEN:
        return
    auth = request.headers.get("authorization", "")
    header_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    cookie_token = request.cookies.get("home_agent_token", "")
    query_token = request.query_params.get("token", "")
    if GATEWAY_TOKEN not in {header_token, cookie_token, query_token}:
        raise HTTPException(status_code=401, detail="missing or invalid token")


def build_prompt(transcript: str) -> str:
    return f"""You are helping with my home automation and home lab.

Working directory:
{HOME_CONFIG_DIR}

Read the markdown guides in this folder first and use them as the source of truth for hostnames, SSH targets, service ownership, troubleshooting paths, and operational constraints.

I am on my phone. Keep progress updates concise and clear. Investigate freely with read-only commands. Before disruptive actions such as service restarts, config edits, SSH changes, package installs, destructive commands, or broad filesystem changes, state the proposed action and wait for explicit approval.

When you need phone approval, end your message with exactly this marker on its own line:
AWAITING_PHONE_APPROVAL: <short action summary>

User request transcribed from voice:
{transcript.strip()}
"""


def session_dir(session_id: str) -> Path:
    path = SESSION_ROOT / time.strftime("%Y-%m-%d") / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


async def run_ffmpeg(input_path: Path, output_path: Path) -> None:
    if shutil.which(FFMPEG_BIN) is None:
        raise HTTPException(status_code=500, detail=f"ffmpeg not found: {FFMPEG_BIN}")
    proc = await asyncio.create_subprocess_exec(
        FFMPEG_BIN,
        "-y",
        "-i",
        str(input_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=422,
            detail={"message": "audio conversion failed", "ffmpeg": stderr.decode("utf-8", "replace")},
        )


async def transcribe_wav(wav_path: Path) -> dict:
    url = f"{PARAKEET_URL.rstrip('/')}/parakeet/transcribe?chunk_seconds=120&context_seconds=2"
    wav_bytes = wav_path.read_bytes()
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            response = await client.post(url, content=wav_bytes, headers={"Content-Type": "audio/wav"})
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail={"message": "Parakeet transcription request failed", "url": url, "error": str(exc)},
            ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"message": "Parakeet transcription failed", "status": response.status_code, "body": response.text},
        )
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = {"text": response.text}
    return payload


def extract_text(payload: dict) -> str:
    for key in ("text", "transcript", "transcript_text", "result"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_transcript_text(value)
    if isinstance(payload.get("segments"), list):
        parts = []
        for segment in payload["segments"]:
            if isinstance(segment, dict) and isinstance(segment.get("text"), str):
                parts.append(segment["text"].strip())
        if parts:
            return " ".join(parts).strip()
    return json.dumps(payload, indent=2)


def normalize_transcript_text(value: str) -> str:
    value = value.strip()
    match = re.search(r"\btext='([^']*)'", value)
    if match:
        return match.group(1).strip()
    return value


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> FileResponse:
    require_token(request)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health(request: Request) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=5.0) as client:
        runner = await client.get(f"{RUNNER_URL.rstrip('/')}/health")
    return {"ok": True, "service": APP_NAME, "runner": runner.json()}


@app.post("/api/transcribe")
async def transcribe(request: Request, audio: UploadFile = File(...)) -> dict:
    require_token(request)
    sid = uuid.uuid4().hex[:12]
    path = session_dir(sid)
    suffix = Path(audio.filename or "input.webm").suffix or ".webm"
    original = path / f"source{suffix}"
    wav = path / "input.wav"
    original.write_bytes(await audio.read())
    await run_ffmpeg(original, wav)
    payload = await transcribe_wav(wav)
    text = extract_text(payload)
    (path / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    (path / "parakeet.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"session_seed": sid, "text": text}


@app.post("/api/sessions")
async def start_session(request: Request) -> dict:
    require_token(request)
    body = await request.json()
    transcript = str(body.get("text", "")).strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="text is required")
    prompt = build_prompt(transcript)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RUNNER_URL.rstrip('/')}/sessions",
            json={"prompt": prompt, "cwd": str(HOME_CONFIG_DIR), "title": transcript[:80]},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.get("/api/sessions")
async def list_sessions(request: Request, limit: int = 50) -> list[dict]:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/sessions", params={"limit": limit})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.get("/api/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.post("/api/sessions/{session_id}/resume")
async def resume_session(request: Request, session_id: str) -> dict:
    require_token(request)
    body = await request.json()
    prompt = str(body.get("text") or body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="text is required")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}/resume",
            json={"prompt": prompt, "title": prompt[:80]},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(request: Request, session_id: str) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}/stop")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.websocket("/ws/sessions/{session_id}")
async def session_ws(websocket: WebSocket, session_id: str) -> None:
    token = websocket.query_params.get("token", "")
    if GATEWAY_TOKEN and token != GATEWAY_TOKEN:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    runner_ws = RUNNER_URL.replace("http://", "ws://").replace("https://", "wss://")
    runner_ws = f"{runner_ws.rstrip('/')}/sessions/{session_id}/ws"
    async with websockets.connect(runner_ws) as upstream:
        async def browser_to_runner() -> None:
            while True:
                message = await websocket.receive_text()
                await upstream.send(message)

        async def runner_to_browser() -> None:
            async for message in upstream:
                await websocket.send_text(message)

        tasks = {asyncio.create_task(browser_to_runner()), asyncio.create_task(runner_to_browser())}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
