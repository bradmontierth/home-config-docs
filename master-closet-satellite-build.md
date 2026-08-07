# Master Closet Voice Satellite — Build Guide

**Purpose:** stand up Path A of `upstairs-poe-satellites-plan.md` — a voice
satellite in the master closet — on a **freshly flashed** Pi. Self-contained:
everything needed is either in this file, in this repo, or at a path named here.

Status at time of writing (2026-08-07): nothing built. PoE splitter ordered, not
arrived. Pi to be flashed clean with RPi Imager. Plan finalized, no open
questions.

---

## 0. Build in two stages — do not skip to stage 2

**Stage 1 — the Pi answers out of its own USB speaker.** No server changes at
all. Proves mic, wake, stage-2 verify, intent, and reply audio end to end. Runs
entirely on your desk.

**Stage 2 — replies move to the master bath zone.** Orchestrator + Node-RED
work (§6). Only after stage 1 is clean.

Staging matters because stage 1 has zero new server code: if something is
broken, it is the Pi. Debugging a new client and a new reply path at once is how
you end up unable to tell which layer is lying.

---

## 1. Hardware

| Item | Detail |
| --- | --- |
| Board | The freed `.24` kitchen Pi (Pi 4). **Flashing clean**, which removes the old `voice-assistant` + `squeezelite` units — the contention risk with the `.251` kitchen box disappears with the reflash. |
| Mic | The "Jabra knock-off" USB puck. **Confirmed mic-only** — it cannot play the chime. |
| Chime speaker | Cheap Amazon USB speaker already owned. USB so power + audio ride one cable. |
| Network | Cat6 drop to the closet shelf + 802.3af splitter (ordered). Until it arrives, ordinary Ethernet or Wi-Fi at the desk is fine. |
| Shelf | Already holds the TRC metal PSU enclosure and the LED controller; mains present. |

**Nothing needs to be purchased.**

---

## 2. Flash and base OS

Raspberry Pi OS (64-bit). In RPi Imager's advanced options set hostname,
user `pi`, your SSH key, and locale. Suggested hostname: `closet-pi`.

```bash
sudo apt update && sudo apt install -y python3-venv python3-dev alsa-utils
```

Confirm the mic and the speaker enumerate, and capture their ALSA names — you
need these for `.env`:

```bash
arecord -l && arecord -L | grep -i plughw   # mic
aplay   -l && aplay   -L | grep -i plughw   # USB speaker
arecord -D plughw:CARD=<MIC> -f S16_LE -r 16000 -c 1 -d 3 /tmp/t.wav
ls -l /tmp/t.wav      # ~96KB for 3s mono@16k. A 44-byte file = header, no samples.
```

A 44-byte WAV means the device opened but never streamed — that is a capture
failure, not a quiet room.

---

## 3. Install the satellite

```bash
mkdir -p /home/pi/voice-pipeline/{data,sounds}
python3 -m venv /home/pi/voice-pipeline/.venv
/home/pi/voice-pipeline/.venv/bin/pip install numpy onnxruntime livekit-wakeword
```

### Files to copy

From the **Beelink** (`192.168.10.217`), all paths absolute:

| Source | Destination on the Pi |
| --- | --- |
| `home_config/voice-assistant/satellite/assistant.py` | `/home/pi/voice-pipeline/assistant.py` |
| `home_config/voice-assistant/satellite/sounds/` (recursive) | `/home/pi/voice-pipeline/sounds/` |
| `home_config/voice-assistant/satellite/voice-assistant.service` | `/etc/systemd/system/voice-assistant.service` |
| `/home/pi/backups/pw_pi-20260728/blobs/okay_computer.onnx` | `/home/pi/wake-bench/okay_computer.onnx` |
| `/home/pi/backups/pw_pi-20260728/blobs/okay_google.onnx` | `/home/pi/wake-bench/okay_google.onnx` |
| `/home/pi/backups/pw_pi-20260728/blobs/silero_vad.onnx` | `/home/pi/voice-pipeline/silero_vad.onnx` |

**The `.onnx` models are not in git.** The backup kit above is the local source
of truth on the Beelink; the live copies are on `.251` in `~/wake-bench/`. The
`stop.onnx` model in that kit is alarm-dismiss only and is **not needed here** —
alarms ring in the kitchen (orchestrator `SATELLITE_ALARM_URL` → `.251`), and
`assistant.py` logs "no stop model … ASR-only" and carries on without it.

