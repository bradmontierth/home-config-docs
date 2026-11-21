#!/usr/bin/env python3
"""Claude knowledge service for the voice assistant's `ask` intent (2026-11-20).

Replaces OpenRouter/GPT-5.4. Answers come from the `claude` CLI on the
household Claude subscription, the same way trainer_max and home-agent use it:
`claude -p --input-format stream-json --output-format stream-json`.

Why a host service: the orchestrator runs in Docker and has no CLI or login;
this runs as the pi user (systemd user unit) and listens on 127.0.0.1 only
(the orchestrator is host-networked).

    POST /ask   {"system": "<static system prompt>", "prompt": "<question>"}
        -> NDJSON stream, one object per line:
           {"t": "meta",  "model": ..., "warm": bool}
           {"t": "delta", "text": "..."}            (answer text as it arrives)
           {"t": "done",  "model": ..., "searches": n, "ms": ..., ...}
           {"t": "error", "error": "..."}           (nothing usable was produced)
    GET  /health

Speed: one process per request, but pre-spawned. A SPARE process sits idle
with the system prompt already loaded; a request takes it and a new spare is
started at once. The spare needs the system prompt in advance, which is why
the prompt must be static (date, room and recent Q&A travel in the question).
The last system prompt seen is kept on disk so a restart warms up again.

Models: MODELS is tried in order. The next one is used only when a model
fails BEFORE any answer text (rate limit, error, process death, or nothing
within FIRST_TEXT_TIMEOUT_S); once text has streamed there is no retry.

Isolation per process: no settings sources (no CLAUDE.md, memory, hooks), no
MCP servers, WebSearch as the only tool, an empty working directory, no
session persistence.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("knowledge")

HOST = os.getenv("KNOWLEDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("KNOWLEDGE_PORT", "8816"))
CLAUDE = os.getenv("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
# model:effort, tried in order (benchmark 2026-11-20: Fable 5.2 low was the
# most accurate and as fast as anything; Opus 5.5 low is the fallback).
MODELS = [tuple(m.split(":")) for m in os.getenv(
    "KNOWLEDGE_MODELS", "claude-fable-5-2:low,claude-opus-5-5:low").split(",")]
STATE_DIR = Path(os.getenv("KNOWLEDGE_STATE_DIR",
                           str(Path.home() / ".cache/claude-knowledge")))
SPARE_MAX_AGE_S = float(os.getenv("KNOWLEDGE_SPARE_MAX_AGE_S", "1200"))
FIRST_TEXT_TIMEOUT_S = float(os.getenv("KNOWLEDGE_FIRST_TEXT_TIMEOUT_S", "45"))
TOTAL_TIMEOUT_S = float(os.getenv("KNOWLEDGE_TOTAL_TIMEOUT_S", "120"))

CWD = STATE_DIR / "cwd"
PROMPT_FILE = STATE_DIR / "system-prompt.txt"


class Proc:
    """One `claude -p` stream-json process, good for exactly one question."""

    def __init__(self, model: str, effort: str, system: str):
        self.model, self.effort, self.system = model, effort, system
        self.born = time.monotonic()
        cmd = [CLAUDE, "-p", "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--model", model, "--effort", effort,
               "--system-prompt", system, "--setting-sources", "",
               "--strict-mcp-config", "--tools", "WebSearch",
               "--allowedTools", "WebSearch", "--no-session-persistence"]
        self.proc = subprocess.Popen(cmd, cwd=str(CWD), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.q: queue.Queue = queue.Queue()
        self.err: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.q.put(line)
        self.q.put(None)

    def _pump_err(self):
        for line in self.proc.stderr:
            self.err.append(line)
            del self.err[:-20]

    def alive(self) -> bool:
        return self.proc.poll() is None

    def age(self) -> float:
        return time.monotonic() - self.born

    def kill(self):
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass

    def run(self, prompt: str):
        """Yield ("delta", text) ... then one ("done", stats) or ("error", msg)."""
        t0 = time.monotonic()
        try:
            self.proc.stdin.write(json.dumps(
                {"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
            self.proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            yield "error", f"write failed: {exc}"
            return
        searches, got_text, first_ms = 0, False, None
        while True:
            waited = time.monotonic() - t0
            limit = TOTAL_TIMEOUT_S if got_text else FIRST_TEXT_TIMEOUT_S
            if waited >= limit:
                yield "error", f"timeout after {round(waited)}s (text={got_text})"
                return
            try:
                line = self.q.get(timeout=min(1.0, limit - waited))
            except queue.Empty:
                continue
            if line is None:
                yield "error", "process exited: " + "".join(self.err)[-300:].strip()
                return
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            if kind == "stream_event":
                ev = e.get("event") or {}
                d = ev.get("delta") or {}
                if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                    if not got_text:
                        got_text = True
                        first_ms = round((time.monotonic() - t0) * 1000)
                    yield "delta", d.get("text", "")
            elif kind == "assistant":
                for b in (e.get("message") or {}).get("content") or []:
                    if b.get("type") in ("tool_use", "server_tool_use") and \
                            "search" in (b.get("name") or "").lower():
                        searches += 1
            elif kind == "result":
                if e.get("is_error") or e.get("subtype") != "success":
                    yield "error", f"{e.get('subtype')}: {str(e.get('result'))[:200]}"
                    return
                if not got_text:
                    # A result with no streamed text (e.g. a synthetic limit
                    # notice) is not an answer.
                    yield "error", f"no answer text: {str(e.get('result'))[:200]}"
                    return
                usage = e.get("usage") or {}
                yield "done", {
                    "model": self.model, "effort": self.effort, "searches": searches,
                    "first_text_ms": first_ms,
                    "ms": round((time.monotonic() - t0) * 1000),
                    "tokens": (usage.get("input_tokens") or 0)
                              + (usage.get("output_tokens") or 0)
                              + (usage.get("cache_read_input_tokens") or 0)
                              + (usage.get("cache_creation_input_tokens") or 0),
                    # What this would cost on the API; the subscription pays.
                    "notional_cost_usd": e.get("total_cost_usd"),
                }
                return


class Pool:
    """One warm spare of the primary model, re-armed after every take."""

    def __init__(self):
        self.lock = threading.Lock()
        self.spare: Proc | None = None
        self.system = PROMPT_FILE.read_text() if PROMPT_FILE.exists() else None
        self.stats = {"requests": 0, "warm_hits": 0, "fallbacks": 0, "errors": 0,
                      "last": None}
        if self.system:
            self._arm()
        threading.Thread(target=self._keeper, daemon=True).start()

    def _arm(self):
        """Start a fresh spare (lock held or startup). Never raises."""
        try:
            model, effort = MODELS[0]
            self.spare = Proc(model, effort, self.system)
            log.info("spare armed: %s %s", model, effort)
        except Exception as exc:  # noqa: BLE001
            self.spare = None
            log.warning("spare spawn failed: %s", exc)

    def _keeper(self):
        while True:
            time.sleep(30)
            with self.lock:
                if not self.system:
                    continue
                s = self.spare
                if s is None or not s.alive() or s.age() > SPARE_MAX_AGE_S:
                    if s is not None:
                        s.kill()
                    self._arm()

    def take(self, system: str) -> tuple[Proc, bool]:
        """A process for the primary model with this system prompt: the warm
        spare when it matches, else a cold one. Re-arms the spare either way."""
        with self.lock:
            if system != self.system:
                self.system = system
                PROMPT_FILE.write_text(system)
                if self.spare is not None:
                    self.spare.kill()
                    self.spare = None
                log.info("system prompt changed (%d chars) -> spare rebuilt", len(system))
            s, warm = self.spare, False
            if s is not None and s.alive() and s.system == system:
                warm = True
            else:
                if s is not None:
                    s.kill()
                s = Proc(MODELS[0][0], MODELS[0][1], system)
            self.spare = None
            self._arm()
            return s, warm


POOL: Pool | None = None


def answer(system: str, prompt: str):
    """Yield wire events for one question, walking MODELS on early failure."""
    POOL.stats["requests"] += 1
    t0 = time.monotonic()
    last_err = "no model configured"
    for i, (model, effort) in enumerate(MODELS):
        if i == 0:
            proc, warm = POOL.take(system)
            POOL.stats["warm_hits"] += int(warm)
        else:
            proc, warm = Proc(model, effort, system), False
            POOL.stats["fallbacks"] += 1
        yielded = False
        try:
            for kind, val in proc.run(prompt):
                if kind == "delta":
                    if not yielded:
                        yielded = True
                        yield {"t": "meta", "model": model, "effort": effort, "warm": warm,
                               "fallback": i > 0}
                    yield {"t": "delta", "text": val}
                elif kind == "done":
                    val.update(warm=warm, fallback=i > 0,
                               total_ms=round((time.monotonic() - t0) * 1000))
                    POOL.stats["last"] = {**val, "at": time.time()}
                    log.info("answered: %s", val)
                    yield {"t": "done", **val}
                    return
                else:
                    last_err = f"{model}: {val}"
                    log.warning("model failed: %s (text streamed=%s)", last_err, yielded)
                    break
        finally:
            proc.kill()
        if yielded:
            break                   # text already went out: no second model
    POOL.stats["errors"] += 1
    yield {"t": "error", "error": last_err}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"       # close-delimited body = simple streaming

    def log_message(self, fmt, *args):  # quiet: our own log lines are enough
        pass

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            return self._json(404, {"error": "not found"})
        s = POOL.spare
        self._json(200, {
            "ok": True, "models": [":".join(m) for m in MODELS],
            "spare": None if s is None else {
                "model": s.model, "alive": s.alive(), "age_s": round(s.age())},
            "system_prompt_chars": len(POOL.system or ""), **POOL.stats})

    def do_POST(self):
        if self.path != "/ask":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            system, prompt = str(req["system"]).strip(), str(req["prompt"]).strip()
            if not system or not prompt:
                raise ValueError("empty system or prompt")
        except Exception as exc:  # noqa: BLE001
            return self._json(400, {"error": f"bad request: {exc}"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        gen = answer(system, prompt)
        try:
            for ev in gen:
                self.wfile.write((json.dumps(ev) + "\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log.info("client went away mid-answer")
        finally:
            gen.close()                 # kills the process if we stopped early


def main():
    global POOL
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    CWD.mkdir(parents=True, exist_ok=True)
    POOL = Pool()

    def stop(*_):
        # Don't leave the idle spare behind (systemd would reap it with the
        # cgroup; a hand-started copy would not).
        s = POOL.spare
        if s is not None:
            s.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    log.info("knowledge service on %s:%s models=%s", HOST, PORT, MODELS)
    srv.serve_forever()


if __name__ == "__main__":
    main()
