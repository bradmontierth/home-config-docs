# Voice latency and knowledge baseline — September 12, 2026

Compact JSON reduced local classification median from 3.19s to 0.58s. Schema-constrained compact JSON measured 0.62s. The first compact prompt introduced two regressions and is not a deployment candidate yet. No voice production settings, filler behavior, fast paths, model routing, or Node-RED flows were changed.

Live review: http://192.168.10.217:8794/#knowledge and http://192.168.10.217:8794/#voice

## Local parser: existing Qwen 3.6 27B with DFlash

| Format | Classification p50 | p95 | Median generated tokens | Model exact | With existing deterministic routes |
|---|---:|---:|---:|---:|---:|
| full | 3.19s | 3.60s | 155.5 | 64/66 | 65/66 |
| compact | 0.58s | 0.87s | 12 | 63/66 | 64/66 |
| schema | 0.62s | 0.90s | 12 | 63/66 | 64/66 |

All three variants returned valid JSON with no transport errors and no scored destructive/noise failures. Compact and constrained compact had identical predictions. Their two production-route misses were a dropped label for the named chicken timer query, and an ambiguous follow-up routed to ask instead of unclear. They corrected the full-format baseline's unsupported-flight-booking miss. All three misclassified the cover-level case model-only; the existing deterministic path corrected it.

The count is 66: the original 62 frozen fixtures, a routing-only climate case to cover the newer intent, and three ask examples. Exact means matching the fixture's expected subset; this is not exhaustive validation of every emitted slot or downstream action. Production-route predictions substitute deterministic results offline; the timing columns still measure model classification, including cases that normally bypass it. The additions do not establish new general-accuracy evidence.

| Ask case | Full JSON | Compact | Compact + schema |
|---|---:|---:|---:|
| ask_lime | 3.33s | 0.44s | 0.45s |
| ask_apple | 3.58s | 0.55s | 0.56s |
| ask_light | 3.69s | 0.55s | 0.59s |

The compact ask outputs were just {"intent":"ask"}. The application can use the original command when query is absent. The schema was an object with an allowed intent enum, optional typed slots, and no additional properties. It does not enforce semantic correctness or every per-intent relationship. A conflicting-prompt probe produced the schema-only marker, confirming that constraints reached vLLM through port 8095. DFlash remained configured.

All variants used the production parser in an isolated container process; the compact-format instruction was appended in memory. Probes were recorded separately. No cache was evicted, no LLM was restarted, and no forced cold-start claim is made. Cases rotated variant order. One initial launch stopped after probes and the first case because of a missing output directory; only the subsequent complete run is scored. It may have warmed caches. Raw results, usage, schema, hashes, parser/client source snapshot and server command are preserved in results/.

Next parser experiment: preserve named slots explicitly and keep compact-format instructions from overriding follow-up routing semantics; then rerun the full suite and add unseen paraphrases. Do not deploy the current prototype solely on its latency. Fast-path expansion remains backlog.

## Knowledge: same production prompt and OpenRouter search integration

| Model / reasoning | Short answer ready p50 | Slowest case | Total cost, six questions |
|---|---:|---:|---:|
| gpt54-medium | 3.86s | 14.93s | $0.1937 |
| gpt54-low | 2.59s | 9.04s | $0.1473 |
| flash38-low | 2.28s | 4.82s | $0.0791 |

| Question | GPT-5.4 medium | GPT-5.4 low | Flash 3.8 low |
|---|---:|---:|---:|
| lime_juice | 2.73s | 1.16s | 1.29s |
| apple_one_year | 8.27s | 5.80s | 3.06s |
| apple_skin_followup | 4.72s | 3.33s | 3.85s |
| tour_yesterday | 14.93s | 9.04s | 4.82s |
| pancake_substitution | 2.32s | 1.57s | 1.50s |
| led_purple | 3.00s | 1.86s | 1.32s |

Times start at the cloud request and end when the entire short spoken answer and separator have arrived. They exclude local classification, speech recognition, TTS, audio transfer, and filler. First-token and full-completion timings are separately retained. The six-question total billed cost was $0.4202. Native search charges are included in provider-reported usage. Missing later accounting records are not zero cost; streaming usage still supplied costs for every response. Actual cache discounts apply, so these are practical stack costs, not normalized per-token model comparisons.

Each candidate answered each question once. Six samples give a useful baseline, not a trustworthy long-run p95 or reliability estimate. The same production prompt and home timestamp were frozen across models, and the apple follow-up used each model's own prior answer. All requests completed with the answer separator and no transport failures. Provider reasoning implementations differ, even when both are called low.

## Provisional quality review

- All three answered the light question's central point correctly: the red and blue spectral components remain; the perceived mixture does not create an averaged wavelength. GPT-5.4 medium gave the most complete clean explanation against the seed rubric.
- Both GPT-5.4 settings accurately used the requested Solid Starts page. Flash mixed in advice from other sources and attributed a raw matchstick option to Solid Starts that was absent from the checked page. Its skin follow-up also added advice not supported by that page. This is a source-fidelity failure, not a blanket safety judgment about all such preparations.
- The lime short answers were useful, but low GPT-5.4 gave a weaker typical yield and Flash added inconsistent arithmetic about smaller limes in the longer answer.
- All rejected the false September Tour-stage premise using search. GPT-5.4 spent more time searching and supplied extra race information.
- All explained the basic pancake substitution chemistry, but precise recipe substitutions in the longer answers need further verification. A correct short answer does not automatically make all extra detail correct.

Review notes are agent-authored and explicitly not human-approved truth. There is no model-judge accuracy percentage. The detailed per-case notes and sources are in knowledge/review-20260912.json and the viewer.

## Speech synthesis check

Saved short answers were synthesized without playback. First-pass synthesis took 0.22–0.45s (median 0.38s). The streaming WAV header used a placeholder length; a cached re-fetch measured actual PCM duration, while the original first-pass synthesis timings were retained. This was separate from the LLM tests and is not an end-to-end voice measurement.

For illustration, adding the measured lime classifier, knowledge-short-answer and first-pass TTS stages gives about 2.08s for compact-schema + Flash, versus 6.29s for full JSON + GPT-5.4 medium. These are sums of independently measured stages, not observed microphone-to-speaker latency.

## Validation and follow-up

The streaming harness passed offline tests for a separator split across chunks, final usage accounting, and a provider error after partial output. Python compilation, JavaScript syntax, viewer payload checks, and the live knowledge endpoint passed. The viewer was restarted to add the new workload; the voice orchestrator and LLM were not restarted.

The benchmark supports trying a corrected compact schema prompt before provisioning a second model. Keep the cloud-provider decision open until named-source fidelity and quantitative cooking answers are better tested. Immediate spoken filler removal remains a separate future production change; no filler setting was changed in this run.
