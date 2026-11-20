# Household knowledge baseline

Six questions: lime juice, Solid Starts apple preparation for a one-year-old,
its skin-on follow-up, yesterday's Tour stage, pancake leavener substitution,
and whether red/blue LED mixing creates a new wavelength.

`run.py` imports the running orchestrator's exact knowledge system prompt and
freezes its clock for all candidates. It calls OpenRouter directly using the
existing mounted credential, never logs the credential, never calls an action
handler, never writes production conversational history, and never plays audio.
The follow-up receives each candidate's own previous answer. Each candidate
gets one call per case, in rotating candidate order. This is a baseline screen,
not a reliable estimate of long-run p95 or accuracy. Retrieval results can vary.

Candidates: GPT-5.4 medium (production setting), GPT-5.4 low, Gemini 3.8 Flash
low. All use native search enabled, high search context, temperature 0.3,
4000-token ceiling, and the existing short-answer/full-answer separator.
Provider mappings of reasoning, token limits and search differ; the model
catalog and exact request are preserved. This compares practical provider
stacks, not isolated weights at equal compute.

Metrics: first content token, complete short answer (separator observed), full
completion, token usage, search calls when reported, citations, provider,
finish reason, and provider-reported cost including search. Missing accounting
is not treated as zero cost. Search counts omitted by the API are not proof
that no internal retrieval occurred. Cache discounts reflect actual billed
usage, not normalized cold-cache pricing. Streaming usage is authoritative for
cost when the later accounting endpoint is not yet ready.

`measure_tts.py` optionally synthesizes saved answers without playing them and
records elapsed synthesis time and audio duration. These later measurements
are separate from the LLM run; summing them is an estimate, not measured
end-to-end voice latency. ASR, endpointing, classifier, playback transfer, and
filler playback are excluded from knowledge latency.

Run inside the existing container (no service restart):

```bash
docker cp /home/pi/home_config/llm-benchmarks voice-orchestrator:/tmp/house-bench
 docker exec voice-orchestrator python /tmp/house-bench/knowledge/run.py \
  --output-dir /tmp/knowledge-results
 docker exec voice-orchestrator python /tmp/house-bench/knowledge/measure_tts.py \
  --results /tmp/knowledge-results
```

Review each spoken answer AND its longer answer, checking claims against the
named source and citations. Agent reviews are explicitly provisional, not
human-approved ground truth. Correct core answers with unsupported extra
claims receive a caution or failure; no model-judge percentage is presented.
Source notes in `cases.json` are withheld from candidates. No provider or
production routing change is made by these scripts.

The date-sensitive Tour case needs re-adjudication on future dates. The first
run freezes September 12, 2026 in America/Denver, making yesterday September
11, outside the July Tour schedule.
