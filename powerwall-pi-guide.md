# Powerwall Pi (pw_pi) — Powerwall telemetry + family-room voice satellite

Written 2026-07-23, the day the family-room satellite went on this box.

## The box

| | |
|---|---|
| Hardware | Raspberry Pi 4 Model B, **1GB RAM**, 4 cores, 455GB SSD root (USB boot) |
| Hostname | `tesla-pw-listener` |
| OS | Raspberry Pi OS (Debian trixie, python 3.13, kernel 6.12) |
| LAN IP | `192.168.40.244` (eth0, VLAN 40) |
| SSH | `ssh pw_pi` from the Beelink (alias in `~/.ssh/config` → key `id_ed25519_pw_pi`, installed 2026-07-23; the older `id_rpi244` key also works) |
| Location | Family room, opposite side of the kitchen/family open space |

RAM is tight but fine: satellite ~91MB RSS + publisher ~54MB, ~480MB still
available. The full desktop stack (lightdm/labwc/pcmanfm/cups) is still
enabled and is the biggest discretionary RAM/user if we ever need headroom.
Thermals are excellent (idle ~40°C, satellite running ~45°C, `throttled=0x0`)
— unlike the old kitchen Pi that soft-limited at 81°C.

## Role 1: Powerwall 3 → MQTT (`pw3-mqtt.service`)

Polls the Powerwall 3 gateway's local **TEDAPI** and republishes a flattened
telemetry snapshot to the house MQTT broker.

- Service: `pw3-mqtt.service` → `/home/pi/bin/pw3_mqtt_publisher.py`
  (venv `~/.venvs/pypowerwall`, uses the `pypowerwall` library in TEDAPI mode
  + `paho-mqtt`).
- Config: `/etc/default/pw3-mqtt` (systemd `EnvironmentFile`). Holds
  `PW_GATEWAY_PASSWORD` (the Tesla gateway password) — **sensitive, don't
  copy it anywhere**; everything else defaults in the script.
- Poll cadence: every **15s** (`POLL_SECONDS`). Each poll opens a fresh
  `pypowerwall.Powerwall(host=192.168.91.1, gw_pwd=…)` and reads
  `tedapi.get_config()` / `get_pw3_vitals()` / `get_status()`.
- Aggregation: vitals arrive as per-VIN sections (`TEPOD--…`, `TEPINV--…`,
  `PVAC--…`, `PVS--…`). The script builds per-battery metrics (SoE estimate
  from `POD_nom_energy_remaining / POD_nom_full_pack_energy`, inverter
  power out, per-string PV power A–F) plus site-level rollups (summed
  energies, deduped alerts, string connected/state/voltage/current).
- Backup reserve is read locally with `pypowerwall.Powerwall.get_reserve()`
  and published as `backup_reserve_percent` in every telemetry snapshot.
  This is the Tesla-app-scaled percentage; do not use the raw TEDAPI
  `site_info.backup_reserve_percent` field directly because it includes
  Tesla's internal 5% scaling.
- Publish: one JSON payload per poll to **topic `pw3/telemetry`** on
  **`192.168.10.217:1883`** (Beelink mosquitto), QoS 1, retain off,
  client id `pw3-publisher`. A fresh MQTT connection per publish — simple
  and robust against broker restarts.
- Grid/outage telemetry (added 2026-07-25) comes from the same local
  `tedapi.get_status()` call and therefore adds no Tesla cloud/API usage.
  The payload includes `grid_outage`, `grid_ok`, `microgrid_ok`,
  `grid_contactor_closed`, island/grid states, utility-side and load-side
  L1/L2 voltages, site/load/solar/battery power, and active control alerts.
  `grid_outage` is the explicit inverse of TEDAPI's boolean `gridOK`; it is
  `null` rather than guessed if TEDAPI does not return a boolean.
- The Docker Node-RED `Tesla` tab (`7fa25727b15db1f0`) converts that payload
  to MQTT discovery entity **`binary_sensor.powerwall_grid_outage`**.
  Its state topic is
  `homeassistant/binary_sensor/pw3/grid_outage/state`; `on` means the utility
  grid is unavailable and the Powerwall is islanded. The state is refreshed
  every poll and has `expire_after: 60`, so stale telemetry becomes
  `unavailable` instead of silently claiming that grid power is healthy.
  Island/contact/voltage/power/alert details are attached as entity
  attributes.
- The Docker Node-RED `Tesla` tab also converts the local
  `backup_reserve_percent` value to MQTT discovery entity
  **`sensor.powerwall_backup_reserve`**. Its retained state topic remains
  `homeassistant/sensor/pw3/backup_reserve/state`, and its attributes include
  `source: local_tedapi`. Node-RED publishes immediately when the reserve
  changes and refreshes the retained state every five minutes.
