"""Per-turn stage timing.

Until now the only latency number recorded was end-to-end `latency_ms`, which
tells you a turn was slow but not which stage got slower. This splits it into
ASR / classifier / TTS so a regression is attributable — the local LLM
classifier in particular had no timing at all.

Implemented as a ContextVar holding a plain dict, so the timers can live at the
three `clients.py` call sites and pick up whichever turn is in flight without
threading a stopwatch through every intent handler. Turns from different
satellites interleave on the event loop; each request coroutine gets its own
copy of the var, and deeper frames mutate the dict it points at rather than
rebinding it.

Outside a turn (alarm-window ASR, the ask filler pre-render, startup work) the
var is None and every timer is a no-op — no bookkeeping for audio that is not
part of anybody's turn.
"""

from __future__ import annotations

import contextlib
import contextvars
import time

_TURN: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "turn_timing", default=None)


def start() -> dict[str, int]:
    """Begin timing a turn. Returns the dict the stage timers will fill in;
    hold on to it rather than calling snapshot() if you like."""
    d: dict[str, int] = {}
    _TURN.set(d)
    return d


@contextlib.contextmanager
def stage(name: str):
    """Add the wall time of this block to `name` for the turn in flight.

    Accumulates rather than overwrites: a turn can legitimately hit a stage
    more than once (the dual-decode tail rescue runs ASR twice, a slot-fill
    turn parses twice, a zone reply renders TTS separately), and the useful
    number is total time spent in that stage."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        d = _TURN.get()
        if d is not None:
            ms = round((time.perf_counter() - t0) * 1000)
            d[name] = d.get(name, 0) + ms


def snapshot() -> dict[str, int]:
    """The stage times recorded so far for the turn in flight ({} if none)."""
    return dict(_TURN.get() or {})
