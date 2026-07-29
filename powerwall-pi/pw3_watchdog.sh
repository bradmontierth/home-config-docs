#!/usr/bin/env bash
set -euo pipefail

# Watchdog for the Powerwall AP link + the telemetry publisher.
#
# DESIGN NOTE (rewritten 2026-07-28 after this script caused an outage):
# the original version bounced the wifi whenever a single `ping -c 1 -W 2` to
# the gateway failed. The Powerwall gateway does not answer ICMP reliably, so
# the watchdog tore down a PERFECTLY HEALTHY link — the publisher had logged
# successful polls seconds earlier — and the gateway's AP then refused to
# re-admit the client (`CTRL-EVENT-ASSOC-REJECT status_code=16`) for the next
# ~10 minutes. Bouncing again every 60s only kept the AP locked out.
#
# So, three rules now:
#   1. A link that is DELIVERING is never touched, whatever ICMP says. Fresh
#      `ok ts=` lines in the publisher journal are proof the link works, and
#      they outrank every other signal.
#   2. Unreachability must be established by repeated probes over ~20s, and
#      over TCP/443 (what TEDAPI actually uses) as well as ICMP.
#   3. A bounce is rate-limited and parks the interface long enough for the
#      AP to clear its stale client entry before reassociating. Reconnecting
#      too fast is what triggers the status-16 lockout.

PW_WIFI_CONN='TeslaPW_CDRTMU'
PW_WIFI_IF='wlan0'
PW_GATEWAY_IP='192.168.91.1'
PW_GATEWAY_PORT=443
PUBLISHER_SERVICE='pw3-mqtt.service'
STALE_WINDOW='3 min'
RESTART_COOLDOWN_SEC=300
BOUNCE_COOLDOWN_SEC=600     # the AP needs minutes, not seconds, to recover
CONNECT_COOLDOWN_SEC=300    # ditto for plain re-association attempts
PARK_SEC=45                 # leave the radio off this long during a bounce
RESTART_STAMP='/tmp/pw3-watchdog-last-restart'
BOUNCE_STAMP='/tmp/pw3-watchdog-last-bounce'
CONNECT_STAMP='/tmp/pw3-watchdog-last-connect'

log() {
  logger -t pw3-watchdog "$*"
  echo "$*"
}

# true if $2 seconds have passed since the timestamp in file $1
cooldown_expired() {
  local file=$1 window=$2 now last=0
  now=$(date +%s)
  [[ -f "$file" ]] && last=$(cat "$file" 2>/dev/null || echo 0)
  (( now - last >= window ))
}

stamp() { date +%s > "$1"; }

# Fresh successful polls mean the wifi link, the gateway and TEDAPI are all
# working. This is the authoritative health signal.
publisher_fresh() {
  journalctl -u "$PUBLISHER_SERVICE" --since "-${STALE_WINDOW}" --no-pager \
    | grep -q 'ok ts='
}

# ICMP *or* a TCP connect to the TEDAPI port. The gateway drops pings under
# load but still serves HTTPS, so either one counts as alive.
gateway_alive() {
  local i
  for i in 1 2 3; do
    if ping -c 2 -W 2 "$PW_GATEWAY_IP" >/dev/null 2>&1; then return 0; fi
    if timeout 3 bash -c "echo > /dev/tcp/${PW_GATEWAY_IP}/${PW_GATEWAY_PORT}" 2>/dev/null; then
      return 0
    fi
    [[ $i -lt 3 ]] && sleep 4
  done
  return 1
}

wifi_connected() {
  local line state name
  line=$(nmcli -t -f DEVICE,STATE,CONNECTION dev | awk -F: -v d="$PW_WIFI_IF" '$1==d{print $0}')
  state=$(awk -F: '{print $2}' <<<"$line")
  name=$(awk -F: '{print $3}' <<<"$line")
  [[ "$state" == "connected" && "$name" == "$PW_WIFI_CONN" ]]
}

bounce_wifi() {
  if ! cooldown_expired "$BOUNCE_STAMP" "$BOUNCE_COOLDOWN_SEC"; then
    log "skip wifi bounce: cooldown active (AP needs time to re-admit us)"
    return 0
  fi
  log "bouncing ${PW_WIFI_CONN}: parking radio ${PARK_SEC}s so the AP clears our stale entry"
  stamp "$BOUNCE_STAMP"
  nmcli dev disconnect "$PW_WIFI_IF" >/dev/null 2>&1 || true
  sleep "$PARK_SEC"
  nmcli con up "$PW_WIFI_CONN" ifname "$PW_WIFI_IF" >/dev/null 2>&1 || true
  sleep 5
}

restart_publisher() {
  if ! cooldown_expired "$RESTART_STAMP" "$RESTART_COOLDOWN_SEC"; then
    log "skip restart: cooldown active"
    return 0
  fi
  log "restarting ${PUBLISHER_SERVICE}"
  stamp "$RESTART_STAMP"
  systemctl restart "$PUBLISHER_SERVICE"
}

# ---- rule 1: if telemetry is flowing, the link is good. Do nothing. --------
if publisher_fresh; then
  if ! systemctl is-active --quiet "$PUBLISHER_SERVICE"; then
    log "${PUBLISHER_SERVICE} not active despite fresh polls"
    restart_publisher
  fi
  exit 0
fi

# ---- no fresh telemetry: work out which layer is broken -------------------
if ! wifi_connected; then
  # Rate-limited on purpose. When the gateway is refusing us
  # (CTRL-EVENT-ASSOC-REJECT status_code=16) hammering `con up` every 60s
  # appears to hold the lockout open — Tesla's AP is slow to re-admit a
  # client that has been cycling. Try occasionally and let it settle.
  if cooldown_expired "$CONNECT_STAMP" "$CONNECT_COOLDOWN_SEC"; then
    log "wifi not associated with ${PW_WIFI_CONN}, connecting"
    stamp "$CONNECT_STAMP"
    nmcli con up "$PW_WIFI_CONN" ifname "$PW_WIFI_IF" >/dev/null 2>&1 || true
    sleep 5
  else
    log "wifi not associated; connect cooldown active, backing off"
  fi
elif ! gateway_alive; then
  log "gateway ${PW_GATEWAY_IP} unreachable on ICMP and tcp/${PW_GATEWAY_PORT} after 3 rounds"
  bounce_wifi
fi

if ! systemctl is-active --quiet "$PUBLISHER_SERVICE"; then
  log "${PUBLISHER_SERVICE} not active"
  restart_publisher
elif ! publisher_fresh; then
  log "no fresh poll in ${STALE_WINDOW}"
  restart_publisher
fi
