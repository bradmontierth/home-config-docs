# Family room BR30 cans lighting

Approved 2026-09-10: reduce exposed BR30 glare, add one daylight OFF, preserve
manual staged ON from coffee-table Picos, wall Pico and voice.

## Runtime and ownership

- Docker `node-red-container-main`, Admin API `http://127.0.0.1:1880`.
- Follow [nodered-flow-agent-guide.md](nodered-flow-agent-guide.md).
- Target: `light.family_room_can_group` (the four BR30s). Kitchen cans are separate.
- Policy lives on **Circadian Lighting CT** `be47927df555c65e`, function
  `fc0910000000000a` (Family room cans policy), with a 60-second tick.
- Source function: `family-room-cans/controller.js`; build/validation/deployment
  scripts and offline scenarios are beside it. Source is a reference: always
  GET the live target tab before making future edits.
- Normal brightness is **`FamilyRoomCansBri`**, strictly 1–50. The old
  `BriCurveCans` has competing legacy writers and ALSO feeds other fixtures;
  those writers/consumers were deliberately preserved for their existing use.
- Color temperature remains `CTCans` (2500–3500 K).

## Approved schedule

- Early Morning / Night: 1%.
- Morning: `round(1 + 49 * (sunPercent/100)^2)`.
- After solar noon: `round(15 + 35 * (sunPercent/100)^2)`.
- Sunset to 20:30: 15%. From 20:30 to 21:00, fade 15→1.
- If sunset is after 20:30, start the half-hour fade at sunset instead.
- Solar input is the existing clamped lowercase `sunPercent` (0–100), not
  uppercase `SunPercent` or the time-modified color-temperature variable.
  Sun data older than 12 minutes suppresses automatic activation/daylight OFF
  and retains the last safe family-room brightness.

## Daylight and manual controls

- When fresh sunPercent first exceeds 25, send one group OFF if it is on and
  not manually overridden/in a protected state. Consume the date even if
  already off or a manual command wins that evaluation. Unknown/unavailable
  light state does not consume it.
- The date and daylight state are in persistent flow context
  `familyRoomCansPolicy`, so an ordinary restart/redeploy does not replay OFF.
  Persistence uses the host's existing localfilesystem context flush behavior.
- Daylight blocks automatic activation. After solar noon, falling below 20
  releases that block. Check occupancy, including continuous occupancy, and
  turn on once eligible. If vacant, wait for occupancy. Explicit OFF cancels
  a pending dusk activation. No shared kitchen motion latch is reset.
- An ON press while off applies the scheduled value. ON while already on, or
  explicit voice "brighter", applies 100% for 90 minutes. Both coffee-table
  Picos, the wall/fan-light Pico, and voice use this same function/timestamp.
- Pending commands bridge brief HA state lag, so two quick ON presses work.
- OFF, observed OFF, or an existing external reset releases the override.
  The existing `familyRoomCansOverride` global is kept for compatibility.
- Manual scheduled ON after daylight OFF follows the curve and is not turned
  off again by the daylight threshold. Existing vacancy, bedtime and TV OFF
  routes remain in place. Night/Away, TV, disable-main-floor, chaos and
  KitchenStayOn gates still prevent automatic activation.
- The kitchen motion cans branch now uses this policy instead of the old
  solar-radiation ≤75 and 20:00 cutoff. Other fixture branches are unchanged.

## Wiring and deployment scope

Existing tabs changed through individual `PUT /flow/:id`:

| Tab | ID | Change |
| --- | --- | --- |
| Circadian Lighting CT | `be47927df555c65e` | Add controller, cache reads, minute tick, state observations |
| Kitchen Motion Lighting | `fb8d8707af18844f` | Route cans activation to controller |
| Update CT Values while On | `55523c67b181dd7b` | Route cans repaint to controller |
| Button Controllers | `194f33df5b26685e` | Replace two cans subflow instances with staged requests |
| Button Controllers 2 | `f21c376895b9f802` | Replace wall Pico cans instance with staged request |
| Voice Buttons | `294429bac2b766ff` | Route cans ON/brighter/OFF; remove old separate override timer |

Subflow definitions are unchanged. All cross-tab links are bidirectional.
The old orphaned cans paint nodes are retained as disconnected reference nodes.
Other Pico button actions and other lighting are unchanged.

For dusk occupancy, query the **Hubitat Kitchen Multisense device 789 cache**,
the live HA porch motion entity, and existing `kitchenCam` global. Do not use
HA `binary_sensor.kitchen_multisense_motion`: its mirror was stale during
inspection (last update July 21). The controller uses the same Hubitat cache
as the existing vacancy flow.

## Validation and troubleshooting

