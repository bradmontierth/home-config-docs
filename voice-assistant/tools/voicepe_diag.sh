#!/usr/bin/env bash
# voicepe_diag.sh [simon|claire] — one-shot Voice PE + bridge diagnosis, run on
# the Beelink. Prints the whole checklist that found the 2026-09-04 Simon wedge
# (device answered ARP, dropped ping/TCP even from the UDM, at -30 dBm):
#   1. bridge /health (mode, audio streaming, last stage-1 trigger)
#   2. Beelink<->device TCP sockets with unacked Send-Q (device not ACKing)
#   3. reachability from the Beelink (ping, TCP 6053)
#   4. reachability from the UDM on the device's own VLAN (neigh/ARP, ping,
#      conntrack UNREPLIED) — if ARP=REACHABLE but ping/TCP fail here, it is
#      the device's IP stack, not the AP/DHCP/MAC/firewall
#   5. HA sensors from the 2026-09-04 firmware (heap, largest block, loop
#      time, PSRAM, WiFi signal, BSSID, uptime, reset reason)
#   6. device-side log (the <room>-voice-pe-logs compose service): last
#      events + drop counts per day
#   7. orchestrator turns for the room in the last 24 h
set -u
ROOM="${1:-simon}"
case "$ROOM" in
  simon)  DEV=192.168.30.62; PORT=8793 ;;
  claire) DEV=192.168.30.60; PORT=8795 ;;
  *) echo "usage: $0 simon|claire"; exit 2 ;;
esac
HA=http://127.0.0.1:8123
TOKEN_FILE=/home/pi/cecret_lake/dashboard_webapp/ha_token
DB=/home/pi/voice-pipeline/data/orchestrator.db
LOGS_CTR="${ROOM}-voice-pe-logs"
hr(){ printf '\n== %s ==\n' "$*"; }

echo "voicepe_diag $ROOM ($DEV, bridge :$PORT) — $(date '+%Y-%m-%d %H:%M:%S %Z')"

hr "1. bridge health :$PORT"
curl -s -m 3 "http://127.0.0.1:$PORT/health" | python3 -c '
import sys,json
try: d=json.load(sys.stdin)
except Exception: print("  bridge not answering"); sys.exit()
for k in ("ok","mode","active_armed","audio_started","last_audio_age_s","audio_rms","turn_active","last_trigger"):
    print(f"  {k}: {d.get(k)}")
print("  stats:", d.get("stats"))'

hr "2. TCP sockets Beelink -> $DEV:6053 (Send-Q>0 = device not ACKing; SYN-SENT = unanswered connect)"
ss -tn | grep "$DEV" | sed 's/^/  /' || echo "  none"

hr "3. from the Beelink"
printf '  ping: '; ping -c 3 -W 1 "$DEV" 2>/dev/null | tail -1
if timeout 3 bash -c "cat < /dev/null > /dev/tcp/$DEV/6053" 2>/dev/null; then echo "  tcp 6053: open"; else echo "  tcp 6053: no SYN-ACK within 3 s"; fi

hr "4. from the UDM (same VLAN as the device)"
ssh -o ConnectTimeout=6 -o BatchMode=yes udmp "
  echo '  neigh:' \$(ip neigh | grep -w $DEV || echo 'no entry');
  printf '  ping: '; ping -c 3 -W 1 $DEV 2>/dev/null | tail -1;
  if timeout 3 bash -c 'cat < /dev/null > /dev/tcp/$DEV/6053' 2>/dev/null; then echo '  tcp 6053: open'; else echo '  tcp 6053: no SYN-ACK'; fi;
  echo '  conntrack:'; conntrack -L 2>/dev/null | grep -w $DEV | sed 's/^/    /' | head -12" 2>&1 || echo "  (ssh udmp failed)"

hr "5. HA sensors (${ROOM}_voice_pe_*)"
if [ -r "$TOKEN_FILE" ]; then
  curl -s -m 8 -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "$HA/api/states" | python3 -c '
