# GX10 real-world LLM benchmark plan

Status: benchmark harness implementation, 2026-08-18

The goal is model-change safety on this household's actual workloads, not a
general-purpose model score. Each candidate is run once over a frozen case set.
An anomalous transport or structural failure may be rerun for diagnosis, but
repeated runs and averaged scores are not part of the benchmark.

## Common rules

- Keep prompts, fixtures, expected answers, scoring, and benchmark versions in
  this directory. Do not change production code or deployed Node-RED flows.
- Save the endpoint, requested model alias, `/models` response, timestamps,
  prompt and case hashes, raw responses, parsed responses, and per-case latency.
- Compare candidate and baseline using the same fixtures and runner revision.
- Treat transport errors, invalid JSON, and unparseable responses as failures.
- Never promote model-generated labels to ground truth without human review.

## 1. Doorbell analysis

Fixture: the existing 13 hand-labelled images on the GX10, covering package
delivery (Amazon and UPS), door-to-door sales, visitors, cleaners, and
no-person/unclear frames. The runner snapshots the production Node-RED prompt,
temperature 0.2, and 80-token limit. It scores normalized role accuracy,
per-role precision/recall/F1, delivery and salesperson recall, carrier accuracy,
invalid answers, latency percentiles, and the production race thresholds of 3.0
and 6.5 seconds. The free-form model answer is always retained.

Initial acceptance gate for a replacement model: no role regression from the
current baseline, no missed delivery or salesperson case, no carrier regression,
and no material latency regression. The 13 cases are a seed suite, not a final
claim about general accuracy; future labelled live archive examples should be
added by role and edge case.

## 2. Voice intent classification

Fixture: a compact gold set spanning every supported intent plus high-value
slots, deterministic fast paths, follow-up/background speech, and slot-clarify
turns. One LLM result per case feeds two views:

1. model-only accuracy, showing what the model itself understands;
2. production-route accuracy, substituting current deterministic fast-path
   results where production would use them.

Score intent accuracy, full expected-subset accuracy, slot accuracy, route
accuracy, per-intent results, invalid output rate, and latency. Expectations are
subsets: fields not material to a case are not used to fail it.

Initial acceptance gate: no production-route exact regression, no regression on
destructive commands or follow-up noise suppression, and no material p95 latency
regression. The gold set is intentionally independent of the existing unit tests,
which mostly stub the model.

## 3. Podcast ad detection/removal

Frozen diversity set:

- Lanterne Rouge Cycling Podcast — Tour de France 2026 Stage 4
- Lanterne Rouge Cycling Podcast — Tour de France 2026 Stage 5
- Daily Tech News Show — Five Eyes, One AI
- Pod Save America — Trump and Pod Save America Agree: JD Is Lame

The runner imports the current `/home/pi/podcast` pipeline and executes its
local provider in an isolated result directory. It scores ad-time precision,
recall, F1 and error; ad-block detection/miss/false-positive counts; boundary
error; residual ad gaps; calls/errors; and wall time. It performs no repeated
averaging.

The truth files began as interval seeds from existing artifacts and were
manually adjudicated on 2026-08-18 under the checked-in annotation policy. All
four are now `approved`; the review corrected boundaries, added missed DTNS and
Pod Save America monetization blocks, and documented why the frozen transcript
is canonical when RSS audio uses dynamic ad insertion. The runner continues to
refuse any future fixture whose truth is not approved.
