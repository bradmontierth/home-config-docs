#!/usr/bin/env bash
# Provision a voice satellite onto a freshly flashed host.
#
# Run FROM THE BEELINK. Pushes code, models and the service unit to an ssh
# alias, then stops and prints the ALSA device listings -- because the two
# values that cannot be guessed (MIC_DEVICE, PLAYBACK_DEVICE) are whatever
# that particular USB dongle enumerated as.
#
# Idempotent: safe to re-run. Never overwrites an existing .env.
#
#   ./provision-satellite.sh master-closet-assist master
#
# After it finishes: edit the .env on the target, then
#   ssh <alias> 'sudo systemctl enable --now voice-assistant'
#
# Written 2026-08-07 for the master closet build (the .24 Pi reflashed clean).
set -euo pipefail

ALIAS="${1:?usage: provision-satellite.sh <ssh-alias> <satellite-id>}"
SAT_ID="${2:?usage: provision-satellite.sh <ssh-alias> <satellite-id>}"

REPO=/home/pi/home_config/voice-assistant/satellite
BLOBS=/home/pi/backups/pw_pi-20260728/blobs
ORCH=http://192.168.10.217:8785

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# The models are NOT in git -- this backup is the only local source, and its
# okay_computer/okay_google/silero blobs are md5-identical to what the kitchen
# runs today (verified 2026-08-07). Fail loudly rather than build a satellite
# with no ears.
for f in okay_computer.onnx okay_google.onnx silero_vad.onnx; do
  [ -f "$BLOBS/$f" ] || { echo "FATAL: missing model $BLOBS/$f" >&2; exit 1; }
done

say "Checking $ALIAS is reachable"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$ALIAS" 'echo "  ok: $(hostname) $(uname -m)"'

say "Installing OS packages"
ssh "$ALIAS" 'sudo apt-get update -qq && \
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-dev alsa-utils >/dev/null && echo "  ok"'

say "Creating tree + venv"
ssh "$ALIAS" 'mkdir -p ~/voice-pipeline/data ~/wake-bench && \
  [ -d ~/voice-pipeline/.venv ] || python3 -m venv ~/voice-pipeline/.venv'
# Pinned to the versions the kitchen satellite runs (2026-08-07). assistant.py
# needs exactly these three; requests on the kitchen box belongs to wake-bench.
ssh "$ALIAS" '~/voice-pipeline/.venv/bin/pip install -q --upgrade pip && \
  ~/voice-pipeline/.venv/bin/pip install -q \
    "numpy==2.5.1" "onnxruntime==1.27.0" "livekit-wakeword==0.2.1" && echo "  ok"'

say "Copying code + chime sounds"
scp -q "$REPO/assistant.py" "$ALIAS:/home/pi/voice-pipeline/assistant.py"
scp -qr "$REPO/sounds" "$ALIAS:/home/pi/voice-pipeline/"

say "Copying wake + VAD models"
scp -q "$BLOBS/okay_computer.onnx" "$BLOBS/okay_google.onnx" "$ALIAS:/home/pi/wake-bench/"
scp -q "$BLOBS/silero_vad.onnx" "$ALIAS:/home/pi/voice-pipeline/"

say "Installing service unit (not enabled yet)"
scp -q "$REPO/voice-assistant.service" "$ALIAS:/tmp/voice-assistant.service"
ssh "$ALIAS" 'sudo mv /tmp/voice-assistant.service /etc/systemd/system/ && \
  sudo systemctl daemon-reload && echo "  ok"'

say "Seeding .env"
# HOP_MS=320 is the family-room "relaxed cycles" value, deliberately NOT the
# kitchen's 192 -- that runs on an x86 mini PC with real cooling. Revisit once
# the PoE hat's fan is on this board.
if ssh "$ALIAS" 'test -f ~/voice-pipeline/.env'; then
  echo "  .env already exists -- left untouched"
else
  ssh "$ALIAS" "cat > ~/voice-pipeline/.env <<'EOF'
SATELLITE_ID=$SAT_ID
ORCH_BASE=$ORCH
MIC_DEVICE=FILL_ME_IN
PLAYBACK_DEVICE=FILL_ME_IN
MODEL_PATHS=/home/pi/wake-bench/okay_computer.onnx,/home/pi/wake-bench/okay_google.onnx
SILERO_MODEL=/home/pi/voice-pipeline/silero_vad.onnx
SILERO_THRESHOLD=0.4
HOP_MS=320
ORT_THREADS=2
MODE=active
EOF"
  echo "  seeded with FILL_ME_IN device placeholders"
fi

say "ALSA capture devices (pick MIC_DEVICE)"
ssh "$ALIAS" 'arecord -L 2>/dev/null | grep -A1 "^plughw:" || arecord -l'

say "ALSA playback devices (pick PLAYBACK_DEVICE)"
ssh "$ALIAS" 'aplay -L 2>/dev/null | grep -A1 "^plughw:" || aplay -l'

cat <<EOF

== Done. Two steps left ==
  1. ssh $ALIAS 'nano ~/voice-pipeline/.env'     # replace both FILL_ME_IN
  2. ssh $ALIAS 'sudo systemctl enable --now voice-assistant'

Then verify (see master-closet-satellite-build.md §4):
  ssh $ALIAS 'arecord -D <mic> -f S16_LE -r 16000 -c1 -d3 /tmp/t.wav && ls -l /tmp/t.wav'
      -- a 44-byte file means the mic enumerated but never streamed
  ssh $ALIAS 'journalctl -u voice-assistant -f'
  docker logs --tail 20 voice-orchestrator   # expect: verify sat=$SAT_ID
EOF
