 GX10 Parakeet ASR Endpoints

Host from LAN: `http://192.168.10.187:8090`

Local host URL on GX10: `http://127.0.0.1:8090`

## Status

```http
GET /healthz
GET /status
GET /parakeet/status
```

`/status` returns queue state plus worker capacity. `/parakeet/status` also checks each resident model worker.

Current capacity policy:

- `max_total`: 3 resident Parakeet workers
- `podcast`: 1 active long job
- `interactive-batch`: 2 active uploaded file jobs
- `realtime`: 2 active WebSocket sessions

## Direct Batch Transcription

Use this for Windows batch mode when you already have the complete WAV file.

```http
POST /parakeet/transcribe?chunk_seconds=300&context_seconds=2
Content-Type: audio/wav

<raw WAV bytes>
```

Example:

```bash
curl -X POST \
  'http://192.168.10.187:8090/parakeet/transcribe?chunk_seconds=300&context_seconds=2' \
  -H 'Content-Type: audio/wav' \
  --data-binary @input.wav
```

Successful response is JSON with:
uccessful response is JSON with:

- `summary.model`
- `summary.audio_duration_seconds`
- `summary.transcribe_seconds`
- `summary.realtime_factor`
- `transcript_text`
- `segments[]` containing `start`, `end`, and `text`
- `chunks[]` with per-chunk timing/debug data

### Oversized Segment Repair

Clients do not need special handling for rare oversized Parakeet timestamp
segments. The GX10 worker inspects Parakeet output before returning it.

If a segment is longer than the worker threshold, currently `75s`, the worker:

1. crops the exact audio interval for that segment,
2. re-runs Parakeet on only that crop using repair chunks,
3. replaces the oversized segment with the repaired Parakeet-derived segments.

The worker does not synthesize proportional timestamps. If the repair pass still
returns oversized or unusable output, the request fails instead of returning
fake boundaries. Responses include repair counters in `summary`, including
`oversized_repair_count`, `oversized_repair_seconds`, and
`remaining_oversized_count`.

If the interactive batch lane is full, the API returns HTTP `429`:

```json
{
  "ok": false,
  "error": "capacity_exhausted",
  "detail": "lane interactive-batch is at capacity",
  "capacity": {}
}
```

## Streaming File Transcription

This endpoint exists for compatibility with the old Jetson API. It currently returns the same JSON shape as direct batch transcription.

```http
POST /parakeet/stream?chunk_seconds=300&context_seconds=2
Content-Type: audio/wav

<raw WAV bytes>
```

## Realtime WebSocket

Use this for Windows realtime mode.

```text
ws://192.168.10.187:8090/parakeet/realtime?sample_rate=16000&chunk_ms=2000&client=windows
```

Client sends binary frames containing mono PCM16 little-endian audio at the declared sample rate. Recommended frame size is about 2 seconds.

Server events are JSON text messages.

Session start:
Session start:

```json
{
  "event": "session.started",
  "session_id": "rt-...",
  "worker": "parakeet-0",
  "sample_rate": 16000,
  "chunk_ms": 2000,
  "client": "windows"
}
```

Per chunk transcript:

```json
{
  "event": "transcript.final",
  "session_id": "rt-...",
  "sequence": 1,
  "text": "recognized text",
  "segments": [],
  "is_final": true
}
```

Stop or flush by sending JSON text:

```json
{"type":"stop"}
```

The server then returns:

```json
{"event":"session.finished","session_id":"rt-..."}
```

Capacity failure returns a `session.error` event and closes the socket with code `1013`.

Current implementation note: realtime is low-latency micro-batch transcription per PCM16 chunk against a reserved resident worker. It does not yet emit native Parakeet partial/revision ev>

## Job API
## Job API

Use this for podcast-style long jobs and any client that wants durable artifacts.

Create job:

```http
POST /jobs/parakeet
Content-Type: application/json

{
  "client": {"name": "podcast", "external_id": "optional-id"},
  "params": {"chunk_seconds": 300, "context_seconds": 2}
}
```

Upload input:

```http
PUT /jobs/{job_id}/inputs/input.wav
Content-Type: audio/wav

<raw WAV bytes>
```

Submit:

```http
POST /jobs/{job_id}/submit
```

Poll:

```http
GET /jobs/{job_id}
GET /jobs
```

Artifacts after success:

```http
GET /jobs/{job_id}/artifacts/benchmark.json
GET /jobs/{job_id}/artifacts/benchmark.txt
GET /jobs/{job_id}/artifacts/run.log
```

## Phrase Biasing

These calls broadcast to all resident workers.

```http
GET /parakeet/bias
POST /parakeet/bias
DELETE /parakeet/bias
```

Example:

```bash
curl -X POST 'http://192.168.10.187:8090/parakeet/bias' \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true,"phrases":["dbt","Tuva Project"],"use_triton":false}'
```
