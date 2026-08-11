"""One place that knows how to open the shared orchestrator database.

Every module here (timers, answers, music_log, turns) keeps its own connection
to the same file and its own schema — that stays. What must NOT be per-module
is the journal mode, which is a persistent property of the database file and
therefore a property of the whole system.

Why WAL matters here (2026-08-11): the database was created in the default
`delete` (rollback-journal) mode, where a reader holds a SHARED lock and a
writer needs EXCLUSIVE. That is fine while the orchestrator is the only
process touching the file, but the voice-ops dashboard reads this same
database from another container. Under rollback journalling a slow dashboard
query can block a live turn from writing — a read-only observer able to stall
the voice path. WAL gives one writer concurrent with many readers instead.

The pragma is idempotent and cheap, so it runs on every open rather than being
a one-off migration somebody has to remember to have applied.
"""

from __future__ import annotations

import logging
import os
import sqlite3

log = logging.getLogger("orchestrator.db")


def connect(path: str) -> sqlite3.Connection:
    """Open `path` with the house settings: WAL, Row factory, and cross-thread
    access allowed (the same single-event-loop pattern every caller here uses).

    Creates the parent directory if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            # Not fatal: the orchestrator still works in rollback-journal mode,
            # it is only the concurrent dashboard reader that suffers. Log loud
            # enough to be findable, then carry on.
            log.warning("journal_mode is %r, not WAL — a concurrent reader can "
                        "block turn writes", mode)
    except sqlite3.Error as exc:  # noqa: BLE001 — never block startup on a pragma
        log.warning("could not set WAL on %s: %s", path, exc)
    return conn
