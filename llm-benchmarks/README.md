# GX10 workload benchmarks

See [BENCHMARK_PLAN.md](BENCHMARK_PLAN.md) for scope, scoring, and acceptance
policy. The package is intentionally separate from production repositories.

## HTML viewer

The live read-only viewer is available at:

```text
http://192.168.10.217:8794/
```

It auto-discovers benchmark JSON files in `results/`, groups runs by workload,
and supports selectable side-by-side metrics and case predictions. Doorbell
fixtures include cached thumbnails; voice cases expose model-only and production
routing; podcast cases show approved and predicted timeline intervals.

Give every new run a stable display name with `--run-label`. Results written to
the default `results/` directory appear on the next page load. The persistent
user service is `gx10-benchmark-viewer.service`.

## Commands

```bash
python3 doorbell/run.py --validate-only
python3 doorbell/run.py --run-label "Qwen 3.8 candidate"

# The host does not carry the voice app dependencies. Run this in the current
# voice-orchestrator image (the result can then be copied back to results/).
docker cp /home/pi/home_config/llm-benchmarks voice-orchestrator:/tmp/llm-benchmarks
docker exec voice-orchestrator python /tmp/llm-benchmarks/voice/run.py \
  --voice-root /app --run-label "Qwen 3.8 candidate" --output /tmp/voice-candidate.json

python3 podcast/run.py --validate-only
# All four truth files are approved; this runs the scored suite.
python3 podcast/run.py --run-label "Qwen 3.8 candidate"
```

## Qwen 3.6 baseline

Run on 2026-08-18 MDT against `qwen3-next` at GX10 port 8095. At run time that
alias resolved to `/models/xs`, served by container `local-llm` with image
`ghcr.io/aeon-7/vllm-aeon-ultimate-dflash:qwen36-v4` and image ID
`sha256:75bd9a38be52a435ae8bdc35bf34807c3c67e3b26ae6d9fb377f5e6f5981665f`.
The server used an 8192-token context, one sequence, compressed-tensors weights,
and DFlash speculative decoding.

- Doorbell: 9/13 role-correct (69.2%), 0/2 salesperson recall, 2/3 delivery
  recall, 1/2 cleaner recall, 4/4 visitor and 2/2 N/A correct. p50 1.09s,
  p95 1.36s. See `results/doorbell-20260819T022609Z.json`.
- Voice: 60/62 model-only exact (96.8%), 61/62 production-route exact (98.4%),
  no invalid results, no destructive/noise failures, p50 3.04s, p95 3.37s.
  The model-only extra miss is `cover_set`, correctly handled by production's
  deterministic path. The remaining production miss is an unsupported flight
  booking routed to `ask` instead of `none`. See
  `results/voice-20260819T022644Z.json`.
- Podcast (approved human truth): all four episodes completed without errors in
  461.8s. Ad-time precision 97.4%, recall 94.7%, F1 96.0%; 19/20 approved ad
  blocks detected and no fully false predicted blocks. See
  `results/podcast-20260819T023631Z-approved-truth.json`, the manual review in
  `podcast/MANUAL_REVIEW_2026-08-18.md`, and the original run's same-named
  artifact directory.

The initial voice result records `weather_now` as an expected deterministic
route. Final validation showed that exact wording correctly takes the LLM route;
the fixture has been corrected for future comparisons. Prediction accuracy and
latency figures are unaffected.

## September 12 latency and knowledge comparison

See [LATENCY_BASELINE_2026-09-12.md](LATENCY_BASELINE_2026-09-12.md) for the
completed 66-case JSON-format comparison and six-question knowledge baseline.
The viewer now includes Knowledge answers with short-answer timings, costs,
citations, and provisional review notes. Production voice settings are unchanged.

The isolated runners are `voice/compare_formats.py` and `knowledge/run.py`;
run them in the existing voice-orchestrator container for app dependencies.
`knowledge/README.md` documents the paid API experiment and optional TTS check.
Results use unique timestamped filenames on future runs. The first run's files
use the local date `20260912` and preserve all candidate responses.

Fast-path expansion is deferred to backlog. The first compact-format prompt
needed semantic regression fixes; the corrected implementation was deployed
on September 13 after 91/91 production-route cases passed. See
[the compact JSON release report](COMPACT_JSON_RELEASE_2026-09-13.md).
The user chose to retain GPT-5.4 medium; the evidence did not justify changing
the knowledge provider for this household's low request volume.