The service unit is generic (`WorkingDirectory=/home/pi/voice-pipeline`,
`EnvironmentFile=-/home/pi/voice-pipeline/.env`) — only its `Description`
mentions the kitchen. No edit required.

### `.env` — `/home/pi/voice-pipeline/.env`

Not in git, by design. Stage-1 values:

```ini
SATELLITE_ID=master
ORCH_BASE=http://192.168.10.217:8785
MIC_DEVICE=plughw:CARD=<from arecord -L>
PLAYBACK_DEVICE=plughw:CARD=<USB speaker from aplay -L>
MODEL_PATHS=/home/pi/wake-bench/okay_computer.onnx,/home/pi/wake-bench/okay_google.onnx
SILERO_MODEL=/home/pi/voice-pipeline/silero_vad.onnx
SILERO_THRESHOLD=0.4
HOP_MS=320
ORT_THREADS=2
MODE=active
```

Rationale for the two tuning values, both inherited rather than invented:
`SILERO_THRESHOLD=0.4` is the kitchen's setting after the oven-corner
command-capture miss. `HOP_MS=320` is the family-room "relaxed cycles" value —
a closet has a generous latency budget and this is a spare Pi 4, so start there
rather than at the kitchen's 224. Do **not** copy the kitchen's `224` blindly:
that value came with a thermal soft-limit fight at 81–82 °C.

Do **not** set `PLAYBACK_RELAY_URL` — that is the family-room mic-only pattern
that relays audio to the kitchen. This box plays its own chime locally.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now voice-assistant
journalctl -u voice-assistant -f
```

---

## 4. Stage-1 verification

```bash
curl -s localhost:8781/health                                   # mode + liveness
curl -s -X POST localhost:8781/trigger                          # forced turn, no wake word
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"mode":"shadow"}' localhost:8781/mode                 # kill switch
```

Wake review page for this box's own clips/events:
`http://<pi-ip>:8781/review`.

Pass criteria: say "okay computer, what's the weather" → chime out of the USB
speaker → spoken answer out of the USB speaker. On the Beelink,
`docker logs voice-orchestrator` should show `verify sat=master`.

**Arbitration is fine with three mics.** `_ARB` (`app.py:160`) is one
house-global holder with `ARB_SUPPRESS_S=3`; the kitchen and the master closet
are far enough apart that one utterance never reaches both. Do not add a
second mic *within earshot* of this one without doing Appendix A item 5
(proximity groups) first.

---

## 5. Resolved entity reference

Everything below verified live on 2026-08-07. **Do not infer room membership
from entity ids in this house** — use Dashy (`lovelace.dash_with_sections`,
Upstairs view, sectioned per room) and verify against the HA API.

### Audio targets (Music Assistant player ids — this is what the amp path uses)

| Room | MA player id | HA entity |
| --- | --- | --- |
| Master bath / shower | `ma_shower` | `media_player.shower` |
| Master bedroom | `ma_master_bedroom` | `media_player.master_bedroom` |
| Loft (test target) | `ma_loft` | `media_player.loft` |
| Simon's room | `ma_simon_room` | `media_player.simon_room` |

The HA entities were renamed 2026-08-07 from `speaker_pi` / `speaker_pi_2`.
The announce path addresses MA player ids and never resolves an HA entity —
the entity strings in `MA_PLAYER_MAP` are lookup keys only.

### The five lights (one group command — they move in tandem)

| # | Light | Entity | Control path |
| --- | --- | --- | --- |
| 1 | Closet LEDs | `light.closet_leds` → `light.closetleds_4` + `_5` | ESPHome `closetleds` |
| 2 | Ceiling bulb | `light.master_closet_light_switch` | Zooz via Hubitat device 44 |
| 3 | Vanity (3) | `light.closetleds_1` / `_2` / `_3` | same ESPHome controller; subflow + rotary encoder w/ press |
| 4 | Toilet | `light.master_toilet_light` | zigbee |
| 5 | Bath floor RGB | `light.masterbathfloor_rgb_light` | ESPHome `masterbathfloor` |

Motion: Hubitat device 911, on the Node-RED **"Upstairs Bathroom"** tab.

### Covers

- `cover.upstairs_bath_blind` — master bath blind (confirmed)
- `cover.boys_room_baby_blind` — Simon's blind (confirmed)

### Other

- `input_boolean.simonalarm` — Simon's room armed flag (Path B gate)
- Voice buttons follow `button.voice_*`, Node-RED tab **"Voice Buttons"**

---

## 6. Stage 2 — backend work

