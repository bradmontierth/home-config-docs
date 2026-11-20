#!/usr/bin/env python3
"""Run the current podcast local pipeline over the frozen four-episode suite."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import now_utc, result_path, run_metadata, text_hash, write_json


def merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def total(intervals: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in merge(intervals))


def intersection_total(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> float:
    return sum(overlap(a, b) for a in merge(left) for b in merge(right))


def parse_intervals(value: Any) -> list[tuple[float, float]]:
    result = []
    for item in value or []:
        if isinstance(item, dict):
            start, end = item.get("start"), item.get("end")
        else:
            start, end = item[0], item[1]
        result.append((float(start), float(end)))
    return merge(result)


def score_intervals(predicted: list[tuple[float, float]], truth: list[tuple[float, float]]) -> dict[str, Any]:
    predicted, truth = merge(predicted), merge(truth)
    pred_total, truth_total = total(predicted), total(truth)
    shared = intersection_total(predicted, truth)
    precision = shared / pred_total if pred_total else (1.0 if not truth_total else 0.0)
    recall = shared / truth_total if truth_total else (1.0 if not pred_total else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    truth_hits = []
    for interval in truth:
        covered = sum(overlap(interval, pred) for pred in predicted)
        threshold = min(10.0, (interval[1] - interval[0]) * 0.25)
        truth_hits.append(covered >= threshold)
    false_predictions = []
    for interval in predicted:
        covered = sum(overlap(interval, gold) for gold in truth)
        threshold = min(5.0, (interval[1] - interval[0]) * 0.10)
        false_predictions.append(covered < threshold)
    boundary_errors = []
    for gold in truth:
        matches = [pred for pred in predicted if overlap(gold, pred) > 0]
        if matches:
            # A model may split two back-to-back sponsors at a tiny transcript
            # pause. Score the outer detected boundary, not whichever fragment
            # happens to overlap more of the approved combined block.
            detected_start = min(pred[0] for pred in matches)
            detected_end = max(pred[1] for pred in matches)
            boundary_errors.extend([abs(detected_start - gold[0]), abs(detected_end - gold[1])])
    return {
        "truth_seconds": round(truth_total, 3), "predicted_seconds": round(pred_total, 3),
        "true_positive_seconds": round(shared, 3), "false_positive_seconds": round(pred_total - shared, 3),
        "missed_seconds": round(truth_total - shared, 3), "precision": precision, "recall": recall, "f1": f1,
        "ad_blocks": len(truth), "blocks_detected": sum(truth_hits), "blocks_missed": len(truth_hits) - sum(truth_hits),
        "predicted_blocks": len(predicted), "false_predicted_blocks": sum(false_predictions),
        "mean_boundary_error_seconds": sum(boundary_errors) / len(boundary_errors) if boundary_errors else None,
    }


def load_pipeline(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("gx10_podcast_benchmark_pipeline", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(cases: list[dict[str, Any]], require_approved: bool) -> list[dict[str, Any]]:
    if len(cases) != 4 or len({case["podcast"] for case in cases}) != 3:
        raise RuntimeError("podcast fixture must contain two LRCP episodes, one DTNS, and one Pod Save America")
    loaded = []
    for case in cases:
        input_path = Path(case["input"])
        truth_path = HERE / case["truth"]
        if not input_path.is_file() or not truth_path.is_file():
            raise RuntimeError(f'missing fixture file for {case["id"]}')
        truth = json.loads(truth_path.read_text())
        intervals = parse_intervals(truth.get("ad_intervals"))
        duration = float(truth["duration_seconds"])
        if not intervals or any(start < 0 or end > duration + 0.25 for start, end in intervals):
            raise RuntimeError(f'invalid intervals for {case["id"]}')
        if require_approved and truth.get("review_status") != "approved":
            raise RuntimeError(f'{case["id"]} truth is {truth.get("review_status")}; manual review is required')
        loaded.append({**case, "truth_data": truth})
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://192.168.10.187:8095/v1")
    parser.add_argument("--model", default="qwen3-next")
    parser.add_argument("--run-label", help="Human-readable model/run name for the HTML comparison viewer")
    parser.add_argument("--podcast-root", default="/home/pi/podcast")
    parser.add_argument("--output")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-unreviewed-truth", action="store_true")
    args = parser.parse_args()
    cases_raw = json.loads((HERE / "cases.json").read_text())
    try:
        cases = validate(cases_raw, require_approved=not (args.validate_only or args.allow_unreviewed_truth))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if args.validate_only:
        states = {case["id"]: case["truth_data"]["review_status"] for case in cases}
        print(f"validated {len(cases)} podcast cases: {json.dumps(states, sort_keys=True)}")
        return 0

    pipeline_path = Path(args.podcast_root) / "bin" / "chapterize_transcript.py"
    pipeline = load_pipeline(pipeline_path)
    os.environ.update({
        "LOCAL_LLM_BASE_URL": args.endpoint,
        "LOCAL_LLM_MODEL": args.model,
        "LOCAL_LLM_AD_BINS_MODE": "perbin",
        "LOCAL_LLM_AD_REFINE_RETRIES": "2",
        "CHAPTERS_PROVIDER": "local",
        "GEMINI_SHADOW": "0",
    })
    prompt_identity = pipeline.build_prompt("", 0, [], "auto", 45.0, None)
    result = run_metadata(benchmark="podcast_ads", version="1.0.0", endpoint=args.endpoint,
                          model=args.model, cases=cases, prompt=prompt_identity)
    result["run_label"] = args.run_label
    result["pipeline_source"] = str(pipeline_path.resolve())
    result["pipeline_source_sha256"] = text_hash(pipeline_path.read_text())
    result["scoring_status"] = (
        "approved" if all(case["truth_data"]["review_status"] == "approved" for case in cases)
        else "provisional_unreviewed_truth"
    )
    output_path = result_path("podcast", args.output)
    artifacts = output_path.with_suffix("")
    artifacts.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, case in enumerate(cases, 1):
        data = pipeline.load_transcript(Path(case["input"]))
        title = (data.get("title") or case["title"]).strip()
        bins, duration = pipeline.build_bins(data, 120.0, 400, 180)
        prompt = pipeline.build_prompt(title, duration, bins, "auto", 45.0, None)
        prompt_builder = lambda group, t=title, d=duration: pipeline.build_prompt(t, d, group, "auto", 45.0, None)
        case_dir = artifacts / case["id"]
        case_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        error = None
        payload = None
        try:
            payload = pipeline.run_local_shadow(
                data=data, title=title, duration=duration, bins=bins, ad_bins_source=bins,
                chapter_prompt=prompt, out_dir=case_dir, stem=case["id"], bin_seconds=120.0,
                gemini_payload={}, original_notes=None, chapter_prompt_builder=prompt_builder, primary=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        wall = time.perf_counter() - started
        predicted = parse_intervals((payload or {}).get("ad_intervals"))
        truth = parse_intervals(case["truth_data"]["ad_intervals"])
        metrics = score_intervals(predicted, truth)
        compare_path = case_dir / f'{case["id"]}.llm_compare.json'
        compare = json.loads(compare_path.read_text()) if compare_path.is_file() else None
        rows.append({"id": case["id"], "podcast": case["podcast"], "title": title,
                     "truth_status": case["truth_data"]["review_status"],
                     "truth_intervals": truth, "predicted_intervals": predicted,
                     "metrics": metrics, "wall_seconds": round(wall, 3), "error": error,
                     "pipeline_compare": compare})
        print(f'[{index}/{len(cases)}] {case["id"]}: recall={metrics["recall"]:.3f} precision={metrics["precision"]:.3f} wall={wall:.1f}s')
    truth_seconds = sum(row["metrics"]["truth_seconds"] for row in rows)
    predicted_seconds = sum(row["metrics"]["predicted_seconds"] for row in rows)
    shared_seconds = sum(row["metrics"]["true_positive_seconds"] for row in rows)
    precision = shared_seconds / predicted_seconds if predicted_seconds else 0.0
    recall = shared_seconds / truth_seconds if truth_seconds else 0.0
    result["finished_at"] = now_utc()
    result["summary"] = {
        "episodes": len(rows), "truth_seconds": truth_seconds, "predicted_seconds": predicted_seconds,
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "blocks_detected": sum(row["metrics"]["blocks_detected"] for row in rows),
        "blocks_missed": sum(row["metrics"]["blocks_missed"] for row in rows),
        "false_predicted_blocks": sum(row["metrics"]["false_predicted_blocks"] for row in rows),
        "wall_seconds": sum(row["wall_seconds"] for row in rows),
        "errors": sum(row["error"] is not None for row in rows),
    }
    result["cases"] = rows
    write_json(output_path, result)
    print(json.dumps(result["summary"], indent=2))
    print(f"result: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
