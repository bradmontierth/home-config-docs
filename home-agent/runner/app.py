import asyncio
import json
import os
import pty
import re
import select
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field


APP_NAME = "home-agent-runner"
DEFAULT_HOME_CONFIG = Path(os.environ.get("HOME_AGENT_HOME_CONFIG", "/home/pi/home_config"))
DEFAULT_SESSION_ROOT = Path(
    os.environ.get("HOME_AGENT_SESSION_ROOT", str(DEFAULT_HOME_CONFIG / "home-agent" / "sessions"))
)
CODEX_BIN = os.environ.get("HOME_AGENT_CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("HOME_AGENT_CODEX_MODEL")
CODEX_SANDBOX = os.environ.get("HOME_AGENT_CODEX_SANDBOX", "workspace-write")
CODEX_APPROVALS = os.environ.get("HOME_AGENT_CODEX_APPROVALS", "never")
CODEX_DANGER_BYPASS = os.environ.get("HOME_AGENT_CODEX_DANGER_BYPASS", "0") == "1"
CODEX_MODE = os.environ.get("HOME_AGENT_CODEX_MODE", "exec")
MAX_COMMAND_OUTPUT_CHARS = int(os.environ.get("HOME_AGENT_MAX_COMMAND_OUTPUT_CHARS", "1800"))


class StartRequest(BaseModel):
    prompt: str = Field(min_length=1)
    cwd: str = str(DEFAULT_HOME_CONFIG)
    title: Optional[str] = None


class SessionInfo(BaseModel):
    session_id: str
    status: str
    cwd: str
    log_path: str
    started_at: float
    returncode: Optional[int] = None


class CodexSession:
    def __init__(self, session_id: str, request: StartRequest):
        self.session_id = session_id
        self.cwd = Path(request.cwd).resolve()
        self.title = request.title or "Voice Codex session"
        self.started_at = time.time()
        self.returncode: Optional[int] = None
        self.status = "starting"
        self.subscribers: set[asyncio.Queue[dict]] = set()
        self.loop = asyncio.get_running_loop()
        self.proc: Optional[subprocess.Popen] = None
        self.master_fd: Optional[int] = None
        self.use_pty = CODEX_MODE == "interactive"
        self.session_dir = DEFAULT_SESSION_ROOT / time.strftime("%Y-%m-%d") / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.session_dir / "codex.log"
        self.prompt_path = self.session_dir / "prompt.txt"
        self.meta_path = self.session_dir / "metadata.json"
        self.prompt_path.write_text(request.prompt, encoding="utf-8")

    def command(self, prompt: str) -> list[str]:
        if CODEX_MODE == "exec":
            cmd = [
                CODEX_BIN,
                "exec",
                "--json",
                "--color",
                "never",
                "-C",
                str(self.cwd),
                "--skip-git-repo-check",
            ]
        else:
            cmd = [CODEX_BIN, "--no-alt-screen", "-C", str(self.cwd)]
        if CODEX_DANGER_BYPASS:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.extend(["-s", CODEX_SANDBOX])
            if CODEX_MODE != "exec":
                cmd.extend(["-a", CODEX_APPROVALS])
        if CODEX_MODEL:
            cmd.extend(["-m", CODEX_MODEL])
        cmd.append(prompt)
        return cmd

    async def start(self, prompt: str) -> None:
        if not self.cwd.exists():
            raise HTTPException(status_code=400, detail=f"cwd does not exist: {self.cwd}")

        cmd = self.command(prompt)
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("NO_COLOR", "1")

        metadata = {
            "session_id": self.session_id,
            "title": self.title,
            "cwd": str(self.cwd),
            "started_at": self.started_at,
            "command": redact_prompt(cmd),
            "danger_bypass": CODEX_DANGER_BYPASS,
            "sandbox": CODEX_SANDBOX,
            "approvals": CODEX_APPROVALS,
            "mode": CODEX_MODE,
        }
        self.meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

        if self.use_pty:
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=os.setsid,
            )
            os.close(slave_fd)
            read_target = self._read_pty_loop
        else:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                close_fds=True,
                preexec_fn=os.setsid,
            )
            read_target = self._read_exec_loop

        self.status = "running"
        threading.Thread(target=read_target, name=f"reader-{self.session_id}", daemon=True).start()
        threading.Thread(target=self._wait_loop, name=f"wait-{self.session_id}", daemon=True).start()
        await self.broadcast({"type": "status", "status": self.status, "session_id": self.session_id})

    def _read_pty_loop(self) -> None:
        assert self.master_fd is not None
        with self.log_path.open("ab") as log:
            while True:
                try:
                    ready, _, _ = select.select([self.master_fd], [], [], 0.2)
                    if not ready:
                        if self.proc and self.proc.poll() is not None:
                            break
                        continue
                    data = os.read(self.master_fd, 4096)
                    if not data:
                        break
                    log.write(data)
                    log.flush()
                    text = data.decode("utf-8", errors="replace")
                    self.loop.call_soon_threadsafe(
                        asyncio.create_task,
                        self.broadcast({"type": "output", "data": text}),
                    )
                except OSError:
                    break

    def _read_exec_loop(self) -> None:
        assert self.proc is not None
        assert self.proc.stdout is not None
        with self.log_path.open("a", encoding="utf-8") as log:
            for line in self.proc.stdout:
                log.write(line)
                log.flush()
                text = format_exec_event(line)
                if not text:
                    continue
                self.loop.call_soon_threadsafe(
                    asyncio.create_task,
                    self.broadcast({"type": "output", "data": text}),
                )

    def _wait_loop(self) -> None:
        assert self.proc is not None
        self.returncode = self.proc.wait()
        self.status = "exited"
        self.loop.call_soon_threadsafe(
            asyncio.create_task,
            self.broadcast(
                {"type": "status", "status": self.status, "returncode": self.returncode}
            ),
        )
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

    async def broadcast(self, event: dict) -> None:
        stale: list[asyncio.Queue[dict]] = []
        for queue in self.subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.subscribers.discard(queue)

    async def send_input(self, text: str) -> None:
        if self.status != "running":
            raise HTTPException(status_code=409, detail="session is not running")
        if not self.use_pty:
            await self.broadcast(
                {
                    "type": "output",
                    "data": "\n[input queued for future sessions; this Codex exec run cannot receive live steering]\n",
                }
            )
            return
        if self.master_fd is None:
            raise HTTPException(status_code=409, detail="session is not running")
        os.write(self.master_fd, text.encode("utf-8"))

    async def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        await asyncio.sleep(2)
        if self.proc.poll() is None:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)

    def info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            status=self.status,
            cwd=str(self.cwd),
            log_path=str(self.log_path),
            started_at=self.started_at,
            returncode=self.returncode,
        )


