#!/usr/bin/env bash
# GPU screenshots on the GX10 (NVIDIA GB10) for WebGL/3D pages; same flags as tools/shot.py.
#   tools/gx10shot.sh <url> <local_out.png> [shot.py flags...]
# Runs tools/shot.py inside the shots-gpu:1 container (Playwright 1.58 + full Chromium,
# built from ~/shots/Dockerfile on the GX10) with SHOT_GPU=1, then copies the PNG back.
# localhost/127.0.0.1 URLs are rewritten to the beelink's LAN address.
# The two NVIDIA vendor files are mounted because the container toolkit ships the
# driver libraries but not the EGL/Vulkan ICD JSON, without which Chrome drops to software.
set -euo pipefail
url="$1"; out="$2"; shift 2
url="${url//127.0.0.1/192.168.10.217}"; url="${url//localhost/192.168.10.217}"
name="shot-$$-$RANDOM.png"
here="$(cd "$(dirname "$0")" && pwd)"
scp -q "$here/shot.py" dgx:shots/shot.py
args=""; for a in "$@"; do args+=" $(printf '%q' "$a")"; done
ssh -o BatchMode=yes dgx "docker run --rm --gpus all --network host --ipc=host --user 1000:1000 -e HOME=/tmp -e NVIDIA_DRIVER_CAPABILITIES=all -e SHOT_GPU=1 \
  -v /usr/share/glvnd/egl_vendor.d/10_nvidia.json:/usr/share/glvnd/egl_vendor.d/10_nvidia.json:ro \
  -v /usr/share/vulkan/icd.d/nvidia_icd.json:/etc/vulkan/icd.d/nvidia_icd.json:ro \
  -v \$HOME/shots:/shots shots-gpu:1 python3 /shots/shot.py $(printf '%q' "$url") /shots/out/$name$args"
scp -q "dgx:shots/out/$name" "$out"
ssh -o BatchMode=yes dgx "rm -f shots/out/$name" || true
