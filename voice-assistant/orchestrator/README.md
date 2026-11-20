# Voice Orchestrator — Timers Vertical Slice

The brain of the kitchen assistant, runs on the **Beelink**. This slice proves
the full two-stage pipeline end to end for cooking timers:

```
satellite WAV ─► /wake ─► Parakeet transcribe ─► stage-2 verify ("okay computer")
                                                      │ reject ─► logged, nothing happens
                                                      ▼ pass
                                       extract command ─► intent LLM (thinking off)
                                                      ▼
                              timer engine (SQLite, absolute ends_at) ─► pre-rendered
                              announcement WAV + spoken reply (TTS router)
                                                      ▼
                              events ─► dashboard /api/live (badge, caption, timer cards)
                              expiry ─► timer_done event + satellite alarm dispatch
```

## Modules
| file | role |
| --- | --- |
| `config.py` | env-driven service URLs, wake phrase, sound-theme enum |
| `clients.py` | Parakeet / LLM / TTS clients (contracts lifted from doorbell shim) |
| `verify.py` | stage-2 fuzzy wake match + command extraction (rapidfuzz) |
| `intent.py` | qwen3-next JSON intent parse, timers schema, validation |
| `timers.py` | SQLite engine + single asyncio expiry scheduler, restart-safe |
| `format.py` | spoken phrasing (durations, confirmations) |
| `sports.py` | scores/schedules from ESPN's unofficial API (structured, no LLM; falls back to ask) |
| `places.py` | nearby maps/business hours from one Google Places Text Search, with cache and daily call guard |
| `tree.py` | "set the star to red" / "make the tree green": Christmas tree WLED (192.168.30.11, `TREE_WLED_URL`), star = segments starting at LED ≥300, found by position because preset segment ids vary; bypasses the classifier like chaos |
| `events.py` | dashboard fan-out + satellite alarm dispatch (both best-effort) |
| `app.py` | FastAPI wiring |

## HTTP surface (port 8785)
- `POST /wake` — raw WAV body (the utterance). Returns verify verdict + result.
- `POST /command` — `{"text": "..."}` bypass for testing / future text paths.
- `GET  /timers` — active timers (for the dashboard).
- `POST /timers/{id}/dismiss` — silence a ringing timer (touch tap / voice).
- `POST /timers/{id}/cancel`  — cancel an active timer.
- `POST /timers/{id}/add?seconds=N` — add/remove time.
- `GET  /timers/{id}/announcement.wav` — pre-rendered alarm announcement.
- `GET  /audio/{name}` — pre-rendered spoken reply.
- `GET  /health`.

## Run / deploy
This dir is the version-controlled **source** (code + Dockerfile). The running
container is deployed on the Beelink from the operational root
**`~/voice-pipeline/`**, whose `docker-compose.yml` builds from here:
```bash
cd ~/voice-pipeline
docker compose up -d --build      # build + (re)start
docker compose logs -f            # tail
docker compose down               # stop
```
Runtime data (SQLite DB, announcement/reply cache) lives in
`~/voice-pipeline/data/` (mounted at `/data`). Env overrides live in that
compose file; defaults target the live homelab (see `config.py`).

Local dev without a container:
```bash
pip install -r requirements.txt
uvicorn orchestrator.app:app --host 0.0.0.0 --port 8785
```

## Dashboard integration
`dashboard_webapp` gained `POST /api/assistant/event`, which re-broadcasts each
event over the existing `/api/live` WebSocket wrapped as
`{"type":"assistant","event":{...}}`. Event types: `verifying`, `wake_rejected`,
`transcript`, `thinking`, `response`, `timer_created`, `timer_updated`,
`timer_cancelled`, `timer_dismissed`, `timer_done`, `show_places`.

## Satellite alarm contract (not built yet)
On expiry the orchestrator POSTs `SATELLITE_ALARM_URL`:
```json
{"timer_id":"…","label":"chicken","sound_theme":"cluck","announce_url":"/timers/…/announcement.wav"}
```
The satellite plays the themed CC0 sound, then fetches + plays the announcement
WAV, looping with ~4s dismiss gaps. Best-effort until that service exists.

## Validated 2026-07-06 (venv, live services)
- set/query/adjust/cancel/cancel-all — correct themes (cluck, steam_whistle).
- `/wake` on synthesized speech: "okay computer set a tea timer…" → verified
  (score 100) → timer created, **1.6s** end-to-end (incl. 2 serial TTS renders).
- negative control "I just don't want to clean…" → rejected (score 42).
- expiry → `ringing` → announcement served → dismiss → `done`.
- restart-safety: timers reload from absolute `ends_at`; an expiry missed
  during downtime fires on startup.
- events confirmed arriving over the real dashboard `/api/live` socket.

## Final ASR hedge (2026-09-04)

