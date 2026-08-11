#!/usr/bin/env python3
"""Replace Voice Broadcast's obsolete audio prewake with an amp lease touch."""

from __future__ import annotations

import json
import sys
from pathlib import Path


TAB_ID = "e3a9d4391d545738"
PREPARE_ID = "vbwake0000000002"
HTTP_ID = "vbwake0000000003"
RESULT_ID = "vbwake0000000004"
COMMENT_ID = "vbwake0000000006"
CATCH_ID = "vbwake0000000009"
LOG_ID = "vbwake0000000010"
REMOVE_IDS = {"vbwake0000000005", "vbwake0000000007", "vbwake0000000008"}


PREPARE_FUNCTION = r'''let payload = msg.payload || {};
if (typeof payload === "string") {
  try { payload = JSON.parse(payload); }
  catch (error) { payload = {}; }
}

let rooms = payload.rooms;
if (rooms == null || rooms === "all") rooms = ["all"];
if (!Array.isArray(rooms)) rooms = [rooms];
const roomSummary = rooms.map(value => String(value)).join(",");

msg.method = "POST";
msg.url = "http://192.168.10.217:8462/v1/touch";
msg.headers = { "Content-Type": "application/json" };
msg.payload = JSON.stringify({
  owner: "node-red-voice-prewake",
  reason: `stage-2 wake:${roomSummary}`,
  wait_for_ready: true
});
return msg;'''


RESULT_FUNCTION = r'''const status = Number(msg.statusCode || 0);
if (status >= 200 && status < 300 && msg.payload?.ready === true) {
  node.status({ fill: "green", shape: "dot", text: "amp ready" });
  return null;
}
node.status({ fill: "yellow", shape: "ring", text: `manager ${status || "invalid"}` });
node.warn(`Voice prewake manager response was not ready: status=${status} payload=${JSON.stringify(msg.payload)}`);
return null;'''


LOG_FUNCTION = r'''node.status({ fill: "red", shape: "ring", text: "manager unavailable" });
node.warn(`Voice prewake manager request failed: ${msg.error?.message || "unknown error"}. The final Amp Speakers path retains fixed-R3 electrical fallback.`);
return null;'''


def find(nodes: list[dict], node_id: str) -> dict:
    for node in nodes:
        if node.get("id") == node_id:
            return node
    raise RuntimeError(f"Required Voice Broadcast node {node_id} was not found")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: update_voice_amp_prewake.py API_TOKEN_FILE")
    token = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("Amp lease API token is empty")
    flow = json.load(sys.stdin)
    if flow.get("id") != TAB_ID or flow.get("label") != "Voice Broadcast":
        raise RuntimeError("Voice Broadcast tab identity check failed")
    nodes = flow.get("nodes", [])

    prepare = find(nodes, PREPARE_ID)
    prepare.update(
        name="Pre-wake: acquire amp readiness",
        func=PREPARE_FUNCTION,
        outputs=1,
        wires=[[HTTP_ID]],
    )

    http_node = find(nodes, HTTP_ID)
    http_node.update(
        name="Amp manager: voice prewake",
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
        wires=[[RESULT_ID]],
        credentials={"user": "", "password": token},
    )

    result = find(nodes, RESULT_ID)
    result.update(
        name="Record prewake result",
        func=RESULT_FUNCTION,
        outputs=0,
        wires=[],
    )

    comment = find(nodes, COMMENT_ID)
    comment.update(
        name="Contract: voice/amp_wake -> electrical amp lease",
        info=(
            "Stage-2 voice wake touches the standalone amp lease manager. "
            "Audio tones cannot wake the amp while it is in trigger mode. "
            "The final Amp Speakers path has a fixed-R3 Home Assistant fallback."
        ),
    )

    flow["nodes"] = [node for node in nodes if node.get("id") not in REMOVE_IDS]
    by_id = {node.get("id"): node for node in flow["nodes"]}
    additions = [
        {
            "id": CATCH_ID,
            "type": "catch",
            "z": TAB_ID,
            "name": "Catch prewake manager failure",
            "scope": [HTTP_ID],
            "uncaught": False,
            "x": 700,
            "y": 420,
            "wires": [[LOG_ID]],
        },
        {
            "id": LOG_ID,
            "type": "function",
            "z": TAB_ID,
            "name": "Log prewake failure",
            "func": LOG_FUNCTION,
            "outputs": 0,
            "timeout": 0,
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 950,
            "y": 420,
            "wires": [],
        },
    ]
    for desired in additions:
        existing = by_id.get(desired["id"])
        if existing:
            existing.clear()
            existing.update(desired)
        else:
            flow["nodes"].append(desired)

    json.dump(flow, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
