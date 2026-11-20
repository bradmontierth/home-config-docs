#!/bin/bash
# Pushover alert when Dad's N100 stops writing DeviceMonitoring rows
# (n100-metrics-mqtt.service -> Node-RED "Device Monitoring" tab -> MariaDB).
# Cron on the Beelink every 5 min. Alerts once on down, once on recovery.
set -u

HOST=${HOST:-root@100.88.96.43}
STALE_S=600
STATE=${STATE:-/home/pi/home_config/dad-n100-watch/state}
LOG=/home/pi/home_config/dad-n100-watch/watch.log
ENV=/home/pi/cecret_lake/pushover/.env

envval() { sed -n "s/^$1=//p" "$ENV" | tail -1 | tr -d '"'"'"'\r'; }

pushover() {
  local device; device=$(envval PUSHOVER_DEVICE)
  [ -n "$device" ] || echo "$(date -Is) WARN PUSHOVER_DEVICE unset, broadcasting" >> "$LOG"
  curl -s -m 20 https://api.pushover.net/1/messages.json \
    --form-string "token=$(envval PUSHOVER_API)" \
    --form-string "user=$(envval PUSHOVER_USER)" \
    ${device:+--form-string "device=$device"} \
    --form-string "title=$1" --form-string "message=$2" >/dev/null
}

age=$(timeout 30 tailscale ssh "$HOST" \
  'docker exec mariadb_container sh -c "mariadb -N -u\$MYSQL_USER -p\$MYSQL_PASSWORD hubitat_logging -e \"SELECT TIMESTAMPDIFF(SECOND, MAX(Created), NOW()) FROM DeviceMonitoring WHERE DeviceNM=\\\"n100Mini\\\" AND Created > NOW() - INTERVAL 1 DAY\""' 2>/dev/null | tr -d '[:space:]')

if [[ "$age" =~ ^[0-9]+$ ]] && [ "$age" -le "$STALE_S" ]; then
  status=ok; detail="last sample ${age}s ago"
elif [[ "$age" =~ ^[0-9]+$ ]]; then
  status=bad; detail="N100 reachable but no metrics for $((age / 60)) min (collector or Node-RED tab)"
elif [ "$age" = NULL ]; then
  status=bad; detail="N100 reachable but no metrics in the last day"
else
  status=bad; detail="N100 unreachable over Tailscale (off, hung, or network down)"
fi

{ read -r prev fails since < "$STATE"; } 2>/dev/null || { prev=ok; fails=0; since=0; }
now=$(date +%s)

if [ "$status" = ok ]; then
  if [ "$prev" = alerted ]; then
    pushover "Dad's N100 is back" "Metrics flowing again ($detail). Down about $(( (now - since) / 60 )) min. Check journalctl --list-boots on the N100 for a reboot."
    echo "$(date -Is) RECOVERED $detail" >> "$LOG"
  fi
  echo "ok 0 0" > "$STATE"
  exit 0
fi

fails=$((fails + 1))
[ "$since" -gt 0 ] || since=$now
echo "$(date -Is) BAD($fails) $detail" >> "$LOG"
# Two consecutive failures (~10 min) before alerting, to ride out Tailscale blips.
if [ "$prev" != alerted ] && [ "$fails" -ge 2 ]; then
  pushover "Dad's N100 is down" "$detail. Button tabs and Auto schedules in Node-RED are not running."
  echo "alerted $fails $since" > "$STATE"
else
  echo "$([ "$prev" = alerted ] && echo alerted || echo bad) $fails $since" > "$STATE"
fi
