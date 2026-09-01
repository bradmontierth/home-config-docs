#!/usr/bin/env bash
# Host-side driver on the GX10: runs run_okay_computer_v2.sh in the NGC container
# with the memwatch fail-safe. --runtime nvidia must be explicit (systemd-run'd
# docker got no GPU on 2026-07-24).
set -uo pipefail
cd /home/pi/wake-train
NAME=wake-train-okay-computer-v2
note() { echo "$(date -Is) $*" | tee -a launch_okay_computer_v2.log; }
docker rm $NAME >/dev/null 2>&1 || true
note "starting $NAME"
bash memwatch.sh $NAME &
docker run --runtime nvidia --name $NAME \
  -v /home/pi/wake-train:/work nvcr.io/nvidia/pytorch:25.10-py3 \
  bash /work/run_okay_computer_v2.sh
rc=$?
wait
note "$NAME exited rc=$rc"
[ -f output/okay_computer_v2/okay_computer_v2/okay_computer_v2.onnx ] || note "WARNING: v2 produced no model"
