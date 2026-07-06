#!/usr/bin/env bash
set -u

LOG_DIR="${LOG_DIR:-/var/log/kitchen-display-health}"
RETENTION_DAYS="${RETENTION_DAYS:-21}"
HOST="$(hostname 2>/dev/null || echo display-pi)"
STAMP="$(date -Is)"
STAMP_FILE="$(date +%Y%m%dT%H%M%S%z)"
OUT="${LOG_DIR}/health-${STAMP_FILE}.log"
LATEST="${LOG_DIR}/latest-health.log"

mkdir -p "$LOG_DIR"

{
  echo "timestamp=${STAMP}"
  echo "host=${HOST}"

  echo
  echo "== uptime =="
  uptime || true

  echo
  echo "== memory =="
  free -h || true

  echo
  echo "== disk =="
  df -h / /tmp /var/log 2>/dev/null || df -h / || true

  echo
  echo "== temperature/throttle =="
  if command -v vcgencmd >/dev/null 2>&1; then
    vcgencmd measure_temp || true
    vcgencmd get_throttled || true
  else
    echo "vcgencmd not installed"
  fi

  echo
  echo "== failed systemd units =="
  systemctl --failed --no-pager || true

  echo
  echo "== network =="
  ip -brief addr || true
  iw dev wlan0 link 2>/dev/null || true

  echo
  echo "== chromium/top processes =="
  ps -eo pid,ppid,stat,pcpu,pmem,rss,vsz,etime,comm,args --sort=-pcpu \
    | awk 'NR == 1 || /chromium|chrome_crashpad|labwc|wayfire|wf-panel|node-red|uvicorn/' \
    | head -40 || true

  echo
  echo "== kernel warnings since previous sample window =="
  journalctl -k -p warning --since "3 minutes ago" --no-pager 2>/dev/null \
    | tail -80 || true

  echo
  echo "== recent relevant journal lines =="
  journalctl -b --since "10 minutes ago" --no-pager 2>/dev/null \
    | grep -Ei 'chrom|kiosk|wayland|labwc|gpu|drm|oom|killed process|thermal|thrott|under-?voltage|voltage|failed|snapclient' \
    | tail -120 || true
} >"${OUT}.tmp" 2>&1

mv "${OUT}.tmp" "$OUT"
cp "$OUT" "$LATEST"

find "$LOG_DIR" -type f -name 'health-*.log' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