Full rationale in `upstairs-poe-satellites-plan.md` Part 1. Summary of what to
build, in order:

1. **Per-satellite target map.** `sat_id → {ma_player, ha_player, playback,
   music_policy, quiet_hours}`, hot-reloaded like `home_commands.json` /
   `broadcast_rooms.json`. Point `master` at `ma_loft` while developing, so
   testing does not disturb the master bedroom.
2. **Reply routing.** Orchestrator publishes `{room, ttsUrl, volume,
   forceBedroom}` on reply; kick the amp wake at **stage-2 verify** so the 3 s
   gate elapses under ASR + intent + TTS. Add the ~10-line `msg.ttsUrl` bypass
   to the **Amp Speakers** subflow (`e711d48f74f78209`) so it plays the
   orchestrator's already-rendered Kokoro audio instead of re-rendering.
   Deploy via the Admin API per `nodered-flow-agent-guide.md`.
3. **`forceBedroom` — do not skip.** The subflow filters out the literal string
   `media_player.master_bedroom` whenever `DisableBedroomAnnouncements` or
   `adrienneWorkingDisableAnnounce` is set; `msg.forceBedroom === true` is the
   only override. Without it a closet reply is dropped with nothing but a
   `node.warn`. **Most likely day-one failure.**
4. **Per-room music policy.** `music.py` targets the kitchen queue
   unconditionally (`config.py:239`) and shuffles artists/playlists
   (`music.py:568`). Needs `{queue_id, single_track}`.
5. **Playback muting.** Suppress this room's detection for the reply duration
   + ~500 ms. Build it now, but see §7 — it cannot be tested from the desk.

### TTS voice — the trap

Kokoro is the house voice, but it lives in **tts-router** (`:8891`):
`main.py:1186` treats a `fast:` / `kokoro:` prefix as forced-Kokoro, and the
orchestrator already speaks as `TTS_VOICE=fast:doorbell` (`config.py:29`).
Inside Amp Speakers the default is **`tts.openai`, voice `picard:calm`** — cloud,
wrong voice. It honours `msg.ttsEntity`, but the `tts.kokoro` HA entity was
`unavailable` as of 2026-08-07, so the override is not a reliable fix. The
`ttsUrl` bypass sidesteps both.

### The "keep the lights on" hold

Node-RED/Hubitat work, independent of the voice path — buildable and testable
via a Voice Button press with no satellite at all.

- **Suppress the motion-driven OFF; never force lights ON.** The vanity has a
  physical rotary encoder with press. A hold that forces state fights a
  deliberate press and makes the room unusable for the window.
- **A manual off should cancel the hold.**
- **Timed, not a toggle** — a `masterLightsHold` global, 30 min, auto-expiring,
  same shape as the staged-brighten 90-minute window. State the window in the
  spoken confirm.
- **Gate every off-path or it reads as broken:** closet motion (Hubitat 911),
  `binary_sensor.master_bath_motion_occupancy`,
  `binary_sensor.master_shower_motion_motion`, and the "Sleeping In or Nap"
  scene tab.
- **Check light #5 first** — the bath floor RGB is an accent light and may have
  its own night behaviour; confirm it should follow the group at all hours.

---

## 7. What the desk cannot prove

Do not mistake a clean desk run for a finished satellite:

- **The self-hearing loop.** At the desk the mic never hears its own reply. In
  the closet it will, off the ceiling, with no AEC reference. §6 item 5 is
  written against a problem that is invisible until the Pi moves.
- **Mic performance at closet distance** — the entire premise of using the
  Jabra there.
- **The five-light hold under real conditions** — the failure is standing still
  while folding laundry.
- **PoE**, pending the splitter.

## 8. Gotchas

- **Repeated announcement testing wedges MA.** `announcement_in_progress`
  sticks true and presents as silent audio with no error. If the test zone goes
  quiet mid-session that is the wedge, not your code; restart
  `music-assistant-server`. Watchdog shipped 2026-07-29.
- **`data/flows.json` on the Beelink is stale** (`nodered-flow-agent-guide.md:48`).
  The Node-RED Admin API on `127.0.0.1:1880` is the source of truth.
- **`home_config` shares one git index across concurrent sessions.** Commit by
  explicit path; never `git add -A`.
- **Never pattern-kill** (`pkill uvicorn/python/node`) on the main Pi. Kill by
  exact PID.
- Keep `assistant.py` in lockstep across boxes — the same file runs on `.251`,
  on pw_pi, and here.
