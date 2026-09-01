#!/usr/bin/env bash
# Runs inside the NGC pytorch container on the GX10 (mounted at /work).
# okay_computer v2 = v1 recipe + the real corpus. Logs to /work/train_okay_computer_v2.log.
# Same prep as run_okay_google.sh incl. the unified-memory OOM cap — do not launch uncapped.
set -euo pipefail
exec > >(tee -a /work/train_okay_computer_v2.log) 2>&1

echo "=== $(date) container prep ==="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq espeak-ng ffmpeg libportaudio2 > /dev/null
cat > /usr/lib/python3.12/sitecustomize.py <<'PYEOF'
try:
    import torch
    if torch.cuda.is_available():
        torch.cuda.set_per_process_memory_fraction(0.12)
except Exception:
    pass
PYEOF
python -c "import torch; assert torch.cuda.is_available(), 'CUDA torch missing before install'"
pip install --quiet "livekit-wakeword[train]"
pip install --quiet --no-deps torchaudio==2.9.0
python - <<'PY'
import torch, torchaudio
import torch_audiomentations
from livekit.wakeword.training import trainer
assert torch.cuda.is_available(), "pip replaced NGC torch with a CPU build; aborting"
print("torch", torch.__version__, "torchaudio", torchaudio.__version__, "cuda ok:", torch.cuda.get_device_name(0))
PY

cd /work
CFG=configs/okay_computer_v2.yaml
MODEL_DIR=/work/output/okay_computer_v2/okay_computer_v2
SETS=/work/real_sets/2026-09-01-labelled
V1=/work/output/okay_computer/okay_computer

echo "=== $(date) reuse v1 synthetic originals (skips ~2h of TTS) ==="
for split in positive_train positive_test negative_train negative_test background_train background_test; do
  mkdir -p "$MODEL_DIR/$split"
  if [ -d "$V1/$split" ] && [ -z "$(ls "$MODEL_DIR/$split" | head -1)" ]; then
    find "$V1/$split" -maxdepth 1 -regextype posix-extended -regex '.*/clip_[0-9]{6}\.wav' -exec cp -t "$MODEL_DIR/$split" {} +
  fi
  echo "  $split: $(ls "$MODEL_DIR/$split" | wc -l) synthetic originals"
done

echo "=== $(date) setup (shared datasets, idempotent) ==="
livekit-wakeword setup --config $CFG

echo "=== $(date) generate (tops up to n_samples) ==="
livekit-wakeword generate $CFG

echo "=== $(date) inject real corpus ==="
python /work/build_real_sets.py inject --sets $SETS --model-dir $MODEL_DIR \
  --dup-positive 10 --backgrounds-dir /work/data/backgrounds_real
for split in positive_train negative_train background_train; do
  echo "  $split now $(find "$MODEL_DIR/$split" -maxdepth 1 -regextype posix-extended -regex '.*/clip_[0-9]{6}\.wav' | wc -l) originals"
done

echo "=== $(date) augment + features ==="
livekit-wakeword augment $CFG

echo "=== $(date) train ==="
livekit-wakeword train $CFG

echo "=== $(date) export ==="
livekit-wakeword export $CFG

echo "=== $(date) synthetic eval (sanity only — the real eval runs on .251) ==="
livekit-wakeword eval $CFG || echo "eval failed (non-fatal)"

echo "=== $(date) ALL DONE ==="
ls -la "$MODEL_DIR"/*.onnx "$MODEL_DIR"/*.json 2>/dev/null || true
