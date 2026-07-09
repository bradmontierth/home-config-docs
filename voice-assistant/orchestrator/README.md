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
`timer_cancelled`, `timer_dismissed`, `timer_done`.

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
