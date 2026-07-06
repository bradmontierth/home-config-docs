# GX10 Qwen TTS API

Current Qwen TTS service:

```text
GX10 host: http://192.168.10.187:8093
Beelink router: http://192.168.10.217:8891
Beelink admin UI: http://192.168.10.217:8890
```

## Recommended Consumption Path

Home Assistant, Node-RED, Wyoming/OpenAI clients, and other home services should call the Beelink router rather than Qwen directly:

```text
POST http://192.168.10.217:8891/v1/audio/speech
```

The router resolves the active default voice and forwards direct premium requests to Qwen on the GX10.

Example using the router default voice:

```bash
curl -X POST http://192.168.10.217:8891/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"input":"The garage door is open.","response_format":"wav"}' \
  --output /tmp/tts.wav
```

Example with an explicit voice:

```bash
curl -X POST http://192.168.10.217:8891/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"input":"The garage door is open.","voice":"michael-scott_calm","response_format":"wav"}' \
  --output /tmp/tts.wav
```

## Default Voice

The router owns the default voice so Node-RED flows do not need to change when voices change.

```bash
curl http://192.168.10.217:8891/internal/default-voice
```

Set the default voice:

```bash
curl -X POST http://192.168.10.217:8891/internal/default-voice \
  -H 'Content-Type: application/json' \
  -d '{"voice_id":"picard_calm"}'
```

The default is persisted in the router cache volume at `/cache/settings.json`.

Home Assistant has file-configured helpers for the next HA reload/restart:

```text
input_select.tts_voice
rest_command.set_tts_default_voice
automation: TTS: Set router default voice
```

Home Assistant was not restarted when these were added.

## Qwen Voice Registry

The Qwen service stores voice references under:

```text
/home/pi/gx10-qwen-tts/state/voices/<voice_id>/reference.wav
/home/pi/gx10-qwen-tts/state/voices/<voice_id>/reference.txt
```

List registered voices directly from Qwen:

```bash
curl http://192.168.10.187:8093/voices
```

Inspect one voice:

```bash
curl http://192.168.10.187:8093/voices/picard_calm
```

Register or overwrite a voice:

```bash
curl -X POST 'http://192.168.10.187:8093/voices/new_voice?overwrite=true' \
  -F ref_wav=@/path/to/reference.wav \
  -F ref_txt=@/path/to/reference.txt
```

Delete a voice:

```bash
curl -X DELETE http://192.168.10.187:8093/voices/new_voice
```

Do not delete the router default voice until the default has been changed.

## Direct Qwen Synthesis

Direct Qwen synthesis is useful for testing, but production callers should prefer the router.

```bash
curl -X POST http://192.168.10.187:8093/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"The garage door is open.","language":"English","voice":"picard_calm"}' \
  --output /tmp/qwen.wav
```

Supported optional generation fields:

```text
temperature
top_p
max_new_tokens
x_vector_only_mode
```

The current benchmark-winning default path used Qwen defaults and the `picard_calm` reference.

## Admin UI

Use the Beelink admin UI for normal voice registration:

```text
http://192.168.10.217:8890
```

The active UI is the Qwen voice enrollment/default voice workflow:

- Upload, record, or load a reference clip.
- Crop/trim/normalize if needed.
- Provide the exact transcript.
- Enroll the voice into Qwen.
- Choose the router default voice.

Legacy cache/queue/Jetson workflows are no longer part of the active Qwen direct-TTS path.
