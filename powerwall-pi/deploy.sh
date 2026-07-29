#!/usr/bin/env bash
# Rebuild pw_pi (tesla-pw-listener) onto a fresh Raspberry Pi OS image.
# Run from the Beelink. See README.md for prerequisites.
#
#   ./deploy.sh                 # target = ssh alias pw_pi
#   ./deploy.sh 192.168.40.99   # target = an explicit host, e.g. first boot on a different IP
set -euo pipefail

TARGET="${1:-pw_pi}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SECRETS=/home/pi/cecret_lake/powerwall_pi
BLOBS=/home/pi/backups/pw_pi-20260728/blobs

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---- preflight -------------------------------------------------------------
for f in "$SECRETS/pw3-mqtt.default" \
         "$SECRETS/TeslaPW_CDRTMU.nmconnection" \
         "$SECRETS/TeslaPW_PEVPJX.nmconnection" \
         "$BLOBS/okay_computer.onnx" "$BLOBS/okay_google.onnx" \
         "$BLOBS/stop.onnx" "$BLOBS/silero_vad.onnx" \
         "$REPO/voice-assistant/satellite/assistant.py"; do
  [[ -f "$f" ]] || { echo "MISSING required file: $f" >&2; exit 1; }
done
ssh -o ConnectTimeout=10 "$TARGET" true || { echo "cannot ssh to $TARGET" >&2; exit 1; }

say "Target: $TARGET  ($(ssh "$TARGET" 'hostname; . /etc/os-release; echo $PRETTY_NAME' | tr '\n' ' '))"

# ---- 1. packages -----------------------------------------------------------
say "1/7 packages"
ssh "$TARGET" 'sudo apt-get update -qq && sudo apt-get install -y -qq \
  python3-venv python3-pip alsa-utils curl >/dev/null && echo ok'

# ---- 2. Powerwall AP wifi (secrets) ---------------------------------------
# Without these the box cannot reach TEDAPI at all — the AP PSKs exist nowhere
# else. NetworkManager refuses to load a connection file that is not 0600 root.
say "2/7 Powerwall AP wifi profiles"
# A fresh Raspberry Pi OS image imaged without wifi leaves NetworkManager's
# radio soft-disabled ("nmcli radio" -> WIFI: disabled) even though rfkill is
# clear and the driver loaded — wlan0 then shows STATE "unavailable" and every
# "con up" fails. Enable it before touching the profiles.
ssh "$TARGET" 'sudo raspi-config nonint do_wifi_country US >/dev/null 2>&1 || true
               sudo rfkill unblock wifi 2>/dev/null || true
               # must be sudo: over a non-interactive ssh session polkit denies
               # the unprivileged call with "Not authorized to perform this operation"
               sudo nmcli radio wifi on
               for i in $(seq 10); do
                 [ "$(nmcli -t -f WIFI radio)" = "enabled" ] && break; sleep 1
               done
               nmcli radio'
for c in TeslaPW_CDRTMU TeslaPW_PEVPJX; do
  ssh "$TARGET" "sudo tee /etc/NetworkManager/system-connections/$c.nmconnection >/dev/null \
    && sudo chown root:root /etc/NetworkManager/system-connections/$c.nmconnection \
    && sudo chmod 600 /etc/NetworkManager/system-connections/$c.nmconnection" \
    < "$SECRETS/$c.nmconnection"
done
ssh "$TARGET" 'sudo nmcli connection reload && sudo nmcli con up TeslaPW_CDRTMU ifname wlan0 || true'

# ---- 3. Powerwall publisher ------------------------------------------------
say "3/7 pw3-mqtt publisher"
ssh "$TARGET" 'mkdir -p /home/pi/bin /home/pi/.venvs'
scp -q "$HERE/pw3_mqtt_publisher.py" "$HERE/pw3_watchdog.sh" "$TARGET":/home/pi/bin/
ssh "$TARGET" 'chmod +x /home/pi/bin/pw3_mqtt_publisher.py /home/pi/bin/pw3_watchdog.sh'
ssh "$TARGET" 'test -d /home/pi/.venvs/pypowerwall || python3 -m venv /home/pi/.venvs/pypowerwall'
ssh "$TARGET" '/home/pi/.venvs/pypowerwall/bin/pip install -q --upgrade pip && \
               /home/pi/.venvs/pypowerwall/bin/pip install -q -r /dev/stdin' \
  < "$HERE/requirements-pypowerwall.txt"
# gateway password — sensitive, goes straight to /etc/default with 0600
ssh "$TARGET" 'sudo tee /etc/default/pw3-mqtt >/dev/null && sudo chmod 600 /etc/default/pw3-mqtt' \
  < "$SECRETS/pw3-mqtt.default"

