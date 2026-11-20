#!/usr/bin/env python3
"""Build the 'Whole House Fan Smoke Pause' Node-RED tab JSON (for PUT/POST /flow).

Usage: build_tab.py > /tmp/whfsp-tab.json
       curl -X PUT http://127.0.0.1:1880/flow/7000fc3fbd48d1e1 -H "Content-Type: application/json" --data-binary @/tmp/whfsp-tab.json
"""
import json
import pathlib

HERE = pathlib.Path(__file__).parent
TAB = "7000fc3fbd48d1e1"  # id Node-RED assigned on first POST; redeploy with PUT /flow/TAB
HA = "23fd91e9137b71c5"          # Home Assistant server config
PUSHOVER_KEYS = "a3903de6e20b03dc"
MYSQL = "50d88d05b3f142e9"       # beelink hubitat_logging
WS_LINK_OUT = "whfsp_ws_raw_out"  # lives on the Weather Station tab

INFO = """Pauses the whole-house fan when outdoor air suddenly goes bad (neighbor grilling,
smoke) and resumes it once the air is clean again.

Signal: raw per-post AQI from the Ecowitt garage PM2.5 (~1 post / 61 s), tapped from
'scale PM' on the Weather Station tab via link. OutdoorAQILocal is a 5-post mean that
lags a spike by 2-3 min, so it is not used for the trip.

Pause: fan on (low or high) AND (2 consecutive posts > 50 OR 5-post mean > 50).
  Saves which relay(s) were on, turns both off, Pushover.
Resume: 5-post mean <= 25 held 10 min (30 min if we re-trip within 1 h of a resume),
  at least one window open -> restore saved relay(s), Pushover.
Cancel (no resume): all windows close, a relay turned on by hand, or 2 h without clean air.

Also logs every raw post to MySQL aqi (deviceId 421, name 'PM2.5raw').
State: flow context 'whfSmoke' (file-backed). Source: home_config/whf-smoke-pause/."""


def change(id_, source, x, y, wire):
    return {"id": id_, "type": "change", "z": TAB, "name": f"source={source}",
            "rules": [{"t": "set", "p": "whfspSource", "pt": "msg", "to": source, "tot": "str"}],
            "action": "", "property": "", "from": "", "to": "", "reg": False,
            "x": x, "y": y, "wires": [[wire]]}


def state_changed(id_, name, entities, x, y, wire, state_type="str"):
    return {"id": id_, "type": "server-state-changed", "z": TAB, "name": name, "server": HA,
            "version": 6, "outputs": 1, "exposeAsEntityConfig": "",
            "entities": {"entity": entities, "substring": [], "regex": []},
            "outputInitially": True, "stateType": state_type, "ifState": "", "ifStateType": "str",
            "ifStateOperator": "is", "outputOnlyOnStateChange": True, "for": "0", "forType": "num",
            "forUnits": "minutes", "ignorePrevStateNull": False, "ignorePrevStateUnknown": False,
            "ignorePrevStateUnavailable": False, "ignoreCurrentStateUnknown": True,
            "ignoreCurrentStateUnavailable": True,
            "outputProperties": [
                {"property": "payload", "propertyType": "msg", "value": "", "valueType": "entityState"},
                {"property": "topic", "propertyType": "msg", "value": "", "valueType": "triggerId"}],
            "x": x, "y": y, "wires": [[wire]]}


SQL = """const v = Number(msg.payload);
if (!Number.isFinite(v)) return null;
msg.topic = "INSERT INTO aqi (deviceId, displayName, value, name) VALUES (421, 'Garage AQI raw', ?, 'PM2.5raw')";
msg.payload = [v];
return msg;"""

nodes = [
    {"id": TAB, "type": "tab", "label": "Whole House Fan Smoke Pause", "disabled": False, "info": INFO},
    {"id": "whfsp_comment", "type": "comment", "z": TAB, "name": "Pause WHF on outdoor AQI spike, resume when clean (see tab info)",
     "info": "", "x": 330, "y": 40, "wires": []},
    {"id": "whfsp_raw_in", "type": "link in", "z": TAB, "name": "Garage raw AQI IN", "links": [WS_LINK_OUT],
     "x": 135, "y": 100, "wires": [["whfsp_src_raw", "whfsp_sql_build"]]},
    change("whfsp_src_raw", "raw", 330, 100, "whfsp_logic"),
    state_changed("whfsp_relays", "WHF relay changed", ["switch.whole_house_fan_relay_low", "switch.whole_house_fan_relay_high"],
                  150, 160, "whfsp_src_relay"),
    change("whfsp_src_relay", "relay", 390, 160, "whfsp_logic"),
    state_changed("whfsp_windows", "Open window count changed", ["sensor.window_status_open_windows"],
                  160, 220, "whfsp_src_windows", state_type="num"),
    change("whfsp_src_windows", "windows", 400, 220, "whfsp_logic"),
    {"id": "whfsp_tick", "type": "inject", "z": TAB, "name": "every 60 s",
     "props": [{"p": "payload"}], "repeat": "60", "crontab": "", "once": False, "onceDelay": 0.1,
     "topic": "", "payload": "", "payloadType": "date", "x": 140, "y": 280, "wires": [["whfsp_src_tick"]]},
    change("whfsp_src_tick", "tick", 380, 280, "whfsp_logic"),
    {"id": "whfsp_logic", "type": "function", "z": TAB, "name": "Smoke pause logic",
     "func": (HERE / "logic.js").read_text(), "outputs": 3, "timeout": 0, "noerr": 0,
     "initialize": "", "finalize": "", "libs": [], "x": 640, "y": 180,
     "wires": [["whfsp_ha"], ["whfsp_push"], ["whfsp_log"]],
     "outputLabels": ["HA switch call", "Pushover", "event log"]},
    {"id": "whfsp_ha", "type": "api-call-service", "z": TAB, "name": "WHF relays", "server": HA,
     "version": 7, "debugenabled": False, "action": "", "floorId": [], "areaId": [], "deviceId": [],
     "entityId": [], "labelId": [], "data": "", "dataType": "jsonata", "mergeContext": "",
     "mustacheAltTags": False, "outputProperties": [], "queue": "none", "blockInputOverrides": False,
     "domain": "", "service": "", "x": 880, "y": 140, "wires": [[]]},
    {"id": "whfsp_push", "type": "pushover api", "z": TAB, "keys": PUSHOVER_KEYS,
     "title": "Whole House Fan Smoke Pause", "name": "Smoke pause notice", "x": 910, "y": 180, "wires": []},
    {"id": "whfsp_log", "type": "debug", "z": TAB, "name": "WHF smoke pause event", "active": True,
     "tosidebar": True, "console": True, "tostatus": False, "complete": "payload", "targetType": "msg",
     "statusVal": "", "statusType": "auto", "x": 910, "y": 220, "wires": []},
    {"id": "whfsp_sql_build", "type": "function", "z": TAB, "name": "raw AQI INSERT", "func": SQL,
     "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
     "x": 340, "y": 360, "wires": [["whfsp_mysql"]]},
    {"id": "whfsp_mysql", "type": "mysql", "z": TAB, "mydb": MYSQL, "name": "hubitat_logging",
     "x": 560, "y": 360, "wires": [[]]},
]

tab = nodes[0]
print(json.dumps({"id": TAB, "label": tab["label"], "disabled": False, "info": INFO, "nodes": nodes[1:]}, indent=1))
