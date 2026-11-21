#!/usr/bin/env python3
"""Household knowledge benchmark, Claude edition (2026-11-20).

Same six cases and the SAME frozen production prompt as the 2026-09-12
OpenRouter run (read from that run's result file), so the numbers sit next to
GPT-5.4 / Gemini in the viewer. Calls go through the `claude` CLI on the
household Claude subscription, the way trainer_max and home-agent already do:
`claude -p --input-format stream-json --output-format stream-json`.

Isolation: no settings sources (no CLAUDE.md, memory, hooks), no MCP servers,
WebSearch as the only tool, a scratch working directory, no session
persistence. Nothing touches production: no orchestrator call, no history,
no audio.

Latency model: each case gets a fresh process that is spawned and left to
warm for WARM_S before the question is written, which is how a production
worker would run (one pre-spawned process waiting per request). Times are
from writing the question:
  first_token_ms   first text delta of the answer
  spoken_ready_ms  separator line seen = the short spoken answer is complete
  total_ms         the CLI's final result event
The follow-up case reuses the apple process, like the voice session does.
"""

import argparse
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"
BASELINE = RESULTS / "knowledge-20260912-gpt54-medium.json"
SENTINEL = "===MORE==="
WARM_S = 4.0
CASE_TIMEOUT_S = 150
CLAUDE = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))

