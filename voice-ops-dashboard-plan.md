# Voice Ops Dashboard Plan

**Status:** **Phase 1 (telemetry spine) DEPLOYED 2026-08-11.** Orchestrator
rebuilt, both Pi satellites and the Simon Voice PE bridge updated; the `turns`
table is live and filling. Phases 2 (backfill) and 3 (the dashboard app) not
started. Live-voice confirmation of `chime_ms` pending — see §12.

**Where:** three places, deliberately.

| Piece | Repo / host | Why there |
| --- | --- | --- |
| `turns` table + per-stage timing instrumentation | `home_config/voice-assistant/orchestrator/` | It is orchestrator code. Every turn already passes through this process. |
| Telemetry back-post (`chime_ms`, `rtt_ms`) | `home_config/voice-assistant/satellite/` | Only the satellite can measure trigger→chime. |
| The dashboard app itself | **new repo `/home/pi/voice-ops`**, own container, port **8787** | A read-only observer must never be able to take the voice path down. |

Companion docs: `voice-assistant-plan.md` (the system being observed),
`voice-assistant-backlog.md`, `speaker-id-plan.md`, `stop-model-v4-plan.md`.

---

## 1. Goal

The voice assistant has grown to four microphone clients, a zone-audio reply
path, ~12 intents, speaker ID, two wake models and a stop model. Answering
*"is it healthy?"* or *"did that change help?"* currently means SSHing to a
satellite, remembering which JSONL holds what, and reading `docker logs` before
they rotate away.

The dashboard replaces that with one desktop page. It is an **ops** view, not a
household one — it goes on the homelab homepage, **not** on the kitchen kiosk.

Explicit non-goals: no control surface (no restarting satellites, no editing
aliases — that already lives at `:8785/home-commands/ui`), no alerting/paging,
no kiosk layout.

---

## 2. What exists today (audited 2026-08-11)

The data mostly exists. It is stranded in four incompatible stores.

| Store | Location | Contents | Problem |
| --- | --- | --- | --- |
| `events.jsonl` | **each satellite**, `$DATA_DIR/events.jsonl` | every stage-1 `trigger` (peak_score, mode, model) and stage-2 `verify` (verified, score, transcript, decode, clip, `rtt_ms`, `server_ms`, `chime_ms`) | never leaves the box; no aggregation. Kitchen: 9,445 lines / 25 days |
| `docker logs voice-orchestrator` | Beelink | intent chosen, dispatch, `latency_ms`, spoken reply | **ephemeral.** The only place "what did it answer?" lives, and it rotates away |
| `speaker_shadow.jsonl` | Beelink `/data` | one row per command turn: ts, sat, transcript, intent, speaker, all scores, margin, embed ms | good, and already complete — see §6.3 |
| `orchestrator.db` | Beelink `/data` | `timers` (154), `answers` (8), `music_resolutions` (14) | the only durable store, and it has no turn history |

Client `/health` shapes differ and must be normalized:

- **Pi satellites** (kitchen `192.168.10.251:8781`, master closet
  `192.168.10.24:8781`): `{ok, mode, phrase, threshold, uptime_s, triggers,
  turns, alarm, alarm_queue, volume}`. Counters reset on restart.
- **Simon Voice PE bridge** (`127.0.0.1:8793`): much richer and completely
  different — `audio_rms`, `audio_peak_10s`, `last_trigger{at,model,score}`,
  `stats{triggers,verified,dropped,alarm_asr_*}`, stop-model fields.
- **familyroom**: shares the kitchen host; **out of commission** since
  2026-07-27 (corrupted boot flash, SSD bought for rebuild).
- **display-pi** (`192.168.10.92`): already has its own monitoring — see
  `display-pi-monitoring-guide.md`. The dashboard should *link* to that, not
  reimplement it.

---

## 3. Baseline, measured 2026-08-11

Recorded here because it is the first real datapoint and the dashboard should
reproduce these numbers on day one. Kitchen satellite, `events.jsonl`,
2026-07-18 → 2026-08-11 (25 days):

