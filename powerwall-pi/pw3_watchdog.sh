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
# Escalating backoff for re-association. PROVEN NECESSARY 2026-07-29: after a
# forced disconnect the Tesla AP refused this client for hours with
# CTRL-EVENT-ASSOC-REJECT status_code=16, and every retry appeared to refresh
# the penalty — a fixed 5-min retry could never outlast it. One hour of TRUE
# silence (box powered off) cleared it, and it associated on the first attempt.
# So: back off further the longer it keeps failing, up to an hour.
CONNECT_BACKOFF_SEC=(300 600 1200 2400 3600)
ABSENT_RETRY_SEC=60         # AP off the air: retry briskly, nothing to offend
REFUSAL_WINDOW='4 min'
STATE_DIR='/var/lib/pw3-watchdog'
CONNECT_FAIL_FILE="${STATE_DIR}/connect-fails"
PARK_SEC=45                 # leave the radio off this long during a bounce
RESTART_STAMP="${STATE_DIR}/last-restart"
BOUNCE_STAMP="${STATE_DIR}/last-bounce"
CONNECT_STAMP="${STATE_DIR}/last-connect"
mkdir -p "$STATE_DIR"

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

# Is the AP even on the air? This distinguishes the two failure modes, which
# need OPPOSITE responses (added 2026-07-30 after a gateway restart at 22:47
# turned into a 10-hour outage):
#   * SSID absent  -> the gateway is down/rebooting. There is nothing there to
#     offend, so retrying is free and we want to be back the instant it returns.
#   * SSID present but refusing us (CTRL-EVENT-ASSOC-REJECT status_code=16)
#     -> we are in the penalty box and every attempt feeds it. Back off hard.
# Returns: present | absent | unknown
#
# "unknown" matters. An EMPTY scan means the scan failed — radio down, rfkill,
# NM busy mid-activation — NOT that the AP is gone. Treating empty as "absent"
# is a bug that bit on 2026-07-30: with wlan0 administratively down the script
# called a plainly-present AP absent and picked the fast retry, which is the
# exact behaviour that feeds the lockout. Only trust "absent" when the scan
# genuinely returned other networks and ours was not among them.
ssid_state() {
  local out
  out=$(nmcli -t -f SSID dev wifi list --rescan yes 2>/dev/null | grep -v '^$' || true)
  if [[ -z "$out" ]]; then echo unknown; return; fi
  if grep -qxF "$PW_WIFI_CONN" <<<"$out"; then echo present; else echo absent; fi
}

# Did our last association attempt get actively refused, rather than finding
# nothing? status_code=16 is the AP saying no; ssid-not-found is it being absent.
recently_refused() {
  journalctl -u wpa_supplicant --since "-${REFUSAL_WINDOW}" --no-pager 2>/dev/null \
    | grep -q 'ASSOC-REJECT.*status_code=16'
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
  fails=0
  [[ -f "$CONNECT_FAIL_FILE" ]] && fails=$(cat "$CONNECT_FAIL_FILE" 2>/dev/null || echo 0)
  idx=$fails
  (( idx >= ${#CONNECT_BACKOFF_SEC[@]} )) && idx=$(( ${#CONNECT_BACKOFF_SEC[@]} - 1 ))
  wait_s=${CONNECT_BACKOFF_SEC[$idx]}
  why="refused"
  if [[ "$(ssid_state)" == "absent" ]] && ! recently_refused; then
    # Scan saw other networks but not ours: the gateway really is off the air,
    # so there is nothing to offend. Retry fast and do NOT escalate the penalty
    # backoff. Any other case (present, or scan unusable) stays conservative.
    wait_s=$ABSENT_RETRY_SEC
    why="ssid-absent"
    fails=0
  fi
  if cooldown_expired "$CONNECT_STAMP" "$wait_s"; then
    log "wifi not associated with ${PW_WIFI_CONN} [${why}], connecting (attempt $((fails+1)), waited ${wait_s}s)"
    stamp "$CONNECT_STAMP"
    # Count the attempt BEFORE making it. `nmcli con up` blocks until NM gives
    # up, and if systemd kills this oneshot first (BUG 2026-07-30) the
    # post-attempt write never happens — the counter stayed 0 for ten hours and
    # the backoff never escalated past its 300s floor. --wait bounds the call.
    echo $((fails+1)) > "$CONNECT_FAIL_FILE"
    nmcli --wait 25 con up "$PW_WIFI_CONN" ifname "$PW_WIFI_IF" >/dev/null 2>&1 || true
    sleep 5
    if wifi_connected; then
      log "associated; clearing backoff"
      echo 0 > "$CONNECT_FAIL_FILE"
    fi
  else
    log "wifi not associated [${why}]; backing off ${wait_s}s (consecutive failures: ${fails})"
  fi
elif ! gateway_alive; then
  log "gateway ${PW_GATEWAY_IP} unreachable on ICMP and tcp/${PW_GATEWAY_PORT} after 3 rounds"
  bounce_wifi
fi

if ! systemctl is-active --quiet "$PUBLISHER_SERVICE"; then
  log "${PUBLISHER_SERVICE} not active"
  restart_publisher
elif ! wifi_connected; then
  # Restarting the publisher cannot help while there is no link to the gateway,
  # and doing it every 5 min buried the journal: on 2026-07-30 the entries that
  # would have shown WHY the AP vanished had already rotated away.
  :
elif ! publisher_fresh; then
  log "no fresh poll in ${STALE_WINDOW}"
  restart_publisher
fi
