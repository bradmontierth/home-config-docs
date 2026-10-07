#!/usr/bin/env python3
"""Optiplex (Lookout camera hub) metrics -> one JSON line per minute on stdout.

Piped into mosquitto_pub -l by optiplex-metrics-mqtt.service, topic
pi/metrics/optiplex, stored by the Beelink Node-RED Device Monitoring tab in
hubitat_logging.DeviceMonitoring (DeviceNM 'Optiplex'). Runs as root (smartctl,
docker). Same base fields as the Pis, plus the archive disk, drive temperatures
and Lookout's own health.
"""
import glob, json, os, sqlite3, subprocess, sys, time, urllib.request

INTERVAL = 60
IFACE = "enp0s31f6"
ARCHIVE = "/srv/lookout-archive"
INDEX = "/srv/lookout/hot/index.db"
CAMERAS = "/home/pi/lookout/app/cameras.json"
SMART_EVERY = 300

prev = {}
smart = {"at": 0}


def cpu_pct():
    f = [int(x) for x in open("/proc/stat").readline().split()[1:9]]
    total, idle = sum(f), f[3] + f[4]
    pct = None
    if "cpu" in prev:
        dt, di = total - prev["cpu"][0], idle - prev["cpu"][1]
        pct = round(100 * (dt - di) / dt, 1) if dt > 0 else 0
    prev["cpu"] = (total, idle)
    return pct


def meminfo():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        m[k] = int(v.split()[0])
    return m


def coretemp():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(f"{d}/name").read().strip() == "coretemp":
                return round(int(open(f"{d}/temp1_input").read()) / 1000, 1)   # package
        except OSError:
            pass
    return None


def cpu_mhz():
    f = [int(open(p).read()) for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")]
    return round(sum(f) / len(f) / 1000) if f else None


def disk(path):
    s = os.statvfs(path)
    total, avail = s.f_blocks * s.f_frsize // 1024, s.f_bavail * s.f_frsize // 1024
    used = total - s.f_bfree * s.f_frsize // 1024
    return used, total, avail, round(100 * used / (used + avail), 1) if used + avail else 0


def net(now):
    rx = int(open(f"/sys/class/net/{IFACE}/statistics/rx_bytes").read())
    tx = int(open(f"/sys/class/net/{IFACE}/statistics/tx_bytes").read())
    out = {"rx_bytes": rx, "tx_bytes": tx}
    if "net" in prev:
        t0, rx0, tx0 = prev["net"]
        dt = now - t0
        if dt > 0 and rx >= rx0 and tx >= tx0:
            out["rx_mbps"] = round((rx - rx0) * 8 / dt / 1e6, 1)
            out["tx_mbps"] = round((tx - tx0) * 8 / dt / 1e6, 1)
    prev["net"] = (now, rx, tx)
    return out


def drive_temps(now):
    if now - smart["at"] >= SMART_EVERY:
        smart["at"] = now
        for key, dev in (("ssd_temp_c", "/dev/sda"), ("hdd_temp_c", "/dev/sdb")):
            try:
                r = subprocess.run(["smartctl", "-A", "-j", dev], capture_output=True, text=True, timeout=20)
                smart[key] = json.loads(r.stdout).get("temperature", {}).get("current")
            except Exception:
                smart[key] = None
    return {k: v for k, v in smart.items() if k != "at"}


def lookout(now):
    out = {}
    cams, only = [], None
    try:
        cams = json.load(open(CAMERAS))
        env = "/home/pi/lookout/.env"
        for line in open(env):
            if line.startswith("LOOKOUT_ONLY="):
                only = {c.strip() for c in line.split("=", 1)[1].split(",") if c.strip()}
        out["cams_total"] = len([c for c in cams if not only or c["id"] in only])
    except Exception:
        pass
    # Recording = the camera's newest file grew in the last 3 min (straight from disk, so
    # it doesn't depend on indexing). cams_down = no footage at all for 30 min.
    try:
        ids = [c["id"] for c in cams if not only or c["id"] in only]
        names = {c["id"]: c["name"] for c in cams}
        age = {}
        for cid in ids:
            # Either disk: RECORD_TIER=archive (2026-10-07) writes to the 6 TB disk.
            # Names are timestamps, so the newest per folder is the max name.
            newest = [max(fs) for d in ("/srv/lookout/hot", ARCHIVE) if (fs := glob.glob(f"{d}/{cid}/*.mp4"))]
            age[cid] = now - max(os.path.getmtime(f) for f in newest) if newest else 1e9
        out["cams_recording"] = sum(1 for a in age.values() if a < 180)
        down = [names[c] for c, a in age.items() if a >= 1800]
        out["cams_down_count"] = len(down)
        out["cams_down"] = ", ".join(down)            # text: used by the alert, not stored
    except Exception:
        pass
    try:
        db = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True, timeout=5)
        configured = {c["id"] for c in cams if not only or c["id"] in only}
        newest = [r[1] for r in db.execute("SELECT cam, MAX(start) FROM segments GROUP BY cam") if r[1] and r[0] in configured]
        out["index_lag_s"] = int(now - min(newest)) if newest else 0   # the most-behind camera
        first = db.execute("SELECT MIN(start) FROM segments WHERE ok=1").fetchone()[0]
        out["retention_h"] = round((now - first) / 3600, 1) if first else 0
        for loc in ("hot", "archive"):
            out[f"{loc}_gb"] = round((db.execute("SELECT COALESCE(SUM(bytes),0) FROM segments WHERE loc=?", (loc,)).fetchone()[0]) / 1e9, 1)
        db.close()
    except Exception:
        pass
    try:
        r = subprocess.run(["docker", "ps", "--filter", "name=lookout-", "--filter", "status=running", "-q"], capture_output=True, text=True, timeout=10)
        out["lookout_containers"] = len(r.stdout.split())
    except Exception:
        pass
    try:
        urllib.request.urlopen("http://127.0.0.1:1984/api/streams", timeout=5).read()
        out["go2rtc_up"] = 1
    except Exception:
        out["go2rtc_up"] = 0
    return out


while True:
    now = time.time()
    m = meminfo()
    load1, load5, load15 = open("/proc/loadavg").read().split()[:3]
    ru, rt, ra, rp = disk("/")
    s = {
        "ts": int(now), "device": "Optiplex", "iface": IFACE,
        "cpu_pct": cpu_pct(), "cpu_mhz": cpu_mhz(), "temp_c": coretemp(),
        "mem_used_kb": m["MemTotal"] - m["MemAvailable"], "mem_total_kb": m["MemTotal"],
        "mem_pct": round(100 * (m["MemTotal"] - m["MemAvailable"]) / m["MemTotal"], 1),
        "swap_used_kb": m["SwapTotal"] - m["SwapFree"], "swap_total_kb": m["SwapTotal"],
        "load1": float(load1), "load5": float(load5), "load15": float(load15),
        "uptime_s": int(float(open("/proc/uptime").read().split()[0])),
        "disk_used_root_kb": ru, "disk_total_root_kb": rt, "disk_avail_root_kb": ra, "disk_pct_root": rp,
    }
    if os.path.ismount(ARCHIVE):
        au, at, aa, ap = disk(ARCHIVE)
        s.update(disk_used_archive_kb=au, disk_total_archive_kb=at, disk_pct_archive=ap)
    s.update(net(now))
    s.update(drive_temps(now))
    s.update(lookout(now))
    if s["cpu_pct"] is not None:          # first sample has no CPU delta yet
        print(json.dumps({k: v for k, v in s.items() if v is not None}), flush=True)
    time.sleep(INTERVAL)
