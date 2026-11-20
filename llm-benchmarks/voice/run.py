#!/usr/bin/env python3
"""Benchmark the live voice intent parser and its deterministic production routes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from common import latency_summary, now_utc, result_path, run_metadata, write_json


def subset_match(actual: dict[str, Any] | None, expected: dict[str, Any]) -> tuple[bool, list[str]]:
    if actual is None:
        return False, list(expected)
    failed = [key for key, value in expected.items()
              if (actual.get(key).lower() if isinstance(actual.get(key), str) else actual.get(key))
              != (value.lower() if isinstance(value, str) else value)]
    return not failed, failed


def deterministic(intent: Any, case: dict[str, Any]) -> dict[str, Any] | None:
    mode = case.get("mode", "fresh")
    text = case["text"]
    sat = case.get("sat")
    if mode == "clarify":
        return None
    if mode == "followup":
        return intent.fast_parse_timer_rename(text)
    return intent.fast_parse_cover_level(text, sat) or intent.fast_parse(text)


async def model_parse(intent: Any, case: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, float, str | None]:
    captured: list[str] = []
    original = intent.clients.parse_intent_raw

    async def recording(messages: list[dict]) -> str:
        raw = await original(messages)
        captured.append(raw)
        return raw

    intent.clients.parse_intent_raw = recording
    started = time.perf_counter()
    try:
        if case.get("mode") == "clarify":
            parsed = await intent.parse_clarify(case["partial"], case["text"], case["question"], case.get("sat"))
        else:
            parsed = await intent.parse(case["text"], case.get("context"), case.get("sat"))
        return parsed, captured[-1] if captured else None, (time.perf_counter() - started) * 1000, None
    except Exception as exc:
        return None, captured[-1] if captured else None, (time.perf_counter() - started) * 1000, f"{type(exc).__name__}: {exc}"
    finally:
        intent.clients.parse_intent_raw = original


def summarize(rows: list[dict[str, Any]], view: str) -> dict[str, Any]:
    exact_key = f"{view}_exact"
    predicted_key = f"{view}_prediction"
    exact = sum(bool(row[exact_key]) for row in rows)
    intent_correct = sum((row[predicted_key] or {}).get("intent") == row["expected"]["intent"] for row in rows)
    slots_total = slots_correct = 0
    per_intent: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        prediction = row[predicted_key] or {}
        per_intent[row["expected"]["intent"]].append(bool(row[exact_key]))
        for key, expected in row["expected"].items():
            if key == "intent":
                continue
            slots_total += 1
            actual = prediction.get(key)
            if isinstance(actual, str) and isinstance(expected, str):
                actual, expected = actual.lower(), expected.lower()
            slots_correct += actual == expected
    return {
        "cases": len(rows), "intent_correct": intent_correct,
        "intent_accuracy": intent_correct / len(rows), "exact_correct": exact,
        "exact_accuracy": exact / len(rows), "slot_fields_correct": slots_correct,
        "slot_fields": slots_total, "slot_accuracy": slots_correct / slots_total if slots_total else None,
        "per_intent": {name: {"correct": sum(vals), "cases": len(vals), "accuracy": sum(vals) / len(vals)}
                       for name, vals in sorted(per_intent.items())},
    }


async def run(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    os.environ["LLM_URL"] = args.endpoint.rstrip("/") + "/chat/completions"
    os.environ["LLM_MODEL"] = args.model
    voice_root = Path(args.voice_root).resolve()
    sys.path.insert(0, str(voice_root))
    from orchestrator import intent

    cases = json.loads(Path(args.cases).read_text())
    supported = set(intent.INTENTS)
    represented = {case["expected"]["intent"] for case in cases}
    missing = supported - represented
    if missing:
        raise RuntimeError(f"fixture does not represent intents: {sorted(missing)}")
    prompt = intent._SYSTEM + intent._FOLLOWUP_NOTE + intent._CLARIFY_NOTE
    result = run_metadata(benchmark="voice_intent", version=args.benchmark_version, endpoint=args.endpoint,
                          model=args.model, cases=cases, prompt=prompt)
    result["run_label"] = args.run_label
    result["production_source"] = str(Path(intent.__file__).resolve())
    result["production_prompt_sha256"] = result["prompt_sha256"]
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        model_prediction, raw, latency, error = await model_parse(intent, case)
        fast = deterministic(intent, case)
        production_prediction = fast if fast is not None else model_prediction
        model_exact, model_failed = subset_match(model_prediction, case["expected"])
        prod_exact, prod_failed = subset_match(production_prediction, case["expected"])
        actual_route = "deterministic" if fast is not None else "llm"
        row = {**case, "raw_response": raw, "model_prediction": model_prediction,
               "production_prediction": production_prediction, "actual_route": actual_route,
               "route_correct": actual_route == case["expected_route"],
               "model_exact": model_exact, "model_failed_fields": model_failed,
               "production_exact": prod_exact, "production_failed_fields": prod_failed,
               "latency_ms": round(latency, 1), "error": error}
        rows.append(row)
        print(f'[{index:02}/{len(cases)}] {case["id"]}: model={model_prediction and model_prediction.get("intent")} '
              f'prod={production_prediction and production_prediction.get("intent")} {latency:.0f} ms')
    result["finished_at"] = now_utc()
    result["summary"] = {
        "model_only": summarize(rows, "model"),
        "production_route": summarize(rows, "production"),
        "route_correct": sum(r["route_correct"] for r in rows),
        "route_accuracy": sum(r["route_correct"] for r in rows) / len(rows),
        "invalid_or_error": sum(r["model_prediction"] is None for r in rows),
        "latency": latency_summary(r["latency_ms"] for r in rows),
        "risk_failures_model": [r["id"] for r in rows if r.get("risk") and not r["model_exact"]],
        "risk_failures_production": [r["id"] for r in rows if r.get("risk") and not r["production_exact"]],
        "predicted_intents": dict(Counter((r["model_prediction"] or {}).get("intent", "invalid") for r in rows)),
    }
    result["cases"] = rows
    path = result_path("voice", args.output)
    write_json(path, result)
    return result, path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://192.168.10.187:8095/v1")
    parser.add_argument("--model", default="qwen3-next")
    parser.add_argument("--run-label", help="Human-readable model/run name for the HTML comparison viewer")
    parser.add_argument("--voice-root", default="/home/pi/home_config/voice-assistant")
    parser.add_argument("--output")
    parser.add_argument("--cases", default=str(HERE / "cases.json"))
    parser.add_argument("--benchmark-version", default="1.0.0")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        cases = json.loads(Path(args.cases).read_text())
        ids = [case["id"] for case in cases]
        if len(ids) != len(set(ids)):
            raise SystemExit("duplicate voice case id")
        print(f"validated {len(cases)} voice cases")
        return 0
    result, path = asyncio.run(run(args))
    print(json.dumps(result["summary"], indent=2))
    print(f"result: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
