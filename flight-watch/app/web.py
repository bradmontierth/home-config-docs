"""Read API for the status page."""
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pathlib import Path

from . import db, rules, scanner

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="flight-watch")
state = {"cfg": {}, "next_scan": None, "trigger": None}


def rows(fn):
    return [dict(r) for r in db.read(fn)]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    runs = rows(lambda con: con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30").fetchall())
    last_ok = next((r for r in runs if r["status"] in ("ok", "partial")), None)
    cfg = state["cfg"]
    return {"now": datetime.now().isoformat(timespec="seconds"), "next_scan": state["next_scan"],
            "progress": scanner.progress, "runs": runs, "last_completed": last_ok,
            "config": {k: cfg.get(k) for k in ("destination", "origins", "depart_from", "depart_to", "nights",
                                               "max_leg_minutes", "scan_at", "rules")}}


def _latest_run_id(run_id):
    if run_id:
        return run_id
    r = db.read(lambda con: con.execute(
        "SELECT id FROM runs WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1").fetchone())
    if not r:
        raise HTTPException(404, "no completed run yet")
    return r["id"]


@app.get("/api/heatmap")
def heatmap(origin: str, run_id: int | None = None):
    rid = _latest_run_id(run_id)
    fares = rows(lambda con: con.execute(
        "SELECT * FROM fares WHERE run_id=? AND origin=?", (rid, origin)).fetchall())
    per = rules.best_per_pair(fares)
    cells = []
    for (o, dep, ret), slots in per.items():
        cheapest = min(slots.values(), key=lambda f: f["price"])
        cells.append({"dep": dep, "ret": ret, "nights": cheapest["nights"], "price": cheapest["price"],
                      "stops": cheapest["stops"], "route": cheapest["route"], "airlines": cheapest["airlines"],
                      "elapsed_min": cheapest["elapsed_min"], "url": cheapest["url"],
                      "nonstop": slots.get("nonstop", {}).get("price")})
    return {"run_id": rid, "origin": origin, "cells": cells}


@app.get("/api/trend")
def trend():
    """Per completed run, per origin: best nonstop + best connecting price across the grid, plus run health."""
    data = rows(lambda con: con.execute("""
        SELECT r.id AS run_id, r.finished_at, r.status, r.ok, r.errors, r.queries, f.origin,
               MIN(CASE WHEN f.stops=0 THEN f.price END) AS nonstop,
               MIN(CASE WHEN f.stops>0 THEN f.price END) AS connecting
        FROM runs r LEFT JOIN fares f ON f.run_id=r.id
        WHERE r.status IN ('ok','partial') GROUP BY r.id, f.origin ORDER BY r.id""").fetchall())
    return {"points": data}


@app.get("/api/alerts")
def alerts(limit: int = Query(50, le=500)):
    return {"alerts": rows(lambda con: con.execute(
        "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall())}


@app.get("/api/fares")
def fares(origin: str, dep: str, ret: str, run_id: int | None = None):
    rid = _latest_run_id(run_id)
    return {"fares": rows(lambda con: con.execute(
        "SELECT * FROM fares WHERE run_id=? AND origin=? AND dep=? AND ret=? ORDER BY price",
        (rid, origin, dep, ret)).fetchall())}


@app.get("/api/history")
def history(origin: str, dep: str, ret: str, days: int = 60):
    """Price history for one date pair (cheapest per class per run)."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    return {"points": rows(lambda con: con.execute("""
        SELECT run_id, MIN(ts) AS ts, MIN(CASE WHEN stops=0 THEN price END) AS nonstop,
               MIN(CASE WHEN stops>0 THEN price END) AS connecting
        FROM fares WHERE origin=? AND dep=? AND ret=? AND ts>=? GROUP BY run_id ORDER BY run_id""",
        (origin, dep, ret, since)).fetchall())}


@app.post("/api/scan")
def trigger_scan():
    if scanner.progress["running"]:
        return {"started": False, "reason": "already running"}
    state["trigger"].set()
    return {"started": True}
