#!/usr/bin/env python3
"""Music Assistant upgrade check: one command before and after the upgrade.

    ma_check.py capture <name>     snapshot MA/snapserver/HA state -> snapshots/<name>.json
    ma_check.py smoke [--audio]    run the checks (audio = quiet Simon Room tests) -> results/<ts>.json
    ma_check.py diff <a> <b>       compare two snapshots

Plan: home_config/music-assistant-upgrade-plan.md. Stdlib only, so it runs the same
whatever happens to the containers. Secrets are read from their files, never printed:
MA token from home-audio-adapter/.env, HA token from cecret_lake/dashboard_webapp/ha_token.
"""
import argparse
import http.server
import json
import math
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
MA_URL = "http://192.168.10.217:8095"
HA_URL = "http://192.168.10.217:8123"
SNAP = ("192.168.10.140", 1705)
NODE_RED = "http://127.0.0.1:1880"
AMP_SUBFLOW = "e711d48f74f78209"   # Node-RED "Amp Speakers subflow" (hardcodes MA player ids)
TEST_PLAYER = "ma_simon_room"
TEST_SNAP_CLIENT = "simon_room"
TEST_VOLUME = 6                    # % on the Simon Room snapclient; quiet on purpose
TONE_PORT = 8812
# Consumers' own HTTP surfaces: each one exercises that consumer's live MA connection.
CONSUMERS = {
    "home-audio-adapter (Tempo bridge)": ("http://127.0.0.1:8461/v1/devices", lambda d: len(d.get("devices", [])) > 0),
    "tts-router": ("http://127.0.0.1:8890/api/ma/players", lambda d: len(d.get("players", [])) > 0),
    "nfc-jukebox": ("http://127.0.0.1:8769/api/now-playing", lambda d: "queue_id" in d),
    "voice orchestrator": ("http://127.0.0.1:8785/music/state", lambda d: d.get("ok") is True),
    "amp-lease-manager": ("http://127.0.0.1:8462/healthz", lambda d: d.get("status") == "ok"),
}


# ---------------------------------------------------------------- plumbing

def _env_value(path, key):
    for line in open(path):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise KeyError(key)


def ma(command, **args):
    tok = _env_value("/home/pi/home-audio-adapter/.env", "MA_TOKEN")
    body = json.dumps({"command": command, "message_id": "check", "args": args}).encode()
    req = urllib.request.Request(MA_URL + "/api", data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    out = json.loads(raw) if raw.strip() else None
    if isinstance(out, dict) and "error_code" in out:
        raise RuntimeError(f"{command}: {out.get('details') or out}")
    return out


def ha(path, data=None):
    tok = open("/home/pi/cecret_lake/dashboard_webapp/ha_token").read().strip()
    req = urllib.request.Request(HA_URL + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
    return raw if path == "/api/template" else json.loads(raw)


def snap(method, params=None):
    s = socket.create_connection(SNAP, 5)
    s.sendall((json.dumps({"id": 1, "jsonrpc": "2.0", "method": method, "params": params or {}}) + "\r\n").encode())
    buf = b""
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                raise RuntimeError("snapserver closed the connection")
            buf += chunk
            for line in buf.split(b"\n"):
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("id") == 1:
                    if "error" in m:
                        raise RuntimeError(f"snapserver {method}: {m['error']}")
                    return m["result"]
    finally:
        s.close()


def snap_server_health():
    """snapserver process start time + SIGSEGV count (the crash this upgrade is meant to fix)."""
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "home-audio-pi",
         "systemctl show snapserver -p ActiveEnterTimestamp --value; "
         "journalctl -u snapserver --since '-30d' --no-pager -o short | grep -c SEGV || true"],
        capture_output=True, text=True, timeout=30).stdout.split("\n")
    return {"active_since": out[0].strip(), "segv_30d": int(out[1].strip() or 0)}


def snap_client(cid):
    st = snap("Server.GetStatus")["server"]
    for g in st["groups"]:
        for c in g["clients"]:
            if c["id"] == cid:
                return g, c
    raise KeyError(cid)


def get_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def player(pid):
    return next((p for p in ma("players/all") if p["player_id"] == pid), None)