# ---- 4. satellite ----------------------------------------------------------
say "4/7 family-room voice satellite"
ssh "$TARGET" 'mkdir -p /home/pi/voice-pipeline/data/clips /home/pi/wake-bench'
scp -q "$REPO/voice-assistant/satellite/assistant.py" "$TARGET":/home/pi/voice-pipeline/
scp -qr "$REPO/voice-assistant/satellite/sounds" "$TARGET":/home/pi/voice-pipeline/
scp -q "$BLOBS/silero_vad.onnx" "$TARGET":/home/pi/voice-pipeline/
scp -q "$BLOBS/okay_computer.onnx" "$BLOBS/okay_google.onnx" "$BLOBS/stop.onnx" "$TARGET":/home/pi/wake-bench/
scp -q "$HERE/satellite/satellite.env" "$TARGET":/home/pi/voice-pipeline/.env
scp -q "$HERE/asoundrc" "$TARGET":/home/pi/.asoundrc
# canonical copy the unit restores from if ~/.asoundrc disappears again
scp -q "$HERE/asoundrc" "$TARGET":/home/pi/voice-pipeline/asoundrc.canonical
ssh "$TARGET" sync   # this file has been lost to unflushed writes before
ssh "$TARGET" 'test -d /home/pi/voice-pipeline/.venv || python3 -m venv /home/pi/voice-pipeline/.venv'
ssh "$TARGET" '/home/pi/voice-pipeline/.venv/bin/pip install -q --upgrade pip && \
               /home/pi/voice-pipeline/.venv/bin/pip install -q -r /dev/stdin' \
  < "$HERE/satellite/requirements-satellite.txt"

# ---- 5. systemd ------------------------------------------------------------
say "5/7 systemd units"
for u in pw3-mqtt.service pw3-watchdog.service pw3-watchdog.timer voice-assistant.service; do
  ssh "$TARGET" "sudo tee /etc/systemd/system/$u >/dev/null" < "$HERE/systemd/$u"
done
ssh "$TARGET" 'sudo systemctl daemon-reload && \
  sudo systemctl enable --now pw3-mqtt.service voice-assistant.service pw3-watchdog.timer'

# ---- 6. ssh access ---------------------------------------------------------
say "6/7 authorized_keys"
ssh "$TARGET" 'mkdir -p /home/pi/.ssh && chmod 700 /home/pi/.ssh && \
  cat >> /home/pi/.ssh/authorized_keys && chmod 600 /home/pi/.ssh/authorized_keys && \
  sort -u -o /home/pi/.ssh/authorized_keys /home/pi/.ssh/authorized_keys' \
  < "$HERE/authorized_keys.txt"

# ---- 7. verify -------------------------------------------------------------
say "7/7 verification"
sleep 5
ssh "$TARGET" '
  echo "--- wifi ---";       nmcli -t -f DEVICE,STATE,CONNECTION dev | grep -E "^(eth0|wlan0)"
  echo "--- gateway ---";    ping -c1 -W2 192.168.91.1 >/dev/null && echo "192.168.91.1 reachable" || echo "GATEWAY UNREACHABLE"
  # The satellite owns the mic exclusively once it is running, so stop it for
  # the duration of the check — otherwise arecord fails on a HEALTHY mic and
  # the result is a false "MIC DEAD". A hung arecord must also be killed by
  # exact PID: the device wedges by BLOCKING, not by returning.
  echo "--- mic ---";        sudo systemctl stop voice-assistant; sleep 1
                             rm -f /tmp/miccheck.wav
                             timeout 10 arecord -D respeaker_ch0 -d 2 -f S16_LE -r 16000 /tmp/miccheck.wav 2>/dev/null || true
                             p=$(pgrep -x arecord | head -1); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
                             s=$(stat -c%s /tmp/miccheck.wav 2>/dev/null || echo 0)
                             if [ "$s" -gt 1000 ]; then echo "capture ok ($s bytes)"
                             else echo "MIC WEDGED ($s bytes; 44 = header only, 0 = never opened)."
                                  echo "  XVF3800 runs its own DSP firmware — USB reset and unbind/rebind"
                                  echo "  do NOT clear this. PHYSICALLY unplug and replug the ReSpeaker."
                                  echo "  If a replug does not fix it, suspect the USB power budget"
                                  echo "  (SSD + array share ~1.2A) and try a powered hub."
                             fi
                             sudo systemctl start voice-assistant; sleep 3
  echo "--- services ---";   systemctl is-active pw3-mqtt voice-assistant; systemctl is-active pw3-watchdog.timer
  echo "--- satellite ---";  curl -s --max-time 5 localhost:8781/health || echo "no /health"
  echo "--- publisher ---";  journalctl -u pw3-mqtt --since -2min --no-pager | tail -3
'
cat <<'EOF'

Remaining manual checks:
  * mosquitto_sub -h 192.168.10.217 -t pw3/telemetry -C 1   (from the Beelink — telemetry arriving?)
  * HA: binary_sensor.powerwall_grid_outage and sensor.powerwall_backup_reserve not "unavailable"
  * Say "okay computer" from the family-room couch — chime should play on the KITCHEN speakers
  * http://192.168.40.244:8781/review  — wake review page for this box
EOF