`/command/audio` decodes through `clients.transcribe_final`. With
`FINAL_ASR_URL` unset it is plain Parakeet (`ASR_URL`), as before. With it set
(Cohere Transcribe on the GX10, `:8099/cohere/transcribe`, same contract as
Parakeet), the clip goes to both at once: the primary's answer is used when it
lands inside `FINAL_ASR_DEADLINE_MS` OR ahead of Parakeet's, whichever is
later (the GX10 slows both models ~3x while the local LLM is generating), never
past `FINAL_ASR_HARD_CAP_MS`, and passes the sanity checks; Parakeet's
otherwise. Partials, wake verify and the shadow decodes stay on Parakeet.

Sanity checks on an in-time primary: a word repeated more than
`FINAL_ASR_MAX_REPEAT` times in a row (`repeat`); more than 3x Parakeet's word
count AND at least `FINAL_ASR_LENGTH_MIN_WORDS` words (`length`); an empty
transcript when Parakeet heard something (`empty`). A primary HTTP/connection
failure or hard-cap miss trips a `FINAL_ASR_BREAKER_S` Parakeet-only window; a
lost race (`timeout`) does not, it cost nothing. The Parakeet leg retries once
on 429 (its API rejects past two in-flight decodes).

Per turn the row records `asr_model`, `asr_primary_ms`, `asr_fallback_ms` and
`asr_fallback_reason` (`none | timeout | http_<code> | error | hard_cap | repeat |
length | empty | breaker`); voice-ops shows them on the turn page. `asr_fallback_ms`
is NULL on a primary win when Parakeet had not finished yet.

Rollback: comment `FINAL_ASR_URL` out of `~/voice-pipeline/docker-compose.yml`
and `docker compose up -d`. Tests: `test_final_asr.py`.

## Compact intent output (2026-09-13)

Intent requests use the optional-field JSON schema in `intent_schema.py`.
Only meaningful parameters need to be generated; `_validate` preserves the
full internal dictionary and supplies defaults. Keep schema field order aligned
with the prompt and validator because the deployed constrained decoder can
restrict key order. Unchanged `ask` questions omit `query` and use the existing
original-command fallback; cleaned questions and action payloads retain it.

Release evidence: 91/91 production-route cases, 581 passing offline tests,
0.677s classification median and 0.917s p95. GPT-5.4 medium is unchanged.
See `../../llm-benchmarks/COMPACT_JSON_RELEASE_2026-09-13.md` for rollout,
limitations, and rollback. Fast-path expansion remains deferred.

Spoken knowledge fillers are disabled by default (`ASK_FILLER=0`). Startup
skips filler audio preparation and questions go directly to the answer stream.
GPT-5.4 medium still handles knowledge answers; classifier latency does not
include its answer time. Legacy fillers can be restored with `ASK_FILLER=1`.

## Animal sound videos (2026-09-29)

`animal_sound` routes lion/elephant/frog requests to the kitchen dashboard's
local clip player. Curated exact phrasings in `animals.fast_parse` bypass the
classifier; the constrained schema also accepts `animal` for other wording.
Successful playback skips TTS and returns `end_session` so satellites do not
listen to the animal as a follow-up. Recent “again”/“stop” requests resolve
against dashboard playback state. Kitchen/family-room only; other rooms get
an explanatory reply. Tests: `test_animals.py`. Full source and playback details:
`/home/pi/dashboard_webapp/ANIMAL_VIDEOS.md`.

## House security status (2026-10-03)

`home_status` answers read-only questions about whether the house is closed
up: "is the house secure", "is the garage open", "are the windows closed",
"are the doors locked". `status_scope` is one of all / doors / garage / locks /
windows / gates; anchored question phrasings in `intent.fast_parse_home_status`
bypass the classifier, and looser wording reaches the same intent through the
prompt rule. `home_status.py` reads HA `/api/states` at question time (not the
Node-RED bedtime globals, which cannot report unavailable) for a closed entity
list mirroring the bedtime tab, and replies from a template. "Secure" is said
only when every item in scope is positively closed or locked; unavailable,
missing, jammed, or an in-between state (locking, closing) older than 120 s is
named as unconfirmed. HA unreachable gives "Sorry, I couldn't check the house
sensors." with no ask fallback. Garage doors read the relay cover first (so
"closing" is reportable) and fall back to the door contact. The Nest x Yale
locks come through a fragile browser-cookie credential and read "can't
confirm" whenever it lapses — expected. Tests: `test_home_status.py`.


Animal library management (2026-10-03): the runtime inventory and aliases now
come from `ANIMAL_CATALOG_PATH` (`/animal-library/catalog.json` in production).
The dashboard's persistent animals directory is bind-mounted read-only into this
container. Inode/mtime/size checks hot-reload atomic catalog replacements for
both exact matching and the classifier prompt. Add clips or aliases at
`http://192.168.10.217:8777/animals`; no code or container restart is needed.
