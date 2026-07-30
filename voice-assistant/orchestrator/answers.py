"""Durable record of every answered question.

Ask answers cost real money and, until now, lived only in RAM: four turns deep
(ASK_HISTORY_TURNS), thirty minutes of recall (ASK_RECALL_TTL_S), and gone
entirely on a restart. Anything the house paid GPT to find out was
unrecoverable by dinner. This module writes each Q+A to the same SQLite file
the timers use so the kitchen display can browse back through them.

Two things shape the API:

* The full answer streams in AFTER the spoken part has already returned, so a
  row is written the moment the spoken answer exists and PATCHED when the
  stream finishes. An interrupted stream then leaves a short-but-real answer
  on the shelf rather than nothing at all.
* Nothing here may ever break a turn. Every call swallows its exceptions and
  logs — a wedged database must not cost the kitchen its answer.

Only questions worth revisiting are stored: paid `ask` turns and `sports`.
Weather and places are deliberately excluded — they are transient, they have
their own views, and they would bury the expensive answers in noise.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any

from . import config

log = logging.getLogger("orchestrator.answers")

# Deep enough to cover "what did we look up last month?", small enough that the
# table never becomes a thing anyone has to think about.
MAX_ROWS = 500

# `full` is a SQL keyword (FULL OUTER JOIN) — full_text keeps the schema quiet.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at  REAL NOT NULL,
    source    TEXT NOT NULL,
    question  TEXT NOT NULL,
    spoken    TEXT NOT NULL,
    full_text TEXT,
    cost_usd  REAL,
    searches  INTEGER,
    tokens    INTEGER
);
CREATE INDEX IF NOT EXISTS answers_asked_at ON answers(asked_at DESC);
"""

_db: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    """Open (once) the shared orchestrator database. Same file as the timers,
    same single-event-loop access pattern, so check_same_thread is off for the
    same reason it is there."""
    global _db
    if _db is None:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        _db = conn
    return _db


def _stats_fields(stats: dict | None) -> tuple[float | None, int | None, int | None]:
    """Pull cost / web-search count / total tokens out of an OpenRouter usage
    block. These are already computed for the log line in openrouter.py; the
    only new thing here is keeping them."""
    if not stats:
        return None, None, None
    cost = stats.get("cost")
    return (
        float(cost) if cost is not None else None,
        stats.get("searches"),
        stats.get("tokens"),
    )


def record(
    question: str,
    spoken: str,
    full: str = "",
    *,
    source: str = "ask",
    stats: dict | None = None,
) -> int | None:
    """Store a Q+A and return its row id (None if the write failed — callers
    treat that as "not recallable" and carry on)."""
    question, spoken = question.strip(), spoken.strip()
    if not question or not spoken:
        return None
    cost, searches, tokens = _stats_fields(stats)
    try:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO answers (asked_at, source, question, spoken, full_text,"
            " cost_usd, searches, tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), source, question, spoken, full.strip() or None,
             cost, searches, tokens),
        )
        conn.commit()
        _prune(conn)
        return cur.lastrowid
    except Exception as exc:  # noqa: BLE001 — never cost the turn its answer
        log.warning("answer record failed: %s", exc)
        return None


def finish(row_id: int | None, full: str, stats: dict | None = None) -> None:
    """Patch in the full answer once the background stream drains. Usage stats
    only exist at that point too, so they land here for streamed turns."""
    if not row_id:
        return
    cost, searches, tokens = _stats_fields(stats)
    try:
        conn = _conn()
        # COALESCE so a stats-less call can never blank out numbers a previous
        # write already captured.
        conn.execute(
            "UPDATE answers SET full_text = ?, cost_usd = COALESCE(?, cost_usd),"
            " searches = COALESCE(?, searches), tokens = COALESCE(?, tokens)"
            " WHERE id = ?",
            (full.strip() or None, cost, searches, tokens, row_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("answer finish failed: %s", exc)


def _prune(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM answers WHERE id NOT IN"
        " (SELECT id FROM answers ORDER BY asked_at DESC LIMIT ?)",
        (MAX_ROWS,),
    )
    conn.commit()


def _row(row: sqlite3.Row, *, body: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row["id"],
        "asked_at": row["asked_at"],
        "source": row["source"],
        "question": row["question"],
        "spoken": row["spoken"],
        "cost_usd": row["cost_usd"],
        "searches": row["searches"],
        "tokens": row["tokens"],
    }
    if body:
        out["full"] = row["full_text"] or row["spoken"]
    return out


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, without the full bodies — the sidebar only needs the
    question and its price tag."""
    limit = max(1, min(int(limit), MAX_ROWS))
    try:
        rows = _conn().execute(
            "SELECT * FROM answers ORDER BY asked_at DESC LIMIT ?", (limit,)
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("answer list failed: %s", exc)
        return []
    return [_row(r, body=False) for r in rows]


def get(row_id: int) -> dict[str, Any] | None:
    try:
        row = _conn().execute(
            "SELECT * FROM answers WHERE id = ?", (row_id,)
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        log.warning("answer fetch failed: %s", exc)
        return None
    return _row(row, body=True) if row else None
