"""One durable row per voice turn — the telemetry spine for the voice-ops
dashboard (`home_config/voice-ops-dashboard-plan.md`).

Before this, turn history was scattered across three stores and one of them was
ephemeral: stage-1/stage-2 wake data lived in a per-satellite `events.jsonl`
that never left the box, the chosen intent and the spoken reply existed only in
`docker logs voice-orchestrator` until they rotated away, and speaker scoring
went to its own JSONL. Nothing could answer "is wake latency worse than last
week?" without an SSH expedition.

The orchestrator is the one process every turn passes through, so it writes the
row. Shape is one row per TURN, not per HTTP call: `/verify` inserts it and
returns the id, then the satellite's `/telemetry` back-post and the following
`/command/audio` update that same row. Turns with no wake step (follow-ups,
text, the dashboard mic button) insert on first sight.

Two fields can only be measured on the satellite — `chime_ms` (trigger→chime,
the 500ms number) and `rtt_ms` — so they arrive by back-post rather than being
guessed at from this side.

Everything here is best-effort. A telemetry failure must never break a turn:
every public function swallows its exceptions and the callers fire them off
without awaiting anything that matters.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from . import config, db

log = logging.getLogger("orchestrator.turns")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id        TEXT PRIMARY KEY,
    at             REAL NOT NULL,
    sat            TEXT,
    kind           TEXT,
    stage1_score   REAL,
    wake_model     TEXT,
    verified       INTEGER,
    wake_score     REAL,
    decode         TEXT,
    reject_reason  TEXT,
    arb_winner     TEXT,
    transcript     TEXT,
    command        TEXT,
    intent         TEXT,
    slots          TEXT,
    response       TEXT,
    speaker        TEXT,
    speaker_score  REAL,
    speaker_margin REAL,
    chime_ms       INTEGER,
    rtt_ms         INTEGER,
    asr_ms         INTEGER,
    classify_ms    INTEGER,
    handler_ms     INTEGER,
    tts_ms         INTEGER,
    total_ms       INTEGER,
    clip           TEXT,
    ok             INTEGER,
    backfilled     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS turns_at  ON turns(at DESC);
CREATE INDEX IF NOT EXISTS turns_sat ON turns(sat, at DESC);
"""

# Columns an UPDATE is allowed to touch. A typo in a caller's kwarg should be a
# dropped field in a log line, not a SQL injection surface or a crash mid-turn.
_UPDATABLE = frozenset({
    "sat", "kind", "stage1_score", "wake_model", "verified", "wake_score",
    "decode", "reject_reason", "arb_winner", "transcript", "command", "intent",
    "slots", "response", "speaker", "speaker_score", "speaker_margin",
    "chime_ms", "rtt_ms", "asr_ms", "classify_ms", "handler_ms", "tts_ms",
    "total_ms", "clip", "ok",
})

_db: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    """Open (once) the shared orchestrator database — same file as the timers,
    answers and music log, same single-event-loop access pattern."""
    global _db
    if _db is None:
        conn = db.connect(config.DB_PATH)
        conn.executescript(_SCHEMA)
        conn.commit()
        _db = conn
    return _db


def new_id() -> str:
    return uuid.uuid4().hex


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown/None fields and JSON-encode the one dict column.

    None is dropped rather than written so a later update can't blank a value
    an earlier one set — `/command/audio` knows nothing about `chime_ms` and
    must not erase the back-post that already landed."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key not in _UPDATABLE:
            log.debug("turns: ignoring unknown field %r", key)
            continue
        if key == "slots" and not isinstance(value, str):
            try:
                value = json.dumps(value, default=str)
            except (TypeError, ValueError):
                continue
        elif isinstance(value, bool):
            value = int(value)
        out[key] = value
    return out


