#!/usr/bin/env python3
"""Generate + push the Grafana dashboard for pixel-monitor.

python3 make_dashboard.py           -> writes grafana-pixel-monitor.json and POSTs it to Grafana :3001
Times use UNIX_TIMESTAMP(ts) so they are correct regardless of the datasource session time zone.
"""
import json
import urllib.request

DS = {"type": "mysql", "uid": "eebnvy9j8hmgwb"}
UID = "pixel-monitor"
# validated categorical slots (dataviz reference palette, dark mode), fixed order
BLUE, ORANGE, AQUA, YELLOW = "#3987e5", "#d95926", "#199e70", "#c98500"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
DEV = "device = '$device'"
BUCKET = "UNIX_TIMESTAMP(ts) DIV ($__interval_ms DIV 1000) * ($__interval_ms DIV 1000)"

panels = []
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def target(sql, fmt="time_series", ref="A"):
    return {"refId": ref, "datasource": DS, "format": fmt, "rawQuery": True, "editorMode": "code", "rawSql": sql}


def stat(title, sql, x, unit="none", decimals=None, thresholds=None, desc="", w=3):
    steps = thresholds or [{"color": "text", "value": None}]
    panels.append({
        "id": nid(), "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": 0, "w": w, "h": 4},
        "targets": [target(sql, "table")],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value" if thresholds else "none", "graphMode": "none",
                    "textMode": "value", "justifyMode": "center"},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals,
                                     "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
    })


def series_override(name, color):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


def ts(title, sqls, gp, unit, colors, desc="", draw="line", min0=True, extra=None, legend=True):
    targets = [target(s, ref=chr(65 + i)) for i, s in enumerate(sqls)]
    custom = {"drawStyle": draw, "lineWidth": 2, "fillOpacity": 0, "pointSize": 8 if draw == "points" else 4,
              "showPoints": "never" if draw == "line" else "always", "spanNulls": 180000,
              "axisSoftMin": 0 if min0 else None, "barAlignment": 0, "gradientMode": "none"}
    if draw == "bars":
        custom.update({"fillOpacity": 80, "lineWidth": 1})
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "fixed", "fixedColor": colors[0][1]}}
    if extra:
        defaults.update(extra)
    panels.append({
        "id": nid(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": gp, "interval": "1m", "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": [series_override(n, c) for n, c in colors]},
        "options": {"legend": {"showLegend": legend, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi", "sort": "none"}},
    })


def table(title, sql, gp, desc="", overrides=None, sort=None):
    panels.append({
        "id": nid(), "type": "table", "title": title, "description": desc, "datasource": DS,
        "gridPos": gp, "targets": [target(sql, "table")],
        "options": {"showHeader": True, "cellHeight": "sm", "sortBy": sort or []},
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides or []},
    })


def unit_override(col, unit, decimals=None):
    props = [{"id": "unit", "value": unit}]
    if decimals is not None:
        props.append({"id": "decimals", "value": decimals})
    return {"matcher": {"id": "byName", "options": col}, "properties": props}


# ---------------------------------------------------------------- headline stats (row 0)
LATEST = f"FROM pixel_metrics WHERE {DEV} ORDER BY ts DESC LIMIT 1"
stat("Memory available", f"SELECT mem_avail_kb*1024 AS v {LATEST}", 0, "bytes", 1,
     [{"color": CRIT, "value": None}, {"color": WARN, "value": 1e9}, {"color": GOOD, "value": 2e9}],
     "MemAvailable right now. Under ~1 GB the phone is reclaiming memory constantly.")
stat("Swap free", f"SELECT swap_free_kb*1024 AS v {LATEST}", 3, "bytes", 1,
     [{"color": CRIT, "value": None}, {"color": WARN, "value": 2.5e8}, {"color": GOOD, "value": 1e9}],
     "Free compressed swap (zram, ~5.9 GB). At 0 the low-memory killer starts killing apps.")
stat("Uptime", f"SELECT uptime_s AS v {LATEST}", 6, "s", 1, None, "Time since the phone last restarted.")
stat("Low-memory kills", f"SELECT COUNT(*) AS v FROM pixel_events WHERE {DEV} AND kind='lmk_kill' AND $__timeFilter(ts)",
     9, "none", 0, None, "Apps killed by lmkd in the selected time range.")
stat("Slow app opens (>1 s)", f"SELECT COUNT(*) AS v FROM pixel_events WHERE {DEV} AND kind='launch' AND value>1000 AND $__timeFilter(ts)",
     12, "none", 0, None, "Activity launches that took over 1 second, in the selected time range.")
stat("ANRs + crashes", f"SELECT COUNT(*) AS v FROM pixel_events WHERE {DEV} AND kind IN ('anr','crash') AND $__timeFilter(ts)",
     15, "none", 0, None, "App-not-responding dialogs and app crashes in the selected time range.")
stat("Lag marks", f"SELECT COUNT(*) AS v FROM pixel_events WHERE {DEV} AND kind='lag_mark' AND $__timeFilter(ts)",
     18, "none", 0, None, "Times you opened http://192.168.10.217:8814/lag on the phone.")
stat("Worst thermal status", f"SELECT COALESCE(MAX(thermal_status),0) AS v FROM pixel_metrics WHERE {DEV} AND $__timeFilter(ts)",
     21, "none", 0, [{"color": GOOD, "value": None}, {"color": WARN, "value": 1}, {"color": CRIT, "value": 3}],
     "0 = none, 1 light, 2 moderate, 3 severe throttling, 4 critical, 5 emergency, 6 shutdown.")
panels[-1]["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {
    "0": {"text": "0 none"}, "1": {"text": "1 light"}, "2": {"text": "2 moderate"}, "3": {"text": "3 severe"},
    "4": {"text": "4 critical"}, "5": {"text": "5 emergency"}, "6": {"text": "6 shutdown"}}}]