import sys,json,datetime as dt
room=sys.argv[1]; TZ=dt.timezone(dt.timedelta(hours=-6))
rows=[s for s in json.load(sys.stdin) if f"{room}_voice_pe" in s["entity_id"]]
want=("free_heap","largest_free_block","loop_time","free_psram","wifi_signal","bssid","uptime","reset_reason","ip_address","mute")
for s in sorted(rows,key=lambda s:s["entity_id"]):
    e=s["entity_id"]
    if not any(w in e for w in want): continue
    t=dt.datetime.fromisoformat(s["last_updated"].replace("Z","+00:00")).astimezone(TZ).strftime("%m-%d %H:%M:%S")
    print(f"  {e:45s} {s['"'"'state'"'"']:>12s} {s['"'"'attributes'"'"'].get('"'"'unit_of_measurement'"'"','"'"''"'"'):5s} @ {t}")
if not rows: print("  no entities (device never flashed with the sensor firmware?)")' "$ROOM"
else echo "  no HA token readable"; fi

hr "6. device-side log ($LOGS_CTR)"
if docker ps --format '{{.Names}}' | grep -qx "$LOGS_CTR"; then
  echo "  latest sensor values from the device log (HA-independent; firmware logs every state):"
  docker logs --since 10m "$LOGS_CTR" 2>&1 | grep -E "\[S\]\[(sensor|text_sensor)\]: '|Sending state" \
    | sed -E "s/^\[([0-9:.]+)\].*\]: '([^']+)'( >> | : Sending state )(.*)$/\2 = \4  (\1 UTC)/; s/ with [0-9]+ decimals.*\(/  (/" \
    | tac | awk -F' = ' '!seen[$1]++' | tac | sed 's/^/    /'
  echo "  drops per day (unresponsive / unexpected disconnect):"
  docker logs --since 240h -t "$LOGS_CTR" 2>&1 | grep -E "unresponsive|unexpected disconnect" | cut -c1-10 | sort | uniq -c | sed 's/^/    /'
  echo "  last 15 non-sensor events:"
  docker logs --since 6h -t "$LOGS_CTR" 2>&1 | grep -v -E "\[D\]\[(sensor|light|voice_assistant|micro_wake|text_sensor)" | grep -E "\[(W|E)\]|INFO|WARNING|Accept|disconnect|wifi|Reset|Boot" | tail -15 | cut -c1-19,31- | sed 's/^/    /'
else echo "  container not running (docker compose up -d $LOGS_CTR in voice-assistant/voice-pe)"; fi

hr "7. orchestrator turns for sat=$ROOM, last 24 h"
python3 - "$ROOM" "$DB" <<'PY'
import sys,sqlite3,time,datetime as dt
room,db=sys.argv[1:3]; TZ=dt.timezone(dt.timedelta(hours=-6))
try:
    c=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    t0=time.time()-86400
    f,v,last=c.execute("select count(*),sum(verified=1),max(at) from turns where sat=? and kind='wake' and at>=?",(room,t0)).fetchone()
    print(f"  stage-1 fires {f}, verified {v or 0}, last row {dt.datetime.fromtimestamp(last,TZ).strftime('%m-%d %H:%M:%S') if last else 'none'}")
    for r in c.execute("select at,stage1_score,verified,substr(transcript,1,40) from turns where sat=? and kind='wake' order by at desc limit 5",(room,)):
        print("   ",dt.datetime.fromtimestamp(r[0],TZ).strftime('%m-%d %H:%M:%S'),r[1:])
except Exception as e: print("  db error:",e)
PY
echo
echo "Reading: ARP REACHABLE on the UDM + ping/TCP dead from the UDM = device IP stack wedged (lwIP pbuf/heap); look at Free heap / Largest free block trend in HA history before the wedge. Power cycle clears it; api.reboot_timeout 5min should self-heal first."
