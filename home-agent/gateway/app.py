import asyncio
import json
import logging
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
LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
HOME_CONFIG_DIR = Path(os.environ.get("HOME_AGENT_HOME_CONFIG", "/home/pi/home_config"))
SESSION_ROOT = Path(
    os.environ.get("HOME_AGENT_SESSION_ROOT", str(HOME_CONFIG_DIR / "home-agent" / "sessions"))
)
RUNNER_URL = os.environ.get("HOME_AGENT_RUNNER_URL", "http://127.0.0.1:8766")
PARAKEET_URL = os.environ.get("HOME_AGENT_PARAKEET_URL", "http://192.168.10.187:8090")
GATEWAY_TOKEN = os.environ.get("HOME_AGENT_TOKEN", "")
FFMPEG_BIN = os.environ.get("HOME_AGENT_FFMPEG_BIN", "ffmpeg")
FCM_PROJECT_ID = os.environ.get("HOME_AGENT_FCM_PROJECT_ID", "")
FCM_SERVICE_ACCOUNT_JSON = os.environ.get(
    "HOME_AGENT_FCM_SERVICE_ACCOUNT_JSON",
    "/home/pi/cecret_lake/home-agent/firebase-service-account.json",
)
PUSH_REGISTRY_PATH = Path(
    os.environ.get("HOME_AGENT_PUSH_REGISTRY", str(SESSION_ROOT / "push_tokens.json"))
)
PUSH_POLL_SECONDS = max(5, int(os.environ.get("HOME_AGENT_PUSH_POLL_SECONDS", "15")))
PUSH_SCAN_SECONDS = max(15, int(os.environ.get("HOME_AGENT_PUSH_SCAN_SECONDS", "60")))
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
ANDROID_APK_PATH = Path(
    os.environ.get(
        "HOME_AGENT_ANDROID_APK_PATH",
        str(HOME_CONFIG_DIR / "home-agent-android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"),
    )
)
ALLOWED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
NOTIFICATION_CHANNEL_ID = "home_agent_sessions"


app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
push_registry_lock = asyncio.Lock()
session_monitor_tasks: dict[str, asyncio.Task] = {}
_fcm_credentials = None
_fcm_project_id: str | None = None


def require_token(request: Request) -> None:
    if not GATEWAY_TOKEN:
        return
    auth = request.headers.get("authorization", "")
    header_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    cookie_token = request.cookies.get("home_agent_token", "")
    query_token = request.query_params.get("token", "")
    if GATEWAY_TOKEN not in {header_token, cookie_token, query_token}:
        raise HTTPException(status_code=401, detail="missing or invalid token")


def fcm_configured() -> bool:
    return bool(resolve_fcm_project_id(silent=True) and FCM_SERVICE_ACCOUNT_JSON)


