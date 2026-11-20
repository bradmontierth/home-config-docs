# Compact intent JSON — deployed September 13, 2026

The existing Qwen 3.6 27B / DFlash server now returns only the intent and
meaningful parameters, with JSON-schema constraints enabled on every intent
request. GPT-5.4 stays at medium reasoning. Fast paths and spoken filler are
unchanged.

Validation before deployment:

- 91/91 expected-subset matches through existing production routing.
- 90/91 model-only matches. The known cover-level case remains classified as
  home_control by the LLM and is correctly handled by the existing cover fast
  path. No new fast paths were added.
- Classification median 0.677s; p95 0.917s. The first request was 2.934s;
  this is not a promise that every cold or queued request is under one second.
- Lime, apple and light questions classified in 0.559s, 0.560s and 0.472s,
  respectively, emitting only `{"intent":"ask"}`.
- 582 offline tests run against the built image: 581 passed, one live-only
  test skipped. Its 14 overlapping-speech cases were included in the live
  91-case release suite. No production actions were dispatched by those tests.

The release set combines the original 66 format cases, 14 existing live
multi-speaker test cases, and 11 extra payload, named-timer, negative-duration,
empty-input and ambiguous-follow-up cases. This is regression evidence, not
an estimate of general accuracy. Two new fixture expectations were corrected
against the existing contract before the final run: a named-person broadcast
starts its message with that person's name; `remove_items` does not consume
`list_type`. The original 66 expectations were unchanged.

Implementation:

- `intent_schema.py` shares the intent enum and describes optional typed fields.
  Field order follows the prompt and normalized dictionary. Testing showed the
  deployed grammar backend can prevent returning earlier fields after a later
  key, so `intent` first and consistent slot order matter.
- `clients.py` sends `response_format` on each request. Semantic normalization
  and downstream validation still happen in `intent.py`.
- The prompt preserves named slots, omits absent/default fields, and forwards
  unchanged knowledge questions through the existing original-command fallback.
  It still emits cleaned queries for overlapping speech or abandoned commands.
- Follow-up rules distinguish contextual questions (`ask`), directly addressed
  but underspecified action requests (`unclear`), and room chatter (`none`).
  Unsupported external action requests remain `none`, while questions about
  such actions remain `ask`.
- New tests cover field order/completeness, default normalization, the actual
  outgoing schema request, original and cleaned ask forwarding, named-timer
  selection with multiple timers, and missing-duration clarification.

The initial candidate builds and their failures are retained under
`results/compact-release-development-20260913/`. Final results are
`results/voice-compact-release-20260913-final.json`; the running-image smoke
check is `results/voice-compact-deploy-20260913-smoke.json`.

Deployment rebuilt only `voice-orchestrator` from its existing operational
compose root. Source checks before building confirmed all other existing
application files matched the running image. There were no active timers at
cutover. The timer database was backed up consistently with SQLite's backup
API; the existing persistent data mount was retained. The GX10 LLM and
Node-RED were not restarted.

Rollback image: `voice-pipeline-voice-orchestrator:before-compact-20260913`.
Original parser/client files and the timer snapshot are in
`/home/pi/voice-pipeline/rollback/compact-20260913/`. To roll back code without
reverting current timer state:

```bash
docker tag voice-pipeline-voice-orchestrator:before-compact-20260913 voice-pipeline-voice-orchestrator
cd /home/pi/voice-pipeline
docker compose up -d --no-deps --no-build voice-orchestrator
```

Do not restore the old database as part of a normal code rollback: doing so
would lose timer changes made after deployment. Restore source files too
before a later rebuild if choosing to retain the old implementation.
