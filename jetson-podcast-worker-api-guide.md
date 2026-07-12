# Jetson Podcast Worker API Guide

> **LEGACY NAME (2026-07): this worker now runs on the GX10, not the Jetson.**
> Everything below was ported; the API lives at `http://192.168.10.187:8090`
> (ssh alias `dgx`), served by the `gx10-parakeet-api` / `gx10-parakeet-worker-*`
> containers, with diarization on the resident `gx10-sortformer-worker`
> (Sortformer `diar_streaming_sortformer_4spk-v2.1` on :8092 internal, not
> pyannote). Script names (`jetson_*`), env vars (`JETSON_*`), and "Jetson"
> wording were kept to avoid churn. The physical Jetson (`jetson-tts`, .197)
> no longer runs podcast services. Where this guide says "Jetson", read GX10;
> where it says pyannote, read Sortformer.

The Jetson is reachable on the local network as:

```bash
ssh jetson-tts
```

Expected SSH config entry:

```sshconfig
Host jetson-tts
  HostName 192.168.10.197
  User pi
```

The REST worker listens on:

```text
http://jetson-tts:8090
http://192.168.10.197:8090
```

The worker has no authentication by design because it is for local LAN use. It does not expose arbitrary Docker commands. It exposes only known job types and known service actions.

## Device role

The Jetson is the shared GPU service host for the podcast pipeline and other local clients.

It owns:

- GPU job queue state
- GPU service start/stop/restart state
- Parakeet resident ASR service
- pyannote diarization job execution
- known TTS service controls

It does not own:

- ABS access
- podcast episode download
- ffmpeg normalization
- podcast queue markers
- RSS/feed publishing

Those stay in `/home/pi/podcast` on the local podcast host.

## Current steady state

The intended normal state is:

- `parakeet-resident`: running
- `kokoro-tts`: running
- `pyannote-smoke`: available for diarization jobs
- old Whisper/TTS experiment containers: stopped unless explicitly needed

Parakeet and Kokoro autostart through the worker. Diarization jobs stop conflicting GPU services when needed.

## Important Jetson paths

Worker deploy directory:

```text
/home/pi/podcast-jetson-worker
```

Worker state directory:

```text
/home/pi/podcast-jetson-worker-state
```

Worker log:

```text
/home/pi/podcast-jetson-worker-state/worker.log
```

Parakeet resident container workspace:

```text
/home/pi/podcast-jetson-worker-state/parakeet-resident
```

Parakeet phrase-bias state inside the container workspace:

```text
/workspace/context_biasing/bias.json
/workspace/context_biasing/bias.phrases.txt
```

## Service status

```bash
curl http://jetson-tts:8090/status
curl http://jetson-tts:8090/services
```

`/status` includes queue state and service health. `/services` lists known service containers.

## Parakeet resident ASR

Parakeet status:

```bash
curl http://jetson-tts:8090/parakeet/status
```

Direct transcription:

```bash
curl -X POST "http://jetson-tts:8090/parakeet/transcribe?chunk_seconds=120&context_seconds=2" \
  -H "Content-Type: audio/wav" \
  --data-binary @input.wav
```

Chunk-event streaming:

```bash
curl -N -X POST "http://jetson-tts:8090/parakeet/stream?chunk_seconds=15&context_seconds=1" \
  -H "Content-Type: audio/wav" \
  --data-binary @input.wav
```

The stream endpoint returns newline-delimited JSON events for an uploaded audio body. It is not a websocket/live microphone protocol.

## Parakeet phrase biasing

Use phrase biasing for domain vocabulary that Parakeet tends to misrecognize, such as `dbt` or `Tuva Project`.

Inspect active phrases:

```bash
curl http://jetson-tts:8090/parakeet/bias
```

Replace active phrases:

```bash
curl -X POST http://jetson-tts:8090/parakeet/bias \
  -H "Content-Type: application/json" \
  -d '{
    "phrases": ["dbt", "Tuva Project", "Apache Airflow", "Dagster"],
    "alpha": 1.0,
    "context_score": 1.0,
    "depth_scaling": 2.0,
    "use_triton": false,
    "strategy": "greedy_batch"
  }'
```

Clear active phrases:

```bash
curl -X DELETE http://jetson-tts:8090/parakeet/bias
```

Important phrase-bias behavior:

- `POST /parakeet/bias` replaces the whole phrase list.
- Clients that want to add one phrase should `GET`, merge locally, then `POST` the full desired list.
- The server dedupes phrases case-insensitively after trimming/collapsing whitespace.
- Keep `use_triton` set to `false` on this Jetson container. The current container does not have Triton installed, and NeMo boosting fails if Triton is enabled.
- Clients cannot provide arbitrary file paths. The worker stores the managed phrase file inside the resident Parakeet workspace.

## Batch job API

Create a Parakeet job:

```bash
curl -X POST http://jetson-tts:8090/jobs/parakeet \
  -H "Content-Type: application/json" \
  -d '{"params":{"chunk_seconds":120,"context_seconds":2}}'
```

Upload input:

```bash
curl -X PUT --data-binary @input.wav \
  http://jetson-tts:8090/jobs/<job_id>/inputs/input.wav
```

Submit job:

```bash
curl -X POST http://jetson-tts:8090/jobs/<job_id>/submit
```

Check job:

```bash
curl http://jetson-tts:8090/jobs/<job_id>
```

Fetch artifact:

```bash
curl http://jetson-tts:8090/jobs/<job_id>/artifacts/benchmark.json
```

The podcast pipeline normally uses this batch API. If resident Parakeet is already running and the requested model matches the configured model, the batch job uses the resident service rather than launching a one-off container.

## Diarization job API

Create a diarization job:

```bash
curl -X POST http://jetson-tts:8090/jobs/diarize \
  -H "Content-Type: application/json" \
  -d '{"params":{"device":"cuda"}}'
```

Required inputs:

- `input.wav`
- `input.json`

Optional input:

- `ad_intervals.json`

Artifacts:

- `output.diarized.json`
- `output.speakers.txt`
- `output.artifact.json`
- `run.log`

## Service control API

Parakeet:

```bash
curl -X POST http://jetson-tts:8090/services/parakeet/start
curl -X POST http://jetson-tts:8090/services/parakeet/stop
curl -X POST http://jetson-tts:8090/services/parakeet/restart
```

Kokoro:

```bash
curl -X POST http://jetson-tts:8090/services/kokoro/start
curl -X POST http://jetson-tts:8090/services/kokoro/stop
curl -X POST http://jetson-tts:8090/services/kokoro/restart
```

Qwen TTS:

```bash
curl -X POST http://jetson-tts:8090/services/qwen-tts/start
curl -X POST http://jetson-tts:8090/services/qwen-tts/stop
curl -X POST http://jetson-tts:8090/services/qwen-tts/restart
```

The Qwen endpoint exists, but the current Jetson setup needs a configured `qwen-tts` container or `JETSON_QWEN_TTS_START_CMD` before that service can actually start.

## Configuration source

The local podcast repo computes the Jetson API base in:

```text
/home/pi/podcast/bin/_env.sh
```

Important defaults:

- `JETSON_SSH_TARGET=pi@jetson-tts`
- `JETSON_API_BASE=http://jetson-tts:8090` after SSH host resolution
- `JETSON_PARAKEET_CONTAINER=parakeet-resident`
- `JETSON_PARAKEET_MODEL=nvidia/parakeet-unified-en-0.6b`
- `JETSON_PARAKEET_AUTOSTART=1`
- `JETSON_KOKORO_CONTAINER=kokoro-tts`
- `JETSON_KOKORO_AUTOSTART=1`
- `JETSON_PYANNOTE_CONTAINER=pyannote-smoke`

Deploy from the podcast repo:

```bash
cd /home/pi/podcast
./bin/deploy_jetson_worker.sh
```

## Mental model

Other apps should treat the Jetson worker as the LAN source of truth for GPU work:

- Post known jobs to `/jobs/...` when work should be queued and serialized.
- Use `/parakeet/transcribe` or `/parakeet/stream` for direct resident Parakeet calls.
- Use `/parakeet/bias` to manage shared vocabulary hints before transcription.
- Use `/services/...` only for the explicitly exposed services.
- Do not SSH into the Jetson for normal client workflows.