# ---------------------------------------------------------------- time series
y = 4
ts("Memory (GB)", [
    f"SELECT {BUCKET} AS time, AVG(mem_avail_kb)/1048576 AS `RAM available`, AVG(swap_total_kb-swap_free_kb)/1048576 AS `Swap used` "
    f"FROM pixel_metrics WHERE {DEV} AND $__timeFilter(ts) GROUP BY 1 ORDER BY 1"],
   {"x": 0, "y": y, "w": 12, "h": 8}, "decgbytes", [("RAM available", BLUE), ("Swap used", ORANGE)],
   "RAM available falling while swap used climbs to its ~5.65 GB ceiling = memory exhausted.")
ts("Swap thrash (per second)", [
    f"SELECT {BUCKET} AS time, AVG(swapin_ps) AS `Pages swapped in`, AVG(majfault_ps) AS `Major page faults` "
    f"FROM pixel_metrics WHERE {DEV} AND $__timeFilter(ts) GROUP BY 1 ORDER BY 1"],
   {"x": 12, "y": y, "w": 12, "h": 8}, "short", [("Pages swapped in", BLUE), ("Major page faults", ORANGE)],
   "How often apps wait for memory to be read back from swap or storage. Sustained spikes are what lag feels like.")
y += 8
ts("App open time (ms)", [
    f"SELECT UNIX_TIMESTAMP(ts) AS time, value AS `Launch ms` FROM pixel_events "
    f"WHERE {DEV} AND kind='launch' AND $__timeFilter(ts) ORDER BY 1"],
   {"x": 0, "y": y, "w": 12, "h": 8}, "ms", [("Launch ms", BLUE)],
   "One dot per app/screen launch. Hover for the time; the table below names the apps. Dashed line = 1 s.",
   draw="points", legend=False,
   extra={"thresholds": {"mode": "absolute", "steps": [{"color": "transparent", "value": None}, {"color": CRIT, "value": 1000}]}})
