#!/bin/sh
set -eu

BROKER="${BROKER:-192.168.10.217}"
PORT="${PORT:-1883}"
TOPIC="${TOPIC:-pi/metrics/master-closet-pi}"
DEVICE_ID="${DEVICE_ID:-Master Closet Pi}"
INTERVAL="${INTERVAL:-60}"
IFACE="${IFACE:-wlan0}"

prev_total=0
prev_idle=0
cpu_pct=0

sample_cpu_pct() {
  set -- $(head -n1 /proc/stat)
  user=$2
  nice=$3
  system=$4
  idle=$5
  iowait=$6
  irq=$7
  softirq=$8
  steal=${9:-0}
  total=$((user + nice + system + idle + iowait + irq + softirq + steal))
  idle_all=$((idle + iowait))

  if [ "$prev_total" -ne 0 ]; then
    delta_total=$((total - prev_total))
    delta_idle=$((idle_all - prev_idle))
    if [ "$delta_total" -gt 0 ]; then
      cpu_pct=$((100 * (delta_total - delta_idle) / delta_total))
    fi
  fi

  prev_total=$total
  prev_idle=$idle_all
}

while :; do
  sample_cpu_pct
  ts=$(date +%s)

  mem_total_kb=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
  mem_available_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
  swap_total_kb=$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)
  swap_free_kb=$(awk '/SwapFree:/ {print $2}' /proc/meminfo)
  mem_used_kb=$((mem_total_kb - mem_available_kb))
  swap_used_kb=$((swap_total_kb - swap_free_kb))
  mem_pct=$((100 * mem_used_kb / mem_total_kb))

  read -r load1 load5 load15 _rest < /proc/loadavg
  read -r uptime_raw _rest < /proc/uptime
  uptime_s=${uptime_raw%.*}

  if [ -r /sys/class/thermal/thermal_zone0/temp ]; then
    temp_millic=$(cat /sys/class/thermal/thermal_zone0/temp)
    temp_c="$(($temp_millic / 1000)).$(($temp_millic % 1000 / 100))"
  else
    temp_c="null"
  fi

  set -- $(df -Pk / | awk 'NR == 2 {gsub(/%/, "", $5); print $2, $3, $4, $5}')
  disk_total_root_kb=$1
  disk_used_root_kb=$2
  disk_avail_root_kb=$3
  disk_pct_root=$4

  if [ -r "/sys/class/net/$IFACE/statistics/rx_bytes" ]; then
    rx_bytes=$(cat "/sys/class/net/$IFACE/statistics/rx_bytes")
    tx_bytes=$(cat "/sys/class/net/$IFACE/statistics/tx_bytes")
  else
    rx_bytes=0
    tx_bytes=0
  fi

  printf '{"ts":%s,"device":"%s","iface":"%s","cpu_pct":%s,"mem_used_kb":%s,"mem_total_kb":%s,"mem_pct":%s,"swap_used_kb":%s,"swap_total_kb":%s,"load1":%s,"load5":%s,"load15":%s,"uptime_s":%s,"temp_c":%s,"disk_used_root_kb":%s,"disk_total_root_kb":%s,"disk_avail_root_kb":%s,"disk_pct_root":%s,"rx_bytes":%s,"tx_bytes":%s}\n' \
    "$ts" "$DEVICE_ID" "$IFACE" "$cpu_pct" \
    "$mem_used_kb" "$mem_total_kb" "$mem_pct" \
    "$swap_used_kb" "$swap_total_kb" \
    "$load1" "$load5" "$load15" "$uptime_s" "$temp_c" \
    "$disk_used_root_kb" "$disk_total_root_kb" "$disk_avail_root_kb" "$disk_pct_root" \
    "$rx_bytes" "$tx_bytes"

  sleep "$INTERVAL"
done | mosquitto_pub -h "$BROKER" -p "$PORT" -t "$TOPIC" -q 0 -l
