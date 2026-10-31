# Breeze TTS on the GX10 (backup copies)

Live files are on the GX10 (`ssh dgx`) under `/home/pi/breeze-tts/`, which is not a
git repo; these are copies for backup. Edit there, then copy here.

| File here | Live path | Container |
| --- | --- | --- |
| `serve_capped.py` | `/home/pi/breeze-tts/serve_capped.py` | `breeze-tts-eval` (:7860) |
| `longform/app.py` | `/home/pi/breeze-tts/longform/app.py` | `breeze-longform` (:7861) |

- `breeze-tts-eval`: the stock Breeze TTS 2 API (`breeze_infer.api`, upstream code in
  `/home/pi/breeze-tts/breeze-tts`, untouched) with a GPU memory cap, plus
  `POST /v1/audio/speech/batch`. That endpoint renders up to 4 texts together through the
  fast path's CUDA graphs: about 2x real time against 1.0x for one text. Extra graph
  buckets are captured after warmup, and Breeze then holds about 12 GB of GPU memory.
- `breeze-longform`: named voices (`/home/pi/breeze-tts/voices`), Parakeet-verified
  pieces, and `/v1/audio/speech/batch` for Home Agent Listen (gateway `speech.py`).

Neither container has a restart policy. After a GX10 reboot run
`docker start breeze-tts-eval breeze-longform`; until then Home Agent Listen uses Kokoro.
Graph warmup takes about 4 minutes. Never run a second Breeze copy next to these: a
side-by-side test dropped the GX10 to 11 GB available.

Container setup (image `breeze-tts-eval:gb10`, built from `/home/pi/breeze-tts/Dockerfile.gb10`):

```bash
docker run -d --name breeze-tts-eval --gpus all -p 7860:7860 \
  -e GPU_MEM_FRACTION=0.13 -e PYTHONPATH=/opt/breeze -e HF_HOME=/hf -e TORCHINDUCTOR_CACHE_DIR=/hf/inductor \
  -v /home/pi/breeze-tts/breeze-tts:/opt/breeze:ro -v /home/pi/breeze-tts/serve_capped.py:/opt/serve_capped.py:ro \
  -v /home/pi/breeze-tts/weights:/w:ro -v /home/pi/breeze-tts/hf:/hf \
  breeze-tts-eval:gb10 python /opt/serve_capped.py /w/breeze-tts-2 --host 0.0.0.0 --port 7860 \
  --fast-backbone-decode --fast-depth-decoder

docker run -d --name breeze-longform --network host -e VOICES_DIR=/voices \
  -v /home/pi/breeze-tts/voices:/voices:ro -v /home/pi/breeze-tts/longform:/srv:ro -w /srv \
  breeze-tts-eval:gb10 python -m uvicorn app:app --host 0.0.0.0 --port 7861
```
