"""Durable record of how each play request resolved.

Until now a play request left no trace anywhere durable. A success logged one
INFO line, a refusal logged NOTHING at all (the LookupError is caught in app.py
and turned straight into speech), and both died with the container — the
"jupiter by holst" miss on 2026-08-06 was only diagnosable because the logs
happened to still be in the buffer, and they were wiped by a redeploy an hour
later.

That matters because the resolver's bars are the one part of music that is
pure calibration. Tuning them from memory means arguing about anecdotes; this
table makes it arithmetic. Every resolution stores the winning score AND the
whole per-bucket ranking, so the question that actually decides the bars —
"when we refuse, how close were we?" — is a SQL query rather than a guess:

    SELECT query, score FROM music_resolutions
     WHERE decision = 'refuse' ORDER BY score DESC;

A near-miss population clustered just under the bar means the bar is wrong; a
population down at 40 means we genuinely don't own the music and refusing is
the right answer.

Same file and same swallow-everything discipline as answers.py: a wedged
database must never cost the kitchen its song.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from . import config, db

log = logging.getLogger("orchestrator.music_log")

# Plays are far more frequent than paid questions, and the rows are tiny
# (a query string and a short JSON blob), so this keeps a much deeper tail
# than answers.MAX_ROWS — band tuning wants weeks, not days.
MAX_ROWS = 2000

# How many ranked candidates to keep per resolution. The resolver only ever
# tracks one entry per bucket, so this is four in practice; the cap is here so
# a future multi-candidate ranker can't quietly bloat the rows.
MAX_CANDIDATES = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS music_resolutions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    query      TEXT NOT NULL,
    decision   TEXT NOT NULL,
    via        TEXT,
    score      REAL,
    kind       TEXT,
    name       TEXT,
    uri        TEXT,
    candidates TEXT
);
CREATE INDEX IF NOT EXISTS music_resolutions_at ON music_resolutions(at DESC);
CREATE INDEX IF NOT EXISTS music_resolutions_decision
    ON music_resolutions(decision, score DESC);
"""

_db: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    """Open (once) the shared orchestrator database — same file as the timers
    and answers, same single-event-loop access pattern."""
    global _db
    if _db is None:
        conn = db.connect(config.DB_PATH)
        conn.executescript(_SCHEMA)
        conn.commit()
        _db = conn
    return _db


def record(query: str, decision: str, *, winner: dict | None = None,
           via: str | None = None, candidates: list[dict] | None = None) -> None:
    """Store one resolution. `decision` is 'play' or 'refuse'.

    score/kind/name/uri describe the winner on a play and THE CLOSEST THING WE
    HAD on a refusal — `decision` already says which, and putting the near-miss
    score in a real column is the whole reason this table exists (leaving it
    NULL on refusals would have made the one query worth running impossible).

    `candidates` is the full per-bucket ranking, kept even on a play so a
    wrong-song complaint can be traced to what the runner-up was."""
    query = (query or "").strip()
    if not query:
        return
    try:
        blob = (json.dumps(candidates[:MAX_CANDIDATES], separators=(",", ":"))
                if candidates else None)
        conn = _conn()
        conn.execute(
            "INSERT INTO music_resolutions (at, query, decision, via, score,"
            " kind, name, uri, candidates) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), query, decision, via,
             (winner or {}).get("score"), (winner or {}).get("kind"),
             (winner or {}).get("name"), (winner or {}).get("uri"), blob),
        )
        conn.commit()
        _prune(conn)
    except Exception as exc:  # noqa: BLE001 — never cost the turn its song
        log.warning("music resolution record failed: %s", exc)


def _prune(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM music_resolutions WHERE id NOT IN"
        " (SELECT id FROM music_resolutions ORDER BY at DESC LIMIT ?)",
        (MAX_ROWS,),
    )
    conn.commit()


def recent(limit: int = 100, decision: str | None = None) -> list[dict[str, Any]]:
    """Newest first, optionally only plays or only refusals."""
    limit = max(1, min(int(limit), MAX_ROWS))
    sql = "SELECT * FROM music_resolutions"
    params: list[Any] = []
    if decision:
        sql += " WHERE decision = ?"
        params.append(decision)
    sql += " ORDER BY at DESC LIMIT ?"
    params.append(limit)
    try:
        rows = _conn().execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("music resolution list failed: %s", exc)
        return []
    out = []
    for r in rows:
        d = dict(r)
        d["candidates"] = json.loads(d["candidates"]) if d["candidates"] else []
        out.append(d)
    return out
