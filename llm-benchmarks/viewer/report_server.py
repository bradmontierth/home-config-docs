#!/usr/bin/env python3
"""Read-only HTML/API server for the GX10 workload benchmark datasets."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "results"
CACHE = RESULTS / ".viewer-cache" / "doorbell"
BENCHMARKS = {
    "doorbell": {"result_name": "doorbell", "title": "Doorbell vision", "accent": "#f97316"},
    "voice": {"result_name": "voice_intent", "title": "Voice intent", "accent": "#22c55e"},
    "podcast": {"result_name": "podcast_ads", "title": "Podcast ads", "accent": "#8b5cf6"},
    "knowledge": {"result_name": "knowledge_qa", "title": "Knowledge answers", "accent": "#06b6d4"},
}
RESULT_TO_SLUG = {value["result_name"]: key for key, value in BENCHMARKS.items()}
_thumb_locks: dict[str, threading.Lock] = {}
_thumb_locks_guard = threading.Lock()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def labels() -> dict[str, str]:
    path = ROOT / "viewer_labels.json"
    try:
        return load_json(path).get("runs", {})
    except Exception:
        return {}


def format_date(value: str | None) -> str:
    if not value:
        return "unknown date"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return value


def metric_summary(slug: str, summary: dict[str, Any]) -> list[dict[str, Any]]:
    if slug == "knowledge":
        return [
            {"label": "Short answer p50 (before TTS)", "value": summary.get("spoken_ready", {}).get("p50_ms"), "format": "ms"},
            {"label": "Short answer p95 (6 cases)", "value": summary.get("spoken_ready", {}).get("p95_ms"), "format": "ms"},
            {"label": "Six-question cost", "value": summary.get("total_cost_usd"), "format": "usd"},
            {"label": "Errors", "value": summary.get("errors"), "format": "integer"},
        ]
    if slug == "doorbell":
        return [
            {"label": "Role accuracy", "value": summary.get("role_accuracy"), "format": "percent"},
            {"label": "Carrier accuracy", "value": summary.get("carrier_accuracy"), "format": "percent"},
            {"label": "Sales recall", "value": (summary.get("per_role") or {}).get("door_to_door_sales", {}).get("recall"), "format": "percent"},
            {"label": "Delivery recall", "value": (summary.get("per_role") or {}).get("package_delivery", {}).get("recall"), "format": "percent"},
            {"label": "p50", "value": (summary.get("latency") or {}).get("p50_ms"), "format": "ms"},
            {"label": "p95", "value": (summary.get("latency") or {}).get("p95_ms"), "format": "ms"},
        ]
    if slug == "voice":
        return [
            {"label": "Production exact", "value": (summary.get("production_route") or {}).get("exact_accuracy"), "format": "percent"},
            {"label": "Model exact", "value": (summary.get("model_only") or {}).get("exact_accuracy"), "format": "percent"},
            {"label": "Slot accuracy", "value": (summary.get("production_route") or {}).get("slot_accuracy"), "format": "percent"},
            {"label": "Invalid", "value": summary.get("invalid_or_error"), "format": "integer"},
            {"label": "p50", "value": (summary.get("latency") or {}).get("p50_ms"), "format": "ms"},
            {"label": "p95", "value": (summary.get("latency") or {}).get("p95_ms"), "format": "ms"},
        ]
    return [
        {"label": "Precision", "value": summary.get("precision"), "format": "percent"},
        {"label": "Recall", "value": summary.get("recall"), "format": "percent"},
        {"label": "F1", "value": summary.get("f1"), "format": "percent"},
        {"label": "Blocks", "value": f'{summary.get("blocks_detected", 0)}/{summary.get("blocks_detected", 0) + summary.get("blocks_missed", 0)}', "format": "text"},
        {"label": "Runtime", "value": summary.get("wall_seconds"), "format": "seconds"},
        {"label": "Errors", "value": summary.get("errors"), "format": "integer"},
    ]


def discover_runs() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    raw: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(RESULTS.glob("*.json")):
        try:
            data = load_json(path)
        except Exception:
            continue
        slug = RESULT_TO_SLUG.get(data.get("benchmark"))
        if not slug or not isinstance(data.get("cases"), list):
            continue
        raw.append((path, data))
        source = data.get("source_result")
        if source:
            source_ids.add(Path(source).stem)
    custom_labels = labels()
    for path, data in raw:
        run_id = path.stem
        started = data.get("started_at")
        model = data.get("requested_model") or data.get("model") or "unknown model"
        label = data.get("run_label") or custom_labels.get(run_id)
        if not label:
            label = f"{model} · {format_date(started)}"
        slug = RESULT_TO_SLUG[data["benchmark"]]
        found.append({
            "id": run_id,
            "benchmark": slug,
            "label": label,
            "model": model,
            "started_at": started,
            "finished_at": data.get("finished_at"),
            "benchmark_version": data.get("benchmark_version"),
            "case_sha256": data.get("case_sha256"),
            "prompt_sha256": data.get("prompt_sha256"),
            "scoring_status": data.get("scoring_status"),
            "truth_revision": data.get("truth_revision"),
            "superseded": run_id in source_ids,
            "metrics": metric_summary(slug, data.get("summary") or {}),
            "summary": data.get("summary") or {},
            "path": str(path),
        })
    found.sort(key=lambda run: run.get("started_at") or "", reverse=True)
    return found


def podcast_boundary_evidence(case: dict[str, Any], truth: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = load_json(Path(case["input"]))
    segments = transcript.get("segments") or []
    evidence = []
    for start, end in truth.get("ad_intervals") or []:
        before = [s for s in segments if float(s.get("end", 0)) <= start][-2:]
        opening = [s for s in segments if float(s.get("start", 0)) < start + 12 and float(s.get("end", 0)) > start][:3]
        closing = [s for s in segments if float(s.get("start", 0)) < end and float(s.get("end", 0)) > end - 12][-3:]
        after = [s for s in segments if float(s.get("start", 0)) >= end][:2]
        evidence.append({"start": start, "end": end, "before": before, "opening": opening,
                         "closing": closing, "after": after})
    return evidence


def dataset(slug: str) -> list[dict[str, Any]]:
    if slug == "doorbell":
        return [{**case, "image_url": "/api/doorbell-image/" + case["file"]}
                for case in load_json(ROOT / "doorbell" / "cases.json")]
    if slug == "voice":
        cases = {c["id"]: c for c in load_json(ROOT / "voice" / "cases.json")}
        for run in discover_runs():
            if run["benchmark"] == "voice":
                for case in load_json(Path(run["path"])).get("cases", []):
                    cases.setdefault(case["id"], {k: case[k] for k in ("id", "text", "expected", "expected_route", "mode", "context", "risk") if k in case})
        return list(cases.values())
    if slug == "knowledge":
        return load_json(ROOT / "knowledge" / "cases.json")
    rows = []
    for case in load_json(ROOT / "podcast" / "cases.json"):
        truth = load_json(ROOT / "podcast" / case["truth"])
        rows.append({**case, "truth_data": truth, "boundary_evidence": podcast_boundary_evidence(case, truth)})
    return rows


def result_payload(run: dict[str, Any]) -> dict[str, Any]:
    data = load_json(Path(run["path"]))
    cases = {}
    for case in data.get("cases") or []:
        clean = {key: value for key, value in case.items() if key != "pipeline_compare"}
        cases[str(case.get("id"))] = clean
    return {key: value for key, value in run.items() if key != "path"} | {"cases": cases}


def benchmark_payload(slug: str) -> dict[str, Any]:
    runs = [run for run in discover_runs() if run["benchmark"] == slug and not run["superseded"]]
    return {
        "benchmark": {"id": slug, **BENCHMARKS[slug]},
        "dataset": dataset(slug),
        "runs": [result_payload(run) for run in runs],
    }


def doorbell_files() -> set[str]:
    return {case["file"] for case in load_json(ROOT / "doorbell" / "cases.json")}


def thumbnail(filename: str) -> Path:
    if filename not in doorbell_files() or Path(filename).name != filename:
        raise FileNotFoundError(filename)
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / (Path(filename).stem + ".jpg")
    if target.is_file() and target.stat().st_size:
        return target
    with _thumb_locks_guard:
        lock = _thumb_locks.setdefault(filename, threading.Lock())
    with lock:
        if target.is_file() and target.stat().st_size:
            return target
        remote = "/home/pi/local-llm/state/doorbell-bench/expanded-13/" + filename
        source = subprocess.run(["ssh", "dgx", "cat", remote], capture_output=True, check=True, timeout=30)
        temp = target.with_suffix(".tmp.jpg")
        normalized = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vf",
             "scale=720:480:force_original_aspect_ratio=decrease,pad=720:480:(ow-iw)/2:(oh-ih)/2:color=111827",
             "-frames:v", "1", "-q:v", "4", "-y", str(temp)],
            input=source.stdout, capture_output=True, check=True, timeout=30,
        )
        if normalized.returncode != 0 or not temp.is_file():
            raise RuntimeError("thumbnail conversion failed")
        os.replace(temp, target)
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "GX10BenchmarkViewer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK,
                   cache: str = "no-cache") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/healthz":
                self.send_json({"ok": True, "service": "gx10-benchmark-viewer",
                                "time": datetime.now(timezone.utc).isoformat()})
                return
            if path == "/api/catalog":
                runs = discover_runs()
                self.send_json({"benchmarks": [{"id": key, **value,
                                                 "case_count": len(dataset(key)),
                                                 "run_count": sum(r["benchmark"] == key and not r["superseded"] for r in runs)}
                                                for key, value in BENCHMARKS.items()],
                                "runs": [{key: value for key, value in run.items() if key != "path"} for run in runs],
                                "generated_at": datetime.now(timezone.utc).isoformat()})
                return
            if path.startswith("/api/benchmark/"):
                slug = path.rsplit("/", 1)[-1]
                if slug not in BENCHMARKS:
                    self.send_json({"error": "unknown benchmark"}, HTTPStatus.NOT_FOUND)
                else:
                    self.send_json(benchmark_payload(slug))
                return
            if path.startswith("/api/doorbell-image/"):
                file = thumbnail(path.rsplit("/", 1)[-1])
                self.send_bytes(file.read_bytes(), "image/jpeg", cache="public, max-age=86400")
                return
            static_name = "index.html" if path in ("/", "") else path.lstrip("/")
            file = (STATIC / static_name).resolve()
            if STATIC.resolve() not in file.parents or not file.is_file():
                self.send_bytes(b"Not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
                content_type += "; charset=utf-8"
            self.send_bytes(file.read_bytes(), content_type)
        except FileNotFoundError:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8794)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"GX10 benchmark viewer listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
