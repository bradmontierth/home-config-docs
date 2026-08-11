"""Import a satellite's historical events.jsonl into the turns table.

Phase 2 of voice-ops-dashboard-plan.md. The satellites have been writing an
append-only event log since well before the turns table existed — the kitchen
alone has 25 days and ~9,400 events — and none of it has ever been queryable.
Importing it means the dashboard opens with real trend lines instead of a flat
three-week wait.

Run it as a module against one or more exported logs:

    python -m orchestrator.backfill_events kitchen=/tmp/kitchen-events.jsonl
    python -m orchestrator.backfill_events --dry-run kitchen=/tmp/k.jsonl

Reconstructing turns from a flat stream
---------------------------------------
The log is per-satellite and strictly ordered, and a turn is a run of events:
`trigger` (stage 1 fired) → `verify` (stage 2 verdict) → `command` (what it
did). So a `trigger` opens a turn and the next trigger closes it. `followup`
events have no wake step at all and stand alone.

That ordering is the only join key available — the historical events carry no
turn id — so this is a reconstruction, not a recovery, and it is marked as one:
every row gets `backfilled=1`.

Tolerating old event shapes
---------------------------
The log spans several rounds of instrumentation, and missing fields are the
normal case rather than corruption:

* `chime_ms` only exists after the 2026-07-12 wake-latency work.
* `model` only exists after dual wake words landed 2026-07-18; before that
  every trigger was okay_computer.
* Early `trigger` events have no following `verify` at all — the satellite was
  in shadow mode, which is a real state and is preserved as such.

Anything unparseable is counted and skipped. One bad line must never abort an
import of thousands of good ones.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from typing import Any, Iterator

from . import config, turns

# Turn-opening / turn-joining event types. Everything else in the log
# (`start`, `mark`, `mark_scores`, `alarm_stop_model`) describes the box rather
# than a turn and is deliberately ignored.
_OPENS = "trigger"
_JOINS = ("verify", "command")
_STANDALONE = "followup"


def _epoch(ts: str) -> float | None:
    """Satellite timestamps are ISO-8601 with an offset
    ("2026-08-11T08:04:18.641-06:00"). Older lines may lack sub-seconds."""
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def _turn_id(sat: str, at: float) -> str:
    """Deterministic id from the turn's first event, so re-running the import
    updates rows instead of duplicating them. Same 32-hex shape as uuid4().hex
    so nothing downstream can tell the two apart by format alone."""
    return hashlib.sha1(f"{sat}:{at:.3f}".encode()).hexdigest()[:32]


def _reject_reason(ev: dict[str, Any]) -> str | None:
    if ev.get("suppressed"):
        return "suppressed"
    if ev.get("verified"):
        return None
    return "empty" if not (ev.get("transcript") or "").strip() else "low_score"


def read_events(path: str) -> Iterator[dict[str, Any]]:
    bad = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                bad += 1
    if bad:
        print(f"  ({bad} unparseable lines skipped)", file=sys.stderr)


def build_turns(events: Iterator[dict[str, Any]], sat: str) -> list[dict[str, Any]]:
    """Fold the event stream into turn rows."""
    rows: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    def close() -> None:
        nonlocal cur
        if cur is not None:
            rows.append(cur)
            cur = None

    for ev in events:
        etype = ev.get("type")
        at = _epoch(ev.get("ts", ""))
        if at is None:
            continue

        if etype == _OPENS:
            close()
            cur = {
                "turn_id": _turn_id(sat, at), "at": at, "sat": sat,
                "kind": "wake", "backfilled": 1,
                "stage1_score": ev.get("peak_score"),
                # Pre-dual-wake triggers had only one model to be.
                "wake_model": ev.get("model") or "okay_computer",
            }
            continue

        if etype == _STANDALONE:
            close()
            rows.append({
                "turn_id": _turn_id(sat, at), "at": at, "sat": sat,
                "kind": "followup", "backfilled": 1,
                "command": ev.get("transcript"), "intent": ev.get("intent"),
                "response": ev.get("response"), "clip": ev.get("clip"),
            })
            continue

        if etype not in _JOINS:
            continue

        if cur is None:
            # A verify or command with no preceding trigger: a truncated log, or
            # the manual/button path, which never fires stage 1. Keep it as its
            # own turn rather than dropping real data on the floor.
            cur = {"turn_id": _turn_id(sat, at), "at": at, "sat": sat,
                   "kind": "manual", "backfilled": 1}

        if etype == "verify":
            cur.update(
                verified=int(bool(ev.get("verified"))),
                wake_score=ev.get("score"),
                decode=ev.get("decode"),
                reject_reason=_reject_reason(ev),
                arb_winner=ev.get("winner"),
                transcript=ev.get("transcript"),
                chime_ms=ev.get("chime_ms"),
                rtt_ms=ev.get("rtt_ms"),
                server_ms=ev.get("server_ms"),
                clip=ev.get("clip"),
            )
            # A rejected or suppressed wake ends the turn there.
            if not ev.get("verified"):
                close()
        elif etype == "command":
            cur.update(command=ev.get("transcript"), intent=ev.get("intent"),
                       response=ev.get("response"), clip=ev.get("clip") or cur.get("clip"))
            close()

    close()
    return rows


def write_turns(rows: list[dict[str, Any]], *, dry_run: bool = False) -> int:
    """INSERT OR REPLACE by turn_id, so a re-run corrects rather than doubles.

    Refuses to overwrite a live row. Ids are derived from timestamps and live
    ids are random, so a collision is essentially impossible — but "essentially"
    is not a good enough reason to let an importer clobber real telemetry."""
    if dry_run:
        return len(rows)
    conn = turns._conn()
    live = {r[0] for r in conn.execute(
        "SELECT turn_id FROM turns WHERE backfilled IS NOT 1")}
    written = 0
    for row in rows:
        if row["turn_id"] in live:
            print(f"  skipping {row['turn_id']}: collides with a live turn",
                  file=sys.stderr)
            continue
        cols = [k for k, v in row.items() if v is not None]
        conn.execute(
            f"INSERT OR REPLACE INTO turns ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [row[c] for c in cols])
        written += 1
    conn.commit()
    return written


def summarize(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no turns"
    verified = sum(1 for r in rows if r.get("verified"))
    wake = sum(1 for r in rows if r.get("kind") == "wake")
    chimes = [r["chime_ms"] for r in rows if r.get("chime_ms")]
    cmds = sum(1 for r in rows if r.get("intent"))
    span = (datetime.fromtimestamp(min(r["at"] for r in rows)).date(),
            datetime.fromtimestamp(max(r["at"] for r in rows)).date())
    out = (f"{len(rows)} turns ({wake} wake, {verified} verified, {cmds} with "
           f"an intent) {span[0]} -> {span[1]}")
    if chimes:
        chimes.sort()
        out += (f"; chime_ms n={len(chimes)} p50={chimes[len(chimes)//2]} "
                f"p90={chimes[int(len(chimes)*.9)]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sources", nargs="+", metavar="SAT=PATH",
                    help="satellite id and its exported events.jsonl")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and summarize, write nothing")
    ap.add_argument("--db", help="override the turns database path")
    args = ap.parse_args(argv)

    if args.db:
        config.DB_PATH = args.db
        turns._db = None

    total = 0
    for source in args.sources:
        if "=" not in source:
            ap.error(f"expected SAT=PATH, got {source!r}")
        sat, path = source.split("=", 1)
        rows = build_turns(read_events(path), sat)
        print(f"{sat}: {summarize(rows)}")
        written = write_turns(rows, dry_run=args.dry_run)
        print(f"{sat}: {'would write' if args.dry_run else 'wrote'} {written} rows")
        total += written
    print(f"total: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
