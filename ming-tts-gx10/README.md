# Ming-3.0-tts on the GX10 — Listen long-form voice (since 2026-11-12)

Replaced Breeze TTS 2 for Home Agent Listen after the 2026-11-12 bake-off
(GX10 /home/pi/tts-bakeoff, samples on :8813/files/tts-bakeoff/). Breeze stays for
storybooks (needs reference + direction) and voice design; it is stopped otherwise.

Chain: gateway `/speak` → `ming-longform` :7861 (the SAME long-form code as Breeze,
/home/pi/breeze-tts/longform/app.py: Parakeet verify, deadline_s retries, per-voice
`tempo`) → `ming-tts` :7870 (serve_ming.py: batched Ming 0.5B decode, Breeze-compatible
POST /v1/audio/speech/batch). Measured with verify: batch 4 ≈ 10–12× real time,
one piece 1.6–3.8×. Ming GPU ≈ 3 GB (cap GPU_MEM_FRACTION 0.12).

GX10 layout: /home/pi/ming-tts/{serve_ming.py, code/ (pinned copy of
github.com/inclusionAI/Ming-3.0-tts incl. the bake-off loader patch), voices/}.
Weights: /data/hf/hub/models--inclusionAI--Ming-3.0-tts-0.5B. Image ming-tts:gb10
(Dockerfile.ming in /home/pi/tts-bakeoff). Voice = clip + txt + json; json `tempo`
0.92 for calm_explainer_f (Ming reads it ~10% faster than Breeze did; Brad's pick).

Run (both restart unless-stopped; breeze-longform must stay stopped — same port 7861):

    M=$(ls -d /data/hf/hub/models--inclusionAI--Ming-3.0-tts-0.5B/snapshots/*/ | head -1 | sed 's#/data/hf#/hf#;s#/$##')
    docker run -d --name ming-tts --restart unless-stopped --gpus all --network host \
      -e HF_HOME=/hf -e GPU_MEM_FRACTION=0.12 -e MING_MODEL=$M -v /data/hf:/hf:ro \
      -v /home/pi/ming-tts/code:/opt/ming/code:ro -v /home/pi/ming-tts:/opt/bake:ro \
      -v /home/pi/ming-tts/voices:/voices:ro -w /opt/bake ming-tts:gb10 \
      python -m uvicorn serve_ming:app --host 0.0.0.0 --port 7870
    docker run -d --name ming-longform --restart unless-stopped --network host \
      -e VOICES_DIR=/voices -e BREEZE_URL=http://127.0.0.1:7870 -e BACKEND_NAME=ming \
      -e PYTHONUNBUFFERED=1 -e GROUP_FACTOR_JSON='{"1": 0.8, "2": 0.5, "3": 0.5, "4": 0.5}' \
      -e GROUP_OVERHEAD_S=2 -v /home/pi/breeze-tts/longform:/srv:ro \
      -v /home/pi/ming-tts/voices:/voices:ro -w /srv breeze-tts-eval:gb10 \
      python -m uvicorn app:app --host 0.0.0.0 --port 7861

`/health` on :7861 reports backend + group_factor/overhead; the gateway uses them for
its play-through estimates and keys the audio cache `<voice>@ming`.
Rollback to Breeze: `docker stop ming-longform && docker start breeze-tts-eval breeze-longform`.
Known limit: Ming copies a reference clip's noise/room — only clean references
(the Qwen announcement clips were too noisy, 15–29 dB SNR).
