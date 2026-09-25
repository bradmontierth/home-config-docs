"""Nightly grid scan of Google Flights via fast-flights, with rate-limit detection."""
import logging
import threading
import time
from datetime import date, datetime, timedelta

from fast_flights import FlightQuery, Passengers, create_query, get_flights

from . import db

LOG = logging.getLogger("scan")

# UTC offsets in winter for the airports we care about; elapsed = arrival - departure across zones.
TZ_OFFSET = {"SLC": -7, "BOI": -7, "OGG": -10, "HNL": -10, "KOA": -10, "LIH": -10, "ITO": -10}

progress = {"running": False, "done": 0, "total": 0, "run_id": None, "started_at": None}
_scan_lock = threading.Lock()


def date_range(start: str, end: str):
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        yield d
        d += timedelta(days=1)


def elapsed_minutes(fl) -> int:
    legs = fl.flights
    first, last = legs[0], legs[-1]
    o, d = first.from_airport.code, last.to_airport.code
    if o in TZ_OFFSET and d in TZ_OFFSET:
        dep = datetime(*first.departure.date, *first.departure.time) - timedelta(hours=TZ_OFFSET[o])
        arr = datetime(*last.arrival.date, *last.arrival.time) - timedelta(hours=TZ_OFFSET[d])
        return int((arr - dep).total_seconds() // 60)
    return sum(l.duration for l in legs)


def fmt_time(t) -> str:
    return f"{t.time[0]:02d}:{t.time[1]:02d}"


def build_query(origin: str, dest: str, dep: str, ret: str, cap: int):
    return create_query(
        flights=[FlightQuery(date=dep, from_airport=origin, to_airport=dest, max_duration_minutes=cap),
                 FlightQuery(date=ret, from_airport=dest, to_airport=origin, max_duration_minutes=cap)],
        trip="round-trip", passengers=Passengers(adults=1), currency="USD")


def looks_blocked(exc: Exception) -> bool:
    """fast-flights fails on a captcha/429 page with a parse error (no ds:1 script), not a clean HTTP error."""
    return isinstance(exc, (AttributeError, IndexError, TypeError, KeyError, ValueError)) or "429" in repr(exc)


def scan(cfg: dict) -> dict:
    """Run one full grid pass. Returns a summary dict (also stored in runs)."""
    if not _scan_lock.acquire(blocking=False):
        return {"error": "scan already running"}
    try:
        return _scan(cfg)
    finally:
        _scan_lock.release()


def _scan(cfg: dict) -> dict:
    origins, dest = cfg["origins"], cfg["destination"]
    nights_list = cfg["nights"]
    cap = int(cfg["max_leg_minutes"])
    pace = float(cfg.get("pace_seconds", 4))
    block_after = int(cfg.get("blocked_after_consecutive_errors", 5))
    deps = [d.isoformat() for d in date_range(str(cfg["depart_from"]), str(cfg["depart_to"]))]
    grid = [(o, dep, n) for o in origins for dep in deps for n in nights_list]

    started = datetime.now().isoformat(timespec="seconds")
    run_id = db.write(lambda con: con.execute(
        "INSERT INTO runs(started_at, queries, status) VALUES (?,?,?)", (started, len(grid), "running")).lastrowid)
    progress.update(running=True, done=0, total=len(grid), run_id=run_id, started_at=started)
    LOG.info("run %s: %d queries (%s -> %s, %s..%s, nights %s, cap %dm)",
             run_id, len(grid), origins, dest, deps[0], deps[-1], nights_list, cap)

    ok = errors = empty = rows = consecutive = 0
    status, note = "ok", None
    for origin, dep, nights in grid:
        ret = (date.fromisoformat(dep) + timedelta(days=nights)).isoformat()
        q = build_query(origin, dest, dep, ret, cap)
        ts = datetime.now().isoformat(timespec="seconds")
        try:
            results = get_flights(q)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            consecutive += 1
            LOG.warning("%s %s->%s failed: %r", origin, dep, ret, exc)
            if consecutive >= block_after and looks_blocked(exc):
                status, note = "blocked", f"{consecutive} consecutive failures; last: {exc!r}"[:300]
                LOG.error("run %s aborted: %s", run_id, note)
                break
            time.sleep(pace * 2)
            continue
        consecutive = 0
        ok += 1
        if not results:
            empty += 1
        url = q.url()
        batch = []
        for fl in results:
            legs = fl.flights
            route = "-".join([legs[0].from_airport.code] + [l.to_airport.code for l in legs])
            batch.append((run_id, ts, origin, dest, dep, ret, nights, len(legs) - 1, ",".join(fl.airlines),
                          route, fmt_time(legs[0].departure), fmt_time(legs[-1].arrival),
                          elapsed_minutes(fl), int(fl.price), url))
        if batch:
            db.write(lambda con: con.executemany(
                "INSERT INTO fares(run_id,ts,origin,dest,dep,ret,nights,stops,airlines,route,dep_time,arr_time,"
                "elapsed_min,price,url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch))
            rows += len(batch)
        progress["done"] += 1
        time.sleep(pace)

    if status == "ok" and errors:
        status = "partial"
    finished = datetime.now().isoformat(timespec="seconds")
    db.write(lambda con: con.execute(
        "UPDATE runs SET finished_at=?, ok=?, errors=?, empty=?, rows=?, status=?, note=? WHERE id=?",
        (finished, ok, errors, empty, rows, status, note, run_id)))
    progress.update(running=False)
    summary = {"run_id": run_id, "started_at": started, "finished_at": finished, "queries": len(grid),
               "ok": ok, "errors": errors, "empty": empty, "rows": rows, "status": status, "note": note}
    LOG.info("run %s done: %s", run_id, summary)
    return summary