panels[-1]["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"] = {"mode": "dashed"}
ts("Low-memory kills (per bucket)", [
    f"SELECT {BUCKET} AS time, COUNT(*) AS `Apps killed` FROM pixel_events "
    f"WHERE {DEV} AND kind='lmk_kill' AND $__timeFilter(ts) GROUP BY 1 ORDER BY 1"],
   {"x": 12, "y": y, "w": 12, "h": 8}, "none", [("Apps killed", ORANGE)],
   "Background apps killed by lmkd. Bursts mean memory ran out; killed apps cold-start next time you open them.",
   draw="bars", legend=False)
y += 8
ts("CPU busy (% of whole phone)", [
    f"SELECT {BUCKET} AS time, AVG(cpu_pct) AS `CPU busy` FROM pixel_metrics WHERE {DEV} AND $__timeFilter(ts) GROUP BY 1 ORDER BY 1"],
   {"x": 0, "y": y, "w": 8, "h": 7}, "percent", [("CPU busy", BLUE)], "All 9 cores combined.", legend=False,
   extra={"max": 100})
ts("Temperatures (°C)", [
    f"SELECT {BUCKET} AS time, AVG(skin_c) AS `Skin`, AVG(soc_c) AS `SoC`, AVG(cpu_big_c) AS `CPU big core`, AVG(battery_c) AS `Battery` "
    f"FROM pixel_metrics WHERE {DEV} AND $__timeFilter(ts) GROUP BY 1 ORDER BY 1"],
   {"x": 8, "y": y, "w": 8, "h": 7}, "celsius", [("Skin", BLUE), ("SoC", ORANGE), ("CPU big core", AQUA), ("Battery", YELLOW)],
   "Pixel throttles mostly on skin temperature (roughly 39 °C+).", min0=False)
ts("Battery (%)", [
    f"SELECT {BUCKET} AS time, AVG(battery_pct) AS `Battery` FROM pixel_metrics WHERE {DEV} AND $__timeFilter(ts) GROUP BY 1 ORDER BY 1"],
   {"x": 16, "y": y, "w": 8, "h": 7}, "percent", [("Battery", AQUA)], legend=False, extra={"max": 100})
y += 7

# ---------------------------------------------------------------- tables
table("Biggest memory users (latest snapshot, MB)",
      f"SELECT proc AS `Process`, ROUND((rss_kb+swap_kb)/1024) AS `Total MB`, ROUND(rss_kb/1024) AS `In RAM MB`, "
      f"ROUND(swap_kb/1024) AS `In swap MB` FROM pixel_proc_mem WHERE {DEV} AND ts = "
      f"(SELECT MAX(ts) FROM pixel_proc_mem WHERE {DEV}) ORDER BY 2 DESC LIMIT 15",
      {"x": 0, "y": y, "w": 12, "h": 10}, "Resident + swapped memory per process (same-named processes merged).")
table("Memory growth over the selected range (MB)",
      f"SELECT proc AS `Process`, ROUND(MAX(CASE WHEN rl=1 THEN tot END)/1024) AS `Now`, "
      f"ROUND(MAX(CASE WHEN rf=1 THEN tot END)/1024) AS `At start`, ROUND(MAX(tot)/1024) AS `Peak`, "
      f"ROUND((MAX(CASE WHEN rl=1 THEN tot END)-MAX(CASE WHEN rf=1 THEN tot END))/1024) AS `Change` FROM ("
      f"SELECT proc, CAST(rss_kb AS SIGNED)+CAST(swap_kb AS SIGNED) AS tot, ROW_NUMBER() OVER (PARTITION BY proc ORDER BY ts DESC) rl, "
      f"ROW_NUMBER() OVER (PARTITION BY proc ORDER BY ts) rf FROM pixel_proc_mem WHERE {DEV} AND $__timeFilter(ts)) x "
      f"GROUP BY proc ORDER BY `Change` DESC LIMIT 15",
      {"x": 12, "y": y, "w": 12, "h": 10},
      "A process that keeps growing between restarts is the leak suspect. Killed/restarted processes reset.")
