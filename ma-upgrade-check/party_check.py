#!/usr/bin/env python3
"""Live check of party mode through the Tempo bridge (home-audio-adapter :8461).

    party_check.py [--target tempo_group_kitchen_tv] [--detach]

Plays a very soft hum to the party group like Tempo would, then checks at the snapserver that
the kitchen party client and the TV share one group on one MA stream, that the kitchen
squeezelite player is not playing, and that the Shield is awake. With --detach it then starts
the kitchen squeezelite player (quietly) and checks the bridge takes the kitchen out of the
group while the TV keeps playing. Stops everything and restores volumes at the end.
"""
import argparse
import json
import math
import os
import struct
import subprocess
import time
import urllib.request
import uuid
import wave

import ma_check as m

BRIDGE = "http://127.0.0.1:8461"
KITCHEN_SQ = "e4:5f:01:67:1e:56"
SHIELD_CLIENT = "624d338e-f13c-444b-8f45-765b085fb035"
PARTY_CLIENT = "kitchen-party"
ADB = "/home/pi/home_config/home-agent-android/.android-sdk/platform-tools/adb"
QUIET = {PARTY_CLIENT: 10, SHIELD_CLIENT: 25}   # snapclient % while the test runs


def post(path, body):
    req = urllib.request.Request(BRIDGE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def hum(path, secs=40):
    sr = 48000
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        frames = bytearray()
        for i in range(secs * sr):
            t = i / sr
            env = min(1.0, t / 0.2, (secs - t) / 0.5)
            v = int(0.02 * env * 32767 * math.sin(2 * math.pi * 330 * t))   # about -34 dBFS
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


def snap_view():
    st = m.snap("Server.GetStatus")["server"]
    return {c["id"]: (g["id"], g["stream_id"], c["config"]["volume"]["percent"])
            for g in st["groups"] for c in g["clients"]}


def shield_awake():
    out = subprocess.run([ADB, "-s", "192.168.40.67:5555", "shell", "dumpsys power | grep -m1 mWakefulness"],
                         capture_output=True, text=True, timeout=10).stdout
    return "Awake" in out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="tempo_group_kitchen_tv")
    ap.add_argument("--detach", action="store_true")
    a = ap.parse_args()
    r = m.Results()
    d = "/tmp/ma-check-tones"
    os.makedirs(d, exist_ok=True)
    hum(os.path.join(d, "party.wav"))
    srv = m.serve(d)
    url = "http://192.168.10.217:%d/party.wav" % m.TONE_PORT
    before = m.snap_server_health()
    saved = {cid: v[2] for cid, v in snap_view().items() if cid in QUIET}
    kitchen_vol = (m.player(KITCHEN_SQ) or {}).get("volume_level")
    print(f"saved snap volumes {saved}; kitchen squeezelite MA volume {kitchen_vol}; shield awake: {shield_awake()}")
    session_id = None
    client_session = "party-check-" + uuid.uuid4().hex[:8]
    try:
        t0 = time.time()
        res = post("/v1/play", {"episode_id": "party-check", "media_url": url, "title": "Party check hum",
                                "position_ms": 0, "target_id": a.target, "client_session_id": client_session,
                                "queue": [{"episode_id": "party-check", "media_url": url, "title": "Party check hum"}]})
        session_id = res.get("session_id")
        for cid, pct in QUIET.items():   # the bridge set 35 / 100; drop to test levels at once
            m.snap("Client.SetVolume", {"id": cid, "volume": {"percent": pct, "muted": False}})
        r.check("bridge play accepted", lambda: (res.get("state") == "playing",
                f"{a.target}: state {res.get('state')} after {time.time() - t0:.1f} s"))

        partner = SHIELD_CLIENT if "tv" in a.target else "loft"
        def grouped():
            v = snap_view()
            ok = v[PARTY_CLIENT][0] == v[partner][0] and v[partner][1] != "default"
            mates = sorted(c for c, x in v.items() if x[0] == v[PARTY_CLIENT][0])
            return ok, f"kitchen party shares group {v[PARTY_CLIENT][0][:8]} on '{v[PARTY_CLIENT][1]}' with {mates}"
        r.check(f"kitchen party + {partner} in one snap group on one stream", grouped)
        r.check("kitchen squeezelite not playing", lambda: ((m.player(KITCHEN_SQ) or {}).get("state") != "playing",
                f"state {(m.player(KITCHEN_SQ) or {}).get('state')}"))
        if "tv" in a.target:
            r.check("Shield awake (TV woken)", lambda: (shield_awake(), "adb mWakefulness"))
        time.sleep(4)

        if a.detach:
            m.ma("players/cmd/volume_set", player_id=KITCHEN_SQ, volume_level=15)
            m.ma("player_queues/play_media", queue_id=KITCHEN_SQ, media=[url], option="replace")
            def detached():
                ok = m.wait_for(lambda: snap_view()[PARTY_CLIENT][0] != snap_view()[SHIELD_CLIENT][0], 10)
                v = snap_view()
                return bool(ok) and v[SHIELD_CLIENT][1] != "default", (
                    f"kitchen party now group {v[PARTY_CLIENT][0][:8]} stream '{v[PARTY_CLIENT][1]}'; "
                    f"TV still on '{v[SHIELD_CLIENT][1]}'")
            r.check("kitchen leaves the party when its own player starts; TV keeps playing", detached)
            m.ma("players/cmd/stop", player_id=KITCHEN_SQ)
    finally:
        if session_id:
            try:
                post("/v1/stop", {"session_id": session_id})
            except Exception as e:
                print("!! bridge stop failed:", e)
        if a.detach and kitchen_vol is not None:
            try:
                m.ma("players/cmd/volume_set", player_id=KITCHEN_SQ, volume_level=kitchen_vol)
            except Exception as e:
                print("!! kitchen volume restore failed:", e)
        time.sleep(2)
        for cid, pct in saved.items():
            for _ in range(10):
                try:
                    m.snap("Client.SetVolume", {"id": cid, "volume": {"percent": pct, "muted": False}})
                    break
                except Exception:
                    time.sleep(1)
        srv.shutdown()
    v = snap_view()
    r.check("after stop: both back on the default stream", lambda: (
        v[PARTY_CLIENT][1] == "default" and v[SHIELD_CLIENT][1] == "default",
        f"kitchen party '{v[PARTY_CLIENT][1]}', TV '{v[SHIELD_CLIENT][1]}'; volumes {v[PARTY_CLIENT][2]}/{v[SHIELD_CLIENT][2]}"))
    time.sleep(2)
    after = m.snap_server_health()
    r.check("snapserver did not crash", lambda: (after == before, f"{before} -> {after}"))
    print(f"\n{sum(x['ok'] for x in r.rows)}/{len(r.rows)} passed")


if __name__ == "__main__":
    main()
