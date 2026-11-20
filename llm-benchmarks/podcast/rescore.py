#!/usr/bin/env python3
"""Re-score saved podcast predictions after human truth adjudication."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import canonical_hash, now_utc, text_hash, write_json


def load_runner() -> Any:
    path = HERE / "run.py"
    spec = importlib.util.spec_from_file_location("podcast_benchmark_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", help="Saved podcast result containing raw predicted intervals")
    parser.add_argument("--output")
    args = parser.parse_args()
    source_path = Path(args.result).expanduser().resolve()
    source_text = source_path.read_text()
    result = json.loads(source_text)
    runner = load_runner()
    cases_raw = json.loads((HERE / "cases.json").read_text())
    cases = runner.validate(cases_raw, require_approved=True)
    truth_by_id = {case["id"]: case for case in cases}
    rows = []
    for old in result["cases"]:
        case = truth_by_id[old["id"]]
        truth = runner.parse_intervals(case["truth_data"]["ad_intervals"])
        predicted = runner.parse_intervals(old["predicted_intervals"])
        rows.append({**old, "truth_status": "approved", "truth_intervals": truth,
                     "metrics": runner.score_intervals(predicted, truth)})
    truth_seconds = sum(row["metrics"]["truth_seconds"] for row in rows)
    predicted_seconds = sum(row["metrics"]["predicted_seconds"] for row in rows)
    shared_seconds = sum(row["metrics"]["true_positive_seconds"] for row in rows)
    precision = shared_seconds / predicted_seconds if predicted_seconds else 0.0
    recall = shared_seconds / truth_seconds if truth_seconds else 0.0
    result.update({
        "rescored_at": now_utc(),
        "scoring_status": "approved_human_truth",
        "truth_revision": "1.0.0",
        "truth_sha256": canonical_hash({case["id"]: case["truth_data"] for case in cases}),
        "annotation_policy_sha256": text_hash((HERE / "ANNOTATION_POLICY.md").read_text()),
        "source_result": str(source_path),
        "source_result_sha256": text_hash(source_text),
        "summary": {
            "episodes": len(rows), "truth_seconds": truth_seconds,
            "predicted_seconds": predicted_seconds, "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "blocks_detected": sum(row["metrics"]["blocks_detected"] for row in rows),
            "blocks_missed": sum(row["metrics"]["blocks_missed"] for row in rows),
            "false_predicted_blocks": sum(row["metrics"]["false_predicted_blocks"] for row in rows),
            "wall_seconds": result["summary"].get("wall_seconds"),
            "errors": result["summary"].get("errors"),
        },
        "cases": rows,
    })
    output = (Path(args.output).expanduser().resolve() if args.output else
              source_path.with_name(source_path.stem + "-approved-truth.json"))
    write_json(output, result)
    print(json.dumps(result["summary"], indent=2))
    print(f"result: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