def resolve_fcm_project_id(silent: bool = False) -> str:
    global _fcm_project_id
    if _fcm_project_id is not None:
        return _fcm_project_id
    if FCM_PROJECT_ID:
        _fcm_project_id = FCM_PROJECT_ID
        return _fcm_project_id
    if not FCM_SERVICE_ACCOUNT_JSON:
        return ""
    try:
        service_account = json.loads(Path(FCM_SERVICE_ACCOUNT_JSON).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if not silent:
            LOGGER.warning("could not read Firebase service account JSON: %s", exc)
        return ""
    _fcm_project_id = str(service_account.get("project_id") or "")
    return _fcm_project_id


def fcm_access_token_sync() -> str:
    global _fcm_credentials
    if not FCM_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("HOME_AGENT_FCM_SERVICE_ACCOUNT_JSON is not set")
    if _fcm_credentials is None:
        from google.oauth2 import service_account

        _fcm_credentials = service_account.Credentials.from_service_account_file(
            FCM_SERVICE_ACCOUNT_JSON,
            scopes=[FCM_SCOPE],
        )
    if not _fcm_credentials.valid:
        from google.auth.transport.requests import Request as GoogleAuthRequest

        _fcm_credentials.refresh(GoogleAuthRequest())
    return str(_fcm_credentials.token)


def load_push_registry() -> dict:
    try:
        data = json.loads(PUSH_REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"tokens": {}}
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("push registry is unreadable: %s", PUSH_REGISTRY_PATH)
        return {"tokens": {}}
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        data["tokens"] = {}
    return data


def save_push_registry(data: dict) -> None:
    PUSH_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PUSH_REGISTRY_PATH.with_suffix(PUSH_REGISTRY_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(PUSH_REGISTRY_PATH)


async def registered_push_tokens() -> list[str]:
    async with push_registry_lock:
        registry = load_push_registry()
        return list(registry.get("tokens", {}).keys())


async def upsert_push_token(fcm_token: str, platform: str, device_label: str) -> None:
    async with push_registry_lock:
        registry = load_push_registry()
        tokens = registry.setdefault("tokens", {})
        now = time.time()
        existing = tokens.get(fcm_token) if isinstance(tokens.get(fcm_token), dict) else {}
        tokens[fcm_token] = {
            "platform": platform or "android",
            "device_label": device_label or "Android device",
            "registered_at": existing.get("registered_at") or now,
            "updated_at": now,
        }
        save_push_registry(registry)


async def delete_push_token(fcm_token: str) -> bool:
    async with push_registry_lock:
        registry = load_push_registry()
        tokens = registry.setdefault("tokens", {})
        existed = fcm_token in tokens
        tokens.pop(fcm_token, None)
        save_push_registry(registry)
        return existed


async def send_fcm_to_token(fcm_token: str, title: str, body: str, data: dict[str, str]) -> str:
    project_id = resolve_fcm_project_id()
    if not project_id or not FCM_SERVICE_ACCOUNT_JSON:
        LOGGER.info("FCM is not configured; skipping push notification")
        return "skipped"
    access_token = await asyncio.to_thread(fcm_access_token_sync)
    message_data = {key: str(value) for key, value in data.items()}
    message_data["notification_title"] = title
    message_data["notification_body"] = body
    payload = {
        "message": {
            "token": fcm_token,
            "data": message_data,
            "android": {
                "priority": "HIGH",
            },
        }
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=payload,
        )
    if response.is_success:
        return "sent"
    text = response.text
    if response.status_code in {400, 404} and "UNREGISTERED" in text:
        return "unregistered"
    LOGGER.warning("FCM send failed for token hash %s: %s", hash(fcm_token), text)
    return "failed"


async def send_push_notification(title: str, body: str, data: dict[str, str]) -> bool:
    tokens = await registered_push_tokens()
    if not tokens:
        return False
    sent = False
    for fcm_token in tokens:
        try:
            result = await send_fcm_to_token(fcm_token, title, body, data)
        except Exception as exc:
            LOGGER.warning("FCM send raised for token hash %s: %s", hash(fcm_token), exc)
            continue
        if result == "sent":
            sent = True
        elif result == "unregistered":
            await delete_push_token(fcm_token)
    return sent


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
    url = f"{PARAKEET_URL.rstrip('/')}/parakeet/transcribe?chunk_seconds=300&context_seconds=2"
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


@app.get("/api/android-apk")
async def download_android_apk(request: Request) -> FileResponse:
    require_token(request)
    if not ANDROID_APK_PATH.is_file():
        raise HTTPException(status_code=404, detail=f"APK not found: {ANDROID_APK_PATH}")
    return FileResponse(
        ANDROID_APK_PATH,
        media_type="application/vnd.android.package-archive",
        filename="home-agent-debug.apk",
    )


@app.post("/api/devices/register")
async def register_device(request: Request) -> dict:
    require_token(request)
    body = await request.json()
    fcm_token = str(body.get("fcm_token") or body.get("token") or "").strip()
    if not fcm_token:
        raise HTTPException(status_code=400, detail="fcm_token is required")
    platform = str(body.get("platform") or "android").strip()[:40]
    device_label = str(body.get("device_label") or "Android device").strip()[:120]
    await upsert_push_token(fcm_token, platform, device_label)
    return {
        "ok": True,
        "registered": True,
        "push_enabled": fcm_configured(),
    }


@app.post("/api/devices/unregister")
async def unregister_device(request: Request) -> dict:
    require_token(request)
    body = await request.json()
    fcm_token = str(body.get("fcm_token") or body.get("token") or "").strip()
    if not fcm_token:
        raise HTTPException(status_code=400, detail="fcm_token is required")
    removed = await delete_push_token(fcm_token)
    return {"ok": True, "removed": removed}


@app.get("/api/codex/accounts")
async def list_codex_accounts(request: Request) -> list[dict]:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/codex/accounts")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.post("/api/codex/accounts/{account_id}/label")
async def update_codex_account_label(request: Request, account_id: str) -> dict:
    require_token(request)
    body = await request.json()
    label = str(body.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{RUNNER_URL.rstrip('/')}/codex/accounts/{account_id}/label",
            json={"label": label},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.post("/api/codex/accounts/{account_id}/login")
async def start_codex_account_login(request: Request, account_id: str) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(f"{RUNNER_URL.rstrip('/')}/codex/accounts/{account_id}/login")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.get("/api/codex/accounts/{account_id}/login/{login_session_id}")
async def get_codex_account_login(request: Request, account_id: str, login_session_id: str) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{RUNNER_URL.rstrip('/')}/codex/accounts/{account_id}/login/{login_session_id}"
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.post("/api/codex/accounts/{account_id}/login/{login_session_id}/cancel")
async def cancel_codex_account_login(request: Request, account_id: str, login_session_id: str) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{RUNNER_URL.rstrip('/')}/codex/accounts/{account_id}/login/{login_session_id}/cancel"
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.get("/api/codex/models")
async def list_codex_models(request: Request) -> list[dict]:
    require_token(request)
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/codex/models")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


@app.get("/api/claude/models")
async def list_claude_models(request: Request) -> list[dict]:
    require_token(request)
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/claude/models")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()


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
    agent = normalize_agent(body.get("agent"))
    reasoning_effort = normalize_reasoning_effort(body.get("reasoning_effort"))
    codex_account = normalize_codex_account(body.get("codex_account"))
    codex_model = normalize_codex_model(body.get("codex_model"))
    prompt = build_prompt(transcript)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RUNNER_URL.rstrip('/')}/sessions",
            json={
                "prompt": prompt,
                "cwd": str(HOME_CONFIG_DIR),
                "title": transcript[:80],
                "agent": agent,
                "reasoning_effort": reasoning_effort,
                "codex_account": codex_account,
                "codex_model": codex_model,
            },
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    payload = response.json()
    if str(payload.get("status") or "") in {"starting", "running"}:
        schedule_session_monitor(str(payload.get("session_id") or ""))
    return payload


@app.get("/api/sessions")
async def list_sessions(request: Request, limit: int = 50) -> list[dict]:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/sessions", params={"limit": limit})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    return [enrich_session_info(item) for item in response.json()]


def enrich_session_info(info: dict) -> dict:
    session_id = str(info.get("session_id") or "")
    metadata = read_session_metadata(session_id)
    root_id, display_title, preview = session_display_context(session_id, metadata or info)
    enriched = dict(info)
    enriched.setdefault("latest_title", enriched.get("title"))
    enriched["display_title"] = display_title
    enriched["preview"] = preview
    enriched["root_session_id"] = root_id
    enriched.setdefault("agent", (metadata or {}).get("agent") or "codex")
    enriched.setdefault("reasoning_effort", (metadata or {}).get("reasoning_effort"))
    enriched.setdefault("codex_account", (metadata or {}).get("codex_account"))
    enriched.setdefault("codex_model", (metadata or {}).get("codex_model"))
    return enriched


def normalize_reasoning_effort(value: object) -> str | None:
    if value is None:
        return None
    effort = str(value).strip().lower()
    if not effort:
        return None
    if effort == "extra_high":
        effort = "xhigh"
    if effort not in ALLOWED_REASONING_EFFORTS:
        raise HTTPException(status_code=400, detail=f"invalid reasoning_effort: {value}")
    return effort


def normalize_agent(value: object) -> str | None:
    if value is None:
        return None
    agent = str(value).strip().lower()
    if not agent:
        return None
    if agent not in {"codex", "claude"}:
        raise HTTPException(status_code=400, detail=f"invalid agent: {value}")
    return agent


def normalize_codex_account(value: object) -> str | None:
    if value is None:
        return None
    account = str(value).strip().lower()
    if not account:
        return None
    if not re.fullmatch(r"[a-z0-9_-]+", account):
        raise HTTPException(status_code=400, detail=f"invalid codex_account: {value}")
    return account


def normalize_codex_model(value: object) -> str | None:
    if value is None:
        return None
    model = str(value).strip()
    if not model:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:/+-]+", model):
        raise HTTPException(status_code=400, detail=f"invalid codex_model: {value}")
    return model


def session_display_context(session_id: str, metadata: dict) -> tuple[str, str, str]:
    chain = session_metadata_chain(session_id, metadata)
    root_metadata = chain[-1][1] if chain else metadata
    root_id = str(root_metadata.get("session_id") or (chain[-1][0] if chain else session_id))
    root_prompt = prompt_request_preview(session_dir_for_id(root_id))
    root_title = root_prompt or clean_session_title(root_metadata.get("title"))
    current_title = clean_session_title(metadata.get("title"))
    display_title = root_title or current_title or session_id
    preview_parts = []
    if current_title and not titles_match(current_title, display_title):
        preview_parts.append(f"Latest: {current_title}")
    return root_id, display_title, "  ".join(preview_parts)


def titles_match(first: str, second: str) -> bool:
    return first == second or first.startswith(second[:80]) or second.startswith(first[:80])


def session_metadata_chain(session_id: str, metadata: dict) -> list[tuple[str, dict]]:
    chain: list[tuple[str, dict]] = [(session_id, metadata)]
    seen = {session_id}
    parent_id = metadata.get("resume_from")
    while isinstance(parent_id, str) and parent_id and parent_id not in seen:
        parent_metadata = read_session_metadata(parent_id)
        if not parent_metadata:
            break
        seen.add(parent_id)
        chain.append((parent_id, parent_metadata))
        parent_id = parent_metadata.get("resume_from")
    return chain


def read_session_metadata(session_id: str) -> dict | None:
    path = next(SESSION_ROOT.glob(f"*/{session_id}/metadata.json"), None)
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def session_dir_for_id(session_id: str) -> Path | None:
    path = next(SESSION_ROOT.glob(f"*/{session_id}/metadata.json"), None)
    return path.parent if path else None


def clean_session_title(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def prompt_request_preview(session_dir: Path | None) -> str:
    if not session_dir:
        return ""
    try:
        text = (session_dir / "prompt.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    marker = "User request transcribed from voice:"
    if marker in text:
        text = text.split(marker, 1)[1]
    return clean_session_title(text)[:240]


def session_push_state_path(session_id: str) -> Path:
    existing_dir = session_dir_for_id(session_id)
    if existing_dir:
        return existing_dir / "push_state.json"
    return SESSION_ROOT / "_push_state" / f"{session_id}.json"


def read_sent_push_events(session_id: str) -> set[str]:
    path = session_push_state_path(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return set()
    events = data.get("sent_events")
    if not isinstance(events, list):
        return set()
    return {str(event) for event in events}


def mark_push_event_sent(session_id: str, event_type: str) -> None:
    path = session_push_state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    sent_events = read_sent_push_events(session_id)
    sent_events.add(event_type)
    path.write_text(
        json.dumps({"session_id": session_id, "sent_events": sorted(sent_events)}, indent=2) + "\n",
        encoding="utf-8",
    )


def schedule_session_monitor(session_id: str) -> None:
    if not session_id:
        return
    existing = session_monitor_tasks.get(session_id)
    if existing and not existing.done():
        return
    session_monitor_tasks[session_id] = asyncio.create_task(monitor_session_for_push(session_id))


async def fetch_runner_session_info(session_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}")
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        LOGGER.warning("runner session info failed for %s: %s", session_id, response.text)
        return None
    return response.json()


async def fetch_runner_session_log_text(session_id: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}/log",
            params={"max_chars": 200000},
        )
    if response.status_code >= 400:
        LOGGER.warning("runner session log failed for %s: %s", session_id, response.text)
        return ""
    return str(response.json().get("text") or "")


async def push_session_event(session_info: dict, event_type: str) -> bool:
    session_id = str(session_info.get("session_id") or "")
    root_id = str(session_info.get("root_session_id") or session_id)
    title = str(session_info.get("display_title") or session_info.get("title") or session_id)
    if event_type == "approval_needed":
        notification_title = "Home Agent needs approval"
        body = "Tap to reopen and reconnect this session."
    elif event_type == "finished":
        notification_title = "Home Agent finished"
        body = "Tap to review the latest response."
    else:
        notification_title = "Home Agent failed"
        body = "Tap to review the failed session."
    return await send_push_notification(
        notification_title,
        body,
        {
            "session_id": session_id,
            "event_type": event_type,
            "root_session_id": root_id,
            "title": title[:200],
        },
    )


async def monitor_session_for_push(session_id: str) -> None:
    try:
        while True:
            session_info = await fetch_runner_session_info(session_id)
            if not session_info:
                return
            sent_events = read_sent_push_events(session_id)
            log_text = await fetch_runner_session_log_text(session_id)
            if "approval_needed" not in sent_events and "AWAITING_PHONE_APPROVAL:" in log_text:
                if await push_session_event(session_info, "approval_needed"):
                    mark_push_event_sent(session_id, "approval_needed")

            status = str(session_info.get("status") or "")
            if status == "exited":
                returncode = session_info.get("returncode")
                event_type = "finished" if returncode == 0 else "failed"
                if event_type not in read_sent_push_events(session_id):
                    if await push_session_event(session_info, event_type):
                        mark_push_event_sent(session_id, event_type)
                return
            if status not in {"starting", "running"}:
                return
            await asyncio.sleep(PUSH_POLL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOGGER.warning("session push monitor failed for %s: %s", session_id, exc)
    finally:
        current = session_monitor_tasks.get(session_id)
        if current is asyncio.current_task():
            session_monitor_tasks.pop(session_id, None)


async def scan_running_sessions_once() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/sessions", params={"limit": 200})
    if response.status_code >= 400:
        LOGGER.warning("runner session scan failed: %s", response.text)
        return
    for session_info in response.json():
        if str(session_info.get("status") or "") in {"starting", "running"}:
            schedule_session_monitor(str(session_info.get("session_id") or ""))


async def scan_running_sessions_loop() -> None:
    while True:
        try:
            await scan_running_sessions_once()
        except Exception as exc:
            LOGGER.warning("running session scan failed: %s", exc)
        await asyncio.sleep(PUSH_SCAN_SECONDS)


@app.on_event("startup")
async def start_push_monitoring() -> None:
    asyncio.create_task(scan_running_sessions_loop())


@app.get("/api/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    payload = response.json()
    if str(payload.get("status") or "") in {"starting", "running"}:
        schedule_session_monitor(str(payload.get("session_id") or ""))
    return payload


@app.get("/api/sessions/{session_id}/log")
async def get_session_log(request: Request, session_id: str, max_chars: int = 200000) -> dict:
    require_token(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}/log",
            params={"max_chars": max_chars},
        )
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
    agent = normalize_agent(body.get("agent"))
    reasoning_effort = normalize_reasoning_effort(body.get("reasoning_effort"))
    codex_account = normalize_codex_account(body.get("codex_account"))
    codex_model = normalize_codex_model(body.get("codex_model"))
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RUNNER_URL.rstrip('/')}/sessions/{session_id}/resume",
            json={
                "prompt": prompt,
                "title": prompt[:80],
                "agent": agent,
                "reasoning_effort": reasoning_effort,
                "codex_account": codex_account,
                "codex_model": codex_model,
            },
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)
    payload = response.json()
    schedule_session_monitor(str(payload.get("session_id") or ""))
    return payload


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
