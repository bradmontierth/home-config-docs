#!/usr/bin/env bash
# Collect the labeled wake corpus off the satellites and mirror it to the GX10.
#
# Every stage-1 trigger writes its 2.5s pre-roll to the satellite's own disk with
# the stage-2 verdict in the filename (verify-ok-* / verify-rej-*). That is a
# free, self-labeling training set — real room, real mic, real noise — but it
# lived in exactly one place: the mic box. pw-poller-pi has already been rebuilt
# once and took its history with it. This is the second and third copy.
#
# The Beelink is the hub because it is the only host that can reach everything:
# the family-room satellite is on VLAN40 and cannot open ssh to the GX10, and the
# GX10 has no keys to any satellite. So we pull to a local archive, then push.
# The local archive is NOT a scratch buffer — it is deliberately kept, so the
# corpus survives on three disks (satellite, Beelink, GX10).
#
# Clips are immutable once written, so --ignore-existing: after the first run
# this transfers only the day's new files and never re-checksums the archive.
# Never --delete: the satellites cap cmd-* clips and could someday cap verify-*,
# and a prune upstream must not erase history down here.
#
# Install (Beelink, user pi):
#   30 3 * * * /home/pi/home_config/voice-assistant/tools/sync_wake_clips.sh
set -uo pipefail

ARCHIVE="${WAKE_CORPUS_DIR:-/home/pi/wake-corpus}"
REMOTE="${WAKE_CORPUS_REMOTE:-dgx:/home/pi/wake-corpus}"
ORCH_DB="${ORCH_DB:-/home/pi/voice-pipeline/data/orchestrator.db}"
LOG="$ARCHIVE/sync.log"

# room -> ssh host. Room name is the archive subdirectory, so clips keep their
# provenance: every satellite names its files verify-ok-<timestamp>.wav and two
# mics in the same open room WILL collide on the same second.
ROOMS=(
  "kitchen:big-speaker-mini-pc"
  "familyroom:pw-poller-pi"
  "master:master-closet-assist"
)

SAT_DATA="voice-pipeline/data"

mkdir -p "$ARCHIVE" || exit 1
exec 9>"$ARCHIVE/.sync.lock"
flock -n 9 || { echo "$(date -Is) another sync still running, skipping" >>"$LOG"; exit 0; }

say() { echo "$(date -Is) $*" | tee -a "$LOG"; }

failures=0
say "sync start -> archive=$ARCHIVE remote=$REMOTE"

for entry in "${ROOMS[@]}"; do
  room="${entry%%:*}"
  host="${entry#*:}"
  dest="$ARCHIVE/$room"
  mkdir -p "$dest/clips"

  before=$(find "$dest/clips" -name '*.wav' | wc -l)

  # verify-* is the labeled wake corpus; mark-* is a hand-tagged miss (a false
  # negative someone tapped in on /review), which is the rarest and most
  # valuable label of all. cmd-* is command audio, rotates at 80, not wake data.
  if ! rsync -a --ignore-existing --timeout=120 \
       --include='verify-*.wav' --include='mark-*.wav' --include='near-*.wav' --exclude='*' \
       -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
       "$host:$SAT_DATA/clips/" "$dest/clips/" >>"$LOG" 2>&1; then
    say "  $room: CLIP PULL FAILED (host down?)"
    failures=$((failures + 1))
    continue
  fi

  # events.jsonl is append-only and carries the transcript + verdict + scores
  # behind each clip. It grows, so it is copied whole rather than --ignore-existing.
  if ! rsync -a --timeout=120 -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
       "$host:$SAT_DATA/events.jsonl" "$dest/events.jsonl" >>"$LOG" 2>&1; then
    say "  $room: events.jsonl pull failed"
    failures=$((failures + 1))
  fi

  after=$(find "$dest/clips" -name '*.wav' | wc -l)
  ok=$(find "$dest/clips" -name 'verify-ok-*.wav' | wc -l)
  rej=$(find "$dest/clips" -name 'verify-rej-*.wav' | wc -l)
  say "  $room: +$((after - before)) new, $after total ($ok ok / $rej rej)"
done

# The join key. stage1_score and wake_model live only in the orchestrator's turns
# table, not in the clip name or the satellite's events file, so without this the
# archive can tell you a clip was rejected but not what score or which model fired
# it. Copied through SQLite's backup API, not cp: the live DB is in WAL and a
# byte copy of a WAL database mid-write is a corrupt database.
if [ -r "$ORCH_DB" ]; then
  if python3 - "$ORCH_DB" "$ARCHIVE/orchestrator-snapshot.db" <<'PY' >>"$LOG" 2>&1
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
n = dst.execute("select count(*) from turns").fetchone()[0]
print(f"turns snapshot rows={n}")
PY
  then
    say "  turns snapshot: $(tail -1 "$LOG" | grep -o 'rows=[0-9]*' || echo 'ok')"
  else
    say "  turns snapshot FAILED"
    failures=$((failures + 1))
  fi
else
  say "  turns snapshot skipped: $ORCH_DB unreadable"
fi

# Second leg. rsync cannot go remote-to-remote, which is the other reason the
# archive is a real archive and not a pipe.
if rsync -a --ignore-existing --timeout=300 \
     -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
     --exclude='sync.log' --exclude='.sync.lock' --exclude='orchestrator-snapshot.db' \
     "$ARCHIVE/" "$REMOTE/" >>"$LOG" 2>&1; then
  # The snapshot is the one file that must overwrite, so it goes separately —
  # --ignore-existing would freeze it at whatever the first run captured.
  rsync -a --timeout=300 -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
    "$ARCHIVE/orchestrator-snapshot.db" "$REMOTE/" >>"$LOG" 2>&1 \
    || { say "  push: turns snapshot failed"; failures=$((failures + 1)); }
  say "pushed to $REMOTE"
else
  say "PUSH TO $REMOTE FAILED"
  failures=$((failures + 1))
fi

total=$(find "$ARCHIVE" -name 'verify-*.wav' | wc -l)
say "sync done: $total verify clips archived, $(du -sh "$ARCHIVE" | cut -f1) on disk, failures=$failures"
exit $((failures > 0))