y += 10
table("Slowest app opens",
      f"SELECT app AS `App`, COUNT(*) AS `Opens`, ROUND(AVG(value)) AS `Avg ms`, MAX(value) AS `Worst ms`, "
      f"SUM(value>1000) AS `Over 1 s` FROM pixel_events WHERE {DEV} AND kind='launch' AND $__timeFilter(ts) "
      f"GROUP BY app ORDER BY `Avg ms` DESC LIMIT 15",
      {"x": 0, "y": y, "w": 8, "h": 10})
table("Top CPU users",
      f"SELECT proc AS `Process`, COUNT(*) AS `Minutes in top 5`, ROUND(AVG(cpu_pct),1) AS `Avg % of a core`, "
      f"MAX(cpu_pct) AS `Peak %` FROM pixel_cpu WHERE {DEV} AND $__timeFilter(ts) GROUP BY proc ORDER BY 2 DESC LIMIT 15",
      {"x": 8, "y": y, "w": 8, "h": 10}, "Sampled once a minute over 2 s; 100% = one full core.")
table("Most-killed apps",
      f"SELECT app AS `App`, COUNT(*) AS `Kills`, ROUND(AVG(value)) AS `Avg MB freed` FROM pixel_events "
      f"WHERE {DEV} AND kind='lmk_kill' AND $__timeFilter(ts) GROUP BY app ORDER BY 2 DESC LIMIT 15",
      {"x": 16, "y": y, "w": 8, "h": 10})
y += 10
table("Event log (lag marks, ANRs, crashes, reboots, slow opens)",
      f"SELECT ts AS `Time`, kind AS `Event`, app AS `App`, value AS `Value`, detail AS `Detail` FROM pixel_events "
      f"WHERE {DEV} AND $__timeFilter(ts) AND (kind IN ('lag_mark','anr','crash','reboot') OR (kind='launch' AND value>1000)) "
      f"ORDER BY ts DESC LIMIT 200",
      {"x": 0, "y": y, "w": 24, "h": 10}, "Launch value = ms.")

annotations = {"list": [
    {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
     "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
    {"name": "Lag marks", "enable": True, "iconColor": SERIOUS, "datasource": DS,
     "target": target(f"SELECT UNIX_TIMESTAMP(ts)*1000 AS time, CONCAT('Lag mark', IFNULL(CONCAT(' — ', app), ''), "
                      f"IFNULL(CONCAT(': ', detail), '')) AS text FROM pixel_events WHERE {DEV} AND kind='lag_mark' AND $__timeFilter(ts)", "table")},
    {"name": "Reboots", "enable": True, "iconColor": BLUE, "datasource": DS,
     "target": target(f"SELECT UNIX_TIMESTAMP(ts)*1000 AS time, CONCAT('Reboot (', detail, ')') AS text FROM pixel_events "
                      f"WHERE {DEV} AND kind='reboot' AND $__timeFilter(ts)", "table")},
]}

dash = {
    "uid": UID, "title": "Pixel Phone Performance", "tags": ["phone", "pixel-monitor"],
    "timezone": "browser", "refresh": "1m", "schemaVersion": 39,
    "time": {"from": "now-24h", "to": "now"},
    "description": "pixel-monitor: adb stats from the phone every minute (home_config/pixel-monitor).",
    "templating": {"list": [{
        "name": "device", "label": "Phone", "type": "query", "datasource": DS,
        "query": "SELECT DISTINCT device FROM pixel_metrics", "definition": "SELECT DISTINCT device FROM pixel_metrics",
        "refresh": 1, "current": {"text": "Brad Pixel 8 Pro", "value": "Brad Pixel 8 Pro"}}]},
    "annotations": annotations,
    "panels": panels,
}

if __name__ == "__main__":
    with open("grafana-pixel-monitor.json", "w") as f:
        json.dump(dash, f, indent=2)
    req = urllib.request.Request("http://localhost:3001/api/dashboards/db", method="POST",
                                 data=json.dumps({"dashboard": dash, "overwrite": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    print(urllib.request.urlopen(req).read().decode())
