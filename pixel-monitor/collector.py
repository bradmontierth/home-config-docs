#!/usr/bin/env python3
"""pixel-monitor: log an Android phone's performance stats to MariaDB over wireless adb.

Every minute: memory/swap, swap-in/out + major-fault rates, CPU %, load, IO
pressure, temperatures + thermal status, battery, screen state, foreground app,
top-5 CPU processes, and new logcat events (lmkd kills, app launch times, ANRs,
crashes, skipped frames). Every 5 minutes: per-process RSS + swap (top 30).

Deliberately avoids `dumpsys meminfo` / `dumpsys procstats`: on a phone whose
swap is full they force every app to page back in and cause the lag we are
trying to measure.

Also serves http://<host>:8814/lag - open it on the phone when it feels laggy;
the moment is stored as a lag_mark event (Grafana annotation).

Config via env (see pixel-monitor.service); DB creds come from DB_ENV_FILE.
"""
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import pymysql

ADB = os.environ.get("ADB", "/home/pi/home_config/home-agent-android/.android-sdk/platform-tools/adb")
DEVICE = os.environ.get("DEVICE_NAME", "Brad Pixel 8 Pro")
MDNS_NAME = os.environ.get("MDNS_NAME", "adb-38241FDJG0097U")   # stable per phone
MODEL = os.environ.get("MODEL", "Pixel_8_Pro")
FALLBACK_ADDRS = [a for a in os.environ.get("FALLBACK_ADDRS", "").split(",") if a]
DB_ENV_FILE = os.environ.get("DB_ENV_FILE", "/home/pi/cecret_lake/pixel_monitor/mysql.env")
STATE_FILE = os.environ.get("STATE_FILE", os.path.expanduser("~/.local/state/pixel-monitor/state.json"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8814"))
INTERVAL = 60
PROC_EVERY = 5          # minutes
RETAIN_DETAIL_DAYS = 180  # pixel_cpu / pixel_proc_mem; metrics + events kept indefinitely

# lmkd kill_reasons enum (system/memory/lmkd/lmkd.cpp)
KILL_REASONS = ["pressure_after_kill", "not_responding", "low_swap_and_thrashing",
                "low_mem_and_swap", "low_mem_and_thrashing", "direct_reclaim_and_thrashing",
                "low_mem_and_swap_util", "low_filecache_after_thrashing", "low_mem",
                "direct_reclaim_stuck"]

LOGCAT_TAGS = ["killinfo:I", "am_anr:I", "am_crash:I", "wm_activity_launch_time:I",
               "Choreographer:I", "OpenGLRenderer:I", "*:S"]


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- DB
def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


class DB:
    def __init__(self):
        self.cfg = load_env(DB_ENV_FILE)
        self.conn = None
        self.lock = threading.Lock()

    def _connect(self):
        c = self.cfg
        self.conn = pymysql.connect(host=c["DB_HOST"], port=int(c["DB_PORT"]), user=c["DB_USER"],
                                    password=c["DB_PASSWORD"], database=c["DB_NAME"],
                                    autocommit=True, connect_timeout=10,
                                    init_command="SET time_zone='+00:00'")

    def run(self, sql, rows=None, many=False):
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.conn is None:
                        self._connect()
                    with self.conn.cursor() as cur:
                        if many:
                            if rows:
                                cur.executemany(sql, rows)
                        else:
                            cur.execute(sql, rows)
                    return
                except pymysql.err.OperationalError:
                    self.conn = None
                    if attempt == 2:
                        raise


# ---------------------------------------------------------------- adb
def adb(*args, timeout=30):
    r = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
    return r.stdout


def online_serials():
    out = adb("devices", "-l", timeout=15)
    serials = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device" and (f"model:{MODEL}" in line or parts[0].startswith(MDNS_NAME)):
            serials.append(parts[0])
    # prefer ip:port (direct) over the mdns alias
    serials.sort(key=lambda s: 0 if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", s) else 1)
    return serials


def find_device(state):
    s = online_serials()
    if s:
        return s[0]
    # rediscover the (changing) wireless-debugging port via mDNS
    for line in adb("mdns", "services", timeout=15).splitlines():
        if line.startswith(MDNS_NAME) and "_adb-tls-connect" in line:
            addr = line.split()[-1]
            adb("connect", addr, timeout=15)
            state["last_addr"] = addr
    s = online_serials()
    if s:
        return s[0]
    for addr in [state.get("last_addr")] + FALLBACK_ADDRS:
        if addr:
            adb("connect", addr, timeout=10)
    s = online_serials()
    return s[0] if s else None


def sh(serial, script, timeout=40):
    return adb("-s", serial, "shell", script, timeout=timeout)


MINUTE_SCRIPT = r"""
echo @@MEM; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
echo @@VM; grep -E '^(pswpin|pswpout|pgmajfault|allocstall_normal|allocstall_movable) ' /proc/vmstat
echo @@STAT; head -1 /proc/stat
echo @@LOAD; cat /proc/loadavg
echo @@UP; cat /proc/uptime
echo @@IO; cat /proc/pressure/io 2>/dev/null
echo @@THERM; dumpsys thermalservice 2>/dev/null | sed -n '/^Thermal Status/p;/Current temperatures from HAL/,/^[A-Z]/p'
echo @@BATT; dumpsys battery 2>/dev/null
echo @@POWER; dumpsys power 2>/dev/null | grep -m1 mWakefulness=
echo @@FOCUS; dumpsys window 2>/dev/null | grep -m1 mCurrentFocus
echo @@PS; ps -A -o PID,NAME
echo @@TOP; top -b -n 2 -d 2 -o PID,%CPU,NAME
"""

PROC_SCRIPT = r"""grep -E '^(VmRSS|VmSwap):' /proc/[0-9]*/status 2>/dev/null
echo @@HOSTS; dumpsys activity processes 2>/dev/null | grep -E 'APP. UID|packageList='"""


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return out


def kv_kb(lines):
    d = {}
    for l in lines:
        m = re.match(r"(\w+):\s+(\d+)", l)
        if m:
            d[m.group(1)] = int(m.group(2))
    return d


def parse_minute(text, state, now):
    s = sections(text)
    m = {}
    mem = kv_kb(s.get("MEM", []))
    m["mem_total_kb"] = mem.get("MemTotal")
    m["mem_avail_kb"] = mem.get("MemAvailable")
    m["swap_total_kb"] = mem.get("SwapTotal")
    m["swap_free_kb"] = mem.get("SwapFree")

    vm = {}
    for l in s.get("VM", []):
        k, _, v = l.partition(" ")
        vm[k] = int(v)
    vm["allocstall"] = vm.pop("allocstall_normal", 0) + vm.pop("allocstall_movable", 0)
    cpu = [int(x) for x in (s.get("STAT") or ["cpu 0"])[0].split()[1:]]
    up = float((s.get("UP") or ["0"])[0].split()[0])
    m["uptime_s"] = int(up)

    prev = state.get("prev")
    if prev and prev.get("uptime", 0) < up and now - prev["t"] < 600:
        dt = now - prev["t"]
        rate = lambda k: max(0, vm.get(k, 0) - prev["vm"].get(k, 0)) / dt
        m["swapin_ps"] = round(rate("pswpin"), 1)
        m["swapout_ps"] = round(rate("pswpout"), 1)
        m["majfault_ps"] = round(rate("pgmajfault"), 1)
        m["direct_reclaim_ps"] = round(rate("allocstall"), 2)
        dtot = sum(cpu) - sum(prev["cpu"])
        didle = (cpu[3] + cpu[4]) - (prev["cpu"][3] + prev["cpu"][4])
        m["cpu_pct"] = round(100 * (dtot - didle) / dtot, 1) if dtot > 0 else None
    state["prev"] = {"t": now, "vm": vm, "cpu": cpu, "uptime": up}

    load = (s.get("LOAD") or [""])[0].split()
    m["load1"] = float(load[0]) if load else None

    for l in s.get("IO", []):
        mm = re.match(r"(some|full) avg10=[\d.]+ avg60=([\d.]+)", l)
        if mm:
            m[f"io_{mm.group(1)}_avg60"] = float(mm.group(2))

    temps = {}
    for l in s.get("THERM", []):
        if l.startswith("Thermal Status:"):
            m["thermal_status"] = int(l.split(":")[1])
        mm = re.search(r"mValue=([-\d.]+), mType=-?\d+, mName=([^,]+),", l)
        if mm:
            temps[mm.group(2)] = round(float(mm.group(1)), 1)
    m["skin_c"] = temps.get("VIRTUAL-SKIN")
    m["soc_c"] = temps.get("soc_therm")
    m["cpu_big_c"] = temps.get("BIG")
    m["battery_c"] = temps.get("battery")

    batt = {}
    for l in s.get("BATT", []):
        k, _, v = l.strip().partition(": ")
        batt[k] = v
    m["battery_pct"] = int(batt["level"]) if batt.get("level", "").isdigit() else None
    powered = any(batt.get(k) == "true" for k in ("AC powered", "USB powered", "Wireless powered", "Dock powered"))
    m["charging"] = int(powered or batt.get("status") == "2")

    pw = " ".join(s.get("POWER", []))
    m["screen_on"] = 1 if "mWakefulness=Awake" in pw else (0 if "mWakefulness=" in pw else None)

    focus = " ".join(s.get("FOCUS", []))
    mm = re.search(r"u\d+ ([\w.]+)/", focus) or re.search(r"u\d+ ([\w.]+)\}", focus)
    m["fg_app"] = mm.group(1) if mm else None

    pids = {}
    for l in s.get("PS", [])[1:]:
        p = l.split(None, 1)
        if len(p) == 2 and p[0].isdigit():
            pids[p[0]] = p[1].strip()

    # `top -n 2` prints two frames; the second one reflects the last 2 s.
    top_lines = s.get("TOP", [])
    frames, cur = [], []
    for l in top_lines:
        p = l.split(None, 2)
        if l.lstrip().startswith("PID"):
            if cur:
                frames.append(cur)
            cur = []
        elif len(p) == 3 and p[0].isdigit():
            try:
                cur.append((p[2].strip(), float(p[1])))
            except ValueError:
                pass
    if cur:
        frames.append(cur)
    frame = frames[-1] if frames else []
    top5 = sorted((x for x in frame if x[0] != "top"), key=lambda x: -x[1])[:5]
    top5 = [(n, c) for n, c in top5 if c > 0]  # %CPU is % of one core (phone has 9)
    return m, pids, top5


def parse_proc_mem(text, pids):
    agg = {}
    for l in text.splitlines():
        mm = re.match(r"/proc/(\d+)/status:(VmRSS|VmSwap):\s+(\d+)", l)
        if mm:
            agg.setdefault(mm.group(1), {})[mm.group(2)] = int(mm.group(3))
    # sandboxed (isolated) processes such as WebView renderers all share one name;
    # label them by the app that hosts them: "webview renderer: com.strava"
    hosts, cur = {}, None
    for l in text.split("@@HOSTS", 1)[-1].splitlines() if "@@HOSTS" in text else []:
        if "APP* UID" in l:
            mm = re.search(r"ProcessRecord\{\w+ (\d+):([^ /}]+)", l)
            cur = (mm.group(1), mm.group(2)) if mm and "sandboxed" in mm.group(2) else None
            continue
        mm = re.search(r"packageList=\{([^}]*)\}", l)
        if mm and cur:
            kind = "webview renderer" if "webview" in cur[1] else cur[1].split(":")[0] + " sandbox"
            hosts[cur[0]] = f"{kind}: {mm.group(1)}"
            cur = None
    rows = []
    for pid, d in agg.items():
        name = hosts.get(pid) or pids.get(pid)
        if not name:
            continue
        rows.append((name, d.get("VmRSS", 0), d.get("VmSwap", 0)))
    # merge any remaining same-named processes
    merged = {}
    for n, r, sw in rows:
        a = merged.setdefault(n, [0, 0])
        a[0] += r
        a[1] += sw
    return sorted(((n, r, sw) for n, (r, sw) in merged.items()), key=lambda x: -(x[1] + x[2]))[:40]


# ---------------------------------------------------------------- logcat events
LINE_RE = re.compile(r"^\s*(\d+\.\d+)\s+(\d+)\s+(\d+)\s+\w\s+([^:]+?)\s*:\s(.*)$")


def fetch_events(serial, state, pids, uids):
    since = state.get("log_since") or time.time() - 120
    out = adb("-s", serial, "logcat", "-d", "-v", "epoch", "-T", f"{since:.3f}",
              "-b", "events", "-b", "main", "-s", *LOGCAT_TAGS, timeout=30)
    events, last = [], since
    for line in out.splitlines():
        mm = LINE_RE.match(line)
        if not mm:
            continue
        t, pid, tag, msg = float(mm.group(1)), mm.group(2), mm.group(4), mm.group(5)
        if t <= since:
            continue
        last = max(last, t)
        ev = None
        if tag == "killinfo":
            f = msg.strip("[]").split(",")
            try:
                kpid, kuid, adj, rss = f[0], int(f[1]), int(f[2]), int(f[4])
                reason = int(f[5])
                swap_free = int(f[13]) if len(f) > 13 else None
            except (ValueError, IndexError):
                continue
            name = pids.get(kpid) or uids.get(kuid) or ("isolated" if kuid >= 99000 else f"uid {kuid}")
            rname = KILL_REASONS[reason] if 0 <= reason < len(KILL_REASONS) else str(reason)
            ev = ("lmk_kill", name, round(rss / 1024, 1),
                  f"adj={adj} reason={rname} swap_free_kb={swap_free}")
        elif tag == "wm_activity_launch_time":
            f = msg.strip("[]").split(",")
            try:
                ms = int(f[-1])
                comp = f[2]
            except (ValueError, IndexError):
                continue
            ev = ("launch", comp.split("/")[0], ms, comp[:255])
        elif tag == "am_anr":
            f = msg.strip("[]").split(",", 4)
            ev = ("anr", f[2] if len(f) > 2 else None, None, (f[4] if len(f) > 4 else msg)[:255])
        elif tag == "am_crash":
            f = msg.strip("[]").split(",", 5)
            ev = ("crash", f[2] if len(f) > 2 else None, None, (f[4] if len(f) > 4 else msg)[:255])
        elif tag == "Choreographer":
            mm2 = re.search(r"Skipped (\d+) frames", msg)
            if mm2:
                ev = ("skipped_frames", pids.get(pid, f"pid {pid}"), int(mm2.group(1)), None)
        elif tag == "OpenGLRenderer":
            mm2 = re.search(r"Davey! duration=(\d+)ms", msg)
            if mm2:
                ev = ("davey", pids.get(pid, f"pid {pid}"), int(mm2.group(1)), None)
        if ev:
            events.append((datetime.fromtimestamp(t, timezone.utc).replace(tzinfo=None), *ev))
    state["log_since"] = last
    return events


def refresh_uids(serial):
    uids = {}
    for line in sh(serial, "pm list packages -U").splitlines():
        mm = re.match(r"package:(\S+) uid:(\d+)", line.strip())
        if mm:
            uids[int(mm.group(2))] = mm.group(1)
    return uids


# ---------------------------------------------------------------- lag-mark HTTP
class Shared:
    fg_app = None


def make_handler(db):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/lag"):
                note = None
                if "?" in self.path:
                    from urllib.parse import parse_qs, urlparse
                    note = (parse_qs(urlparse(self.path).query).get("note") or [None])[0]
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                try:
                    db.run("INSERT INTO pixel_events (ts, device, kind, app, value, detail) VALUES (%s,%s,%s,%s,%s,%s)",
                           (now, DEVICE, "lag_mark", Shared.fg_app, None, (note or "")[:255] or None))
                    body = (f"<html><head><meta name=viewport content='width=device-width'></head>"
                            f"<body style='font:20px sans-serif;padding:2em;background:#1a1a19;color:#f0efec'>"
                            f"<p>Lag marked at {time.strftime('%H:%M:%S')}.</p>"
                            f"<p style='color:#c3c2b7'>App on screen: {Shared.fg_app or 'unknown'}</p></body></html>")
                    code = 200
                except Exception as e:  # noqa: BLE001
                    body, code = f"error: {e}", 500
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body.encode())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"device": DEVICE, "fg_app": Shared.fg_app}).encode())

        def log_message(self, *a):
            pass
    return H


