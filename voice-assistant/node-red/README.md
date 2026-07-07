# Node-RED — Voice Assistant Mode switch

HA MQTT-discovery **switch** `switch.kitchen_voice_assistant_active` that flips
the satellite between modes:

- **ON**  → satellite `active` (full voice pipeline)
- **OFF** → satellite `shadow` (detect + log only; nothing audible)

Created entirely from Node-RED via the Admin API (`POST /flow` → new tab
"Voice Assistant Mode") — **no Home Assistant restart and no Node-RED restart**.
The switch publishes its own retained discovery config, so HA materializes the
entity within seconds of deploy.

## Flow (tab `7d4069eae9ec1eda`)
1. **Discovery**: inject (once, 3s after deploy) → build config →
   `homeassistant/switch/kitchen_voice_assistant/active/config` (retained).
2. **Command**: `voice/kitchen/assistant_active/set` (HA publishes ON/OFF) →
   map ON→active / OFF→shadow → `POST http://192.168.10.24:8781/mode` →
   echo state to `.../state` (retained).
3. **Sync**: poll satellite `/health` every 30s → publish `.../state` +
   `.../availability=online` (keeps the switch honest if mode is changed by
   curl or the device restarts).

Reuses the existing "beelink mini" mosquitto broker node (`82f540b7378c2e35`,
192.168.10.217:1883). Topics under `voice/kitchen/assistant_active/`.

## Redeploy / edit
`voice-assistant-mode-flow.json` is the exported source of truth.
`deploy_mode_switch.py` regenerates + re-POSTs the flow (idempotent-ish: creates
a new tab each run — delete the old tab first, or `PUT /flow/7d4069eae9ec1eda`
to update in place).

```bash
python3 deploy_mode_switch.py            # POST /flow -> new tab
# update existing tab instead:
#   curl -X PUT localhost:1880/flow/7d4069eae9ec1eda -H 'Content-Type: application/json' -d @voice-assistant-mode-flow.json
```

## Day-mode volume (tab `0f4b1b8a369d5d91`)
`deploy_volume_flow.py` / `voice-assistant-volume-flow.json`: reuses the
existing global **`mode`** (Day / Early Morning / Evening / Night / Away) — the
same source that sets `defaultSpeakerVolume` for Music Assistant announcements —
and POSTs a mapped level to the satellite `POST /volume` every 2 min (+ on
deploy). Mapping: **Day 60 / Early Morning + Evening 40 / Night 30** (default 40).

The satellite applies it as **software gain to its own audio only** (chimes /
alarm / TTS) — the ALSA mixer is untouched, so music on the shared card is
unaffected. The alarm is floored at 50% (`ALARM_VOLUME_FLOOR`) so a cooking
timer stays audible at night. Volume persists in `~/voice-pipeline/data/volume`.

To change the tiers: edit `MAP` in `deploy_volume_flow.py` and redeploy (or edit
the function node in place). NB: our audio is direct-`aplay`, NOT Music
Assistant — MA's announce volume never touches it, which is why this separate
gain exists.

## Not covered (yet)
Only 2 states (active/shadow). The third `off` state (mic fully paused) is
supported by the satellite `/mode` endpoint but not exposed here — add a second
switch or a `select` (active/shadow/off) later if wanted.