```sh
TZ=America/Denver node family-room-cans/test-controller.cjs
python3 family-room-cans/build.py
python3 family-room-cans/validate.py
```

`build.py` uses the recorded backup directory. For future changes take new
snapshots; don't deploy from an old backup. `deploy.py` refuses any changed tab
and verifies every scoped API deployment by re-reading it. Per-tab deploys
reinitialize their nodes; there is no service restart or full-flow deployment.

Read-only inspection: `/context/global` includes `FamilyRoomCansBri`,
`FamilyRoomCansDaylightBlocked`, `FamilyRoomCansLastApplied`,
`FamilyRoomCansLastError` if any, and the compatible override flag.
`/context/flow/be47927df555c65e` includes the detailed policy state.
Logs use the prefix `Family room cans:`. The minute tick deduplicates unchanged
brightness/color and respects the active manual override.

The pre-change Admin API flows, six individual tab snapshots, and project
reference copy are in `family-room-cans/backups/`; `backup-path.txt` records
the deployment's backup. These full snapshots are gitignored. Roll back only
the affected tabs through the Admin API after reviewing concurrent edits.

## Deployment validation — 2026-09-10

- All six tab updates deployed and re-read successfully; full live flow comparison
  found no functional changes outside the prepared scope (33 added/removed/changed
  nodes). Admin API also normalizes empty link-out wires and redundant subflow
  identifiers. No service restart or subflow-definition change.
- Sixteen offline scenarios passed: curve endpoints, early morning, late sunset,
  occupied daylight OFF, manual ON after daylight OFF, rapid staged presses,
  90-minute expiry, persisted state, dusk occupancy, control gates, and stale/
  unavailable inputs. Syntax and bidirectional-link checks passed.
- Live existing repaint entry `c6de008f2166733c` was exercised through the Admin
  API. Controller context recorded `command: repaint`, fresh solar data, correct
  house mode and occupancy, and a 9% scheduled value at 20:42.
- All four bulbs and the group were already OFF (since 20:30, before deployment)
  and remained OFF. No live 100% flash or forced lighting cycle was used.
- Controller minute ticks observed running; no controller/HA action errors found.
  Logs separately showed the unrelated existing RadonEye file-path failure and
  an unreachable WLED Tree startup read after the button-tab deploy.
- Tomorrow's real sun-threshold crossing and physical Pico feel remain
  observational checks; synthetic scenarios cover their logic.

## Chaos color preservation — 2026-09-13

The dashboard `input_button.chaos_color_changer` sets
`input_boolean.disable_main_floor_lights`, independently of the separate
`input_boolean.chaos_mode` toggle. The cans controller previously checked the
main-floor disable flag for automatic activation/daylight OFF but omitted it
from already-on scheduled repaint. Its minute tick, observe or motion requests
could therefore overwrite RGB with scheduled white during the disabled period.
Repaint now requires both flags to be off. Explicit manual ON/brighter/OFF and
existing mode-exit/reset behavior remain unchanged.

Deployed only the Circadian Lighting CT and Voice Buttons tabs using scoped
Admin API updates and verified exact readbacks. Eighteen controller scenarios
pass, including RGB preservation on tick/repaint/motion/observe and normal
repaint resumption when the disable flag clears.

Voice aliases `give me chaos mode`, `set me chaos mode`, `chaos mode`,
`set chaos mode`, `turn on chaos mode`, `enable chaos mode`, `start chaos mode`,
and `change chaos colors` press `button.voice_chaos_color_changer`. That MQTT
button routes to the same `input_button.chaos_color_changer` used by the
HA dashboard, so repeating it changes the colors again. These are household
aliases, using the existing direct home-command path; no new LLM intent.
Both the repository seed and persistent live command table were updated.

Validation: registered HA button, discovery/topic/action-chain checks, ten
mocked dispatch cases from kitchen/familyroom with no speech or light actions.
No physical color-changing test was performed. Artifacts and API backups:
`diagnostics/chaos-voice/`.

## Named chaos colors — 2026-09-13

“Give me chaos in blue”, “give me chaos in red”, and “give me chaos in blue
and red” now use a deterministic parser in `voice-assistant/orchestrator/chaos.py`
and the existing `home_control` dispatch. One named color fills both A/B slots;
two preserve spoken order. Supported names are red, orange, yellow, green,
cyan, blue, purple, magenta, pink, and white. Saturated colors use 100%
saturation. Unsupported colors or more than two prompt for one or two colors,
without publishing a command. Plain chaos-mode aliases still randomize colors.

The orchestrator publishes a non-retained JSON message to `voice/chaos/colors`
through HA's MQTT service. Three new nodes on the Chaos tab validate the color
names against the same palette, store the existing chaosValues structure,
then enable the main-floor automation hold before invoking the existing
switch LED and fixture outputs. No separate LLM schema or model is needed.