def start(sat: str | None, kind: str, **fields: Any) -> str:
    """Insert a turn and return its id. Returns the id even on a write failure
    so callers can hand it to the satellite regardless — a turn that is missing
    from the table is a hole in a chart, not a broken turn."""
    turn_id = new_id()
    try:
        row = _clean({"sat": sat, "kind": kind, **fields})
        cols = ["turn_id", "at", *row]
        conn = _conn()
        conn.execute(
            f"INSERT INTO turns ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [turn_id, time.time(), *row.values()],
        )
        conn.commit()
        _prune(conn)
    except Exception as exc:  # noqa: BLE001 — telemetry never breaks a turn
        log.warning("turns start failed: %s", exc)
    return turn_id


def update(turn_id: str | None, **fields: Any) -> None:
    """Merge fields into an existing turn. No-op on an unknown or missing id —
    a satellite that restarts mid-turn, or a `/command/audio` whose `/verify`
    predates this feature, should be silently partial rather than an error."""
    if not turn_id:
        return
    try:
        row = _clean(fields)
        if not row:
            return
        conn = _conn()
        conn.execute(
            f"UPDATE turns SET {','.join(f'{k}=?' for k in row)} WHERE turn_id=?",
            [*row.values(), turn_id],
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("turns update failed: %s", exc)


def finish(turn_id: str | None, result: dict[str, Any], *,
           timings: dict[str, int] | None = None,
           total_ms: int | None = None) -> None:
    """Record the outcome of a dispatched turn: intent, spoken reply, per-stage
    timings.

    `handler_ms` is a RESIDUAL — total minus the three measured stages — so it
    covers intent dispatch plus anything else on the turn's critical path
    (Home Assistant calls, Music Assistant, a Node-RED hop). Speaker embedding
    runs concurrently and so is deliberately not subtracted; the residual is a
    budget, not a sum of parts. Clamped at zero because the stage timers
    accumulate re-entrant calls and can, on a slot-fill turn, exceed the outer
    wall clock.
    """
    timings = dict(timings or {})
    fields: dict[str, Any] = {
        "intent": result.get("intent"),
        "response": result.get("response"),
        "ok": result.get("ok"),
        "asr_ms": timings.get("asr"),
        "classify_ms": timings.get("classify"),
        "tts_ms": timings.get("tts"),
        "total_ms": total_ms,
    }
    if total_ms is not None:
        measured = sum(timings.get(k, 0) for k in ("asr", "classify", "tts"))
        fields["handler_ms"] = max(0, total_ms - measured)
    slots = result.get("slots")
    if slots:
        fields["slots"] = slots
    update(turn_id, **fields)


def note_speaker(turn_id: str | None, ident: dict[str, Any] | None) -> None:
    """Attach this turn's voice identification. `ident` is speaker.identify()'s
    result, including the "unsure" verdict — an unsure turn is exactly the data
    the enrollment work needs, so it is stored rather than dropped."""
    if not ident:
        return
    update(turn_id,
           speaker=ident.get("speaker"),
           speaker_score=ident.get("score"),
           speaker_margin=ident.get("margin"))


def _prune(conn: sqlite3.Connection) -> None:
    """Enforce config.TURNS_MAX_ROWS — DISABLED by default, and meant to stay
    that way.

    Turn text is tiny: measured 2026-08-11, a satellite's whole event history
    was 1.6 MB for 25 days, and a year of richer rows lands near 150 MB against
    1.3 TB free. The retained WAV clips are ~230x the size of the text
    describing them, and those are deliberately kept as the wake/stop/speaker
    training corpus. So there is no space argument for deleting history here.
    The knob exists only so a future operator has a lever without needing a
    migration."""
    limit = config.TURNS_MAX_ROWS
    if not limit:
        return
    conn.execute(
        "DELETE FROM turns WHERE turn_id IN ("
        "  SELECT turn_id FROM turns ORDER BY at DESC LIMIT -1 OFFSET ?)",
        (limit,),
    )
    conn.commit()


def recent(limit: int = 50, sat: str | None = None) -> list[dict[str, Any]]:
    """Newest turns first. Read path for the dashboard's own /turns endpoint
    and for eyeballing the table from a shell during the build."""
    try:
        conn = _conn()
        if sat:
            rows = conn.execute(
                "SELECT * FROM turns WHERE sat=? ORDER BY at DESC LIMIT ?",
                (sat, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM turns ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("turns recent failed: %s", exc)
        return []