def wait_for(fn, timeout, every=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


# ---------------------------------------------------------------- capture

def capture():
    players = ma("players/all")
    queues = ma("player_queues/all")
    info = ma("info")
    providers = ma("providers")
    st = snap("Server.GetStatus")["server"]
    ma_entities = json.loads(ha("/api/template", {"template": "{{ integration_entities('music_assistant') | list | tojson }}"}))
    states = {s["entity_id"]: s for s in ha("/api/states")}
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ma": {"server_version": info.get("server_version"), "schema_version": info.get("schema_version")},
        "players": {p["player_id"]: {
            "name": p.get("display_name") or p.get("name"), "provider": p.get("provider"),
            "type": p.get("type"), "available": p.get("available"),
            "features": sorted(p.get("supported_features") or []),
            "keys": sorted(p.keys()),
        } for p in players},
        "queues": sorted(q.get("queue_id") for q in queues),
        "providers": sorted({f"{p.get('domain')}:{p.get('instance_id')}:{'up' if p.get('available') else 'down'}" for p in providers}),
        "snapserver": {
            "version": st["server"]["snapserver"]["version"],
            "clients": {c["id"]: {"name": c["config"]["name"] or c["host"]["name"], "connected": c["connected"],
                                  "latency": c["config"]["latency"]} for g in st["groups"] for c in g["clients"]},
            "streams": sorted(s["id"] for s in st["streams"]),
        },
        "ha_entities": {e: states[e]["state"] if e in states else "missing" for e in ma_entities},
    }