def redact_prompt(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    return [*cmd[:-1], "<prompt>"]


def format_exec_event(line: str) -> str:
    if line.startswith("Reading additional input from stdin"):
        return ""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return line

    event_type = event.get("type")
    if event_type == "thread.started":
        return "[codex] session started\n"
    if event_type == "turn.started":
        return "[codex] working...\n"
    if event_type == "turn.completed":
        usage = event.get("usage") or {}
        output_tokens = usage.get("output_tokens")
        if output_tokens is None:
            return "[codex] done\n"
        return f"[codex] done ({output_tokens} output tokens)\n"
    if event_type == "error":
        message = event.get("message") or "unknown error"
        return f"[codex error] {message}\n"
    if event_type == "turn.failed":
        error = event.get("error") or {}
        message = error.get("message") or "turn failed"
        return f"[codex failed] {message}\n"
    if event_type == "item.started":
        item = event.get("item") or {}
        return format_item_started(item)
    if event_type == "item.completed":
        item = event.get("item") or {}
        return format_item_completed(item)
    return ""


def format_item_started(item: dict) -> str:
    item_type = item.get("type")
    if item_type == "command_execution":
        command = item.get("command") or item.get("cmd") or ""
        return f"\n$ {command}\n" if command else "\n[codex] running command\n"
    if item_type == "reasoning":
        return "\n[codex] reasoning\n"
    return ""


def format_item_completed(item: dict) -> str:
    item_type = item.get("type")
    if item_type == "agent_message":
        text = item.get("text") or ""
        return f"\n{text.strip()}\n" if text.strip() else ""
    if item_type == "command_execution":
        command = item.get("command") or item.get("cmd") or ""
        output = item.get("aggregated_output") or item.get("output") or item.get("stdout") or ""
        exit_code = item.get("exit_code")
        chunks = []
        if output:
            chunks.append(format_command_output(command, str(output)))
        if exit_code not in (None, 0):
            chunks.append(f"[exit {exit_code}]")
        return "\n".join(chunks).rstrip() + "\n" if chunks else ""
    if item_type == "reasoning":
        text = item.get("text") or item.get("summary") or ""
        return f"{text.strip()}\n" if isinstance(text, str) and text.strip() else ""
    return ""


def format_command_output(command: str, output: str) -> str:
    output = output.rstrip()
    if not output:
        return ""

    read_targets = read_command_targets(command)
    if read_targets:
        line_count = len(output.splitlines())
        target_label = ", ".join(read_targets[:3])
        if len(read_targets) > 3:
            target_label += f", +{len(read_targets) - 3} more"
        return f"[read {target_label}; {line_count} lines hidden]"

    if len(output) <= MAX_COMMAND_OUTPUT_CHARS:
        return output

    line_count = len(output.splitlines())
    return f"{output[:MAX_COMMAND_OUTPUT_CHARS].rstrip()}\n[output truncated: {line_count} lines, {len(output)} chars]"


def read_command_targets(command: str) -> list[str]:
    targets: list[str] = []
    patterns = [
        r"(?:^|[\s;&|])cat\s+((?:[^\s|;&]+(?:\s+|$))+)",
        r"(?:^|[\s;&|])sed\s+-n\s+(?:['\"][^'\"]+['\"]|[^\s]+)\s+([^\s|;&]+)",
        r"(?:^|[\s;&|])head\s+(?:-[^\s]+\s+)?([^\s|;&]+)",
        r"(?:^|[\s;&|])tail\s+(?:-[^\s]+\s+)?([^\s|;&]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, command):
            raw = match.group(1)
            for token in re.split(r"\s+", raw.strip()):
                token = token.strip("'\"")
                if token and not token.startswith("-") and looks_like_file_target(token):
                    targets.append(token)
    return dedupe_preserving_order(targets)


def looks_like_file_target(token: str) -> bool:
    if token in {"/dev/null", "-"}:
        return False
    return bool(re.search(r"[/.\w-]+\.(md|txt|yaml|yml|json|toml|ini|conf|service|py|kt|kts|js|ts|css|html|sh)$", token))


def dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


app = FastAPI(title=APP_NAME)
sessions: dict[str, CodexSession] = {}


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": APP_NAME}


@app.post("/sessions", response_model=SessionInfo)
async def start_session(request: StartRequest) -> SessionInfo:
    session_id = uuid.uuid4().hex[:12]
    session = CodexSession(session_id, request)
    sessions[session_id] = session
    await session.start(request.prompt)
    return session.info()


@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str) -> SessionInfo:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="unknown session")
    return session.info()


