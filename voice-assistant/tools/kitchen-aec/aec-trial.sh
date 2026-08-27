#!/bin/bash
# Post-chime first-word-loss trial (2026-08-26). Steps relax the XVF3800
# residual-echo suppressor one notch at a time; all VOLATILE until `save`
# (a power cycle of the array reverts). Usage:
#   ./aec-trial.sh show      current values
#   ./aec-trial.sh step1     PP_DTSENSITIVE 13   (best double-talk + extra near-end detector)
#   ./aec-trial.sh step2     + PP_GAMMA_ETAIL 0.5
#   ./aec-trial.sh step3     + PP_NLATTENONOFF 0
#   ./aec-trial.sh nuke      + PP_ECHOONOFF 0    (linear AEC only)
#   ./aec-trial.sh revert    factory: 0 / 1.0 / 1 / 1
#   ./aec-trial.sh results   command captures since the last step (what the pipeline heard)
#   ./aec-trial.sh save      SAVE_CONFIGURATION (persist to flash)
set -e
H=/home/pi/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/linux_x86_64
LOG=/home/pi/aec-trial.log
x() { (cd "$H" && sudo -n ./xvf_host "$@" 2>&1 | grep -av device_init | tr -cd '\11\12\15\40-\176'); }
show() { for p in PP_DTSENSITIVE PP_GAMMA_ETAIL PP_NLATTENONOFF PP_ECHOONOFF; do x $p; done; }
mark() { echo "$(date +%s) $(date '+%F %T') $1" >> "$LOG"; }
case "${1:-show}" in
  show)   show ;;
  step1)  x PP_DTSENSITIVE 13; mark step1; show ;;
  step2)  x PP_DTSENSITIVE 13; x PP_GAMMA_ETAIL 0.5; mark step2; show ;;
  step3)  x PP_DTSENSITIVE 13; x PP_GAMMA_ETAIL 0.5; x PP_NLATTENONOFF 0; mark step3; show ;;
  nuke)   x PP_DTSENSITIVE 13; x PP_GAMMA_ETAIL 0.5; x PP_NLATTENONOFF 0; x PP_ECHOONOFF 0; mark nuke; show ;;
  revert) x PP_DTSENSITIVE 0; x PP_GAMMA_ETAIL 1.0; x PP_NLATTENONOFF 1; x PP_ECHOONOFF 1; mark revert; show ;;
  save)   x SAVE_CONFIGURATION; mark save ;;
  results)
    python3 - "$LOG" <<'PY'
import json,sys,datetime
log=sys.argv[1]
try:
    last=open(log).read().strip().splitlines()[-1].split()
    since=float(last[0]); label=last[-1]
except Exception:
    since=0; label="(no step yet)"
print(f"command captures since {label} @ {datetime.datetime.fromtimestamp(since):%H:%M:%S}:" if since else "all recent command captures:")
rows=[]
for line in open("/home/pi/voice-pipeline/data/events.jsonl"):
    try: e=json.loads(line)
    except Exception: continue
    if e.get("type")!="command": continue
    ts=datetime.datetime.fromisoformat(e["ts"]).timestamp()
    if ts<since: continue
    rows.append((e["ts"][11:19], e.get("transcript") or "", e.get("intent"), e.get("clip")))
for t,tr,it,c in rows[-20:]:
    print(f"  {t}  {tr!r:60}  -> {it}   {c}")
if not rows: print("  (none yet)")
PY
    ;;
  *) echo "usage: $0 show|step1|step2|step3|nuke|revert|results|save"; exit 1 ;;
esac
