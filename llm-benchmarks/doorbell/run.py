#!/usr/bin/env python3
"""Run the frozen doorbell vision benchmark against an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import latency_summary, now_utc, result_path, run_metadata, write_json

ROLES = ("package_delivery", "door_to_door_sales", "cleaner", "visitor", "none_unclear")


def fetch_image(host: str, remote_dir: str, filename: str, max_side: int) -> tuple[bytes, str]:
    path = f"{remote_dir.rstrip('/')}/{filename}"
    proc = subprocess.run(["ssh", host, "cat", path], capture_output=True, check=True)
    normalized = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-vf", f"scale={max_side}:{max_side}:force_original_aspect_ratio=decrease",
         "-frames:v", "1", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        input=proc.stdout, capture_output=True, check=True,
    )
    return normalized.stdout, path


def call_model(endpoint: str, model: str, prompt: str, image: bytes, timeout: float) -> tuple[str, float, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}},
        ]}],
        "temperature": 0.2,
        "max_tokens": 80,
    }
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer not-used"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read())
    latency = (time.perf_counter() - started) * 1000
    content = body["choices"][0]["message"].get("content") or ""
    return str(content).strip(), latency, body.get("usage")


def normalize(text: str) -> tuple[str | None, str | None]:
    value = " ".join(text.lower().split())
    if not value:
        return None, None
    carrier = next((name for name in ("amazon", "ups", "fedex", "usps", "dhl") if name in value), None)
    if value in {"n/a", "n/a.", "na", "none"} or "no one" in value or "cannot determine" in value:
        return "none_unclear", carrier
    if any(word in value for word in ("salesperson", "sales person", "door-to-door", "door to door", "solicitor", "sales representative")):
        return "door_to_door_sales", carrier
    if any(word in value for word in ("delivery", "deliverer", "dropping off", "package", "parcel", "courier")):
        return "package_delivery", carrier
    if any(word in value for word in ("cleaner", "cleaning crew", "housekeeper", "housekeeping")):
        return "cleaner", carrier
    return "visitor", carrier


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r["predicted_role"] is not None]
    exact = sum(r["role_correct"] for r in rows)
    per_role: dict[str, Any] = {}
    for role in ROLES:
        tp = sum(r["expected_role"] == role and r["predicted_role"] == role for r in rows)
        fp = sum(r["expected_role"] != role and r["predicted_role"] == role for r in rows)
        fn = sum(r["expected_role"] == role and r["predicted_role"] != role for r in rows)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else 0.0
        per_role[role] = {"support": tp + fn, "tp": tp, "fp": fp, "fn": fn,
                          "precision": precision, "recall": recall, "f1": f1}
    carrier_rows = [r for r in rows if r["expected_carrier"]]
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    confusion = Counter(f'{r["expected_role"]}->{r["predicted_role"] or "invalid"}' for r in rows)
    return {
        "cases": len(rows), "valid_responses": len(valid), "invalid_responses": len(rows) - len(valid),
        "role_correct": exact, "role_accuracy": exact / len(rows) if rows else None,
        "carrier_correct": sum(r["carrier_correct"] for r in carrier_rows),
        "carrier_cases": len(carrier_rows),
        "carrier_accuracy": sum(r["carrier_correct"] for r in carrier_rows) / len(carrier_rows) if carrier_rows else None,
        "under_3000_ms": sum(v <= 3000 for v in latencies), "under_6500_ms": sum(v <= 6500 for v in latencies),
        "latency": latency_summary(latencies), "per_role": per_role, "confusion": dict(sorted(confusion.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://192.168.10.187:8095/v1")
    parser.add_argument("--model", default="qwen3-next")
    parser.add_argument("--run-label", help="Human-readable model/run name for the HTML comparison viewer")
    parser.add_argument("--image-host", default="dgx")
    parser.add_argument("--image-dir", default="/home/pi/local-llm/state/doorbell-bench/expanded-13")
    parser.add_argument("--max-image-side", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())
    prompt = (HERE / "prompt.txt").read_text().strip()
    if len(cases) != 13 or {c["expected_role"] for c in cases} - set(ROLES):
        raise SystemExit("invalid doorbell fixture")
    if args.validate_only:
        print(f"validated {len(cases)} doorbell cases")
        return 0
    result = run_metadata(benchmark="doorbell", version="1.0.0", endpoint=args.endpoint,
                          model=args.model, cases=cases, prompt=prompt)
    result["run_label"] = args.run_label
    rows = []
    for index, case in enumerate(cases, 1):
        row = dict(case)
        try:
            image, remote_path = fetch_image(args.image_host, args.image_dir, case["file"], args.max_image_side)
            answer, latency, usage = call_model(args.endpoint, args.model, prompt, image, args.timeout)
            role, carrier = normalize(answer)
            row.update(remote_path=remote_path, raw_response=answer, predicted_role=role,
                       predicted_carrier=carrier, latency_ms=round(latency, 1), usage=usage, error=None)
        except Exception as exc:
            row.update(raw_response=None, predicted_role=None, predicted_carrier=None,
                       latency_ms=None, usage=None, error=f"{type(exc).__name__}: {exc}")
        row["role_correct"] = row["predicted_role"] == row["expected_role"]
        row["carrier_correct"] = row["predicted_carrier"] == row["expected_carrier"]
        rows.append(row)
        print(f'[{index:02}/{len(cases)}] {case["id"]}: {row["predicted_role"]} {row.get("latency_ms")} ms')
    result["finished_at"] = now_utc()
    result["request"] = {"temperature": 0.2, "max_tokens": 80, "max_image_side": args.max_image_side}
    result["summary"] = score(rows)
    result["cases"] = rows
    path = result_path("doorbell", args.output)
    write_json(path, result)
    print(json.dumps(result["summary"], indent=2))
    print(f"result: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
