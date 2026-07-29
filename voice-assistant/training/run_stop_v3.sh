#!/usr/bin/env bash
# Runs inside the NGC pytorch container on the GX10 (mounted at /work).
# Trains the "stop" alarm-dismiss model (v3 — real long unattended ring backgrounds); logs to /work/train_stop_v3.log.
# Identical prep to run_okay_google.sh incl. the unified-memory OOM cap —
# see run_training.sh for the history. Do not launch uncapped.
set -euo pipefail
exec > >(tee -a /work/train_stop_v3.log) 2>&1

echo "=== $(date) container prep ==="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq espeak-ng ffmpeg libportaudio2 > /dev/null

# GB10 unified memory: GPU allocations go through NVRM and are invisible to
# the kernel OOM killer's RSS accounting. Uncapped, training OOMed the whole
# box on 2026-07-05 (killed vllm, livelocked ssh, needed a power cycle).
# Cap torch's CUDA allocator for every python in this container: 0.12 of
# 121GB ≈ 14GB. Training dies with a CUDA OOM instead of taking the host.
cat > /usr/lib/python3.12/sitecustomize.py <<'PYEOF'
try:
    import torch
    if torch.cuda.is_available():
        torch.cuda.set_per_process_memory_fraction(0.12)
except Exception:
    pass
PYEOF

# NGC image ships CUDA-enabled torch for aarch64; PyPI would give us a CPU
# build, so make sure pip doesn't replace it while resolving [train] extras.
python -c "import torch; assert torch.cuda.is_available(), 'CUDA torch missing before install'"
pip install --quiet "livekit-wakeword[train]"
# The [train] resolver upgrades torchaudio to a PyPI build whose ABI doesn't
# match NGC's custom torch (undefined symbol on import). Pin the wheel that
# matches NGC torch's minor version; --no-deps so pip can't touch torch.
pip install --quiet --no-deps torchaudio==2.9.0
python - <<'PY'
import torch, torchaudio
import torch_audiomentations
from livekit.wakeword.training import trainer
assert torch.cuda.is_available(), "pip replaced NGC torch with a CPU build; aborting"
print("torch", torch.__version__, "torchaudio", torchaudio.__version__,
      "cuda ok:", torch.cuda.get_device_name(0))
PY

cd /work
echo "=== $(date) setup (shared datasets) ==="
livekit-wakeword setup --config configs/stop_v3.yaml

echo "=== $(date) stop ==="
livekit-wakeword run configs/stop_v3.yaml

echo "=== $(date) ALL DONE ==="
ls -la /work/output/stop_v3/
