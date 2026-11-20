#!/bin/bash
# Scoped deploy of the 2026-09-30 solar-session fix to the Tesla tab only.
# Refuses if the live policy function drifted from policy-before.js.
set -euo pipefail
cd "$(dirname "$0")"
TAB=7fa25727b15db1f0 NODE=b0b4f535c19d2ed7 API=http://127.0.0.1:1880
BK=/home/pi/home_config/.local/tesla-tab-before-fix-$(date +%Y%m%d_%H%M%S).json
curl -fsS $API/flow/$TAB > "$BK"; chmod 600 "$BK"
jq -j --arg n $NODE '.nodes[]|select(.id==$n)|.func' "$BK" | cmp -s - policy-before.js || { echo "live drift; refusing"; exit 1; }
jq --arg n $NODE --rawfile f policy-after.js '(.nodes[]|select(.id==$n)|.func)=$f' "$BK" > /tmp/tesla-tab-candidate.json
curl -fsS -X PUT $API/flow/$TAB -H 'Content-Type: application/json' --data-binary @/tmp/tesla-tab-candidate.json; echo
curl -fsS $API/flow/$TAB | jq -j --arg n $NODE '.nodes[]|select(.id==$n)|.func' | cmp - policy-after.js && echo "deployed + verified; backup $BK"