| Metric | Value |
| --- | --- |
| Stage-1 triggers | 4,594 |
| Reached `/verify` | 4,558 |
| Stage-2 confirmed | **190 → 4.2% pass rate** |
| trigger→chime `chime_ms` | **p50 226ms, p90 405ms, max 866ms** (n=190) |
| `/verify` round trip `rtt_ms` | p50 393ms, p90 549ms |
| orchestrator-side `server_ms` | p50 364ms, p90 422ms |

Simon Voice PE, since last restart: 79 triggers → 13 verified (16%).
Master closet, 14h uptime: **0 triggers** — plausibly just a quiet closet, but
it is exactly the ambiguity the fleet strip exists to kill.

**The 500ms wake→chime target is being met at p50 and p90.** The headline
*problem* metric is the 4.2% stage-1 pass rate: stage 2 is doing enormous work
rejecting a mic that sits beside the big speakers.

---

## 4. Architecture

### 4.1 The `turns` table is the prerequisite

One row per turn, in `orchestrator.db`, written by the orchestrator — the one
process every turn already passes through. It sees `/verify` and
`/command/audio` from every satellite, so it can capture sat, wake score,
decode path, transcript, intent, speaker and a latency breakdown without any
new plumbing.

The two fields it *cannot* see — `chime_ms` and `rtt_ms` — the satellite
already computes today (`assistant.py`, the `run_turn` instrumentation block).
A small back-POST to a new orchestrator `/telemetry` endpoint closes the gap.

This single table turns ~80% of the desired views into a SQL query.

### 4.2 The dashboard is a separate project

Own repo, own compose project, own container. Two concrete reasons beyond
taste:

1. **`voice-pipeline` holds no code.** It is runtime config (compose, env, and
   the hot-reloaded `home_commands` / `broadcast_rooms` / `satellite_zones`
   tables). A web app in there muddies a repo whose job is "this is what the
   runtime is wired to."
2. **Blast radius.** Sharing the orchestrator's compose project means a
   rebuild, a wedged process or an OOM in the dashboard can take voice down
   with it.

It reads `orchestrator.db` directly, read-only, and polls satellite `/health`
itself. It never writes to the DB and never calls the orchestrator's turn path.

### 4.3 GOTCHA: the DB must move to WAL first

`orchestrator.db` is currently `journal_mode=delete` (verified 2026-08-11). In
rollback-journal mode a reader holds a SHARED lock and a writer needs
EXCLUSIVE — **a slow dashboard query can block a live turn from writing**, and
a write can block the dashboard. That is a direct regression to the voice path.

Fix: `PRAGMA journal_mode=WAL` once, set declaratively in `_conn()` so it is
not a one-off manual step. WAL is a persistent property of the file; setting it
in one module applies to all of them (`timers.py`, `answers.py`,
`music_log.py`, and the new turns module each open their own connection to the
same file).

**Second-order gotcha, worth a comment in the compose file:** a *read-only*
SQLite connection to a WAL database still needs write access to the `-shm`
file. So the dashboard container must bind-mount `/home/pi/voice-pipeline/data`
**read-write**, while opening the connection with `file:...?mode=ro`. The `ro`
flag is what prevents writes; the `rw` mount is what makes WAL work at all.

This will look like a mistake to a future reader — document it inline. (Same
class of trap as the `:ro` single-file mount that went stale on inode swaps in
`home-control-intent-plan.md`.)

### 4.4 Schema sketch

```sql
CREATE TABLE IF NOT EXISTS turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           REAL NOT NULL,          -- turn start, epoch seconds
    sat          TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- trigger | verify | command | followup
    -- wake
    stage1_score REAL,                   -- peak_score from the satellite
    wake_model   TEXT,                   -- okay_computer | okay_google
    verified     INTEGER,                -- 0/1, NULL if never reached stage 2
    wake_score   REAL,                   -- stage-2 fuzz score
    decode       TEXT,                   -- full | tail
    reject_reason TEXT,                  -- low_score | empty | suppressed | policy
    arb_winner   TEXT,                   -- set when suppressed by arbitration
    -- content
    transcript   TEXT,
    command      TEXT,
    intent       TEXT,
    slots        TEXT,                   -- JSON
    response     TEXT,                   -- what was actually spoken
    -- who
    speaker      TEXT,                   -- brad | adrienne | unsure
    speaker_score REAL,
    speaker_margin REAL,
    -- timing (ms)
    chime_ms     INTEGER,                -- satellite: trigger -> chime
    rtt_ms       INTEGER,                -- satellite: /verify round trip
    asr_ms       INTEGER,
    classify_ms  INTEGER,                -- NEW, see 6.2
    handler_ms   INTEGER,
    tts_ms       INTEGER,
    total_ms     INTEGER,
    -- provenance
    clip         TEXT,                   -- filename on the satellite
    ok           INTEGER
);
CREATE INDEX IF NOT EXISTS turns_at  ON turns(at DESC);
CREATE INDEX IF NOT EXISTS turns_sat ON turns(sat, at DESC);
```