# ---------------------------------------------------------------- main loop
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


METRIC_COLS = ["uptime_s", "mem_total_kb", "mem_avail_kb", "swap_total_kb", "swap_free_kb", "load1",
               "cpu_pct", "io_some_avg60", "io_full_avg60", "swapin_ps", "swapout_ps", "majfault_ps",
               "direct_reclaim_ps", "thermal_status", "skin_c", "soc_c", "cpu_big_c", "battery_c",
               "battery_pct", "charging", "screen_on", "fg_app"]


def tick(db, state, cache):
    serial = find_device(state)
    if not serial:
        if state.get("connected", True):
            log("phone not reachable; waiting")
        state["connected"] = False
        state.pop("prev", None)
        return
    if not state.get("connected"):
        log(f"connected via {serial}")
    state["connected"] = True
    if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", serial):
        state["last_addr"] = serial

    now = time.time()
    ts = datetime.fromtimestamp(int(now), timezone.utc).replace(tzinfo=None)
    m, pids, top5 = parse_minute(sh(serial, MINUTE_SCRIPT), state, now)
    if m.get("mem_total_kb") is None:
        log("empty sample; skipping")
        return
    Shared.fg_app = m.get("fg_app")

    # reboot detection
    last_up = state.get("last_uptime")
    if last_up is not None and m["uptime_s"] < last_up:
        boot = datetime.fromtimestamp(now - m["uptime_s"], timezone.utc).replace(tzinfo=None)
        db.run("INSERT INTO pixel_events (ts, device, kind, detail) VALUES (%s,%s,'reboot',%s)",
               (boot, DEVICE, f"previous uptime {last_up // 3600} h"))
    state["last_uptime"] = m["uptime_s"]

    cols = ", ".join(["ts", "device"] + METRIC_COLS)
    ph = ", ".join(["%s"] * (2 + len(METRIC_COLS)))
    db.run(f"INSERT IGNORE INTO pixel_metrics ({cols}) VALUES ({ph})",
           [ts, DEVICE] + [m.get(c) for c in METRIC_COLS])
    db.run("INSERT INTO pixel_cpu (ts, device, proc, cpu_pct) VALUES (%s,%s,%s,%s)",
           [(ts, DEVICE, n[:160], c) for n, c in top5], many=True)

    if now - cache.get("uid_t", 0) > 3600:
        cache["uids"] = refresh_uids(serial)
        cache["uid_t"] = now
    events = fetch_events(serial, state, pids, cache.get("uids", {}))
    db.run("INSERT INTO pixel_events (ts, device, kind, app, value, detail) VALUES (%s,%s,%s,%s,%s,%s)",
           [(t, DEVICE, k, (a or "")[:160] or None, v, d) for t, k, a, v, d in events], many=True)

    minute = int(now // 60)
    if minute % PROC_EVERY == 0 or not cache.get("proc_done"):
        rows = parse_proc_mem(sh(serial, PROC_SCRIPT), pids)
        db.run("INSERT INTO pixel_proc_mem (ts, device, proc, rss_kb, swap_kb) VALUES (%s,%s,%s,%s,%s)",
               [(ts, DEVICE, n[:160], r, sw) for n, r, sw in rows], many=True)
        cache["proc_done"] = True

    day = time.strftime("%Y-%m-%d")
    if cache.get("pruned") != day:
        for t in ("pixel_cpu", "pixel_proc_mem"):
            db.run(f"DELETE FROM {t} WHERE ts < UTC_TIMESTAMP() - INTERVAL {RETAIN_DETAIL_DAYS} DAY")
        cache["pruned"] = day


def main():
    db = DB()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), make_handler(db))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"pixel-monitor for {DEVICE}; lag marks at :{HTTP_PORT}/lag")
    state, cache = load_state(), {}
    once = "--once" in sys.argv
    while True:
        start = time.time()
        try:
            tick(db, state, cache)
        except Exception as e:  # noqa: BLE001
            log(f"tick error: {type(e).__name__}: {e}")
            state.pop("prev", None)
        save_state(state)
        if once:
            return
        time.sleep(max(5, INTERVAL - (time.time() - start)))


if __name__ == "__main__":
    main()
