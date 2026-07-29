#!/usr/bin/env bash
set -euo pipefail

PW_WIFI_CONN='TeslaPW_CDRTMU'
PW_WIFI_IF='wlan0'
PW_GATEWAY_IP='192.168.91.1'
PUBLISHER_SERVICE='pw3-mqtt.service'
STALE_WINDOW='3 min'
RESTART_COOLDOWN_SEC=300
STAMP_FILE='/tmp/pw3-watchdog-last-restart'

log() {
  logger -t pw3-watchdog "$*"
  echo "$*"
}

restart_publisher() {
  local now last=0
  now=$(date +%s)
  if [[ -f "$STAMP_FILE" ]]; then
    last=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  fi
  if (( now - last < RESTART_COOLDOWN_SEC )); then
    log "skip restart: cooldown active"
    return 0
  fi
  log "restarting ${PUBLISHER_SERVICE}"
  systemctl restart "$PUBLISHER_SERVICE"
  echo "$now" > "$STAMP_FILE"
}

conn_line=$(nmcli -t -f DEVICE,STATE,CONNECTION dev | awk -F: -v d="$PW_WIFI_IF" '$1==d{print $0}')
conn_state=$(awk -F: '{print $2}' <<<"$conn_line")
conn_name=$(awk -F: '{print $3}' <<<"$conn_line")

if [[ "$conn_state" != "connected" || "$conn_name" != "$PW_WIFI_CONN" ]]; then
  log "wifi not ready (${conn_state:-unknown}/${conn_name:-none}), reconnecting ${PW_WIFI_CONN}"
  nmcli con up "$PW_WIFI_CONN" ifname "$PW_WIFI_IF" >/dev/null || true
  sleep 2
fi

if ! ping -c 1 -W 2 "$PW_GATEWAY_IP" >/dev/null 2>&1; then
  log "gateway ${PW_GATEWAY_IP} unreachable, bouncing wifi"
  nmcli dev disconnect "$PW_WIFI_IF" >/dev/null 2>&1 || true
  sleep 1
  nmcli con up "$PW_WIFI_CONN" ifname "$PW_WIFI_IF" >/dev/null || true
  sleep 3
fi

if ! systemctl is-active --quiet "$PUBLISHER_SERVICE"; then
  log "${PUBLISHER_SERVICE} not active"
  restart_publisher
fi

if ! journalctl -u "$PUBLISHER_SERVICE" --since "-${STALE_WINDOW}" --no-pager | grep -q 'ok ts='; then
  log "no fresh poll in ${STALE_WINDOW}"
  restart_publisher
fi