def cmd_capture(a):
    snapdir = os.path.join(HERE, "snapshots")
    os.makedirs(snapdir, exist_ok=True)
    data = capture()
    p = os.path.join(snapdir, a.name + ".json")
    with open(p, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    print(f"wrote {p}: MA {data['ma']['server_version']}, {len(data['players'])} players, "
          f"{len(data['snapserver']['clients'])} snapclients, {len(data['ha_entities'])} HA entities")


def cmd_diff(a):
    x = json.load(open(os.path.join(HERE, "snapshots", a.a + ".json")))
    y = json.load(open(os.path.join(HERE, "snapshots", a.b + ".json")))
    def walk(p, u, v):
        if isinstance(u, dict) and isinstance(v, dict):
            for k in sorted(set(u) | set(v)):
                walk(f"{p}.{k}", u.get(k, "<absent>"), v.get(k, "<absent>"))
        elif u != v:
            print(f"{p[1:]}: {u!r} -> {v!r}")
    walk("", {k: x[k] for k in x if k != "captured_at"}, {k: y[k] for k in y if k != "captured_at"})


# ---------------------------------------------------------------- smoke

class Results:
    def __init__(self):
        self.rows = []

    def check(self, name, fn):
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception as e:  # a failing check must not stop the rest
            ok, detail = False, f"{type(e).__name__}: {e}"
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail, "secs": round(time.time() - t0, 1)})
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def tone_files(d):
    """Soft two-note chime (2 s) for the announcement and a quiet 10 s hum for the play test."""
    def write(path, secs, notes):
        sr = 48000
        with wave.open(path, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
            frames = bytearray()
            for i in range(int(secs * sr)):
                t = i / sr
                env = min(1.0, t / 0.05, (secs - t) / 0.3)
                f = notes[min(int(t / (secs / len(notes))), len(notes) - 1)]
                v = int(0.06 * env * 32767 * math.sin(2 * math.pi * f * t))   # about -24 dBFS
                frames += struct.pack("<hh", v, v)
            w.writeframes(bytes(frames))
    os.makedirs(d, exist_ok=True)
    write(os.path.join(d, "chime.wav"), 2.0, [660, 880])
    write(os.path.join(d, "hum.wav"), 10.0, [440])


def serve(d):
    handler = lambda *x, **k: http.server.SimpleHTTPRequestHandler(*x, directory=d, **k)
    http.server.SimpleHTTPRequestHandler.log_message = lambda *x: None
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", TONE_PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def cmd_smoke(a):
    r = Results()
    base = json.load(open(os.path.join(HERE, "snapshots", a.baseline + ".json"))) if a.baseline else None
    health_before = snap_server_health()
    print(f"snapserver up since {health_before['active_since']}, SEGV in 30 d: {health_before['segv_30d']}")

    info = ma("info")
    r.check("MA reachable", lambda: (True, f"server {info.get('server_version')}, schema {info.get('schema_version')}"))

    players = {p["player_id"]: p for p in ma("players/all")}
    # Only players that were available in the baseline: MA drops idle browser "This Device"
    # players (builtin_player) on restart, which is not a regression.
    expected = {p for p, v in base["players"].items() if v["available"]} if base else set()
    expected |= {"ma_loft", "ma_simon_room", "ma_claire_room", "ma_master_bedroom", "ma_shower", "e4:5f:01:67:1e:56"}
    def players_ok():
        missing = sorted(p for p in expected if p not in players)
        down = sorted(p for p in expected if p in players and not players[p].get("available")
                      and not (base and not base["players"][p]["available"]))
        return not missing and not down, f"{len(players)} players; missing {missing or 'none'}; newly unavailable {down or 'none'}"
    r.check("expected players present + available", players_ok)

    def subflow_ids():
        flows = get_json(NODE_RED + "/flows")
        flows = flows["flows"] if isinstance(flows, dict) else flows
        txt = json.dumps([n for n in flows if n.get("z") == AMP_SUBFLOW or n.get("id") == AMP_SUBFLOW])
        ids = set(re.findall(r"ma_[a-z_]+|e4:5f:01:67:1e:56", txt))
        bad = sorted(i for i in ids if i not in players)
        return not bad and ids, f"Amp Speakers subflow uses {sorted(ids)}; unknown to MA: {bad or 'none'}"
    r.check("Node-RED announcement subflow player ids exist", subflow_ids)

    def queues_ok():
        q = {x.get("queue_id") for x in ma("player_queues/all")}
        need = {"e4:5f:01:67:1e:56", TEST_PLAYER}
        return need <= q, f"{len(q)} queues; kitchen + Simon queues {'present' if need <= q else 'MISSING'}"
    r.check("queues", queues_ok)

    def library_ok():
        res = ma("music/search", search_query="raffi", media_types=["track"], limit=5)
        tracks = (res or {}).get("tracks") or []
        return len(tracks) > 0, f"search 'raffi' -> {len(tracks)} tracks"
    r.check("library search (Navidrome provider)", library_ok)

    def providers_ok():
        provs = ma("providers")
        doms = {p.get("domain"): p.get("available") for p in provs}
        need = ["snapcast", "squeezelite", "opensubsonic"]
        return all(doms.get(n) for n in need), {n: doms.get(n) for n in need}
    r.check("providers loaded", providers_ok)

    def snap_ok():
        st = snap("Server.GetStatus")["server"]
        clients = {c["id"]: c["connected"] for g in st["groups"] for c in g["clients"]}
        want = ["loft", "simon_room", "claire_room", "master_bedroom", "shower"]
        down = [c for c in want if not clients.get(c)]
        return not down, f"snapserver {st['server']['snapserver']['version']}; zone clients down: {down or 'none'}; streams {[s['id'] for s in st['streams']]}"
    r.check("snapserver zones connected", snap_ok)

    def ha_ok():
        ents = json.loads(ha("/api/template", {"template": "{{ integration_entities('music_assistant') | list | tojson }}"}))
        states = {s["entity_id"]: s["state"] for s in ha("/api/states")}
        bad = [e for e in ents if states.get(e) in (None, "unavailable")
               and not (base and base["ha_entities"].get(e) == "unavailable")]
        return len(ents) > 0 and not bad, f"{len(ents)} MA entities in HA; newly unavailable: {bad or 'none'}"
    r.check("Home Assistant MA entities", ha_ok)

    for name, (url, good) in CONSUMERS.items():
        r.check(f"consumer: {name}", lambda url=url, good=good: (good(get_json(url)), url))

    if a.audio:
        audio_checks(r)

    time.sleep(3)   # a crash right at the end shows as a pending restart; let systemd finish it
    health_after = snap_server_health()
    r.check("snapserver did not crash during the run",
            lambda: (health_after["active_since"] == health_before["active_since"]
                     and health_after["segv_30d"] == health_before["segv_30d"],
                     f"up since {health_after['active_since']}; SEGV in 30 d {health_before['segv_30d']} -> {health_after['segv_30d']}"))

    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    out = os.path.join(HERE, "results", time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(out, "w") as f:
        json.dump({"ma": info.get("server_version"), "audio": a.audio, "rows": r.rows}, f, indent=1)
    failed = [x for x in r.rows if not x["ok"]]
    print(f"\n{len(r.rows) - len(failed)}/{len(r.rows)} passed -> {out}")
    return 1 if failed else 0


def audio_checks(r):
    """Quiet tests on Simon Room only. Volume is read from and restored at the snapserver (MA's cached value lies)."""
    d = "/tmp/ma-check-tones"
    tone_files(d)
    srv = serve(d)
    host = "http://192.168.10.217:%d" % TONE_PORT
    _, c = snap_client(TEST_SNAP_CLIENT)
    saved = c["config"]["volume"]["percent"]
    print(f"Simon Room snapclient volume {saved}% (restored at the end)")
    try:
        if player(TEST_PLAYER).get("state") == "playing":
            r.check("Simon Room idle before audio tests", lambda: (False, "something is playing there; audio tests skipped"))
            return

        def volume_roundtrip():
            ma("players/cmd/volume_set", player_id=TEST_PLAYER, volume_level=TEST_VOLUME + 1)
            got = wait_for(lambda: snap_client(TEST_SNAP_CLIENT)[1]["config"]["volume"]["percent"] == TEST_VOLUME + 1, 5)
            ma("players/cmd/volume_set", player_id=TEST_PLAYER, volume_level=TEST_VOLUME)
            back = wait_for(lambda: snap_client(TEST_SNAP_CLIENT)[1]["config"]["volume"]["percent"] == TEST_VOLUME, 5)
            return bool(got and back), f"MA volume_set reached the snapclient: {TEST_VOLUME + 1}% {'yes' if got else 'NO'}, {TEST_VOLUME}% {'yes' if back else 'NO'}"
        r.check("volume set via MA lands on the snapclient", volume_roundtrip)

        def announcement():
            t0 = time.time()
            ma("players/cmd/play_announcement", player_id=TEST_PLAYER, url=host + "/chime.wav",
               use_pre_announce=False, volume_level=TEST_VOLUME)
            started = time.time() - t0
            done = wait_for(lambda: not player(TEST_PLAYER).get("announcement_in_progress"), 60)
            grp = snap_client(TEST_SNAP_CLIENT)[0]["stream_id"]
            vol = snap_client(TEST_SNAP_CLIENT)[1]["config"]["volume"]["percent"]
            ok = bool(done) and grp == "default" and vol == TEST_VOLUME
            return ok, (f"call returned after {started:.1f} s; announcement_in_progress cleared: {'yes' if done else 'NO (wedge)'}; "
                        f"group back on '{grp}'; volume after {vol}%")
        r.check("announcement on Simon Room (the wedge this upgrade fixes)", announcement)

        def play_stop():
            ma("player_queues/play_media", queue_id=TEST_PLAYER, media=[host + "/hum.wav"], option="replace")
            playing = wait_for(lambda: player(TEST_PLAYER).get("state") == "playing", 15)
            on_stream = wait_for(lambda: snap_client(TEST_SNAP_CLIENT)[0]["stream_id"] != "default", 10)
            stream_id = snap_client(TEST_SNAP_CLIENT)[0]["stream_id"]
            time.sleep(3)
            ma("players/cmd/stop", player_id=TEST_PLAYER)
            idle = wait_for(lambda: player(TEST_PLAYER).get("state") in ("idle", "paused", "stopped"), 15)
            back = wait_for(lambda: snap_client(TEST_SNAP_CLIENT)[0]["stream_id"] == "default", 10)
            return bool(playing and on_stream and idle and back), (
                f"playing {'yes' if playing else 'NO'} on stream '{stream_id}'; stopped {'yes' if idle else 'NO'}; "
                f"group back on default {'yes' if back else 'NO'}")
        r.check("play + stop on Simon Room", play_stop)
    finally:
        # Retry: if the snapserver just crashed it is back within a few seconds.
        for attempt in range(15):
            try:
                snap("Client.SetVolume", {"id": TEST_SNAP_CLIENT, "volume": {"percent": saved, "muted": False}})
                print(f"restored Simon Room snapclient volume to {saved}%")
                break
            except Exception as e:
                if attempt == 14:
                    print(f"!! could not restore Simon Room volume to {saved}%: {e}")
                time.sleep(1)
        srv.shutdown()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture"); c.add_argument("name"); c.set_defaults(fn=cmd_capture)
    s = sub.add_parser("smoke"); s.add_argument("--audio", action="store_true")
    s.add_argument("--baseline", default="pre-upgrade"); s.set_defaults(fn=cmd_smoke)
    d = sub.add_parser("diff"); d.add_argument("a"); d.add_argument("b"); d.set_defaults(fn=cmd_diff)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
