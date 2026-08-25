# Voice PE firmware (Simon's and Claire's rooms)

This is an isolated build environment for the two Home Assistant Voice Preview
Editions: `simon-voice-pe.yaml` (Simon's room, 192.168.30.62, bridge port 8793)
and `claire-voice-pe.yaml` (Claire's room, 192.168.30.60, bridge port 8795,
added 2026-08-25). The two YAMLs differ only in `name`/`friendly_name`; keep
them that way. Everything below applies to both — substitute the YAML, the
device IP and the bridge service name (`claire-voice-bridge`, data dir
`bridge/data-claire`, its own `routed-audio-armed` marker). It never uses the production or legacy ESPHome
containers and never mounts either dashboard's build cache.

The image and upstream firmware are pinned:

- ESPHome builder: `2026.7.4`
- Voice PE base and `voice_kit`: `26.6.0`

The shared ESPHome secrets file is mounted read-only. The only shared-state
change required is the additional `voice_pe_api_key` entry; existing ESPHome
builds do not read it.

The stock auto-start-on-client automation is removed. Only the bridge's
`bridge_start_stream` API action can open the continuous microphone stream, so
Home Assistant reconnecting first cannot accidentally run a native Assist turn.

## Validate and compile

```bash
cd /home/pi/home_config/voice-assistant/voice-pe
docker compose run --rm esphome-voice-pe config simon-voice-pe.yaml
docker compose run --rm esphome-voice-pe compile simon-voice-pe.yaml
```

Build outputs remain under `config/.esphome/`, isolated from every other
device. Preserve both the OTA and factory images before the first flash.

## Upload

Only after bridge and rollback preparation:

```bash
docker compose run --rm esphome-voice-pe upload simon-voice-pe.yaml \
  --device 192.168.30.62
```

The first upload uses the factory firmware's passwordless native OTA service.
The custom image installs the normal shared ESPHome OTA password for later
uploads. Never send a `firmware.factory.bin` through OTA.

The first wireless upload was proven on 2026-08-10 from this disposable
container; it took 17 seconds. Claire's unit was flashed the same way on
2026-08-25 (13 s) while it still sat on the main VLAN at 192.168.10.61 — the
house firmware uses the IoT SSID, so a fresh unit hops to VLAN30 with a new
DHCP address on its first custom boot. Find it again by scanning VLAN30 for
port 6053 hosts that demand encryption and reading the name out of the noise
handshake (`InvalidEncryptionKeyAPIError.received_name`). Current recovery
artifacts and hashes are in `FIRMWARE-SHA256SUMS`; image copies live under
`/home/pi/backups/voice-pe/`.

## Bridge and safety hold

The separate `simon-voice-bridge` service owns the continuous ESPHome API
stream and runs the house `okay_computer` + `okay_google` ONNX models. It
starts in `shadow`, exposes health on Beelink port 8793, and cannot enter
`active` unless `bridge/data/routed-audio-armed` exists.

```bash
docker compose up -d --build simon-voice-bridge
curl -s http://127.0.0.1:8793/health | jq   # Simon
curl -s http://127.0.0.1:8795/health | jq   # Claire (8794 belongs to llm-benchmarks' report server)
```

Before routed-audio testing:

1. Confirm the Voice PE hardware mute switch is off and health shows non-zero
   `audio_rms`/`audio_peak_10s`.
2. Prove a spoken wake appears as `last_trigger` while the bridge remains in
   `shadow`; this produces no LED, chime, ASR, command, or Snapclient audio.
   For a silent stage-2 check, POST `{"mode":"probe"}` instead: it uses the
   orchestrator's `/verify/probe` route, which runs Parakeet but deliberately
   skips turn arbitration, dashboard events, amp wake, feedback, and commands.
3. Confirm the affected rooms are clear.
4. Create `bridge/data/routed-audio-armed`, then POST `{"mode":"active"}` to
   `http://127.0.0.1:8793/mode`.

Removing that marker and recreating the container returns it to the locked
shadow state. Quiet hours (20:00–07:00 America/Denver) and
`input_boolean.simonalarm` are independently rechecked by the bridge and the
orchestrator before any command ASR.

## Timer stop safety

The `stop.onnx` head is intentionally disabled
(`STOP_MODEL_ENABLED=false`, `STOP_DISMISS_ENABLED=false`). Two empty-room Simon timer-ring tests on
2026-08-10 showed that the marimba ring itself is adversarial to this model:
the peak scores were 0.925 and 0.904, with as many as three consecutive
windows above the 0.5 threshold. Requiring two hits or modestly raising the
threshold is therefore not a safe fix.

Until the stop model is retrained and revalidated with Simon-room ringing as
hard-negative audio, it must not be loaded or call the alarm stop endpoint.
Timer voice dismissal instead uses the Pi satellites' proven
alarm-only ASR path: overlapping 2.5-second Parakeet windows every second,
using the `kitchen-alarm` stop-heavy bias profile. It accepts stop, cancel,
dismiss, turn off, enough, quiet, the wake phrase, and guarded fuzzy variants.
The spoken announcement is flushed before the listener arms. A successful
dismissal plays the same local `dismiss.wav` confirmation as the Pi satellites;
it has no Snapclient routing path. The center button remains an independent
fallback.