DEFAULT_VARIANTS = [
    "haiku55-low:claude-haiku-5-5:low",
    "sonnet55-low:claude-sonnet-5-5:low",
    "opus55-low:claude-opus-5-5:low",
    "opus55-medium:claude-opus-5-5:medium",
    "fable52-low:claude-fable-5-2:low",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class Session:
    """One warm `claude -p` stream-json process."""

    def __init__(self, model: str, effort: str, prompt: str, cwd: str):
        cmd = [CLAUDE, "-p", "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--model", model, "--effort", effort,
               "--system-prompt", prompt, "--setting-sources", "",
               "--strict-mcp-config", "--tools", "WebSearch",
               "--allowedTools", "WebSearch", "--no-session-persistence"]
        self.proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.err: list[str] = []
        threading.Thread(target=lambda: self.err.extend(self.proc.stderr), daemon=True).start()
        self.served_model = None

    def _pump(self):
        for line in self.proc.stdout:
            self.q.put((time.monotonic(), line))
        self.q.put((time.monotonic(), None))

    def ask(self, text: str) -> dict:
        msg = {"type": "user", "message": {"role": "user", "content": text}}
        t0 = time.monotonic()
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        acc, first, spoken_at, searches = "", None, None, 0
        result = None
        while True:
            try:
                t, line = self.q.get(timeout=max(1, CASE_TIMEOUT_S - (time.monotonic() - t0)))
            except queue.Empty:
                return {"error": "timeout", "raw_text": acc, "total_ms": None,
                        "first_token_ms": first, "spoken_ready_ms": spoken_at,
                        "web_searches": searches}
            if line is None:
                return {"error": "process exited: " + "".join(self.err)[-400:],
                        "raw_text": acc, "total_ms": None, "first_token_ms": first,
                        "spoken_ready_ms": spoken_at, "web_searches": searches}
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            ms = round((t - t0) * 1000, 1)
            if kind == "system" and e.get("subtype") == "init":
                self.served_model = e.get("model")
            elif kind == "stream_event":
                ev = e.get("event") or {}
                d = ev.get("delta") or {}
                if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                    if first is None:
                        first = ms
                    acc += d.get("text", "")
                    if spoken_at is None and SENTINEL in acc:
                        spoken_at = ms
            elif kind == "assistant":
                for b in (e.get("message") or {}).get("content") or []:
                    if b.get("type") in ("tool_use", "server_tool_use") and \
                            "search" in (b.get("name") or "").lower():
                        searches += 1
            elif kind == "result":
                result = e
                text = e.get("result") or acc
                return {
                    "error": None if e.get("subtype") == "success" else e.get("subtype"),
                    "raw_text": text,
                    "first_token_ms": first,
                    "spoken_ready_ms": spoken_at if spoken_at is not None else ms,
                    "separator_seen": spoken_at is not None,
                    "total_ms": ms,
                    "web_searches": searches,
                    "num_turns": e.get("num_turns"),
                    "api_duration_ms": e.get("duration_api_ms"),
                    "usage": e.get("usage"),
                    "notional_cost_usd": e.get("total_cost_usd"),
                }

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def spoken(raw: str) -> str:
    return raw.split(SENTINEL, 1)[0].strip()


def pctl(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 1)


def summarize(cases):
    out = {}
    for key in ("first_token_ms", "spoken_ready_ms", "total_ms"):
        v = [c.get(key) for c in cases if not c.get("error")]
        out[key] = {"count": len([x for x in v if x is not None]), "p50_ms": pctl(v, 0.5),
                    "p95_ms": pctl(v, 0.95), "max_ms": pctl(v, 1.0)}
    out["errors"] = sum(1 for c in cases if c.get("error"))
    out["web_searches"] = sum(c.get("web_searches") or 0 for c in cases)
    out["notional_cost_usd"] = round(sum(c.get("notional_cost_usd") or 0 for c in cases), 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", action="append",
                    help="label:model:effort (repeatable); default = the five Claude candidates")
    ap.add_argument("--rep", type=int, default=1, help="repetition number, for the label")
    ap.add_argument("--output-dir", default=str(RESULTS))
    args = ap.parse_args()
    variants = [v.split(":") for v in (args.variant or DEFAULT_VARIANTS)]
    base = json.loads(BASELINE.read_text())
    prompt = base["prompt"]                      # frozen 2026-09-12 production prompt
    cases = json.loads((HERE / "cases.json").read_text())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cwd = tempfile.mkdtemp(prefix="claude-knowledge-")
    results = {}
    for label, model, effort in variants:
        results[label] = {
            "benchmark": "knowledge_qa", "benchmark_version": "1.0.0",
            "run_label": f"claude {label}" + (f" rep{args.rep}" if args.rep > 1 else ""),
            "started_at": now_utc(), "requested_model": model, "reasoning_effort": effort,
            "transport": "claude CLI (subscription), stream-json, warm process",
            "prompt": prompt, "prompt_sha256": base.get("prompt_sha256"),
            "case_sha256": base.get("case_sha256"), "cases": [],
            "scoring_status": "agent_review_pending_human_approval",
            "measurement": "times from question write to a pre-warmed process; "
                           "spoken_ready_ms = separator seen; no audio",
        }
    held: dict[str, Session] = {}
    order = [c for c in cases if not c.get("follows")]
    for index, case in enumerate(order):
        rot = variants[index % len(variants):] + variants[:index % len(variants)]
        for label, model, effort in rot:
            s = Session(model, effort, prompt, cwd)
            time.sleep(WARM_S)
            row = {**case, **s.ask(case["text"])}
            row["served_model"] = s.served_model
            row["spoken"] = spoken(row.get("raw_text") or "")
            results[label]["cases"].append(row)
            print(f"{label:15} {case['id']:22} first={row.get('first_token_ms')} "
                  f"spoken={row.get('spoken_ready_ms')} total={row.get('total_ms')} "
                  f"searches={row.get('web_searches')} err={row.get('error')}", flush=True)
            follow = next((c for c in cases if c.get("follows") == case["id"]), None)
            if follow and not row.get("error"):
                frow = {**follow, **s.ask(follow["text"])}
                frow["served_model"] = s.served_model
                frow["spoken"] = spoken(frow.get("raw_text") or "")
                results[label]["cases"].append(frow)
                print(f"{label:15} {follow['id']:22} first={frow.get('first_token_ms')} "
                      f"spoken={frow.get('spoken_ready_ms')} total={frow.get('total_ms')} "
                      f"searches={frow.get('web_searches')} err={frow.get('error')}", flush=True)
            s.close()
            d = results[label]
            d["finished_at"] = now_utc()
            d["summary"] = summarize(d["cases"])
            (out_dir / f"knowledge-{stamp}-claude-{label}.json").write_text(
                json.dumps(d, indent=1, ensure_ascii=False))
    for label, d in results.items():
        s = d["summary"]
        print(f"SUMMARY {label:15} spoken p50={s['spoken_ready_ms']['p50_ms']} "
              f"p95={s['spoken_ready_ms']['p95_ms']} first p50={s['first_token_ms']['p50_ms']} "
              f"total p50={s['total_ms']['p50_ms']} searches={s['web_searches']} "
              f"errors={s['errors']}")


if __name__ == "__main__":
    main()