Follow the existing house pattern in `answers.py` / `music_log.py`: module-level
`_SCHEMA`, lazy `_conn()`, `check_same_thread=False`, writes are fire-and-forget
and **must never raise into the turn**.

---

## 5. Retention — decided

**Turn text: retained forever. No prune.** The numbers make this a non-decision:

| | 25 days (kitchen) | per year |
| --- | --- | --- |
| Turn **text** | 1.6 MB | ~90 MB (~150 MB with indexes) |
| Retained **WAV clips** | **375 MB** (4,641 files, ~81 KB each) | ~5.5 GB |
| `alarm_rings` | 40 MB | growing |

Beelink has 1.3 TB free. A year of turn text is ~0.01% of it. Add a
`TURNS_MAX_ROWS` config knob **defaulted to off/None** purely so future-you has
a lever without a migration — nothing is ever deleted by default. Same for
`answers.full_text`.

**Clips: keep everything, on purpose.** Decision by Brad 2026-08-11 — the
retained `verify-*` and command clips are the **training and eval corpus** for
wake-model tuning, the stop-model v2 retrain and speaker-ID enrollment. More
data is the point. `CMD_CLIPS_KEEP=80` caps command clips; `verify-*` clips are
deliberately uncapped.

> **Do not "clean up" `data/clips` or `data/alarm_rings`.** They are a labeled
> corpus, not debt. Revisit only if a satellite's root filesystem is actually
> threatened — which is why §8 puts a per-satellite disk tile on the page.

Note the asymmetry for future reasoning: clips are ~230× the size of the text
describing them. Text is free; audio is the only thing that will ever need a
policy.

---

## 6. Phase 1 — telemetry spine (orchestrator + satellite)

Ships first and starts accumulating data **before any UI exists**.

### 6.1 `turns.py` in the orchestrator

New module beside `answers.py`. Written from `/verify`, `/command/audio`,
`/command/shadow` and the followup path. Fire-and-forget via
`asyncio.create_task`, wrapped so a telemetry failure is invisible to the turn
(the pattern `speaker.shadow()` already uses).

### 6.2 Per-stage timing

Today only end-to-end `latency_ms` is recorded (`app.py:1147, 1210, 1268, 1337,
1509`). `clients.parse_intent_raw()` — the local LLM classifier on the GX10 —
**has no timing at all**. Add:

- `asr_ms` around `clients.transcribe()`
- `classify_ms` around `clients.parse_intent_raw()` ← the one Brad specifically
  asked for
- `handler_ms` around intent dispatch
- `tts_ms` around `clients.synthesize()`

Cheap (a `time.perf_counter()` pair each) and it converts "it felt slow" into
"which stage regressed."

### 6.3 Speaker attribution — already done, no batch job needed

The nightly batch-classification idea is **unnecessary**. `speaker.identify()`
already runs unconditionally on every command turn (`app.py:1333`), and
`speaker.log_task()` writes every one to `speaker_shadow.jsonl` — including
turns whose intent never consumed the result. Per-voice attribution for the
whole corpus already exists retroactively; the turns table just needs to carry
the same fields inline.

### 6.4 Satellite back-post

`assistant.py` already computes `rtt_ms`, `server_ms` and `chime_ms` and writes
them to its local `events.jsonl`. Add a fire-and-forget POST of the same event
to a new orchestrator `/telemetry` endpoint, keyed by sat + timestamp so it
merges onto the row `/verify` already created. Keep the local JSONL write —
it is the fallback when the Beelink is down, and the backfill source.

