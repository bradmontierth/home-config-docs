#!/usr/bin/env bash
set -euo pipefail

# Detect a wedged ReSpeaker XVF3800 and say so out loud.
#
# WHY THIS EXISTS: the array has wedged three times in five days (2026-07-25,
# -28, -29), always around a power interruption, and the satellite fails
# SILENTLY when it does — the service stays "active", /health returns
# {"ok": true}, and nothing is logged. Both times it was found only because a
# human stood in the family room saying "okay computer" to a deaf mic.
#
# THE SIGNATURE: while the array is wedged, every capture read returns
# `Input/output error`, and the ALSA capture substream sits at
#   state: RUNNING     hw_ptr: 0     (never advancing)
# That is readable from /proc while the satellite still owns the device, so
# this check never has to stop the service or fight for the mic.
#
# NOTE: only a PHYSICAL unplug/replug clears the wedge. Verified 2026-07-29
# that ALL of these fail: deauthorize/authorize, usb driver unbind/rebind,
# snd_usb_audio reload, USBDEVFS_RESET, and even a full hour of pulled power.
# So this script alerts; it deliberately does not pretend it can self-heal.

CARD_NAME='Array'
MQTT_HOST='192.168.10.217'
MQTT_TOPIC='familyroom/satellite/mic_health'
SERVICE='voice-assistant.service'
VENV_PY='/home/pi/.venvs/pypowerwall/bin/python'
SAMPLE_GAP=3

log() { logger -t mic-health "$*"; echo "$*"; }

publish() {  # $1=ok(true|false) $2=reason
  [[ -x "$VENV_PY" ]] || return 0
  "$VENV_PY" - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys, socket
try:
    import paho.mqtt.publish as publish
except Exception:
    sys.exit(0)
ok, reason = sys.argv[1] == "true", sys.argv[2]
payload = json.dumps({"ok": ok, "reason": reason, "host": socket.gethostname()})
try:
    publish.single("familyroom/satellite/mic_health", payload,
                   hostname="192.168.10.217", port=1883, qos=1, retain=True)
except Exception:
    pass
PY
}

card=$(awk -v n="$CARD_NAME" '$0 ~ "\\["n" *\\]" {print $1; exit}' /proc/asound/cards 2>/dev/null || true)
if [[ -z "$card" ]]; then
  log "ALERT: ReSpeaker not present as an ALSA card — check USB enumeration"
  publish false "array_not_enumerated"
  exit 1
fi

status="/proc/asound/card${card}/pcm0c/sub0/status"
if [[ ! -r "$status" ]]; then
  log "ALERT: no capture substream status at $status"
  publish false "no_substream"
  exit 1
fi

if ! systemctl is-active --quiet "$SERVICE"; then
  log "ALERT: $SERVICE is not active"
  publish false "service_inactive"
  exit 1
fi

state=$(awk '/^state:/{print $2}' "$status")
if [[ "$state" == "closed" ]]; then
  # Satellite between captures; not conclusive either way. Stay quiet.
  exit 0
fi

p0=$(awk '/^hw_ptr/{print $3}' "$status")
sleep "$SAMPLE_GAP"
p1=$(awk '/^hw_ptr/{print $3}' "$status" 2>/dev/null || echo "$p0")

if [[ "$state" == "RUNNING" && "$p0" == "$p1" ]]; then
  log "ALERT: mic WEDGED — capture stream RUNNING but hw_ptr frozen at ${p0} over ${SAMPLE_GAP}s. PHYSICALLY UNPLUG AND REPLUG THE RESPEAKER; no software reset clears this."
  publish false "hw_ptr_frozen"
  exit 1
fi

publish true "streaming"
exit 0