@app.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str) -> dict:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="unknown session")
    await session.stop()
    return {"ok": True}


@app.websocket("/sessions/{session_id}/ws")
async def session_ws(websocket: WebSocket, session_id: str) -> None:
    session = sessions.get(session_id)
    if not session:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)
    session.subscribers.add(queue)
    await websocket.send_json({"type": "status", "status": session.status, "session_id": session_id})

    async def outbound() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    async def inbound() -> None:
        while True:
            event = await websocket.receive_json()
            event_type = event.get("type")
            if event_type == "input":
                text = str(event.get("data", ""))
                if not text.endswith("\n"):
                    text += "\n"
                try:
                    await session.send_input(text)
                except HTTPException as exc:
                    await websocket.send_json({"type": "output", "data": f"\n[input ignored: {exc.detail}]\n"})
            elif event_type == "raw":
                try:
                    await session.send_input(str(event.get("data", "")))
                except HTTPException as exc:
                    await websocket.send_json({"type": "output", "data": f"\n[input ignored: {exc.detail}]\n"})
            elif event_type == "stop":
                await session.stop()

    outbound_task = asyncio.create_task(outbound())
    inbound_task = asyncio.create_task(inbound())
    try:
        done, pending = await asyncio.wait(
            {outbound_task, inbound_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        session.subscribers.discard(queue)
