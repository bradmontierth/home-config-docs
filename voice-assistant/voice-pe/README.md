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
house firmware uses the NoT SSID (VLAN30, no outbound internet — VLAN40 is the IoT one), so a fresh unit hops to VLAN30 with a new
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

## Observability, self-heal, and the wedge playbook (2026-09-04)

Simon's unit wedged its IP stack on 2026-09-04 (ring red-twinkle = firmware
"no HA connection"; answered ARP but dropped ping/TCP even from the UDM on
its own VLAN, at −30 dBm; drops had been escalating since 08-30). Nothing on
the device said why, so both yamls now carry:

- `sensor:` **Free heap, Largest free block, Loop time, Free PSRAM** (debug
  platform, 30 s), **WiFi signal** (30 s), **Uptime** (60 s); `text_sensor:`
  **Reset reason, BSSID, IP address**. **They do NOT reach HA**: HA 2025.11's
  ESPHome integration builds unique_ids from an object_id this firmware
  (API 1.14) no longer sends, so it keeps one sensor per type
  (`wifi_signal`, `reset_reason`) and drops the rest ("Platform esphome does
  not generate unique IDs") — explicit `id:`s did not help. Until HA is
  upgraded past ~2026.1 the **record is the device log**: `logger.logs
  sensor/text_sensor: DEBUG` makes the firmware print every state
  (`[S][sensor]: 'Free heap' >> 117416 B`, every 30 s) into the
  `<room>-voice-pe-logs` container. Healthy baseline 2026-09-04 after boot:
  free heap ~117 kB, largest block 106 kB, loop 17–18 ms, PSRAM 4.1 MB,
  Simon −30 dBm / Claire −47 dBm.
- **Never adopt a Voice PE into HA** (Claire was, briefly, 2026-09-04): a
  fresh HA entry registers an `assist_satellite` client, the device allows
  ONE voice-assistant client ("Multiple API Clients attempting to connect to
  Voice Assistant"), and the bridge silently loses the mic after the next
  reboot. Simon's old entry (`Home Assistant Voice 094708`) predates that
  entity and coexists; do not re-create it.
- `wifi: power_save_mode: none` (compiled default was LIGHT; Claire pinged
  2–186 ms). `api: reboot_timeout: 5min` (was 15) so a wedge that sheds every
  client self-reboots inside ~10 min instead of waiting for a power pull.
- Device-side logs are durable: `simon-voice-pe-logs` / `claire-voice-pe-logs`
  compose services (`esphome logs`, restart unless-stopped, docker json-file
  rotation 50 MB×5). `docker logs -t simon-voice-pe-logs` for host timestamps.
  The pre-09-04 ad-hoc streamer's output is archived at
  `/home/pi/backups/voice-pe/device-logs/`.
- **Watchdog:** `watchdog/voicepe_watchdog.py` runs every minute from the
  user timer `voicepe-watchdog.timer` (`watchdog/install.sh`, no sudo). A
  bridge that is unreachable / `audio_started=false` / audio stale >60 s for
  3 min gets one Pushover push with the TCP probe of the device and the HA
  heap/WiFi/uptime/reset-reason snapshot, then one "back" push on recovery.
  Covers simon, claire and the master Echo Dot bridge.

**When the ring goes red or the push arrives**, before pulling power:

```bash
/home/pi/home_config/voice-assistant/tools/voicepe_diag.sh simon   # or claire
```

It prints bridge health, Beelink↔device sockets (Send-Q = device not
ACKing), ping/TCP from the Beelink and from the UDM (ARP REACHABLE + no
ping/TCP there = the device, not the AP/DHCP/MAC), the latest sensor values
parsed from the device log (heap, largest block, loop time, WiFi, uptime),
the device log's last events + drops per day, and the last turns. Save that output with the incident; then power-cycle if
`reboot_timeout` has not already done it.

Flash procedure is unchanged (compile + upload above); record new image
hashes in `FIRMWARE-SHA256SUMS` and copy the images to
`/home/pi/backups/voice-pe/<device>-<date>/`.
