"""Cooking-timer engine: SQLite state + a single asyncio expiry scheduler.

Design (see voice-assistant-plan.md "Timers Design"):
- Absolute `ends_at` epoch seconds in SQLite -> restart-safe; a reboot during a
  25-minute roast resumes with the right time left.
- No per-second tick streaming: the dashboard gets whole timer objects on
  change and counts down locally.
- Announcement WAV ("Your chicken timer is done") is pre-rendered at creation
  and cached on disk, so the alarm is instant and independent of the GX10.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from . import clients, config

log = logging.getLogger("orchestrator.timers")

# state values
RUNNING = "running"
RINGING = "ringing"
DONE = "done"
CANCELLED = "cancelled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS timers (
    id             TEXT PRIMARY KEY,
    label          TEXT,
    sound_theme    TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    created_at     REAL NOT NULL,
    ends_at        REAL NOT NULL,
    state          TEXT NOT NULL,
    announce_wav   TEXT
);
"""

ExpireCb = Callable[[dict[str, Any]], Awaitable[None]]


def announcement_text(label: str | None) -> str:
    return f"Your {label} timer is done." if label else "Your timer is done."


class TimerEngine:
    def __init__(self, on_expire: ExpireCb | None = None) -> None:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        os.makedirs(config.ANNOUNCE_CACHE_DIR, exist_ok=True)
        self._db = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)
        self._db.commit()
        self._on_expire = on_expire
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="timer-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- queries -----------------------------------------------------------
    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["remaining_seconds"] = max(0, round(d["ends_at"] - time.time()))
        d["has_announcement"] = bool(d.get("announce_wav"))
        d.pop("announce_wav", None)
        return d

    def active(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM timers WHERE state IN (?, ?) ORDER BY ends_at",
            (RUNNING, RINGING),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, timer_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM timers WHERE id=?", (timer_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def announce_wav_path(self, timer_id: str) -> str | None:
        row = self._db.execute(
            "SELECT announce_wav FROM timers WHERE id=?", (timer_id,)
        ).fetchone()
        return row["announce_wav"] if row and row["announce_wav"] else None

    def _resolve(self, label: str | None) -> sqlite3.Row | None:
        """Find one active timer for a command. Named match first; otherwise
        (ringing beats nearest-to-finish) so 'stop the timer' does the sane
        thing. Returns the raw row."""
        if label:
            row = self._db.execute(
                "SELECT * FROM timers WHERE state IN (?,?) AND label=? ORDER BY ends_at LIMIT 1",
                (RUNNING, RINGING, label),
            ).fetchone()
            if row:
                return row
        # ringing wins, then soonest to finish
        return self._db.execute(
            "SELECT * FROM timers WHERE state IN (?,?) "
            "ORDER BY (state=?) DESC, ends_at ASC LIMIT 1",
            (RUNNING, RINGING, RINGING),
        ).fetchone()

    # -- mutations ---------------------------------------------------------
    async def create(
        self, label: str | None, duration_seconds: int, sound_theme: str
    ) -> dict[str, Any]:
        now = time.time()
        timer_id = uuid.uuid4().hex[:12]
        ends_at = now + duration_seconds
        wav_path = await self._prerender(timer_id, label)
        self._db.execute(
            "INSERT INTO timers VALUES (?,?,?,?,?,?,?,?)",
            (timer_id, label, sound_theme, duration_seconds, now, ends_at, RUNNING, wav_path),
        )
        self._db.commit()
        self._wake.set()
        log.info("timer created id=%s label=%s dur=%ss theme=%s",
                 timer_id, label, duration_seconds, sound_theme)
        return self.get(timer_id)  # type: ignore[return-value]

    async def adjust(self, label: str | None, delta_seconds: int) -> dict[str, Any] | None:
        async with self._lock:
            row = self._resolve(label)
            if not row:
                return None
            ends_at = max(time.time(), row["ends_at"] + delta_seconds)
            duration = max(0, row["duration_seconds"] + delta_seconds)
            # coming back from ringing to running if we added time
            new_state = RUNNING if ends_at > time.time() else row["state"]
            self._db.execute(
                "UPDATE timers SET ends_at=?, duration_seconds=?, state=? WHERE id=?",
                (ends_at, duration, new_state, row["id"]),
            )
            self._db.commit()
        self._wake.set()
        return self.get(row["id"])

    def cancel(self, label: str | None) -> dict[str, Any] | None:
        row = self._resolve(label)
        if not row:
            return None
        self._db.execute("UPDATE timers SET state=? WHERE id=?", (CANCELLED, row["id"]))
        self._db.commit()
        self._wake.set()
        return self.get(row["id"])

    def cancel_all(self) -> list[dict[str, Any]]:
        active = self.active()
        self._db.execute(
            "UPDATE timers SET state=? WHERE state IN (?,?)", (CANCELLED, RUNNING, RINGING)
        )
        self._db.commit()
        self._wake.set()
        return active

    def cancel_by_id(self, timer_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM timers WHERE id=? AND state IN (?,?)",
            (timer_id, RUNNING, RINGING),
        ).fetchone()
        if not row:
            return None
        self._db.execute("UPDATE timers SET state=? WHERE id=?", (CANCELLED, timer_id))
        self._db.commit()
        self._wake.set()
        return self.get(timer_id)

    def dismiss(self, timer_id: str) -> dict[str, Any] | None:
        """Silence a ringing timer (touch tap or voice)."""
        row = self._db.execute(
            "SELECT * FROM timers WHERE id=? AND state=?", (timer_id, RINGING)
        ).fetchone()
        if not row:
            return None
        self._db.execute("UPDATE timers SET state=? WHERE id=?", (DONE, timer_id))
        self._db.commit()
        return self.get(timer_id)

    def dismiss_any_ringing(self) -> dict[str, Any] | None:
        """Any wake word during an alarm silences the ringing timer."""
        row = self._db.execute(
            "SELECT id FROM timers WHERE state=? ORDER BY ends_at LIMIT 1", (RINGING,)
        ).fetchone()
        return self.dismiss(row["id"]) if row else None

    # -- internals ---------------------------------------------------------
    async def _prerender(self, timer_id: str, label: str | None) -> str | None:
        """Render the announcement WAV now so the alarm is instant later.
        Best-effort: a TTS failure just leaves it null (rendered on demand)."""
        try:
            wav = await clients.synthesize(announcement_text(label))
        except Exception as exc:  # noqa: BLE001 — TTS is non-critical at create
            log.warning("announcement prerender failed for %s: %s", timer_id, exc)
            return None
        path = os.path.join(config.ANNOUNCE_CACHE_DIR, f"{timer_id}.wav")
        with open(path, "wb") as fh:
            fh.write(wav)
        return path

    async def _run(self) -> None:
        """Sleep until the next ends_at, fire expiries, repeat. Woken early by
        any mutation via self._wake."""
        # Catch up on anything that expired while we were down.
        await self._fire_due()
        while True:
            row = self._db.execute(
                "SELECT MIN(ends_at) AS next FROM timers WHERE state=?", (RUNNING,)
            ).fetchone()
            next_at = row["next"]
            timeout = None if next_at is None else max(0.0, next_at - time.time())
            self._wake.clear()
            try:
                if timeout is None:
                    await self._wake.wait()
                else:
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass  # a timer is due
            await self._fire_due()

    async def _fire_due(self) -> None:
        now = time.time()
        due = self._db.execute(
            "SELECT * FROM timers WHERE state=? AND ends_at<=?", (RUNNING, now)
        ).fetchall()
        for row in due:
            self._db.execute("UPDATE timers SET state=? WHERE id=?", (RINGING, row["id"]))
            self._db.commit()
            timer = self.get(row["id"])
            log.info("timer EXPIRED id=%s label=%s", row["id"], row["label"])
            if self._on_expire and timer:
                try:
                    await self._on_expire(timer)
                except Exception:  # noqa: BLE001
                    log.exception("on_expire callback failed for %s", row["id"])
