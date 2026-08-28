import asyncio
import json
import os
import pty
import re
import select
import shutil
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
CODEX_REASONING_EFFORT = os.environ.get("HOME_AGENT_CODEX_REASONING_EFFORT", "medium")
CODEX_ACCOUNTS_ROOT = Path(
    os.environ.get("HOME_AGENT_CODEX_ACCOUNTS_ROOT", "/home/pi/cecret_lake/home-agent/codex-accounts")
)
CODEX_ACCOUNTS = os.environ.get(
    "HOME_AGENT_CODEX_ACCOUNTS",
    "account1:Account 1,account2:Account 2,account3:Account 3,machine:Machine Login",
)
CODEX_DEFAULT_ACCOUNT = os.environ.get("HOME_AGENT_CODEX_DEFAULT_ACCOUNT", "account1")
CODEX_ACCOUNT_LABELS_PATH = Path(
    os.environ.get("HOME_AGENT_CODEX_ACCOUNT_LABELS", "/home/pi/cecret_lake/home-agent/codex-account-labels.json")
)
MACHINE_CODEX_ACCOUNT_ID = "machine"
ALLOWED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
ALLOWED_AGENTS = {"codex", "claude"}
CLAUDE_BIN = (
    os.environ.get("HOME_AGENT_CLAUDE_BIN")
    or shutil.which("claude")
    or "/home/pi/.local/bin/claude"
)
CLAUDE_MODELS = os.environ.get(
    "HOME_AGENT_CLAUDE_MODELS",
    "claude-fable-5:Fable 5,claude-opus-4-8:Opus 4.8,claude-sonnet-5:Sonnet 5,claude-haiku-4-5:Haiku 4.5",
)
# How long a Claude session may sit idle waiting on background work (background
# Bash, Monitor, subagents, ScheduleWakeup) before the runner closes stdin and
# lets the CLI wind the session down. ScheduleWakeup can legitimately sleep up
# to 60 min, so keep this generous.
CLAUDE_WAIT_MAX_S = int(os.environ.get("HOME_AGENT_CLAUDE_WAIT_MAX_S", "3900"))
MAX_COMMAND_OUTPUT_CHARS = int(os.environ.get("HOME_AGENT_MAX_COMMAND_OUTPUT_CHARS", "1800"))
SHOW_SUCCESSFUL_COMMAND_OUTPUT = os.environ.get("HOME_AGENT_SHOW_COMMAND_OUTPUT", "0") == "1"
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class StartRequest(BaseModel):
    prompt: str = Field(min_length=1)
    cwd: str = str(DEFAULT_HOME_CONFIG)
    title: Optional[str] = None
    resume_from: Optional[str] = None
    codex_thread_id: Optional[str] = None
    agent: Optional[str] = None
    reasoning_effort: Optional[str] = None
    codex_account: Optional[str] = None
    codex_model: Optional[str] = None


class ResumeRequest(BaseModel):
    prompt: str = Field(min_length=1)
    title: Optional[str] = None
    agent: Optional[str] = None
    reasoning_effort: Optional[str] = None
    codex_account: Optional[str] = None
    codex_model: Optional[str] = None


class CodexAccountInfo(BaseModel):
    account_id: str
    label: str
    codex_home: str
    is_default: bool = False
    authenticated: bool = False


class CodexAccountLabelRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class CodexLoginSessionInfo(BaseModel):
    login_session_id: str
    account_id: str
    status: str
    verification_uri: Optional[str] = None
    user_code: Optional[str] = None
    output: str = ""
    returncode: Optional[int] = None
    error: Optional[str] = None


class CodexModelInfo(BaseModel):
    model_id: str
    label: str
    description: str = ""
    is_default: bool = False
    deprecated: bool = False
    replacement: Optional[str] = None


class SessionInfo(BaseModel):
    session_id: str
    status: str
    cwd: str
    log_path: str
    started_at: float
    returncode: Optional[int] = None
    title: Optional[str] = None
    display_title: Optional[str] = None
    latest_title: Optional[str] = None
    preview: Optional[str] = None
    root_session_id: Optional[str] = None
    agent: Optional[str] = "codex"
    reasoning_effort: Optional[str] = None
    codex_account: Optional[str] = None
    codex_model: Optional[str] = None
    codex_thread_id: Optional[str] = None
    resume_from: Optional[str] = None


class SessionLog(BaseModel):
    session_id: str
    text: str
    truncated: bool = False


