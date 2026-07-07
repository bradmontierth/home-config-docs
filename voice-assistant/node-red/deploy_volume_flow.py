"""Deploy a Node-RED flow that drives the satellite's day-mode volume, reusing
the existing global `mode` (Day / Early Morning / Evening / Night / Away) — the
same source that sets defaultSpeakerVolume for Music Assistant announcements.

Polls global.mode every 2 min (+ once at start) and POSTs the mapped level to
the satellite /volume. The satellite applies it as software gain to its own
audio (chimes/alarm/TTS) and floors the alarm at 50%.
"""
import json
import urllib.request

NR = "http://127.0.0.1:1880"
SAT = "http://192.168.10.24:8781"

# day-mode -> volume (0-100). Day 60 / Early Morning + Evening 40 / Night 30.
MAP = {"Day": 60, "Early Morning": 40, "Evening": 40, "Night": 30, "Away": 40}
DEFAULT = 40

map_fn = (
    "const mode = global.get('mode');\n"
    "const map = " + json.dumps(MAP) + ";\n"
    "const level = (map[mode] != null) ? map[mode] : " + str(DEFAULT) + ";\n"
    "msg.payload = { level: level };\n"
    "msg.headers = { 'Content-Type': 'application/json' };\n"
    "msg.topic = 'va-volume ' + mode + ' -> ' + level;\n"
    "return msg;\n"
)

nodes = [
    {"id": "vav_inj", "type": "inject", "name": "poll mode 2m",
     "props": [], "repeat": "120", "crontab": "", "once": True, "onceDelay": "6",
     "topic": "", "x": 190, "y": 100, "wires": [["vav_map"]]},
    {"id": "vav_map", "type": "function", "name": "mode -> volume level",
     "func": map_fn, "outputs": 1, "noerr": 0, "initialize": "", "finalize": "",
     "libs": [], "x": 430, "y": 100, "wires": [["vav_post"]]},
    {"id": "vav_post", "type": "http request", "name": "POST satellite /volume",
     "method": "POST", "ret": "obj", "paytoqs": "ignore", "url": SAT + "/volume",
     "tls": "", "persist": False, "proxy": "", "insecureHTTPParser": False,
     "authType": "", "senderr": False, "headers": [], "x": 690, "y": 100, "wires": [[]]},
]

flow = {"label": "Voice Assistant Volume", "nodes": nodes, "configs": [], "subflows": []}
req = urllib.request.Request(NR + "/flow", data=json.dumps(flow).encode(),
                            headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    print("POST /flow ->", r.status, r.read().decode())