---

## 7. Phase 2 — backfill

**Decided: yes, backfill.** Import the 25 days of `events.jsonl` from each
satellite so the trend lines exist on day one instead of in three weeks.

- One-shot script, re-runnable, idempotent (dedupe on `sat` + event ts).
- Must tolerate **older event shapes** — `chime_ms` only appears after the
  2026-07-12 latency work, `model` only after dual-wake landed 2026-07-18.
  Missing field → NULL, never a crash.
- Backfilled rows have no `intent` / `response` (those were only ever in docker
  logs). Mark them so the UI can grey out what is genuinely unknowable rather
  than showing it as a gap in coverage.
- `speaker_shadow.jsonl` backfills `speaker` / `intent` for turns it overlaps.

---

## 8. Phase 3 — the dashboard app

`/home/pi/voice-ops`, FastAPI + server-rendered HTML (same shape as
`thermal_viewer` / `cookmode`), port **8787** — free, and it sits neatly beside
home-commands 8785 and cookmode 8786.

### Row 1 — Fleet strip
One card per box: kitchen `.251`, master closet `.24`, Simon PE bridge,
familyroom (down), display-pi `.92`, orchestrator container. Green/amber/red on:
reachable, uptime, **mode** (a satellite silently resetting to shadow on restart
is a known failure mode), last trigger age, last confirmed turn age, **disk %**
(the clip corpus grows forever by design — this is how that stays safe).

### Row 2 — Latency over time
trigger→chime p50/p90 per satellite per day, with a 500ms reference rule.
Second chart splits server-side: asr / classify / handler / tts.

### Row 3 — Wake funnel
triggers → reached verify → confirmed → intent matched → answered, per satellite
per day, with rejection reasons broken out (low score, empty transcript,
arbitration-suppressed, policy-blocked).

### Row 4 — Utilization
Turns/day by intent, by room, by speaker. Timers set in last 7 days. Ask cost
(`answers.cost_usd` / `searches` already stored). Music refusal rate from
`music_resolutions`.

### Row 5 — Turn browser (the drawer)
Click a satellite → reverse-chronological turns → expand one for: raw
transcript, wake score, decode path, intent + slots, speaker verdict + margin,
the spoken response, per-stage timings, and a link to the retained clip. This is
the piece that replaces "which log was it in again?"

### Sidebar — Anomalies
Auto-surfaced, no thresholds to remember: satellite in shadow mode, stage-2 pass
rate off its own 7-day baseline, p90 chime over 500ms, MA announcement lock
stuck, **stop-model self-dismiss count > 0** (Brad's stated rollback trigger —
that should be a tile, not a manual `stop_report.py` run).

---

## 9. Repo setup

New private repo, house secret-safe convention (see the `homelab-git-backing`
memory):

- `.gitignore`: `.env`/`*.env` (keep `.env.example`), `.venv/ venv/
  __pycache__/ *.py[cod]`, `data/ *.sqlite* *.db`
- Guard before first commit — must be EMPTY:
  `git ls-files --cached | grep -iE '\.env$|secret|token|\.sqlite3?$|\.db$|\.pem$|\.key$|service-account' | grep -v '\.example$'`
- **Keep it PRIVATE** — `.env.example` will carry internal LAN IPs, and the app
  reads a DB containing transcripts of everything said in the house. `data/` is
  gitignored for the same reason `speaker_shadow.jsonl` and
  `speaker_profiles.json` already are: biometrics and speech logs stay local.
- The GitHub PAT at `cecret_lake/github/pat` is **read-only** →
  **Brad must create the empty private repo first**, then push over SSH
  (`github-illuminate`).
- Add a tile to `/home/pi/homepage/config/services.yaml`.
- The nightly `homelab_config_backup.sh` auto-discovers any `docker-compose.yml`
  under `/home/pi`, so the compose + `.env` are covered once it exists.

---

## 10. Decisions made (2026-08-11, Brad)

1. **Turn-text retention: unbounded, prune disabled.** Rows are tiny; the knob
   exists but defaults off.