class CodexSession:
    def __init__(self, session_id: str, request: StartRequest):
        self.session_id = session_id
        self.cwd = Path(request.cwd).resolve()
        self.title = request.title or "Voice Codex session"
        self.resume_from = request.resume_from
        self.codex_thread_id = request.codex_thread_id
        self.agent = normalize_agent(request.agent)
        self.reasoning_effort = normalize_reasoning_effort(request.reasoning_effort)
        self.codex_account = normalize_codex_account(request.codex_account)
        self.codex_home = codex_home_for_account(self.codex_account)
        if self.agent == "claude":
            self.codex_model = normalize_codex_model(request.codex_model) or default_claude_model()
        else:
            self.codex_model = resolve_codex_model(request.codex_model, self.codex_home)
        self.started_at = time.time()
        self.returncode: Optional[int] = None
        self.status = "starting"
        self.subscribers: set[asyncio.Queue[dict]] = set()
        self.loop = asyncio.get_running_loop()
        self.proc: Optional[subprocess.Popen] = None
        self.master_fd: Optional[int] = None
        self.use_pty = CODEX_MODE == "interactive"
        self.auth_required_sent = False
        # Claude bidirectional-session state. The CLI runs with
        # --input-format stream-json and stays alive until stdin closes, so
        # background work it parked on (run_in_background Bash, Monitor,
        # subagents, ScheduleWakeup) survives the end of a turn and the harness
        # wakes the model itself. We decide "done" from the task list, not exit.
        self.render_state = ClaudeRenderState()
        self.stdin_closed = False
        self.wait_token = 0
        self.waiting_since: Optional[float] = None
        self.session_dir = DEFAULT_SESSION_ROOT / time.strftime("%Y-%m-%d") / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.session_dir / "codex.log"
        self.prompt_path = self.session_dir / "prompt.txt"
        self.meta_path = self.session_dir / "metadata.json"
        self.prompt_path.write_text(request.prompt, encoding="utf-8")

    def command(self, prompt: str) -> list[str]:
        if self.agent == "claude":
            return self.claude_command(prompt)
        if CODEX_MODE == "exec":
            if self.codex_thread_id:
                cmd = [
                    CODEX_BIN,
                    "exec",
                    "resume",
                    "--json",
                    "--all",
                    "--skip-git-repo-check",
                ]
            else:
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
        if self.codex_model:
            cmd.extend(["-m", self.codex_model])
        if self.reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
        if CODEX_MODE == "exec" and self.codex_thread_id:
            cmd.append(self.codex_thread_id)
        cmd.append(prompt)
        return cmd

    def claude_command(self, prompt: str) -> list[str]:
        # The prompt is NOT passed on argv: it is written to stdin as a
        # stream-json user message so the session stays open afterwards.
        cmd = [
            CLAUDE_BIN,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if CODEX_DANGER_BYPASS:
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd.extend(["--permission-mode", "acceptEdits"])
        if self.codex_thread_id:
            cmd.extend(["--resume", self.codex_thread_id])
        if self.codex_model:
            cmd.extend(["--model", self.codex_model])
        if self.reasoning_effort:
            cmd.extend(["--effort", self.reasoning_effort])
        return cmd

    async def start(self, prompt: str) -> None:
        if not self.cwd.exists():
            raise HTTPException(status_code=400, detail=f"cwd does not exist: {self.cwd}")

        cmd = self.command(prompt)
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("NO_COLOR", "1")
        if self.agent == "claude" or uses_machine_codex_home(self.codex_account):
            env.pop("CODEX_HOME", None)
        else:
            self.codex_home.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(self.codex_home)

        metadata = {
            "session_id": self.session_id,
            "agent": self.agent,
            "title": self.title,
            "cwd": str(self.cwd),
            "started_at": self.started_at,
            "command": redact_prompt(cmd),
            "danger_bypass": CODEX_DANGER_BYPASS,
            "sandbox": CODEX_SANDBOX,
            "approvals": CODEX_APPROVALS,
            "mode": CODEX_MODE,
            "reasoning_effort": self.reasoning_effort,
            "codex_account": self.codex_account,
            "codex_model": self.codex_model,
            "codex_home": str(self.codex_home),
            "codex_thread_id": self.codex_thread_id,
            "resume_from": self.resume_from,
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
                stdin=subprocess.PIPE if self.agent == "claude" else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                close_fds=True,
                preexec_fn=os.setsid,
            )
            read_target = self._read_exec_loop
            if self.agent == "claude":
                self.write_user_message(prompt)

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
                    self.maybe_broadcast_auth_required(text)
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
                self.record_exec_metadata(line)
                self.maybe_broadcast_auth_required(line)
                if self.agent == "claude":
                    text = self.handle_claude_line(line)
                else:
                    text = format_exec_event(line)
                if not text:
                    continue
                self.loop.call_soon_threadsafe(
                    asyncio.create_task,
                    self.broadcast({"type": "output", "data": text}),
                )

    def record_exec_metadata(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        thread_id = extract_thread_id(event)
        if thread_id:
            self.codex_thread_id = thread_id
            self.update_metadata({"codex_thread_id": thread_id})

    # ---- Claude bidirectional session handling -------------------------------

    def write_user_message(self, text: str) -> None:
        """Queue a user turn on the live Claude session's stdin."""
        if not self.proc or self.proc.stdin is None or self.stdin_closed:
            raise HTTPException(status_code=409, detail="claude session is not accepting input")
        payload = {"type": "user", "message": {"role": "user", "content": text}}
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self.stdin_closed = True
            raise HTTPException(status_code=409, detail=f"claude session stdin is closed: {exc}") from exc
        # Runner-authored marker so log replays show the follow-up turn; the
        # CLI only echoes tool_result user messages, not the ones we send.
        marker = {"type": "runner", "subtype": "user_input", "text": text}
        try:
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(marker) + "\n")
        except OSError:
            pass

    def close_stdin(self) -> None:
        """End the Claude session: EOF on stdin makes the CLI wind down and exit."""
        if self.stdin_closed:
            return
        self.stdin_closed = True
        if self.proc and self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def accepts_follow_up(self) -> bool:
        return (
            self.agent == "claude"
            and self.proc is not None
            and self.proc.poll() is None
            and not self.stdin_closed
            and self.status in {"running", "waiting"}
        )

    def _set_status(self, status: str, **extra: object) -> None:
        self.status = status
        event = {"type": "status", "status": status, "session_id": self.session_id}
        event.update(extra)
        self.loop.call_soon_threadsafe(asyncio.create_task, self.broadcast(event))

    def _schedule_wait_cap(self, token: int) -> None:
        def arm() -> None:
            self.loop.call_later(CLAUDE_WAIT_MAX_S, self._wait_cap_fired, token)

        self.loop.call_soon_threadsafe(arm)

    def _wait_cap_fired(self, token: int) -> None:
        if self.status != "waiting" or self.wait_token != token:
            return
        pending = ", ".join(self.render_state.pending_tasks.values()) or "unknown"
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "output",
                    "data": (
                        f"\n[claude] still waiting on background work after {CLAUDE_WAIT_MAX_S}s "
                        f"({pending}); ending session.\n"
                    ),
                }
            )
        )
        self.close_stdin()

    def handle_claude_line(self, line: str) -> str:
        """Drive session lifecycle from the stream-json events and render output."""
        event = parse_json_line(line)
        if event is None:
            return "" if line.startswith("Reading additional input from stdin") else line
        state = self.render_state
        text = render_claude_event(event, state)
        event_type = event.get("type")
        subtype = event.get("subtype")

        if event_type == "system" and subtype == "init" and state.wake_count > 0 and self.status == "waiting":
            # A wake: the harness started a new turn on its own.
            self.waiting_since = None
            self._set_status("running")
        elif event_type == "result":
            if event.get("is_error") or not state.pending_tasks:
                # Idle with nothing in flight: the conversation is over.
                self.close_stdin()
            else:
                # Idle but the model parked on background work. Keep the session
                # alive; the CLI starts the next turn when the work completes.
                self.wait_token += 1
                self.waiting_since = time.time()
                self._set_status("waiting", tasks=list(state.pending_tasks.values()))
                self._schedule_wait_cap(self.wait_token)
        elif event_type in {"assistant", "user", "runner"} and self.status == "waiting":
            self.waiting_since = None
            self._set_status("running")
        return text

    def maybe_broadcast_auth_required(self, line: str) -> None:
        if self.agent != "codex":
            return
        if self.auth_required_sent or not is_auth_required_text(line):
            return
        self.auth_required_sent = True
        self.loop.call_soon_threadsafe(
            asyncio.create_task,
            self.broadcast(
                {
                    "type": "auth_required",
                    "account_id": self.codex_account,
                    "data": (
                        f"\n[codex auth] Login needed for Codex account "
                        f"{self.codex_account}. Open Settings and run Re-auth.\n"
                    ),
                }
            ),
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

    def update_metadata(self, updates: dict) -> None:
        try:
            metadata = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            metadata = {}
        metadata.update(updates)
        self.meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    async def send_input(self, text: str) -> None:
        if self.accepts_follow_up():
            self.write_user_message(text.rstrip("\n"))
            await self.broadcast({"type": "output", "data": "\n[input queued for claude]\n"})
            return
        if self.status != "running":
            raise HTTPException(status_code=409, detail="session is not running")
        if not self.use_pty:
            await self.broadcast(
                {
                    "type": "output",
                    "data": "\n[input queued for future sessions; this run cannot receive live steering]\n",
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
        root_id, display_title, preview = session_display_context(
            self.session_id,
            {
                "session_id": self.session_id,
                "title": self.title,
                "resume_from": self.resume_from,
                "reasoning_effort": self.reasoning_effort,
                "codex_account": self.codex_account,
                "codex_model": self.codex_model,
            },
        )
        return SessionInfo(
            session_id=self.session_id,
            status=self.status,
            cwd=str(self.cwd),
            log_path=str(self.log_path),
            started_at=self.started_at,
            returncode=self.returncode,
            title=self.title,
            display_title=display_title,
            latest_title=self.title,
            preview=preview,
            root_session_id=root_id,
            agent=self.agent,
            reasoning_effort=self.reasoning_effort,
            codex_account=self.codex_account,
            codex_model=self.codex_model,
            codex_thread_id=self.codex_thread_id,
            resume_from=self.resume_from,
        )


class CodexLoginSession:
    def __init__(self, login_session_id: str, account_id: str):
        self.login_session_id = login_session_id
        self.account_id = normalize_codex_account(account_id)
        self.codex_home = codex_home_for_account(self.account_id)
        self.status = "starting"
        self.output = ""
        self.returncode: Optional[int] = None
        self.error: Optional[str] = None
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()

    def start(self) -> None:
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("NO_COLOR", "1")
        if uses_machine_codex_home(self.account_id):
            env.pop("CODEX_HOME", None)
        else:
            self.codex_home.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(self.codex_home)

        try:
            self.proc = subprocess.Popen(
                [CODEX_BIN, "login", "--device-auth"],
                cwd=str(DEFAULT_HOME_CONFIG),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                close_fds=True,
                preexec_fn=os.setsid,
            )
        except OSError as exc:
            self.status = "failed"
            self.error = str(exc)
            return

        self.status = "running"
        threading.Thread(target=self._read_loop, name=f"codex-login-{self.login_session_id}", daemon=True).start()
        threading.Thread(target=self._wait_loop, name=f"codex-login-wait-{self.login_session_id}", daemon=True).start()

    def _read_loop(self) -> None:
        assert self.proc is not None
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = strip_ansi(line)
            with self.lock:
                self.output += line
                if len(self.output) > 12000:
                    self.output = self.output[-12000:]

    def _wait_loop(self) -> None:
        assert self.proc is not None
        self.returncode = self.proc.wait()
        with self.lock:
            if self.status == "cancelled":
                return
            self.status = "complete" if self.returncode == 0 else "failed"
            if self.returncode != 0 and not self.error:
                self.error = f"codex login exited with {self.returncode}"

    def cancel(self) -> None:
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        with self.lock:
            self.status = "cancelled"

    def info(self) -> CodexLoginSessionInfo:
        with self.lock:
            output = self.output
            status = self.status
            error = self.error
        verification_uri, user_code = parse_device_auth_output(output)
        return CodexLoginSessionInfo(
            login_session_id=self.login_session_id,
            account_id=self.account_id,
            status=status,
            verification_uri=verification_uri,
            user_code=user_code,
            output=output,
            returncode=self.returncode,
            error=error,
        )


def redact_prompt(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    return [*cmd[:-1], "<prompt>"]


def normalize_agent(value: object) -> str:
    agent = str(value or "codex").strip().lower()
    if agent not in ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail=f"invalid agent: {value}")
    return agent


def configured_claude_models() -> list[CodexModelInfo]:
    models: list[CodexModelInfo] = []
    seen: set[str] = set()
    for entry in CLAUDE_MODELS.split(","):
        raw = entry.strip()
        if not raw:
            continue
        model_id, _, label = raw.partition(":")
        normalized = normalize_codex_model(model_id)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        models.append(
            CodexModelInfo(
                model_id=normalized,
                label=label.strip() or normalized,
                is_default=not models,
            )
        )
    return models


def default_claude_model() -> Optional[str]:
    models = configured_claude_models()
    return models[0].model_id if models else None


def extract_thread_id(event: dict) -> Optional[str]:
    if event.get("type") == "thread.started":
        thread_id = event.get("thread_id")
    elif event.get("type") == "system" and event.get("subtype") == "init":
        thread_id = event.get("session_id")
    else:
        return None
    return thread_id if isinstance(thread_id, str) and thread_id else None


def normalize_reasoning_effort(value: Optional[str]) -> str:
    effort = (value or CODEX_REASONING_EFFORT or "medium").strip().lower()
    if effort == "extra_high":
        effort = "xhigh"
    if effort not in ALLOWED_REASONING_EFFORTS:
        raise HTTPException(status_code=400, detail=f"invalid reasoning_effort: {value}")
    return effort


def normalize_codex_model(value: object) -> Optional[str]:
    raw = value
    model = str(raw or "").strip()
    if not model:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:/+-]+", model):
        raise HTTPException(status_code=400, detail=f"invalid codex_model: {value}")
    return model


def resolve_codex_model(value: object, codex_home: Path) -> Optional[str]:
    requested = normalize_codex_model(value)
    if requested:
        return requested
    if CODEX_MODEL:
        return normalize_codex_model(CODEX_MODEL)
    return read_codex_config_model(codex_home) or default_codex_model_from_config_only()


def default_codex_model() -> Optional[str]:
    current = default_codex_model_from_config_only()
    if current:
        return current
    models = discover_codex_models()
    first = next((model for model in models if model.is_default), None) or (models[0] if models else None)
    return first.model_id if first else None


def read_codex_config_model(codex_home: Path) -> Optional[str]:
    config_path = codex_home / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^\s*model\s*=\s*["\']([^"\']+)["\']\s*$', text)
    return normalize_codex_model(match.group(1)) if match else None


def discover_codex_models() -> list[CodexModelInfo]:
    for source in (codex_models_from_cli, codex_models_from_cache):
        models = source()
        if models:
            return mark_default_codex_model(models)
    fallback = default_codex_model()
    if fallback:
        return [CodexModelInfo(model_id=fallback, label=fallback, is_default=True)]
    return []


def codex_models_from_cli() -> list[CodexModelInfo]:
    try:
        proc = subprocess.run(
            [CODEX_BIN, "debug", "models"],
            cwd=str(DEFAULT_HOME_CONFIG),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return parse_codex_model_catalog(payload)


def codex_models_from_cache() -> list[CodexModelInfo]:
    cache_path = default_machine_codex_home() / "models_cache.json"
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return parse_codex_model_catalog(payload)


def parse_codex_model_catalog(payload: object) -> list[CodexModelInfo]:
    if not isinstance(payload, dict):
        return []
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []
    models: list[CodexModelInfo] = []
    seen: set[str] = set()
    migrations = codex_model_migrations(default_machine_codex_home())
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        visibility = str(item.get("visibility") or "")
        if visibility and visibility != "list":
            continue
        model_id = normalize_codex_model(item.get("slug"))
        if not model_id or model_id in seen:
            continue
        upgrade = item.get("upgrade") if isinstance(item.get("upgrade"), dict) else {}
        replacement = migrations.get(model_id)
        if not replacement and upgrade:
            replacement = normalize_codex_model(upgrade.get("model") or upgrade.get("replacement"))
        status = str(item.get("status") or "").lower()
        seen.add(model_id)
        models.append(
            CodexModelInfo(
                model_id=model_id,
                label=str(item.get("display_name") or model_id),
                description=str(item.get("description") or ""),
                deprecated=model_id in migrations or bool(item.get("deprecated")) or status == "deprecated",
                replacement=replacement,
            )
        )
    models.sort(key=lambda model: model.model_id, reverse=True)
    return models


def mark_default_codex_model(models: list[CodexModelInfo]) -> list[CodexModelInfo]:
    current = default_codex_model_from_config_only()
    if not current and models:
        current = models[0].model_id
    for model in models:
        model.is_default = model.model_id == current
    return models


def default_codex_model_from_config_only() -> Optional[str]:
    if CODEX_MODEL:
        return normalize_codex_model(CODEX_MODEL)
    return read_codex_config_model(default_machine_codex_home())


def codex_model_migrations(codex_home: Path) -> dict[str, str]:
    config_path = codex_home / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = re.search(r"(?ms)^\s*\[notice\.model_migrations\]\s*(.*?)(?:^\s*\[|\Z)", text)
    if not match:
        return {}
    migrations: dict[str, str] = {}
    for old_model, new_model in re.findall(r'(?m)^\s*["\']([^"\']+)["\']\s*=\s*["\']([^"\']+)["\']\s*$', match.group(1)):
        old = normalize_codex_model(old_model)
        new = normalize_codex_model(new_model)
        if old and new:
            migrations[old] = new
    return migrations


def configured_codex_accounts() -> list[CodexAccountInfo]:
    accounts: list[CodexAccountInfo] = []
    seen: set[str] = set()
    label_overrides = load_codex_account_labels()
    for entry in CODEX_ACCOUNTS.split(","):
        raw = entry.strip()
        if not raw:
            continue
        account_id, _, label = raw.partition(":")
        account_id = normalize_account_id(account_id)
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        account_home = codex_home_for_account(account_id)
        accounts.append(
            CodexAccountInfo(
                account_id=account_id,
                label=label_overrides.get(account_id) or label.strip() or account_id,
                codex_home=str(account_home),
                is_default=account_id == normalize_account_id(CODEX_DEFAULT_ACCOUNT),
                authenticated=(account_home / "auth.json").is_file(),
            )
        )
    if not accounts:
        account_id = "default"
        account_home = codex_home_for_account(account_id)
        accounts.append(
            CodexAccountInfo(
                account_id=account_id,
                label="Default",
                codex_home=str(account_home),
                is_default=True,
                authenticated=(account_home / "auth.json").is_file(),
            )
        )
    if not any(account.is_default for account in accounts):
        accounts[0].is_default = True
    return accounts


def load_codex_account_labels() -> dict[str, str]:
    try:
        data = json.loads(CODEX_ACCOUNT_LABELS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    labels: dict[str, str] = {}
    for raw_account_id, raw_label in data.items():
        account_id = normalize_account_id(raw_account_id)
        label = normalize_account_label(raw_label)
        if account_id and label:
            labels[account_id] = label
    return labels


def save_codex_account_label(account_id: str, label: str) -> CodexAccountInfo:
    normalized_account_id = normalize_codex_account(account_id)
    normalized_label = normalize_account_label(label)
    if not normalized_label:
        raise HTTPException(status_code=400, detail="label is required")
    labels = load_codex_account_labels()
    labels[normalized_account_id] = normalized_label
    CODEX_ACCOUNT_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CODEX_ACCOUNT_LABELS_PATH.with_suffix(CODEX_ACCOUNT_LABELS_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(CODEX_ACCOUNT_LABELS_PATH)
    for account in configured_codex_accounts():
        if account.account_id == normalized_account_id:
            return account
    raise HTTPException(status_code=404, detail="account not found")


def normalize_account_id(value: object) -> str:
    account_id = str(value or "").strip().lower()
    account_id = re.sub(r"[^a-z0-9_-]+", "-", account_id).strip("-")
    return account_id


def normalize_account_label(value: object) -> str:
    label = " ".join(str(value or "").split()).strip()
    return label[:80]


def default_codex_account() -> str:
    for account in configured_codex_accounts():
        if account.is_default:
            return account.account_id
    return configured_codex_accounts()[0].account_id


def normalize_codex_account(value: object) -> str:
    requested = normalize_account_id(value) if value is not None else ""
    account_ids = {account.account_id for account in configured_codex_accounts()}
    if not requested:
        requested = default_codex_account()
    if requested not in account_ids:
        raise HTTPException(status_code=400, detail=f"invalid codex_account: {value}")
    return requested


def codex_home_for_account(account_id: str) -> Path:
    safe_id = normalize_account_id(account_id)
    if uses_machine_codex_home(safe_id):
        return default_machine_codex_home()
    if not safe_id:
        safe_id = "default"
    return CODEX_ACCOUNTS_ROOT / safe_id


def uses_machine_codex_home(account_id: str) -> bool:
    return normalize_account_id(account_id) == MACHINE_CODEX_ACCOUNT_ID


def default_machine_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def parse_device_auth_output(output: str) -> tuple[Optional[str], Optional[str]]:
    cleaned = strip_ansi(output)
    uri_match = re.search(r"https?://[^\s)>\]]+", cleaned)
    code_match = re.search(r"\b([A-Z0-9]{4}(?:-[A-Z0-9]{4,6})+)\b", cleaned)
    user_code = code_match.group(1) if code_match else None
    return (uri_match.group(0).rstrip(".,") if uri_match else None, user_code)


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def is_auth_required_text(text: str) -> bool:
    lowered = text.lower()
    auth_markers = (
        "token_invalidated",
        "refresh_token_invalidated",
        "refresh_token_reused",
        "401 unauthorized",
        "please log out and sign in again",
        "please try signing in again",
        "access token could not be refreshed",
    )
    return any(marker in lowered for marker in auth_markers)


class ClaudeRenderState:
    """Per-session context needed to render a Claude stream-json log faithfully.

    Shared by the live reader and the log replay so both show the same thing:
    repeated `init` events (wakes) render as "woke up", task notifications get
    their descriptions, and a `result` with work in flight renders as waiting.
    """

    def __init__(self) -> None:
        self.wake_count = 0
        self.pending_tasks: dict[str, str] = {}
        self.task_descriptions: dict[str, str] = {}


def parse_json_line(line: str) -> Optional[dict]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def render_claude_event(event: dict, state: ClaudeRenderState) -> str:
    event_type = event.get("type")
    subtype = event.get("subtype")
    if event_type == "runner":
        if subtype == "user_input":
            text = str(event.get("text") or "").strip()
            return f"\n> {text}\n" if text else ""
        return ""
    if event_type == "system":
        if subtype == "background_tasks_changed":
            tasks = event.get("tasks") or []
            state.pending_tasks = {
                str(t.get("task_id")): str(t.get("description") or t.get("task_type") or "task")
                for t in tasks
                if isinstance(t, dict) and t.get("task_id")
            }
            state.task_descriptions.update(state.pending_tasks)
            return ""
        if subtype == "task_notification":
            task_id = str(event.get("task_id") or "")
            desc = state.task_descriptions.get(task_id, task_id or "task")
            status = str(event.get("status") or "finished")
            return f"\n[claude] background task {status}: {desc}\n"
        if subtype == "init":
            state.wake_count += 1
            if state.wake_count > 1:
                return "\n[claude] woke up\n"
            return format_claude_event(event)
        return ""
    if event_type == "result":
        text = format_claude_event(event)
        if not event.get("is_error") and state.pending_tasks:
            pending = ", ".join(state.pending_tasks.values())
            return f"{text}[claude] waiting on background work: {pending}\n"
        return text
    return format_claude_event(event)


def format_exec_event(line: str, claude_state: Optional[ClaudeRenderState] = None) -> str:
    if line.startswith("Reading additional input from stdin"):
        return ""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return line
    if not isinstance(event, dict):
        return line

    event_type = event.get("type")
    if event_type in {"system", "assistant", "user", "result", "runner"}:
        if claude_state is not None:
            return render_claude_event(event, claude_state)
        return format_claude_event(event)
    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        if thread_id:
            return f"[codex] session started ({thread_id})\n"
        return "[codex] session started\n"
    if event_type == "turn.started":
        return "[codex] working...\n"
    if event_type == "turn.completed":
        usage = event.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        cached_input_tokens = usage.get("cached_input_tokens")
        output_tokens = usage.get("output_tokens")
        parts = []
        if input_tokens is not None:
            parts.append(f"{input_tokens} input")
        if cached_input_tokens is not None:
            parts.append(f"{cached_input_tokens} cached")
        if output_tokens is not None:
            parts.append(f"{output_tokens} output")
        if not parts:
            return "[codex] done\n"
        return f"[codex] done ({', '.join(parts)} tokens)\n"
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


def format_claude_event(event: dict) -> str:
    event_type = event.get("type")
    if event_type == "system":
        if event.get("subtype") == "init":
            session_id = event.get("session_id")
            model = event.get("model") or ""
            model_label = f" [{model}]" if model else ""
            if session_id:
                return f"[claude] session started ({session_id}){model_label}\n"
            return "[claude] session started\n"
        return ""
    if event_type == "assistant":
        message = event.get("message") or {}
        parts: list[str] = []
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"text", "thinking"}:
                text = str(block.get("text") or block.get("thinking") or "").strip()
                if text:
                    parts.append(f"\n{text}\n")
            elif block_type == "tool_use":
                parts.append(format_claude_tool_use(block))
        return "".join(parts)
    if event_type == "user":
        message = event.get("message") or {}
        parts = []
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                parts.append(format_claude_tool_result(block))
        return "".join(parts)
    if event_type == "result":
        if event.get("is_error"):
            reason = event.get("result") or event.get("error") or "run failed"
            return f"[claude failed] {reason}\n"
        details: list[str] = []
        duration_ms = event.get("duration_ms")
        if isinstance(duration_ms, (int, float)) and duration_ms > 0:
            details.append(f"{duration_ms / 1000:.0f}s")
        usage = event.get("usage") or {}
        output_tokens = usage.get("output_tokens")
        if output_tokens is not None:
            details.append(f"{output_tokens} output tokens")
        cost = event.get("total_cost_usd")
        if isinstance(cost, (int, float)) and cost > 0:
            details.append(f"${cost:.2f}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"[claude] done{suffix}\n"
    return ""


def format_claude_tool_use(block: dict) -> str:
    name = str(block.get("name") or "tool")
    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
    if name == "Bash":
        command = str(tool_input.get("command") or "")
        return f"\n$ {command}\n" if command else "\n[claude] running command\n"
    target = ""
    for key in ("file_path", "path", "pattern", "query", "url", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            target = value
            break
    label = f" {target}" if target else ""
    return f"[{name}{label}]\n"


def format_claude_tool_result(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        text = "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    else:
        text = str(content or "")
    text = text.rstrip()
    if block.get("is_error"):
        snippet = text[:MAX_COMMAND_OUTPUT_CHARS].rstrip()
        return f"[tool error]\n{snippet}\n" if snippet else "[tool error]\n"
    if not text:
        return ""
    if SHOW_SUCCESSFUL_COMMAND_OUTPUT and len(text) <= MAX_COMMAND_OUTPUT_CHARS:
        return f"{text}\n"
    line_count = len(text.splitlines())
    return f"[tool output hidden: {line_count} lines, {len(text)} chars]\n"


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
        text = format_command_result(command, str(output), exit_code)
        return f"{text.rstrip()}\n" if text else ""
    if item_type == "reasoning":
        text = item.get("text") or item.get("summary") or ""
        return f"{text.strip()}\n" if isinstance(text, str) and text.strip() else ""
    return ""


def format_command_result(command: str, output: str, exit_code: Optional[int]) -> str:
    output = output.rstrip()
    if exit_code not in (None, 0):
        chunks = [f"[exit {exit_code}]"]
        if output:
            chunks.append(format_command_output(command, output, force_show=True))
        return "\n".join(chunks)

    read_targets = read_command_targets(command)
    if read_targets:
        return summarize_hidden_output("read", read_targets, output)

    action = command_action(command)
    if not output:
        return f"[{action} completed]"

    if SHOW_SUCCESSFUL_COMMAND_OUTPUT:
        return format_command_output(command, output, force_show=False)

    line_count = len(output.splitlines())
    return f"[{action} output hidden: {line_count} lines, {len(output)} chars]"


def format_command_output(command: str, output: str, force_show: bool = False) -> str:
    output = output.rstrip()
    if not output:
        return ""

    read_targets = read_command_targets(command)
    if read_targets:
        return summarize_hidden_output("read", read_targets, output)

    if not force_show and len(output) <= MAX_COMMAND_OUTPUT_CHARS:
        return output

    line_count = len(output.splitlines())
    return f"{output[:MAX_COMMAND_OUTPUT_CHARS].rstrip()}\n[output truncated: {line_count} lines, {len(output)} chars]"


def summarize_hidden_output(action: str, targets: list[str], output: str) -> str:
    line_count = len(output.splitlines())
    target_label = ", ".join(targets[:3])
    if len(targets) > 3:
        target_label += f", +{len(targets) - 3} more"
    return f"[{action} {target_label}; {line_count} lines hidden]"


def command_action(command: str) -> str:
    lowered = command.lower()
    if re.search(r"(^|[\s;&|\"'])rg\s+", lowered):
        return "search"
    if re.search(r"(^|[\s;&|\"'])jq\s+", lowered):
        return "query"
    if re.search(r"(^|[\s;&|\"'])curl\s+", lowered):
        return "request"
    if re.search(r"(^|[\s;&|\"'])docker\s+logs\s+", lowered):
        return "logs"
    if re.search(r"(^|[\s;&|\"'])ls(\s|$)", lowered):
        return "list"
    return "command"


def read_command_targets(command: str) -> list[str]:
    targets: list[str] = []
    patterns = [
        r"(?:^|[\s;&|\"'])cat\s+((?:[^\s|;&]+(?:\s+|$))+)",
        r"(?:^|[\s;&|\"'])sed\s+-n\s+(?:['\"][^'\"]+['\"]|[^\s]+)\s+([^\s|;&]+)",
        r"(?:^|[\s;&|\"'])head\s+(?:-[^\s]+\s+)?([^\s|;&]+)",
        r"(?:^|[\s;&|\"'])tail\s+(?:-[^\s]+\s+)?([^\s|;&]+)",
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


def session_info_from_metadata(path: Path) -> Optional[SessionInfo]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    session_id = str(metadata.get("session_id") or path.parent.name)
    log_path = str(path.parent / "codex.log")
    title = metadata.get("title")
    agent = str(metadata.get("agent") or "codex")
    root_id, display_title, preview = session_display_context(session_id, metadata)
    return SessionInfo(
        session_id=session_id,
        status="archived",
        cwd=str(metadata.get("cwd") or DEFAULT_HOME_CONFIG),
        log_path=log_path,
        started_at=float(metadata.get("started_at") or path.stat().st_mtime),
        returncode=None,
        title=title,
        display_title=display_title,
        latest_title=title,
        preview=preview,
        root_session_id=root_id,
        agent=agent,
        reasoning_effort=metadata.get("reasoning_effort") or CODEX_REASONING_EFFORT,
        codex_account=metadata.get("codex_account") or default_codex_account(),
        codex_model=metadata.get("codex_model")
        or (default_claude_model() if agent == "claude" else default_codex_model()),
        codex_thread_id=metadata.get("codex_thread_id") or find_codex_thread_id(session_id),
        resume_from=metadata.get("resume_from"),
    )


def session_display_context(session_id: str, metadata: dict) -> tuple[str, str, str]:
    chain = session_metadata_chain(session_id, metadata)
    root_metadata = chain[-1][1] if chain else metadata
    root_id = str(root_metadata.get("session_id") or (chain[-1][0] if chain else session_id))
    root_prompt = prompt_request_preview(session_dir_for_id(root_id))
    root_title = root_prompt or clean_session_title(root_metadata.get("title"))

    current_title = clean_session_title(metadata.get("title"))
    display_title = root_title or current_title or session_id
    preview_parts = []
    if root_prompt and root_prompt != display_title:
        preview_parts.append(root_prompt)
    if current_title and not titles_match(current_title, display_title):
        preview_parts.append(f"Latest: {current_title}")
    return root_id, display_title, "  ".join(preview_parts[:2])


def titles_match(first: str, second: str) -> bool:
    return first == second or first.startswith(second[:80]) or second.startswith(first[:80])


def session_metadata_chain(session_id: str, metadata: dict) -> list[tuple[str, dict]]:
    chain: list[tuple[str, dict]] = [(session_id, metadata)]
    seen = {session_id}
    parent_id = metadata.get("resume_from")
    while isinstance(parent_id, str) and parent_id and parent_id not in seen:
        parent_path = next(DEFAULT_SESSION_ROOT.glob(f"*/{parent_id}/metadata.json"), None)
        if not parent_path:
            break
        try:
            parent_metadata = json.loads(parent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            break
        seen.add(parent_id)
        chain.append((parent_id, parent_metadata))
        parent_id = parent_metadata.get("resume_from")
    return chain


def session_dir_for_id(session_id: str) -> Optional[Path]:
    meta_path = next(DEFAULT_SESSION_ROOT.glob(f"*/{session_id}/metadata.json"), None)
    return meta_path.parent if meta_path else None


def clean_session_title(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def prompt_request_preview(session_dir: Optional[Path]) -> str:
    if not session_dir:
        return ""
    prompt_path = session_dir / "prompt.txt"
    try:
        text = prompt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    marker = "User request transcribed from voice:"
    if marker in text:
        text = text.split(marker, 1)[1]
    return clean_session_title(text)[:240]


def find_session_info(session_id: str) -> Optional[SessionInfo]:
    for path in DEFAULT_SESSION_ROOT.glob(f"*/{session_id}/metadata.json"):
        return session_info_from_metadata(path)
    return None


def find_codex_thread_id(session_id: str) -> Optional[str]:
    log_path = next(DEFAULT_SESSION_ROOT.glob(f"*/{session_id}/codex.log"), None)
    if not log_path:
        return None
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as log:
            for line in log:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                thread_id = extract_thread_id(event)
                if thread_id:
                    return thread_id
    except FileNotFoundError:
        return None
    return None


def find_session_log_path(session_id: str) -> Optional[Path]:
    session = sessions.get(session_id)
    if session:
        return session.log_path
    info = find_session_info(session_id)
    if info:
        return Path(info.log_path)
    return None


def format_session_log(path: Path, max_chars: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", False

    formatted_parts: list[str] = []
    parsed_any = False
    claude_state = ClaudeRenderState()
    for line in raw.splitlines(keepends=True):
        text = format_exec_event(line, claude_state)
        if text:
            formatted_parts.append(text)
            parsed_any = True

    text = "".join(formatted_parts) if parsed_any else raw
    if max_chars > 0 and len(text) > max_chars:
        return "[history truncated]\n" + text[-max_chars:], True
    return text, False


app = FastAPI(title=APP_NAME)
sessions: dict[str, CodexSession] = {}
login_sessions: dict[str, CodexLoginSession] = {}


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": APP_NAME}


@app.get("/codex/accounts", response_model=list[CodexAccountInfo])
async def list_codex_accounts() -> list[CodexAccountInfo]:
    return configured_codex_accounts()


@app.get("/codex/models", response_model=list[CodexModelInfo])
async def list_codex_models() -> list[CodexModelInfo]:
    return discover_codex_models()


@app.get("/claude/models", response_model=list[CodexModelInfo])
async def list_claude_models() -> list[CodexModelInfo]:
    return configured_claude_models()


@app.post("/codex/accounts/{account_id}/label", response_model=CodexAccountInfo)
async def update_codex_account_label(account_id: str, request: CodexAccountLabelRequest) -> CodexAccountInfo:
    return save_codex_account_label(account_id, request.label)


@app.post("/codex/accounts/{account_id}/login", response_model=CodexLoginSessionInfo)
async def start_codex_account_login(account_id: str) -> CodexLoginSessionInfo:
    normalized_account_id = normalize_codex_account(account_id)
    login_session_id = uuid.uuid4().hex[:12]
    login_session = CodexLoginSession(login_session_id, normalized_account_id)
    login_sessions[login_session_id] = login_session
    login_session.start()
    return login_session.info()


@app.get("/codex/accounts/{account_id}/login/{login_session_id}", response_model=CodexLoginSessionInfo)
async def get_codex_account_login(account_id: str, login_session_id: str) -> CodexLoginSessionInfo:
    normalized_account_id = normalize_codex_account(account_id)
    login_session = login_sessions.get(login_session_id)
    if not login_session or login_session.account_id != normalized_account_id:
        raise HTTPException(status_code=404, detail="unknown login session")
    return login_session.info()


@app.post("/codex/accounts/{account_id}/login/{login_session_id}/cancel", response_model=CodexLoginSessionInfo)
async def cancel_codex_account_login(account_id: str, login_session_id: str) -> CodexLoginSessionInfo:
    normalized_account_id = normalize_codex_account(account_id)
    login_session = login_sessions.get(login_session_id)
    if not login_session or login_session.account_id != normalized_account_id:
        raise HTTPException(status_code=404, detail="unknown login session")
    login_session.cancel()
    return login_session.info()


@app.post("/sessions", response_model=SessionInfo)
async def start_session(request: StartRequest) -> SessionInfo:
    session_id = uuid.uuid4().hex[:12]
    try:
        session = CodexSession(session_id, request)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"session storage is not writable: {exc}",
        ) from exc
    sessions[session_id] = session
    await session.start(request.prompt)
    return session.info()


@app.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(limit: int = 50) -> list[SessionInfo]:
    records: list[SessionInfo] = []
    for path in sorted(DEFAULT_SESSION_ROOT.glob("*/*/metadata.json"), reverse=True):
        info = session_info_from_metadata(path)
        if info:
            live = sessions.get(info.session_id)
            records.append(live.info() if live else info)
        if len(records) >= limit:
            break
    return records


@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str) -> SessionInfo:
    session = sessions.get(session_id)
    if session:
        return session.info()
    info = find_session_info(session_id)
    if not info:
        raise HTTPException(status_code=404, detail="unknown session")
    return info


@app.get("/sessions/{session_id}/log", response_model=SessionLog)
async def get_session_log(session_id: str, max_chars: int = 200000) -> SessionLog:
    path = find_session_log_path(session_id)
    if not path:
        raise HTTPException(status_code=404, detail="unknown session")
    text, truncated = format_session_log(path, max_chars=max_chars)
    return SessionLog(session_id=session_id, text=text, truncated=truncated)


@app.post("/sessions/{session_id}/resume", response_model=SessionInfo)
async def resume_session(session_id: str, request: ResumeRequest) -> SessionInfo:
    source = sessions.get(session_id)
    source_info = source.info() if source else find_session_info(session_id)
    if not source_info:
        raise HTTPException(status_code=404, detail="unknown session")

    if source is not None and source.accepts_follow_up():
        # The Claude process is still alive (running, or parked on background
        # work). Two processes must never share one session id, so the
        # follow-up goes to the live process's stdin and the same session
        # continues instead of spawning `--resume`.
        source.write_user_message(request.prompt)
        await source.broadcast(
            {"type": "output", "data": f"\n[resume] follow-up queued on live session: {request.prompt[:120]}\n"}
        )
        return source.info()

    thread_id = source_info.codex_thread_id or find_codex_thread_id(session_id)
    if not thread_id:
        raise HTTPException(status_code=409, detail="session has no agent thread id yet")

    # A conversation stays on the agent that started it: thread ids are not
    # portable between Codex and Claude, so the resume ignores the client's
    # current agent toggle (and any model that belongs to the other agent).
    source_agent = normalize_agent(source_info.agent)
    requested_agent = normalize_agent(request.agent) if request.agent else source_agent
    requested_model = request.codex_model if requested_agent == source_agent else None
    child_request = StartRequest(
        prompt=request.prompt,
        cwd=source_info.cwd,
        title=request.title or f"Resume: {source_info.title or session_id}",
        resume_from=session_id,
        codex_thread_id=thread_id,
        agent=source_agent,
        reasoning_effort=request.reasoning_effort or source_info.reasoning_effort,
        codex_account=request.codex_account or source_info.codex_account,
        codex_model=requested_model or source_info.codex_model,
    )
    child_id = uuid.uuid4().hex[:12]
    try:
        child = CodexSession(child_id, child_request)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"session storage is not writable: {exc}",
        ) from exc
    sessions[child_id] = child
    await child.start(request.prompt)
    return child.info()


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
