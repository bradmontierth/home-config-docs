#!/usr/bin/env bash
# Generate alarm-ring background clips for wake-word training augmentation.
# Reproduces the satellite's real ring acoustics: theme WAV + 2.0s gap
# (ALARM_GAP_S) looped, at varied gains/offsets, 30% with a cached TTS
# announcement overlaid (the first ring loop plays one).
set -euo pipefail
THEMES=/home/pi/home_config/voice-assistant/satellite/sounds/themes
ANNOUNCE=/home/pi/voice-pipeline/data/announce
OUT=/tmp/claude-1000/-home-pi-home-config/964d2d44-e8f9-4595-a04d-a32df72c152e/scratchpad/alarm_backgrounds
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

# Deterministic PRNG so reruns are reproducible
RANDOM=42

# 60s ring beds per theme at 16k mono (theme + 2.0s silence, looped)
for t in "$THEMES"/*.wav; do
  name=$(basename "$t" .wav)
  ffmpeg -v error -y -i "$t" -ar 16000 -ac 1 "$WORK/${name}_16k.wav"
  ffmpeg -v error -y -f lavfi -i anullsrc=r=16000:cl=mono -t 2.0 "$WORK/silence.wav"
  : > "$WORK/${name}.txt"
  for i in $(seq 1 24); do
    printf "file '%s'\nfile '%s'\n" "$WORK/${name}_16k.wav" "$WORK/silence.wav" >> "$WORK/${name}.txt"
  done
  ffmpeg -v error -y -f concat -safe 0 -i "$WORK/${name}.txt" -t 60 "$WORK/bed_${name}.wav"
done

# Pre-resample a sample of announcements to 16k mono
mkdir -p "$WORK/ann"
ls "$ANNOUNCE"/*.wav | shuf -n 60 --random-source=<(yes 42) | while read -r a; do
  ffmpeg -v error -y -i "$a" -ar 16000 -ac 1 "$WORK/ann/$(basename "$a")" 2>/dev/null || true
done
mapfile -t ANNS < <(ls "$WORK"/ann/*.wav)

# 100 x 20s variants per theme: random offset, random gain, 30% + announcement
for bed in "$WORK"/bed_*.wav; do
  name=$(basename "$bed" .wav); name=${name#bed_}
  for i in $(seq -w 0 99); do
    off=$((RANDOM % 300))           # 0-30.0s start offset, 0.1s steps
    gain=$((30 + RANDOM % 71))      # 0.30-1.00
    if [ $((RANDOM % 10)) -lt 3 ] && [ ${#ANNS[@]} -gt 0 ]; then
      ann=${ANNS[$((RANDOM % ${#ANNS[@]}))]}
      again=$((50 + RANDOM % 51))   # 0.50-1.00
      ffmpeg -v error -y -ss $(printf '%d.%d' $((off/10)) $((off%10))) -i "$bed" -i "$ann" \
        -filter_complex "[0]volume=0.$gain[r];[1]volume=0.$again[a];[r][a]amix=inputs=2:duration=first:normalize=0" \
        -t 20 -ar 16000 -ac 1 -sample_fmt s16 "$OUT/${name}_${i}_ann.wav"
    else
      ffmpeg -v error -y -ss $(printf '%d.%d' $((off/10)) $((off%10))) -i "$bed" \
        -af "volume=0.$gain" -t 20 -ar 16000 -ac 1 -sample_fmt s16 "$OUT/${name}_${i}.wav"
    fi
  done
done
echo "generated: $(ls "$OUT" | wc -l) files, $(du -sh "$OUT" | cut -f1)"