- **Do not poll Tesla Fleet API `site_info` for reserve telemetry.** The old
  five-minute Node-RED poll was removed on 2026-07-26 because every request
  went to Tesla's billable cloud Fleet API. Fleet API is retained only for
  occasional reserve-changing commands. After a command, Node-RED waits for
  propagation and verifies the result from the local Home Assistant reserve
  sensor, without a second cloud data request.
- The Docker Node-RED `Powerwall Reserve Forecast` tab
  (`8d7289c449f38a92`) reads this local reserve sensor and recalculates both
  periodically and immediately when the reserve state changes. The Tesla
  tab's TOU-risk alert compares the reserve embedded in the forecast with
  the current local reserve and suppresses action if they disagree.
- That same Node-RED tab sends transition-only Pushover notifications through
  the existing `a3903de6e20b03dc` Pushover configuration. Node
  `pw_grid_outage_changed` watches the HA binary sensor with
  `outputInitially: false` and state-change-only output; function
  `pw_grid_transition_alert` suppresses startup/repeated/unknown/unavailable
  states. `off -> on` sends a priority-1 **Grid power is out** notification
  with live voltage/load/solar/battery details. `on -> off` sends a normal
  **Grid power restored** notification with outage duration when known. A
  Node-RED deploy during an existing outage does not fabricate a new outage
  notification.

Ops: `journalctl -u pw3-mqtt -f` on the box; consumer side is whatever
subscribes to `pw3/telemetry` on the Beelink broker (Node-RED/HA).

## Networking: how it reaches the Powerwall AND the house

Dual-homed on purpose — this is the whole reason the box exists:

- **wlan0 → Powerwall's own Wi-Fi AP** (`TeslaPW_CDRTMU`, NetworkManager
  connection of the same name, autoconnect on). Gets `192.168.91.124/24`;
  the gateway is `192.168.91.1`. TEDAPI is **only** reachable on this
  network — that's a Tesla design constraint, and why a house device must
  physically join the Powerwall AP.
- **eth0 → house LAN VLAN 40** (`192.168.40.244/24`, gw `192.168.40.1`).
  Default-route wins via metric (eth0 100 vs wlan0 600), so ALL normal
  traffic — MQTT publish, voice satellite HTTP, SSH — goes out ethernet;
  only `192.168.91.0/24` rides the Wi-Fi leg.

**Inter-VLAN firewall gotcha (matters for the satellite):** from VLAN 40
this box can reach the Beelink (`192.168.10.217` — MQTT, orchestrator) but
**not** other 10.x hosts — the kitchen satellite `192.168.10.251:8781`
times out. That's why satellite playback relays *via the orchestrator
proxy* (below) instead of hitting the kitchen box directly. If a firewall
rule 40.244→10.251:8781 is ever added, `PLAYBACK_RELAY_URL` can point
straight at `http://192.168.10.251:8781` and drop the extra hop.

## Role 2: family-room voice satellite (`voice-assistant.service`)

Deployed 2026-07-23 — backlog item "Family-room second mic (phase 2
satellite) v1". Mic-only fallback satellite: same dual-wake code as the
kitchen, but **all of its audio output (chime/TTS/replies) plays on the
kitchen big speakers** — the kitchen box stays the house's one voice.
During music it's the mic that *isn't* parked next to the speakers, so it
wins wake-over-music by geometry.

- ReSpeaker XVF3800 4-mic array on USB (card `Array`), captured via
  `~/.asoundrc` pcm `respeaker_ch0` (ch0 = processed beam; ch1 is the AEC
  reference and must not be downmixed in). That file lives only on the box's
  USB root drive, so here is the canonical copy — rebuild from this, not from
  memory (it was lost once already, see the outage note below). Mic-only box:
  no playback alias, replies relay to the kitchen.

  ```
  pcm.respeaker_ch0 {
    type plug
    slave.pcm {
      type route
      slave.pcm "hw:CARD=Array"
      slave.channels 2
      ttable.0.0 1.0
    }
  }
  ```

  The array advertises exactly `S16_LE / 2ch / 16000` and nothing else
  (`cat /proc/asound/card3/stream0`), which is a fast way to confirm a
  capture failure is NOT a rate/format mismatch.

