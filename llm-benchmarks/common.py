#!/usr/bin/env python3
"""Small shared utilities for the GX10 workload benchmarks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def percentile(values: Iterable[float], pct: float) -> float | None:
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    pos = (len(vals) - 1) * pct / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def latency_summary(values: Iterable[float]) -> dict[str, Any]:
    vals = list(values)
    return {
        "count": len(vals),
        "min_ms": round(min(vals), 1) if vals else None,
        "p50_ms": round(percentile(vals, 50), 1) if vals else None,
        "p95_ms": round(percentile(vals, 95), 1) if vals else None,
        "max_ms": round(max(vals), 1) if vals else None,
    }


def endpoint_models(base_url: str, timeout: float = 10.0) -> Any:
    url = base_url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer not-used"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "url": url}


def run_metadata(*, benchmark: str, version: str, endpoint: str, model: str,
                 cases: Any, prompt: str) -> dict[str, Any]:
    return {
        "benchmark": benchmark,
        "benchmark_version": version,
        "started_at": now_utc(),
        "host": platform.node(),
        "endpoint": endpoint,
        "requested_model": model,
        "served_models": endpoint_models(endpoint),
        "case_sha256": canonical_hash(cases),
        "prompt_sha256": text_hash(prompt),
        "python": platform.python_version(),
    }


def result_path(name: str, output: str | None = None) -> Path:
    if output:
        path = Path(output).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = ROOT / "results" / f"{name}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
