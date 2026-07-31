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
# Cold-cycling the wifi CHIP. DISABLED 2026-07-30: the escalation ran for real,
# and it does NOT cure the lockout -- see wifi_cold_reset(). Left in the script
# because the manual `--cold-reset` flag and the restore path are still useful,
# but nothing fires it automatically any more. Set to 1 only to re-run the
# experiment deliberately.
COLD_RESET_ENABLED=0
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

# Read WL_ON. gpioget prints e.g. "WL_ON"=active
wl_on_read() {
  if gpioget --as-is -c "$WL_ON_CHIP" "$WL_ON_LINE" 2>/dev/null | grep -q '=active'; then
    echo 1
  else
    echo 0
  fi
}

# Drive WL_ON to $1 (0|1), holding for $2 seconds, then VERIFY it took.
#
# BUG 2026-07-30, and it cost 85 minutes of telemetry: gpioset in libgpiod v2
# does NOT exit on its own. `--hold-period` is a MINIMUM, not a termination
# condition -- the process keeps holding the line until something kills it.
# Called unbounded, it hung with WL_ON LOW; systemd SIGTERMed the unit on
# TimeoutStartSec 300s later, and the wifi chip sat unpowered until a human
# noticed the solar data was stale. So: bound every call with `timeout`, and
# then read the line back, because "gpioset exited" says nothing about whether
# the value stuck. (It does survive the kill on this hardware -- verified --
# but that is exactly the kind of thing not to assume.)
wl_on_set() {
  local want=$1 hold=${2:-2} i
  for i in 1 2 3; do
    timeout "$(( hold + 3 ))" gpioset -c "$WL_ON_CHIP" -C pw3-watchdog \
      -p "${hold}s" "${WL_ON_LINE}=${want}" >/dev/null 2>&1 || true
    if [[ "$(wl_on_read)" == "$want" ]]; then return 0; fi
    log "WARN: ${WL_ON_LINE} did not read back as ${want} (attempt ${i})"
  done
  return 1
}

# Bring the wifi chip back from ANY state, including powered-off and driverless.
#
# This must never be gated on something that needs a working radio. A half-
# finished cold reset leaves no wlan0 at all, and the original escalation was
# gated on `ssid_state == present` -- which needs a radio to scan with. The box
# therefore could not dig itself out of a hole it had dug itself: it sat with
# the chip powered down, logging "wifi not associated" once a minute, unable to
# ever retry. Recovery has to be unconditional.
restore_radio() {
  local i
  if ! wl_on_set 1 2; then
    log "ALERT: cannot raise ${WL_ON_LINE} — the wifi chip will stay unpowered"
  fi
  if [[ ! -e "/sys/bus/platform/drivers/mmc-bcm2835/${MMC_HOST}" ]]; then
    echo "$MMC_HOST" > /sys/bus/platform/drivers/mmc-bcm2835/bind 2>/dev/null || true
    sleep 3
  fi
  /usr/sbin/modprobe brcmfmac 2>/dev/null || true
  for i in $(seq 20); do
    if [[ -d "/sys/class/net/${PW_WIFI_IF}" ]]; then
      log "${PW_WIFI_IF} restored"
      return 0
    fi
    sleep 1
  done
  log "ALERT: ${PW_WIFI_IF} still missing after a restore attempt — needs hands"
  return 1
}

# Cold-cycle the BCM4345 wifi chip by dropping its power-enable line.
#
# The mechanism is real: the device tree has NO mmc-pwrseq node, and WL_ON
# (gpiochip1 line 1, raspberrypi-exp-gpio) has NO kernel consumer -- the
# VideoCore firmware raises it at boot and nothing in Linux ever lowers it, so
# rfkill / `rmmod brcmfmac` / SDIO unbind-rebind / a warm reboot all leave the
# chip powered. This routine genuinely does power it down; that part works.
#
# BUT IT DOES NOT CURE THE LOCKOUT. Tested for real 2026-07-30: the chip sat
# unpowered for 85 minutes, came back with a fresh firmware download, and the
# AP still answered every association with status_code=16 -- while its beacon
# was arriving at SIGNAL 100. So the "client chip has latched bad state" theory
# is dead, and a mains power cycle of the whole Pi remains the only known cure.
# Whatever mains power changes, it is not the state of this chip.
#
# Left here and reachable via `--cold-reset` because it is a clean way to fully
# re-init the radio, but COLD_RESET_ENABLED=0 -- nothing fires it automatically.
# Do not re-enable it as a lockout remedy without new evidence.
#
# SAFETY: this is a hard disconnect, so it must never run on a healthy link.
wifi_cold_reset() {
  local why="${1:-escalation}" i
  log "COLD RESET (${why}): power-cycling the wifi chip via ${WL_ON_LINE}"
  nmcli dev disconnect "$PW_WIFI_IF" >/dev/null 2>&1 || true
  for i in brcmfmac_cyw brcmfmac brcmutil; do
    /usr/sbin/rmmod "$i" 2>/dev/null || true
  done
  echo "$MMC_HOST" > /sys/bus/platform/drivers/mmc-bcm2835/unbind 2>/dev/null || true

  # From here the chip is powered off and there is no wlan0. If we die in this
  # window the box is left with no radio at all, so make every exit path put it
  # back -- including systemd's SIGTERM, which is precisely what happened on
  # 2026-07-30 when gpioset hung and took the chip down with it for 85 minutes.
  trap 'restore_radio || true' EXIT
  trap 'restore_radio || true; exit 1' TERM INT

  wl_on_set 0 "$WL_OFF_SEC" || log "WARN: could not drive ${WL_ON_LINE} low; chip was NOT power-cycled"

  trap - EXIT TERM INT
  if ! restore_radio; then
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

# The radio can be missing entirely -- powered off and driverless -- most
# easily because a cold reset died halfway through. Recover UNCONDITIONALLY and
# before anything else: on 2026-07-30 this state persisted for 85 minutes
# because every recovery path was gated behind a check that needed a radio.
if [[ ! -d "/sys/class/net/${PW_WIFI_IF}" ]]; then
  log "${PW_WIFI_IF} is missing entirely — restoring the radio before anything else"
  restore_radio || true
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
  if (( COLD_RESET_ENABLED )) && ! wifi_connected; then
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
