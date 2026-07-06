#!/usr/bin/env bash
set -u

LOG_DIR="${LOG_DIR:-/var/log/kitchen-display-health}"
RETENTION_DAYS="${RETENTION_DAYS:-21}"
URL="${KITCHEN_DISPLAY_CHECK_URL:-http://192.168.10.217:8123/photo-viewer/kitchen-display}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-25}"
HOST="$(hostname 2>/dev/null || echo display-pi)"
STAMP="$(date -Is)"
STAMP_FILE="$(date +%Y%m%dT%H%M%S%z)"
RUN_DIR="/tmp/kitchen-display-browser-check.$$"
OUT="${LOG_DIR}/browser-check-${STAMP_FILE}.log"
LATEST="${LOG_DIR}/latest-browser-check.log"

mkdir -p "$LOG_DIR" "$RUN_DIR"

cleanup() {
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT

{
  echo "timestamp=${STAMP}"
  echo "host=${HOST}"
  echo "url=${URL}"
  echo "timeout_seconds=${TIMEOUT_SECONDS}"

  echo
  echo "== http reachability =="
  if command -v curl >/dev/null 2>&1; then
    curl -k -L -sS -o /dev/null \
      -w 'http_code=%{http_code} time_total=%{time_total} size=%{size_download}\n' \
      --max-time 10 "$URL" || true
  else
    echo "curl not installed"
  fi

  echo
  echo "== bounded headless chromium screenshot =="
  if command -v chromium >/dev/null 2>&1; then
    SCREENSHOT="${RUN_DIR}/screenshot.png"
    CHROME_LOG="${RUN_DIR}/chromium.log"
    START="$(date +%s)"
    timeout --kill-after=5s "${TIMEOUT_SECONDS}s" \
      chromium \
        --headless=new \
        --disable-gpu \
        --no-sandbox \
        --hide-scrollbars \
        --window-size=1280,720 \
        --force-device-scale-factor=1 \
        --user-data-dir="${RUN_DIR}/profile" \
        --screenshot="$SCREENSHOT" \
        "$URL" >"$CHROME_LOG" 2>&1
    STATUS=$?
    END="$(date +%s)"
    echo "exit_status=${STATUS}"
    echo "duration_seconds=$((END - START))"
    if [ -f "$SCREENSHOT" ]; then
      echo "screenshot_bytes=$(stat -c%s "$SCREENSHOT" 2>/dev/null || echo 0)"
    else
      echo "screenshot_bytes=0"
    fi
    echo "-- chromium log tail --"
    tail -80 "$CHROME_LOG" 2>/dev/null || true
  else
    echo "chromium not installed"
  fi

  echo
  echo "== leftover headless chromium check =="
  ps -C chromium -o pid,ppid,stat,pcpu,pmem,etime,args --no-headers 2>/dev/null \
    | grep -E -- '--headless=new|--screenshot=' || true
} >"${OUT}.tmp" 2>&1

mv "${OUT}.tmp" "$OUT"
cp "$OUT" "$LATEST"

find "$LOG_DIR" -type f -name 'browser-check-*.log' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
