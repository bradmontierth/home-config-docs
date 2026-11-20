"""Prepare a scoped Ecobee tab update from a saved Admin API GET (no deploy).

Usage: python3 prepare.py BACKUP_DIRECTORY
Produces *.after.json there; refuses unexpected or already-patched functions.
The before tab must still match GET /flow/:id immediately before deploying.
"""
import copy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
TAB = "9ab666e4123d2c15"


def replace_once(text, old, new):
    assert text.count(old) == 1, f"Expected exactly one anchor: {old!r}"
    return text.replace(old, new, 1)


def prepare(tab):
    tab = copy.deepcopy(tab)
    nodes = {n["id"]: n for n in tab["nodes"]}
    mega = nodes["11ea46a27b5ca06a"]
    code = mega["func"]
    code = replace_once(code, "// 2. Determine if the fan", '''// Voice comfort is a bounded offset, never the manual thermostat override.
// Expiry is checked on every evaluation as well as by the recovery timer.
let comfortHold = flow.get("mainComfortHold");
const comfortValid = comfortHold && [-1, 1].includes(comfortHold.delta) &&
  Number.isFinite(comfortHold.expiresAt) && comfortHold.expiresAt > Date.now() &&
  comfortHold.expiresAt <= Date.now() + 60 * 60 * 1000;
if (comfortHold && !comfortValid) {
  flow.set("mainComfortHold", null);
  comfortHold = null;
}
const comfortEligible = baseOutputMode === "home" && !gridOutage &&
  !global.get("thermostatoverride") &&
  ["heat", "cool"].includes(currentEcobeeState?.hvacHeatCool);
const comfortDelta = comfortValid && comfortEligible ? comfortHold.delta : 0;

// 2. Determine if the fan''')
    code = replace_once(code,
        "setPoint = isNightOrAway ? heatNightAwaySP : heatHomeSP;",
        '''setPoint = isNightOrAway ? heatNightAwaySP : heatHomeSP + comfortDelta;
    if (comfortDelta) caseHit = "voiceComfortHeat";''')
    code = replace_once(code, "      79,\n      77", "      79 + comfortDelta,\n      77 + comfortDelta")
    code = replace_once(code, "  sensorCooling: {", '''  comfortHold: {
    active: Boolean(comfortValid),
    requestedDelta: comfortValid ? comfortHold.delta : 0,
    appliedDelta: comfortDelta,
    expiresAt: comfortValid ? new Date(comfortHold.expiresAt).toISOString() : null,
    weatherStartAt: 79 + comfortDelta,
    weatherStopAt: 77 + comfortDelta
  },
  sensorCooling: {''')
    mega["func"] = code
    status = nodes["ec_status_builder"]
    status["func"] = replace_once(status["func"], "  updated_at: new Date().toISOString()", '''  comfort_hold_active: payload.comfortHold?.active ?? false,
  comfort_requested_delta: payload.comfortHold?.requestedDelta ?? 0,
  comfort_applied_delta: payload.comfortHold?.appliedDelta ?? 0,
  comfort_expires_at: payload.comfortHold?.expiresAt ?? null,
  weather_start_at: payload.comfortHold?.weatherStartAt ?? 79,
  weather_stop_at: payload.comfortHold?.weatherStopAt ?? 77,
  updated_at: new Date().toISOString()''')

    def add(id, type, name, x, y, wires, **rest):
        assert id not in nodes
        tab["nodes"].append(dict(id=id, type=type, z=TAB, name=name,
                                x=x, y=y, wires=wires, **rest))

    add("ec_comfort_hold", "function", "Main house 60 minute voice hold", 470, 1520,
        [["ec_grid_outage_current"]], func=(HERE / "hold.js").read_text(), outputs=1,
        timeout=0, noerr=0, initialize="", finalize="", libs=[])
    for i, kind in enumerate(("hot", "cold")):
        add("ec_comfort_" + kind, "mqtt in", "Main house too " + kind, 190, 1480 + i * 60,
            [["ec_comfort_hold"]], topic="voice/button/main_comfort_" + kind,
            qos="1", datatype="utf8", broker="82f540b7378c2e35", nl=False,
            rap=True, rh=0, inputs=0)
    add("ec_comfort_recover", "inject", "Recover comfort hold / discovery", 240, 1640,
        [["ec_comfort_hold", "ec_comfort_discovery"]],
        props=[{"p": "payload"}, {"p": "topic", "vt": "str"}],
        repeat="", crontab="", once=True, onceDelay=5,
        topic="main_comfort/recover", payload="", payloadType="date")
    discovery = '''const device = {identifiers: ["voice_buttons"], name: "Voice Buttons",
    manufacturer: "Node-RED", model: "voice assistant curated commands"};
return [["hot", "cold"].map(kind => ({
    topic: "homeassistant/button/voice_buttons/voice_main_comfort_" + kind + "/config",
    retain: true, qos: 0,
    payload: JSON.stringify({
        unique_id: "voice_main_comfort_" + kind,
        object_id: "voice_main_comfort_" + kind,
        name: "Main house too " + kind,
        command_topic: "voice/button/main_comfort_" + kind,
        payload_press: "PRESS",
        icon: kind === "hot" ? "mdi:thermometer-chevron-down" : "mdi:thermometer-chevron-up",
        device
    })
}))];'''
    add("ec_comfort_discovery", "function", "Main house comfort button discovery", 570, 1660,
        [["ec_status_config_mqtt"]], func=discovery, outputs=1, timeout=0,
        noerr=0, initialize="", finalize="", libs=[])
    return tab


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    before = json.loads((folder / (TAB + ".before.json")).read_text())
    after = prepare(before)
    (folder / (TAB + ".after.json")).write_text(json.dumps(after, indent=2) + "\n")
    print(f"Prepared {len(after['nodes']) - len(before['nodes'])} new nodes; no deploy.")
