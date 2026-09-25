"""Scheduler + alert dispatch + web server."""
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import uvicorn
import yaml

from . import db, notify, rules, scanner, web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("primp").setLevel(logging.WARNING)
LOG = logging.getLogger("main")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.yaml")


def load_cfg() -> dict:
    with open(CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def prior_minimums(run_id: int, window_days: int, origins) -> dict:
    since = (datetime.now() - timedelta(days=window_days)).isoformat()
    out = {}
    for r in db.read(lambda con: con.execute("""
            SELECT origin, CASE WHEN stops=0 THEN 'nonstop' ELSE 'connecting' END AS cls, MIN(price) AS p
            FROM fares WHERE run_id<? AND ts>=? GROUP BY origin, cls""", (run_id, since)).fetchall()):
        out[(r["origin"], r["cls"])] = r["p"]
    # trailing best combined: cheapest same-dates total per prior run
    best = None
    for r in db.read(lambda con: con.execute(
            "SELECT id FROM runs WHERE id<? AND finished_at>=? AND status IN ('ok','partial')", (run_id, since)).fetchall()):
        fares = [dict(x) for x in db.read(lambda con: con.execute("SELECT * FROM fares WHERE run_id=?", (r["id"],)).fetchall())]
        cb = rules.combined_best(fares, origins)
        if cb and (best is None or cb[0] < best):
            best = cb[0]
    if best is not None:
        out[("combined", "any")] = best
    return out


def after_run(cfg: dict, summary: dict) -> None:
    run_id = summary["run_id"]
    origins = cfg["origins"]
    fares = [dict(r) for r in db.read(lambda con: con.execute("SELECT * FROM fares WHERE run_id=?", (run_id,)).fetchall())]
    bests = rules.best_per_origin(fares)
    msgs = [("flightwatch/status", summary, True)]
    for o, slots in bests.items():
        msgs.append((f"flightwatch/best/{o}", {c: {k: f[k] for k in ("price", "dep", "ret", "route", "airlines", "elapsed_min")}
                                               for c, f in slots.items()}, True))
    if summary["status"] == "blocked":
        key = "blocked"
        last = db.read(lambda con: con.execute("SELECT ts FROM alert_state WHERE key=?", (key,)).fetchone())
        if not last or datetime.now() - datetime.fromisoformat(last["ts"]) > timedelta(hours=20):
            notify.pushover("Flight watch: Google blocked?", f"Run {run_id} aborted after {summary['ok']} ok / {summary['errors']} errors.\n{summary['note']}", priority=0)
            db.write(lambda con: con.execute("INSERT OR REPLACE INTO alert_state(key,price,ts) VALUES (?,?,?)",
                                             (key, 0, datetime.now().isoformat(timespec='seconds'))))
        notify.mqtt_publish(msgs)
        return
    if not fares:
        notify.mqtt_publish(msgs)
        return
    prior = prior_minimums(run_id, int(cfg["rules"].get("new_low_window_days", 14)), origins)
    state = {r["key"]: (r["price"], r["ts"]) for r in db.read(lambda con: con.execute("SELECT * FROM alert_state").fetchall())}
    alerts = rules.evaluate(fares, prior, cfg["rules"], origins, state)
    now = datetime.now().isoformat(timespec="seconds")
    for a in alerts:
        title = {"nonstop_max": f"✈ Nonstop deal {a['origin']}→{cfg['destination']} ${a['price']}",
                 "connecting_max": f"✈ 1-stop deal {a['origin']}→{cfg['destination']} ${a['price']}",
                 "new_low": f"✈ New low {a['origin']}→{cfg['destination']} ${a['price']}",
                 "combined": f"✈ Both families: ${a['price']}/adult"}[a["rule"]]
        sent = notify.pushover(title, a["message"], url=a.get("url") or "")
        db.write(lambda con: (
            con.execute("INSERT INTO alerts(ts,rule,origin,dep,ret,route,stops,price,message,sent) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (now, a["rule"], a["origin"], a["dep"], a["ret"], a["route"], a["stops"], a["price"], a["message"], int(sent))),
            con.execute("INSERT OR REPLACE INTO alert_state(key,price,ts) VALUES (?,?,?)", (a["key"], a["price"], now))))
        msgs.append(("flightwatch/alert", {k: a[k] for k in ("rule", "origin", "dep", "ret", "price", "message")}, False))
    notify.mqtt_publish(msgs)
    LOG.info("run %s: %d alerts", run_id, len(alerts))


def next_scan_time(scan_at: str) -> datetime:
    hh, mm = (int(x) for x in scan_at.split(":"))
    now = datetime.now()
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def scheduler(trigger: threading.Event) -> None:
    cfg = load_cfg()
    last = db.read(lambda con: con.execute(
        "SELECT finished_at FROM runs WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1").fetchone())
    catch_up = float(cfg.get("catch_up_hours", 26))
    if not last or datetime.now() - datetime.fromisoformat(last["finished_at"]) > timedelta(hours=catch_up):
        LOG.info("no run in the last %.0fh; scanning now", catch_up)
        trigger.set()
    while True:
        cfg = load_cfg()
        web.state["cfg"] = cfg
        nxt = next_scan_time(cfg["scan_at"])
        web.state["next_scan"] = nxt.isoformat(timespec="seconds")
        if trigger.wait(timeout=max(1, (nxt - datetime.now()).total_seconds())):
            trigger.clear()
        try:
            summary = scanner.scan(cfg)
            if "run_id" in summary:
                after_run(cfg, summary)
        except Exception:  # noqa: BLE001
            LOG.exception("scan failed")
            scanner.progress["running"] = False
        time.sleep(1)


def main() -> None:
    db.init()
    trigger = threading.Event()
    web.state["cfg"] = load_cfg()
    web.state["trigger"] = trigger
    threading.Thread(target=scheduler, args=(trigger,), daemon=True, name="scheduler").start()
    uvicorn.run(web.app, host="0.0.0.0", port=int(os.environ.get("PORT", "8783")), log_level="warning")


if __name__ == "__main__":
    main()
