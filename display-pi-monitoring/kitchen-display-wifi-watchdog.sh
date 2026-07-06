#!/usr/bin/env bash
set -u

CONNECTION="${CONNECTION:-netplan-wlan0-Mirkwood}"
IFACE="${IFACE:-wlan0}"
TARGET_HOST="${TARGET_HOST:-192.168.10.217}"
STATE_DIR="${STATE_DIR:-/var/lib/kitchen-display-wifi-watchdog}"
LOG_DIR="${LOG_DIR:-/var/log/kitchen-display-health}"
MAX_FAILURES_BEFORE_NM_RESTART="${MAX_FAILURES_BEFORE_NM_RESTART:-5}"
FAIL_FILE="${STATE_DIR}/failures"
LOG_FILE="${LOG_DIR}/wifi-watchdog.log"

mkdir -p "$STATE_DIR" "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG_FILE"
}

get_failures() {
  if [ -f "$FAIL_FILE" ]; then
    read -r value <"$FAIL_FILE" || value=0
    case "$value" in
      ''|*[!0-9]*) echo 0 ;;
      *) echo "$value" ;;
    esac
  else
    echo 0
  fi
}

set_failures() {
  printf '%s\n' "$1" >"$FAIL_FILE"
}

link_is_connected() {
  nmcli -t -f GENERAL.STATE device show "$IFACE" 2>/dev/null \
    | grep -q '^GENERAL.STATE:100'
}

target_is_reachable() {
  ping -c 1 -W 2 "$TARGET_HOST" >/dev/null 2>&1
}

if link_is_connected && target_is_reachable; then
  if [ "$(get_failures)" != "0" ]; then
    log "recovered iface=${IFACE} target=${TARGET_HOST}"
  fi
  set_failures 0
  exit 0
fi

failures=$(( $(get_failures) + 1 ))
set_failures "$failures"
log "wifi unhealthy failures=${failures} iface=${IFACE} target=${TARGET_HOST}; attempting reconnect"

nmcli radio wifi on >>"$LOG_FILE" 2>&1 || true
nmcli device set "$IFACE" managed yes >>"$LOG_FILE" 2>&1 || true
nmcli connection up "$CONNECTION" ifname "$IFACE" --wait 20 >>"$LOG_FILE" 2>&1 || true

sleep 5

if link_is_connected && target_is_reachable; then
  log "reconnect succeeded iface=${IFACE} connection=${CONNECTION}"
  set_failures 0
  exit 0
fi

if [ "$failures" -ge "$MAX_FAILURES_BEFORE_NM_RESTART" ]; then
  log "still unhealthy after ${failures} checks; restarting NetworkManager"
  set_failures 0
  systemctl restart NetworkManager.service >>"$LOG_FILE" 2>&1 || true
fi
