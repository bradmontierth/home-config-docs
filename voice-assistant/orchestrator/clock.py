"""Time and date questions, answered from the clock.

"What time is it" is the one question in the house that never needed a model:
the answer is already sitting in the process. It used to route to "ask", which
shipped the question to a remote model — seconds of latency and a few cents,
occasionally a web search — to read back a clock we were holding the whole time.

The kinds are separated because the natural spoken answers differ in length,
not because people are precise about the words. "What day is it" almost always
wants the whole date, so it answers like "what's the date" does; only an
explicit "day of the week" gets the bare weekday back.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config

KINDS = ("time", "date", "weekday", "month", "year")
DAYS = ("today", "tomorrow", "yesterday")

_OFFSETS = {"today": 0, "tomorrow": 1, "yesterday": -1}
# A shifted day needs its own sentence frame; "It's Thursday, January 14" for
# tomorrow would be a wrong answer, not just an awkward one.
_FRAMES = {
    "today": "It's {}.",
    "tomorrow": "Tomorrow is {}.",
    "yesterday": "Yesterday was {}.",
}


def _now() -> datetime:
    # ASK_TIMEZONE is the household timezone — named for its first consumer,
    # the ask prompt. The container itself runs UTC, so this is not optional.
    return datetime.now(ZoneInfo(config.ASK_TIMEZONE))


def spoken_time(dt: datetime) -> str:
    """"3:42 PM", and "3 PM" on the hour — the same shape sports.py speaks game
    times in, so the TTS voice is already known to read it correctly."""
    return dt.strftime("%-I:%M %p").replace(":00 ", " ")


def spoken_date(dt: datetime) -> str:
    return f"{dt:%A}, {dt:%B} {dt.day}, {dt.year}"


def answer(parsed: dict, now: datetime | None = None) -> str:
    kind = parsed.get("time_kind") or "time"
    day = parsed.get("time_day") or "today"
    now = now or _now()
    if kind == "time":
        # A clock reading is always about right now: nobody asking "what time
        # is it tomorrow" means it literally, so time_day is ignored here.
        return f"It's {spoken_time(now)}."
    if kind == "month":
        return f"It's {now:%B}."
    if kind == "year":
        return f"It's {now.year}."
    target = now + timedelta(days=_OFFSETS.get(day, 0))
    text = f"{target:%A}" if kind == "weekday" else spoken_date(target)
    return _FRAMES.get(day, _FRAMES["today"]).format(text)


def handle(parsed: dict) -> dict:
    """Sync, unlike the other intent handlers — there is nothing to await."""
    return {"response": answer(parsed), "ok": True}