2. **Clip retention: keep everything, deliberately.** It is the training/eval
   corpus for wake, stop and speaker models. Not debt. Revisit only on real
   disk pressure.
3. **Backfill: yes**, the 25 days of `events.jsonl`.
4. **Hosting: own repo + own container on the Beelink**, not `dashboard_webapp`
   and not inside `voice-pipeline`. Desktop-only, on the homelab homepage.
   Explicitly **not** on the kitchen display.
5. No nightly speaker-classification batch job — already covered per-turn
   (§6.3).

## 11. Open / deferred

- Alerting (Pushover on a red tile) — deliberately out of scope for v1.
- familyroom satellite is down; its card should read "decommissioned pending
  SSD rebuild" rather than red-alarming forever.
- Master closet's 0 triggers was **overnight and genuinely quiet** (Brad,
  2026-08-11). Not a fault — closed.

---

## 12. Phase 1 build record (2026-08-11)

**Shipped.** New: `orchestrator/turns.py` (the table), `orchestrator/timing.py`
(stage timers), `orchestrator/db.py` (shared connect + the WAL switch),
`test_turns.py` + `test_turn_wiring.py` (34 tests; full suite 349, green).
Changed: `app.py` (row lifecycle across `/verify`, `/command/audio`,
`/command`, plus new `POST /telemetry` and `GET /turns`), `clients.py` (three
stage timers), `config.py` (`TURNS_MAX_ROWS`), `answers.py` / `music_log.py` /
`timers.py` (onto `db.connect`), `satellite/assistant.py` (back-post +
`turn_id` threading), `voice-pe/bridge/bridge.py` (`turn_id` threading).

**Deployed:** orchestrator container rebuilt; `assistant.py` copied to the
kitchen `.251` and master closet `.24` (backups
`assistant.py.bak-20260811-turns` on both) and services restarted; Simon
bridge container rebuilt. DB backed up first to
`/data/orchestrator.db.bak-20260811-preturns`, then migrated to WAL — verified
`journal_mode=wal` on the live file.

**Verified live:** a text turn writes a complete row (intent, response,
timings); a real Simon wake landed as a row with `reject_reason='empty'`; the
`/telemetry` back-post was exercised from the kitchen box over the network and
merged onto an existing row. Test telemetry was cleared from the table
afterwards so it cannot pollute latency stats.

### Design notes worth keeping

- **The `/verify` INSERT is synchronous on purpose.** It sits on the chime
  path, which is the 500ms budget, so the instinct is to fire-and-forget it.
  Don't: the satellite POSTs `/telemetry` the moment `/verify` returns, and an
  async INSERT can lose that race — the UPDATE lands first and no-ops. A local
  WAL commit is sub-millisecond against an ASR call already costing ~360ms.
- **`classify_ms IS NULL` means the regex fast path hit**, not missing data.
  The dashboard should read it that way — it is a free measure of fast-path
  coverage.
- **Simon's Voice PE bridge sends no `/telemetry`.** The Voice PE plays its own
  chime in ESPHome, so the bridge process never sees a trigger→chime number.
  Simon rows will always have NULL `chime_ms`; that is correct, not a gap. It
  does thread `turn_id`, so its turns are still one row.
- **The Simon bridge starts in shadow mode by design** (`MODE: shadow` pinned
  in compose, and the code refuses `active` at startup — a deliberate
  interlock). Any rebuild of that container needs
  `POST :8793/mode {"mode":"active"}` afterwards or Simon's room is silently
  out of service. The Pi satellites do not share this: they carry `MODE=active`
  in their `.env` and come back armed.

### Pending

- **Live-voice test**: say "okay computer" at the kitchen and master closet
  mics and confirm `chime_ms` / `stage1_score` / `wake_model` land on the row.
  This is the one link only real audio exercises.
- The 4 text turns used to smoke-test are still in the table (`kind='text'`).
  Harmless; they are excluded from wake-funnel arithmetic by kind.

### First real measurement

The classifier number that never existed before: a shopping-list add measured
**`classify_ms` 5,370** against a 7,273ms turn — the local LLM is ~74% of a
non-fast-path turn, with `handler_ms` 1,745 and `tts_ms` 158 behind it. Worth
a look once Phase 3 can chart it across intents.
