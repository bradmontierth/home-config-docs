"""Deploy a HA MQTT-discovery switch (Kitchen Voice Assistant / Active) into
Node-RED via the Admin API (POST /flow -> new tab, no restart).

ON  -> satellite mode active
OFF -> satellite mode shadow
State synced back from the satellite /health every 30s.
"""
import json
import urllib.request

NR = "http://127.0.0.1:1880"
BROKER = "82f540b7378c2e35"          # existing "beelink mini" mosquitto node
SAT = "http://192.168.10.24:8781"
CMD_T = "voice/kitchen/assistant_active/set"
STATE_T = "voice/kitchen/assistant_active/state"
AVAIL_T = "voice/kitchen/assistant_active/availability"

# --- discovery config payload (matches existing NR discovery convention) ---
CONFIG = {
    "name": "Active",
    "unique_id": "kitchen_voice_assistant_active",
    "object_id": "kitchen_voice_assistant_active",
    "device": {
        "identifiers": ["kitchen_voice_assistant"],
        "name": "Kitchen Voice Assistant",
        "manufacturer": "homegrown",
        "model": "satellite",
    },
    "command_topic": CMD_T,
    "state_topic": STATE_T,
    "availability_topic": AVAIL_T,
    "payload_on": "ON",
    "payload_off": "OFF",
    "icon": "mdi:microphone-message",
}

build_config = (
    'msg.topic = "homeassistant/switch/kitchen_voice_assistant/active/config";\n'
    "msg.payload = " + json.dumps(CONFIG) + ";\n"
    "msg.retain = true;\n"
    "return msg;\n"
)

map_cmd = (
    "const on = String(msg.payload).toUpperCase() === 'ON';\n"
    "msg.payload = { mode: on ? 'active' : 'shadow' };\n"
    "msg.headers = { 'Content-Type': 'application/json' };\n"
    "return msg;\n"
)

cmd_state = (
    "const mode = (msg.payload && msg.payload.mode) || 'shadow';\n"
    "return { topic: '" + STATE_T + "', payload: mode === 'active' ? 'ON' : 'OFF', retain: true };\n"
)

health_state = (
    "if (!msg.payload || !msg.payload.mode) return null;\n"
    "const on = msg.payload.mode === 'active';\n"
    "return [[\n"
    "  { topic: '" + STATE_T + "', payload: on ? 'ON' : 'OFF', retain: true },\n"
    "  { topic: '" + AVAIL_T + "', payload: 'online', retain: true }\n"
    "]];\n"
)


def fn(nid, name, code, outputs, wires, x, y):
    return {"id": nid, "type": "function", "name": name, "func": code,
            "outputs": outputs, "noerr": 0, "initialize": "", "finalize": "",
            "libs": [], "wires": wires, "x": x, "y": y}


def mqtt_out(nid, name, x, y):
    return {"id": nid, "type": "mqtt out", "name": name, "topic": "", "qos": "0",
            "retain": "", "respTopic": "", "contentType": "", "userProps": "",
            "correl": "", "expiry": "", "broker": BROKER, "x": x, "y": y, "wires": []}


def http_req(nid, name, method, url, wires, x, y):
    return {"id": nid, "type": "http request", "name": name, "method": method,
            "ret": "obj", "paytoqs": "ignore", "url": url, "tls": "", "persist": False,
            "proxy": "", "insecureHTTPParser": False, "authType": "", "senderr": False,
            "headers": [], "wires": wires, "x": x, "y": y}


def inject(nid, name, repeat, once, delay, wires, x, y):
    return {"id": nid, "type": "inject", "name": name, "props": [],
            "repeat": repeat, "crontab": "", "once": once, "onceDelay": delay,
            "topic": "", "x": x, "y": y, "wires": wires}


def mqtt_in(nid, name, topic, wires, x, y):
    return {"id": nid, "type": "mqtt in", "name": name, "topic": topic, "qos": "0",
            "datatype": "utf8", "broker": BROKER, "nl": False, "rap": True,
            "rh": 0, "inputs": 0, "x": x, "y": y, "wires": wires}


nodes = [
    # row 1: discovery publish (once, 3s after deploy)
    inject("va_disc_inj", "publish discovery", "", True, "3", [["va_build_cfg"]], 180, 80),
    fn("va_build_cfg", "build switch config", build_config, 1, [["va_pub"]], 400, 80),
    mqtt_out("va_pub", "-> homeassistant/.../config", 640, 80),
    # row 2: HA command -> satellite /mode -> state back
    mqtt_in("va_cmd_in", "cmd: assistant_active/set", CMD_T, [["va_map"]], 180, 160),
    fn("va_map", "ON->active / OFF->shadow", map_cmd, 1, [["va_post"]], 420, 160),
    http_req("va_post", "POST satellite /mode", "POST", SAT + "/mode", [["va_cmd_state"]], 650, 160),
    fn("va_cmd_state", "mode->state", cmd_state, 1, [["va_state_out"]], 860, 160),
    mqtt_out("va_state_out", "-> state", 1050, 160),
    # row 3: sync state from satellite health (every 30s + at start)
    inject("va_hp_inj", "poll health 30s", "30", True, "5", [["va_get_health"]], 180, 240),
    http_req("va_get_health", "GET satellite /health", "GET", SAT + "/health", [["va_hs"]], 420, 240),
    fn("va_hs", "health->state+avail", health_state, 1, [["va_sync_out"]], 660, 240),
    mqtt_out("va_sync_out", "-> state+avail", 880, 240),
]

flow = {"label": "Voice Assistant Mode", "nodes": nodes, "configs": [], "subflows": []}

req = urllib.request.Request(
    NR + "/flow", data=json.dumps(flow).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
with urllib.request.urlopen(req, timeout=15) as r:
    print("POST /flow ->", r.status, r.read().decode())
