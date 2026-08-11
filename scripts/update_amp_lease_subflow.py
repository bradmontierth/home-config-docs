#!/usr/bin/env python3
"""Transform a Node-RED v2 /flows export to use the amp lease manager.

Input is read from stdin and the transformed v2 document is written to stdout.
The API token is injected as a native HTTP Request node credential so Node-RED
stores it in its encrypted credentials file rather than in function source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


SUBFLOW_ID = "e711d48f74f78209"
PREPARE_ID = "10c0ca8be5f42ee8"
AFTER_UNGROUP_ID = "38c85e5e0aae3532"
MANAGER_HTTP_ID = "9763bd366b45322b"
FALLBACK_DELAY_ID = "20e1653894ffbf30"
MARK_GATE_ID = "3d59cab524fb93cd"
WAIT_ID = "ef360378f9b0bc66"

CHECK_ID = "4a7f000000000001"
FALLBACK_HA_ID = "4a7f000000000002"
MANAGER_CATCH_ID = "4a7f000000000003"
CATCH_PREP_ID = "4a7f000000000004"
FALLBACK_CATCH_ID = "4a7f000000000005"
FALLBACK_LOG_ID = "4a7f000000000006"


PREPARE_FUNCTION = r'''const messageText = msg.alexa;
const ttsEntity = msg.ttsEntity || "tts.openai";
const voice = msg.voice || "picard:calm";
const maApiUrl = msg.maApiUrl || "http://192.168.10.217:8095/api";
const defaultSpeakerVolume = Number(global.get("defaultSpeakerVolume"));
const volume = Number.isFinite(Number(msg.volume))
  ? Number(msg.volume)
  : Number.isFinite(defaultSpeakerVolume)
    ? defaultSpeakerVolume
    : null;

const MASTER_BEDROOM_PLAYER = "media_player.master_bedroom";
const MA_PLAYER_MAP = {
  "media_player.loft": "ma_loft",
  "media_player.claire_room": "ma_claire_room",
  "media_player.simon_room": "ma_simon_room",
  "media_player.master_bedroom": "ma_master_bedroom",
  "media_player.shower": "ma_shower",
  "media_player.squeezeplay_e4_5f_01_67_1e_56": "e4:5f:01:67:1e:56"
};

function maPlayerId(entityId) {
  return MA_PLAYER_MAP[String(entityId).toLowerCase()] || null;
}

let players = [];
if (Array.isArray(msg.players) && msg.players.length) {
  players = msg.players.slice();
} else if (Array.isArray(msg.speakers) && msg.speakers.length) {
  players = msg.speakers.slice();
} else {
  node.warn("No msg.players or msg.speakers provided; fail-closed with no announcement.");
  return null;
}

if (!messageText) {
  node.warn("No msg.alexa text found; nothing to send.");
  return null;
}

const disableBedroom = msg.forceBedroom === true ? false : (
  Boolean(global.get("DisableBedroomAnnouncements")) ||
  Boolean(global.get("adrienneWorkingDisableAnnounce")));

if (disableBedroom) {
  const target = MASTER_BEDROOM_PLAYER.toLowerCase();
  players = players.filter(p => String(p).toLowerCase() !== target);
}

const validPlayers = [];
for (const player of players) {
  const normalized = String(player).toLowerCase();
  if (!maPlayerId(normalized)) {
    node.warn(`Skipping non-amp or unknown player: ${player}`);
    continue;
  }
  if (!validPlayers.includes(normalized)) validPlayers.push(normalized);
}
players = validPlayers;

if (!players.length) {
  node.warn("No target amp players after filtering; skipping announcement.");
  return null;
}

for (const player of players) {
  const maPlayer = maPlayerId(player);
  const announceKey = `${Date.now()}-${Math.random().toString(16).slice(2)}-${maPlayer}`;

  const leaseRequest = {
    method: "POST",
    url: "http://192.168.10.217:8462/v1/touch",
    headers: { "Content-Type": "application/json" },
    announceKey,
    announcePlayer: player,
    maPlayer,
    announcementVolume: volume,
    payload: JSON.stringify({
      owner: "node-red",
      reason: `announcement:${maPlayer}`,
      wait_for_ready: true
    })
  };

  const ungroup = {
    method: "POST",
    url: "http://192.168.10.217:8461/v1/isolate",
    headers: { "Content-Type": "application/json" },
    announceKey,
    announcePlayer: player,
    maPlayer,
    announcementVolume: volume,
    payload: JSON.stringify({ player_id: maPlayer }),
    wakeRequest: leaseRequest
  };

  const synth = RED.util.cloneMessage(msg);
  synth.announceKey = announceKey;
  synth.announcePlayer = player;
  synth.maPlayer = maPlayer;
  synth.announcementVolume = volume;
  synth.payload = {
    protocol: "http",
    method: "post",
    path: "tts_get_url",
    data: {
      engine_id: ttsEntity,
      message: messageText,
      cache: false,
      options: { voice }
    },
    responseType: "json"
  };

  node.send([ungroup, synth, null]);
}

return null;'''


AFTER_UNGROUP_FUNCTION = r'''const leaseRequest = msg.wakeRequest || null;
if (!leaseRequest) {
  node.error("Missing amp lease request after player isolation", msg);
  return [null, null];
}
leaseRequest.ungroupResponse = msg.payload;
return [leaseRequest, null];'''


CHECK_FUNCTION = r'''const status = Number(msg.statusCode || 0);
const ready = status >= 200 && status < 300
  && msg.payload?.ready === true
  && msg.payload?.relay_state === "on";

if (ready) return [msg, null];

msg.ampLeaseFailure = {
  statusCode: msg.statusCode || null,
  response: msg.payload || null
};
node.warn(`Amp lease manager did not confirm readiness for ${msg.maPlayer || "unknown"}; using fixed-R3 electrical fallback`);
msg.payload = {};
return [null, msg];'''


CATCH_PREP_FUNCTION = r'''node.warn(`Amp lease manager request failed for ${msg.maPlayer || "unknown"}; using fixed-R3 electrical fallback: ${msg.error?.message || "unknown error"}`);
msg.ampLeaseFailure = msg.error || { message: "request failed" };
msg.payload = {};
return msg;'''


FALLBACK_LOG_FUNCTION = r'''node.error(`CRITICAL: fixed-R3 amp fallback failed; suppressing announcement for ${msg.maPlayer || "unknown"}: ${msg.error?.message || "unknown error"}`, msg);
return null;'''


def node_by_id(flows: list[dict], node_id: str) -> dict:
    for node in flows:
        if node.get("id") == node_id:
            return node
    raise RuntimeError(f"Required Node-RED node {node_id} was not found")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: update_amp_lease_subflow.py API_TOKEN_FILE")
    api_token = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    if not api_token:
        raise RuntimeError("Amp lease API token is empty")

    document = json.load(sys.stdin)
    flows = document.get("flows")
    if not isinstance(flows, list):
        raise RuntimeError("Expected a Node-RED v2 /flows document")

    subflow = node_by_id(flows, SUBFLOW_ID)
    if subflow.get("type") != "subflow" or subflow.get("name") != "Amp Speakers":
        raise RuntimeError("Amp Speakers subflow identity check failed")

    prepare = node_by_id(flows, PREPARE_ID)
    prepare.update(
        name="Prepare isolate + lease + TTS",
        func=PREPARE_FUNCTION,
        outputs=3,
        wires=[["2dbb90efc6d115ea"], ["8983c87d7af1ccf5"], []],
    )

    after_ungroup = node_by_id(flows, AFTER_UNGROUP_ID)
    after_ungroup.update(
        name="After isolate: acquire amp readiness",
        func=AFTER_UNGROUP_FUNCTION,
        outputs=2,
        wires=[[MANAGER_HTTP_ID], []],
    )

    manager_http = node_by_id(flows, MANAGER_HTTP_ID)
    manager_http.update(
        name="Amp manager: touch + wait ready",
        method="use",
        ret="obj",
        paytoqs="ignore",
        url="",
        tls="",
        persist=False,
        proxy="",
        insecureHTTPParser=False,
        authType="bearer",
        senderr=True,
        headers=[],
        wires=[[CHECK_ID]],
        credentials={"user": "", "password": api_token},
    )

    fallback_delay = node_by_id(flows, FALLBACK_DELAY_ID)
    fallback_delay.update(
        name="Fallback: wait 5s after R3 on",
        pauseType="delay",
        timeout="5",
        timeoutUnits="seconds",
        wires=[[MARK_GATE_ID]],
    )

    mark_gate = node_by_id(flows, MARK_GATE_ID)
    mark_gate.update(name="Mark amp ready")

    new_nodes = {
        CHECK_ID: {
            "id": CHECK_ID,
            "type": "function",
            "z": SUBFLOW_ID,
            "name": "Check manager readiness",
            "func": CHECK_FUNCTION,
            "outputs": 2,
            "timeout": 0,
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 1170,
            "y": 80,
            "wires": [[MARK_GATE_ID], [FALLBACK_HA_ID]],
        },
        FALLBACK_HA_ID: {
            "id": FALLBACK_HA_ID,
            "type": "api-call-service",
            "z": SUBFLOW_ID,
            "name": "Fallback: turn on amp R3",
            "server": "23fd91e9137b71c5",
            "version": 7,
            "debugenabled": False,
            "action": "switch.turn_on",
            "floorId": [],
            "areaId": [],
            "deviceId": [],
            "entityId": ["switch.whole_home_audio_amp_trigger"],
            "labelId": [],
            "data": "",
            "dataType": "jsonata",
            "mergeContext": "",
            "mustacheAltTags": False,
            "outputProperties": [],
            "queue": "none",
            "blockInputOverrides": True,
            "domain": "switch",
            "service": "turn_on",
            "x": 1180,
            "y": 140,
            "wires": [[FALLBACK_DELAY_ID]],
        },
        MANAGER_CATCH_ID: {
            "id": MANAGER_CATCH_ID,
            "type": "catch",
            "z": SUBFLOW_ID,
            "name": "Catch manager request failure",
            "scope": [MANAGER_HTTP_ID],
            "uncaught": False,
            "x": 690,
            "y": 80,
            "wires": [[CATCH_PREP_ID]],
        },
        CATCH_PREP_ID: {
            "id": CATCH_PREP_ID,
            "type": "function",
            "z": SUBFLOW_ID,
            "name": "Prepare electrical fallback",
            "func": CATCH_PREP_FUNCTION,
            "outputs": 1,
            "timeout": 0,
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 940,
            "y": 140,
            "wires": [[FALLBACK_HA_ID]],
        },
        FALLBACK_CATCH_ID: {
            "id": FALLBACK_CATCH_ID,
            "type": "catch",
            "z": SUBFLOW_ID,
            "name": "Catch R3 fallback failure",
            "scope": [FALLBACK_HA_ID],
            "uncaught": False,
            "x": 930,
            "y": 200,
            "wires": [[FALLBACK_LOG_ID]],
        },
        FALLBACK_LOG_ID: {
            "id": FALLBACK_LOG_ID,
            "type": "function",
            "z": SUBFLOW_ID,
            "name": "Suppress if electrical wake fails",
            "func": FALLBACK_LOG_FUNCTION,
            "outputs": 0,
            "timeout": 0,
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 1210,
            "y": 200,
            "wires": [],
        },
    }

    existing = {node.get("id"): node for node in flows}
    for node_id, desired in new_nodes.items():
        if node_id in existing:
            existing[node_id].clear()
            existing[node_id].update(desired)
        else:
            flows.append(desired)

    json.dump(document, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