- **Power-outage damage, 2026-07-25 20:59 (two separate faults).** The
  Powerwalls drained, the Pi lost power mid-write, and it came back needing a
  manual restart. Two things broke, and they look like one problem:
  1. *`~/.asoundrc` vanished.* ext4 logged `EFSCORRUPTED` at exactly
     20:59:26 and the next boot ran `orphan cleanup on readonly fs`, which
     removed the orphaned inode. The satellite then crash-looped ~1,500 times
     (`arecord: audio open error: No such file or directory` — that message
     means the ALSA *alias* is missing, not the mic). Restored 2026-07-26.
     Also left two block groups with `bad block bitmap checksum` (bg 720,
     816); the fs was `clean with errors` and had never been checked since it
     was created in Dec 2025.
  2. *The ReSpeaker itself wedged.* Separately from the config, the array
     enumerates normally but every capture read returns `Input/output error`
     at its own native format. It survived `USBDEVFS_RESET` **and** a
     `/sys/bus/usb/drivers/usb` unbind/rebind — the XVF3800 runs its own DSP
     firmware, and a bus-level reset does not power-cycle it. **Only a
     physical unplug/replug (or a full power-down — a warm reboot can leave
     USB rails powered) clears this.** Note the root filesystem is a PNY USB
     flash drive on the same controller; if a replug doesn't fix it, suspect
     the USB power budget and try a powered hub.

  Lesson: after any outage on this box, check BOTH — that `~/.asoundrc` still
  exists, and that `arecord -D respeaker_ch0 -d 2 /tmp/t.wav` actually
  produces more than a 44-byte header. A 44-byte file is a header with no
  samples, i.e. the device opened but never streamed.
- Code: `/home/pi/voice-pipeline/assistant.py` — same file as the kitchen,
  deployed from `home_config/voice-assistant/satellite/`. Venv
  `/home/pi/voice-pipeline/.venv` (numpy, onnxruntime, livekit-wakeword).
  Wake models in `/home/pi/wake-bench/` (okay_computer + okay_google).
- `.env` (`/home/pi/voice-pipeline/.env`):
  - `SATELLITE_ID=familyroom` — sent as `?sat=` on `/verify` and
    `/command/audio` so the orchestrator can arbitrate.
  - `PLAYBACK_RELAY_URL=http://192.168.10.217:8785/satellite` — playback
    relay via the orchestrator's `/satellite/play` proxy (see firewall
    note). Relay is synchronous: the POST returns when the audio finished
    playing in the kitchen, preserving the satellite's capture timing.
  - `HOP_MS=320` — the "relaxed cycles" setting. On-device benchmark
    (2026-07-23): dual-model predict ≈ **130ms** → ~40% duty. Deliberately
    NOT the kitchen's 192: this is a 1GB Pi 4 and a fallback mic with a
    generous latency budget. Headroom exists (45°C) if we ever want 256.
  - `ORT_THREADS=2`, `SILERO_THRESHOLD=0.4`, `MODE=active`.
- Service: `voice-assistant.service` (systemd, enabled). Logs:
  `journalctl -u voice-assistant -f`. Wake review page:
  `http://192.168.40.244:8781/review` (this box's own clips/events — the
  homepage "Wake Review" tile still points at the kitchen's).
- The day-mode volume/mode Node-RED flow only drives the **kitchen**
  satellite; this box's volume is irrelevant (it relays raw audio and the
  kitchen scales it with kitchen volume), and its mode persists via `.env`.
- Alarms still ring **only** in the kitchen (orchestrator
  `SATELLITE_ALARM_URL` → .251). Saying "stop the timer" near this mic is
  just a normal turn and works.

### First-past-the-post arbitration (orchestrator)

Both mics hear the same "okay computer". The orchestrator (`app.py`)
resolves it: the **first satellite whose wake VERIFIES claims the turn**
(`ARB_SUPPRESS_S=3` window); the other satellite's `/verify` is answered
`{"suppressed": true, "winner": …}` — checked both at entry (skips the
duplicate ASR decode) and again after ASR (covers the in-flight race).
The loser plays no chime, but still silently captures the command and
POSTs it to `/command/shadow`, where it's transcribed and **logged only**
(`grep 'shadow command' in docker logs voice-orchestrator`) — that log,
next to the winner's `command sat=…` line, is the evidence base for the
v2 dual-transcribe chooser (only build it if shadows regularly beat
winners). During music the drowned kitchen mic fails verify, so the far
mic wins by default — arbitration is arrival-order deterministic, not a
race we tuned.

### Verified working (2026-07-23)

- Replayed a real wake clip: kitchen verifies+claims → familyroom
  suppressed with winner=kitchen → verifies normally after the 3s window.
- Relay chain pw_pi → orchestrator `/satellite/play` → kitchen `/play`:
  chime audibly plays on the kitchen big speakers.
- Manual `/trigger` on pw_pi: full turn (relayed chime → capture →
  clean `no_speech_onset`).
- Live voice from the couch: **pending Brad**.

### Ops quick reference

```bash
ssh pw_pi                                   # from the Beelink
journalctl -u voice-assistant -f            # satellite logs
journalctl -u pw3-mqtt -f                   # powerwall publisher logs
curl -s localhost:8781/health               # satellite health/mode
curl -s -X POST -d '{"mode":"shadow"}' localhost:8781/mode   # kill switch
vcgencmd measure_temp; vcgencmd get_throttled                # thermals
```

Redeploy satellite code: `scp home_config/voice-assistant/satellite/assistant.py
pw_pi:/home/pi/voice-pipeline/ && ssh pw_pi sudo systemctl restart
voice-assistant` (same file also goes to the kitchen box .251 — keep them in
lockstep).