Validation: 584 passing offline tests, one live-only skip; color-slot/RGB tests
include actual family-room A/B builders and rejection of malformed payloads.
The deployed tab was re-read and compared; orchestrator health passed. No
physical color activation was performed. Artifacts: `diagnostics/chaos-colors/`.
Prior orchestrator image: `voice-pipeline-voice-orchestrator:before-chaos-colors-20260913`.

## Chaos brightness and separate above-cabinet white — 2026-09-13

Both shared chaos A/B can payload builders now explicitly send
`brightness_pct: 100`. This applies to random chaos, named colors, and the
existing dashboard picker; it no longer inherits an earlier white-light level.

The Update CT Values while On cabinet branch previously sent group ON whenever
TVStatus was false, including the above-cabinet Zigbee white strip that chaos
had switched off. It now checks the main-floor automation hold first. A hold
routes updates only to Dishwasher, Coffee Corner and Fridge controllers.
Without a hold, both TV branches check the above strip's actual state: an OFF
above strip keeps updates on those same lower three controllers. The full
group repaint remains available when the above strip is already ON and the
hold is off. Explicit manual controls are not changed.

Deployed only Chaos and Update CT Values while On through scoped Admin API
updates with exact readbacks. Offline checks verify full brightness, preserved
A/B RGB values, hold/unknown-state routing and the exact lower controller list.
Artifacts and rollback snapshots: `diagnostics/chaos-lighting-fixes/`.

## Voice chaos off — 2026-09-13

`chaos off`, `turn off chaos`, `turn off chaos mode`, `disable chaos mode`,
`stop chaos mode`, and `exit chaos mode` now press `button.voice_chaos_off`.
The Voice Buttons tab clears both `input_boolean.chaos_mode` and
`input_boolean.disable_main_floor_lights`, waits one second for cached states,
and invokes the existing `back to normal` function and Global CT repaint.
The explicit repaint also works when the chaos toggle was already off (the
color-changer-only case). This reuses the white repaint path; the cabinet
controller double-tap recollection was not confirmed as a chaos-off binding.

A routing regression surfaced in validation: kitchen-local fuzzy `cans off`
won before the exact household `chaos off` alias. Home command resolution now
checks exact reachable aliases first (local exact still takes precedence),
then retains the prior local-before-household fuzzy fallback.

Validation: 585 passing tests, one live-only skip; six chaos-off phrases and
Node-RED discovery, release-both-flags sequencing, and white repaint payload
checks. No physical chaos-off activation was performed. Artifacts:
`diagnostics/chaos-off/`. Previous image tag: `before-chaos-off-20260913`.

## Chaos color gamma + can pair groups — 2026-10-11

**Colors.** The entry fixture (`entry-fixture-simon` red/green/blue channels)
and family-room pole (`light.familyroompole_rgb`) are ESPHome PWM with
`gamma_correct: 1.0`, so linear HSV→RGB rendered orange as yellow (green 50%)
and purple as pink (red 50%). WLED and LIFX gamma-correct internally and were
fine. The Chaos tab now applies gamma 2.8 before sending RGB to those two:
`building color commands` passes `msg.color` {r,g,b} (hue/sat inputs cleared,
because the subflow prefers them), and `Lamp Pole Param` sends the RGB-on /
WWCW-off calls itself to a new `pole rgb (gamma)` node, bypassing the
`lamp pole` subflow. The shared subflows were NOT changed (a subflow-def edit
needs a full Node-RED deploy), so other callers of those subflows remain linear.

**Cans.** No 1/3 + 2/4 Zigbee groups existed; `rgb A`/`rgb B` targeted entity
lists, i.e. four per-bulb unicasts, and on the weak mesh (LQI 43–76) one bulb
routinely landed ~12 s late. New z2m groups: id 8 `Family room cans 1-3`
(`light.family_room_cans_1_3`) and id 9 `Family room cans 2-4`
(`light.family_room_cans_2_4`). One color → `rgb A` sends to
`light.family_room_can_group`, `rgb B` sends nothing; two colors → one call per
pair group. Groupcasts are not acknowledged/retried, so a bulb that misses one
stays on the old color — the mesh is still the underlying problem.

Artifacts: `diagnostics/chaos-gamma-groups/` (before/after tab, readback,
offline test `t.cjs`). z2m config backup (contains network key) is outside the
repo at `/home/pi/backups/z2m/configuration-before-chaos-groups-20261011.yaml`.
Rollback: `PUT /flow/5521c5218d82b93c` with `chaos-tab-before.json`.
