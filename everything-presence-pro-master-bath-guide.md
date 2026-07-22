# Everything Presence Pro — Master Bathroom (stuck mmWave + reboot fail-safe)

Last updated: 2026-07-18

## Device

- Everything Presence Pro (PoE/Ethernet), upstairs master bathroom, upside-down mounted.
- ESPHome device name/prefix: `everything_presence_pro_e63ec8`
- Firmware 1.2.1 (latest as of 2026-07-18).
- Home Assistant: local on the Beelink, `http://127.0.0.1:8123`.
  Token for scripts: `/home/pi/cecret_lake/dashboard_webapp/ha_token`
  (192.168.123.47 in older guides is NOT reachable from the Beelink; use 127.0.0.1.)

## Key entities

- `binary_sensor.everything_presence_pro_e63ec8_motion` — PIR (turns lights ON in Node-RED)
- `binary_sensor.everything_presence_pro_e63ec8_mmwave_presence` — combined radar
  (OR of the two below; used to turn lights OFF when clear)
- `binary_sensor.everything_presence_pro_e63ec8_tracking_presence` — moving-target radar (healthy)
- `binary_sensor.everything_presence_pro_e63ec8_static_presence` — stationary-body radar
  (**this is the sub-sensor that gets stuck ON**)
- `button.everything_presence_pro_e63ec8_restart_device` — reboots the device (~10 s)
- `sensor.everything_presence_pro_e63ec8_target_1_{x,y,distance,speed,resolution}` — tracking targets

Settings snapshot 2026-07-18: tracking range 600 cm, static range 0.6–6.2 m,
static trigger/sustain sensitivity 5/7, occupancy timeout 15 s,
`auto_clear_stuck_targets` ON (3 min) — note this only clears *tracking* targets,
it does NOT clear stuck *static* presence.

## Problem (diagnosed 2026-07-18)

Two separate issues:

1. **Static presence gets stuck ON for hours→days**, so the lights never turn off.
   Recorder history: stuck ~31 h (07-16 13:22 → 07-17 20:17), then again 26+ h
   (07-17 20:49 → 07-18). While stuck, the radar held a phantom stationary target
   (~132 cm, speed 0). PIR keeps cycling normally throughout.
2. **Coverage gaps** at the far end of the closet and the toilet area — range is already
   maxed (6 m), so this is mounting geometry / occlusion, not a settings knob.
   Unresolved; would need re-aiming/relocating the sensor or a second sensor.

Reboot test 2026-07-18 ~18:09 local: restart cleared static presence and the phantom
target for ~4 s, then static presence re-latched immediately with zero tracking
targets and PIR off. So a reboot is not always a durable fix — there may be a
persistent reflector (fan/vent/towel/mirror multipath) or firmware latch bug.
Next tuning candidates if the fail-safe fires too often: lower static *sustain*
sensitivity 7 → 6 (or 5), or pull `static_presence_max_range` / trigger range in;
one change at a time.

## Fail-safe (deployed 2026-07-18)

Node-RED (Docker `node-red-container-main`, Admin API `http://127.0.0.1:1880`),
tab **Upstairs Bathroom** (`387d0335cb7186aa`). Five nodes, all ids prefixed
`eppmb_failsafe_`:

`inject (every 15 min)` → `api-current-state mmWave → msg.mmwave` →
`api-current-state PIR → msg.pir` → `function` → `api-call-service button.press restart`

Function fires the restart only when ALL of:

- mmWave presence `on` continuously ≥ 60 min
- PIR `off` continuously ≥ 45 min
- last fail-safe restart ≥ 60 min ago (flow context `eppmb_failsafe_last_restart`)

On fire it logs `node.warn("EPP failsafe: ...")` — visible in
`docker logs node-red-container-main`.

### Ops

- Inspect: `curl -s http://127.0.0.1:1880/flow/387d0335cb7186aa | jq '.nodes[] | select(.id|startswith("eppmb_failsafe"))'`
- Manual test: `curl -X POST http://127.0.0.1:1880/inject/eppmb_failsafe_poll_v1`
  (safe: only restarts if the stuck conditions are actually met)
- All flow changes via the Admin API per `nodered-flow-agent-guide.md` —
  never edit deployed flows.json directly.
- Manual unstick from CLI:
  `curl -X POST -H "Authorization: Bearer $(cat /home/pi/cecret_lake/dashboard_webapp/ha_token)" -H 'Content-Type: application/json' -d '{"entity_id":"button.everything_presence_pro_e63ec8_restart_device"}' http://127.0.0.1:8123/api/services/button/press`

### Interaction with the lights-off logic

During the ~10 s reboot the EPP entities go `unavailable`; the existing off-logic
(`eppmb_*` nodes) only acts on `off`, so a fail-safe reboot does not itself
turn the lights off — the lights turn off on the next normal all-clear evaluation
once the (un-stuck) sensor reports clear.
