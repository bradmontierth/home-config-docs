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
#   4. (2026-07-30) After 45 min of continuous refusal with the AP visible,
#      escalate to a genuine power cycle of the wifi chip -- see
#      wifi_cold_reset(). Until now the only known cure was pulling the Pi's
#      mains power, which also wedges the ReSpeaker every time.

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
# Last resort: cold-cycle the wifi CHIP. See wifi_cold_reset() for why this is
# not the same thing as any of the resets we tried on 2026-07-29/30.
COLD_RESET_AFTER_SEC=2700     # 45 min continuously unassociated
COLD_RESET_COOLDOWN_SEC=5400  # and never more than once per 90 min
MMC_HOST='fe300000.mmcnr'     # SDIO host for the wifi chip ONLY (root is on USB)
WL_ON_CHIP='gpiochip1'        # raspberrypi-exp-gpio
WL_ON_LINE='WL_ON'
WL_OFF_SEC=10
# /run is tmpfs: cleared on every boot, root-owned. Both matter. A reboot or
# power cycle is the ONE thing known to clear the Tesla AP lockout, so the
# backoff MUST reset then — carrying a stale counter across a boot made the
# watchdog sit out the first 600s after the very power cycle that fixed it
# (BUG 2026-07-30, introduced by moving this off /tmp for ownership reasons;
# ownership was the real problem, persistence was never wanted).
STATE_DIR='/run/pw3-watchdog'
CONNECT_FAIL_FILE="${STATE_DIR}/connect-fails"
PARK_SEC=45                 # leave the radio off this long during a bounce
RESTART_STAMP="${STATE_DIR}/last-restart"
BOUNCE_STAMP="${STATE_DIR}/last-bounce"
CONNECT_STAMP="${STATE_DIR}/last-connect"
DOWN_SINCE_FILE="${STATE_DIR}/down-since"
COLD_RESET_STAMP="${STATE_DIR}/last-cold-reset"
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

# How long have we been continuously unassociated? 0 if we are up.
down_for() {
  local now last
  now=$(date +%s)
  if [[ ! -f "$DOWN_SINCE_FILE" ]]; then echo 0; return; fi
  last=$(cat "$DOWN_SINCE_FILE" 2>/dev/null || echo "$now")
  echo $(( now - last ))
}

# Cold-cycle the BCM4345 wifi chip by dropping its power-enable line.
#
# WHY (established 2026-07-30): the device tree has NO mmc-pwrseq node, and
# WL_ON (gpiochip1 line 1, raspberrypi-exp-gpio) has NO kernel consumer -- the
# VideoCore firmware raises it at boot and nothing in Linux ever lowers it.
# Compare BT_ON one line above, which shows consumer="shutdown" because the
# bluetooth driver really does own its enable line.
#
# So the chip stays powered through EVERYTHING we tried during the 10-hour
# lockout: rfkill, `nmcli radio wifi off`, `rmmod brcmfmac`, unbind/rebind of
# the SDIO host, and a warm reboot. Re-downloading firmware into a chip that
# never lost power re-initialises the MAC and the driver-visible state; it does
# not reset the PHY, PMU or RF calibration. Pulling the Pi's mains power did,
# which is why that -- and only that -- ever cleared the status_code=16 refusal.
#
# This routine is the software equivalent of that power pull. BT_ON is a
# separate line, so bluetooth is untouched, and no USB rail moves -- which
# matters because a real power cycle wedges the ReSpeaker every single time.
#
# SAFETY: this is a hard disconnect, and a hard disconnect is exactly what is
# suspected of triggering the lockout in the first place. It must therefore
# NEVER run on a healthy link. The caller gates it behind 45 minutes of
# continuous failure with the AP visible -- by then telemetry is already dead,
# so the attempt costs nothing and can only help. If the lockout turns out to
# be AP-side after all, this will simply not work, which is itself the answer.
wifi_cold_reset() {
  local why="${1:-escalation}" i
  log "COLD RESET (${why}): power-cycling the wifi chip via ${WL_ON_LINE}"
  nmcli dev disconnect "$PW_WIFI_IF" >/dev/null 2>&1 || true
  for i in brcmfmac_cyw brcmfmac brcmutil; do
    /usr/sbin/rmmod "$i" 2>/dev/null || true
  done
  echo "$MMC_HOST" > /sys/bus/platform/drivers/mmc-bcm2835/unbind 2>/dev/null || true

  # Two separate gpioset calls on purpose: gpioset only guarantees the output
  # value for as long as it is running, so "hold low" and "drive high" cannot
  # be one invocation. --hold-period keeps each value for the stated time.
  if ! gpioset -c "$WL_ON_CHIP" -C pw3-watchdog -p "${WL_OFF_SEC}s" "${WL_ON_LINE}=0"; then
    log "WARN: could not drive ${WL_ON_LINE} low; chip was NOT power-cycled"
  fi
  gpioset -c "$WL_ON_CHIP" -C pw3-watchdog -p 2s "${WL_ON_LINE}=1" || true

  echo "$MMC_HOST" > /sys/bus/platform/drivers/mmc-bcm2835/bind 2>/dev/null || true
  /usr/sbin/modprobe brcmfmac 2>/dev/null || true

  for i in $(seq 30); do
    [[ -d "/sys/class/net/${PW_WIFI_IF}" ]] && break
    sleep 1
  done
  if [[ ! -d "/sys/class/net/${PW_WIFI_IF}" ]]; then
    log "ALERT: ${PW_WIFI_IF} did NOT come back after the cold reset — wifi is down until this Pi reboots"
    return 1
  fi

  log "chip is back; reassociating"
  sleep 3
  # The old backoff counted attempts made by a chip that no longer exists.
  echo 0 > "$CONNECT_FAIL_FILE"
  rm -f "$CONNECT_STAMP"
  nmcli --wait 25 con up "$PW_WIFI_CONN" ifname "$PW_WIFI_IF" >/dev/null 2>&1 || true
  sleep 5
  if wifi_connected; then
    log "COLD RESET CURED THE LOCKOUT — associated immediately after ${WL_ON_LINE} cycle. The fault is client-side."
    rm -f "$DOWN_SINCE_FILE"
  else
    log "still refused after a genuine chip power cycle — the client-side theory is wrong, look AP-side"
  fi
}

# Manual trigger, for use during a confirmed lockout: pw3_watchdog.sh --cold-reset
if [[ "${1:-}" == "--cold-reset" ]]; then
  stamp "$COLD_RESET_STAMP"
  wifi_cold_reset "manual"
  exit $?
fi

# Track how long the link has been down; the escalation below keys off it.
if wifi_connected; then
  rm -f "$DOWN_SINCE_FILE"
elif [[ ! -f "$DOWN_SINCE_FILE" ]]; then
  stamp "$DOWN_SINCE_FILE"
fi

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

  # ---- escalation: nothing else has worked for 45 min, cold-cycle the chip --
  # Gated on the AP being VISIBLE. If the SSID is absent the gateway is simply
  # off the air and our radio is not the problem; power-cycling it would prove
  # nothing and cost us the association we are waiting to make.
  if ! wifi_connected; then
    down=$(down_for)
    if (( down >= COLD_RESET_AFTER_SEC )) \
       && cooldown_expired "$COLD_RESET_STAMP" "$COLD_RESET_COOLDOWN_SEC" \
       && [[ "$(ssid_state)" == "present" ]]; then
      stamp "$COLD_RESET_STAMP"
      wifi_cold_reset "down ${down}s, AP visible, all retries refused" || true
    fi
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
