# Live power consumer audit — 2026-09-27

**Historical baseline:** phase two was subsequently deployed live, without shadow
mode, at the user’s request. Current behavior and verification are documented in
[phase2/README.md](emporia-vue3/phase2/README.md). The inspection below describes
the pre-migration flows.

Read-only inspection of both local Node-RED Admin APIs (`1880`, `1881`), with
GET /flow for IotaWatt, Dishwasher, Washer Dryer, Tesla, and Energy MQTT Sensors.
Also inspected HA YAML and stored helper configurations and dashboard source.
No deployed changes, device commands, notifications, or restarts were performed.
This is code-path verification plus isolated function replay, not an end-to-end
1 Hz deployment test. Arbitrary external MQTT subscribers were not enumerated.

## Current source and fan-out

On port 1880, IotaWatt tab `8123861fc119b96b` inject `2481552dfbd0a209` polls
every 10 seconds. `9f5e2674dbf4b715` requests a rolling 30-second window grouped
into 5-second readings. The CSV output branches to SQL storage and
`25f87fb94d765e9b` (latest complete row). Thus automation messages normally arrive
at 10-second cadence, not 5 seconds. That row fans out to:

- Dryer: `payload.col8` = DryerTotal, link `48cca974a2626b6a` → `533102b1284e532f`.
- Range: `payload.col10` = RangeTotal, link `c43787f970a9f90a` → `031caeda013785ee`.
- Dishwasher: `payload.col6` = Kitchen; sets `global.dishwasherPower` and sends a
  numeric payload after a 1-second delay through `375b2343b573e62b` → `b470598e126b7d11`.
- `global.Tesla` from col9, `global.AC` from col7, `global.Furnace` from col4.
- `c1df37dc6271e886` publishes individual retained HA MQTT sensor states and
  discovery. Its config output has a direct MQTT connection as well as a gated
  branch: do not reuse this fan-out for every 1 Hz update.

Port 1881 has a manually injected IotaWatt query example, with no repeat schedule
or startup injection; no additional scheduled power consumer found there.

## Consumers and 1 Hz readiness

### Range / oven / toe-kick WLED

`e150d598f34fd51f` actually tests **500 W**, despite its '50 watts' name.
It sets `Range`, latches `RangeLights`, selects red WLED preset from UnderUnderCab,
sends to `192.168.30.120/json`, and sets `input_boolean.oven_status`.
Low power starts a 60-second Range-off and 5-minute OvenOn-off timer; high power
resets them. Existing RangeLights latch avoids repeated lighting-on commands.

`12057a28cc4a9f11` stores every timestamp above 100 W in `global.OvenStatus` and
sets OvenOn after 180 seconds. This measures elapsed time, but its array grows
without a bound until power falls below 100 W. Replace with a single start time
and explicit freshness handling before increasing message rate.

Indirect consumers: Away Lights Off and Bedtime Routine read `global.Range` for
oven reminders. Ecobee Master's `11ea46a27b5ca06a` reads `global.OvenOn` as a reason
to run the circulation fan. Preserve these meanings during migration.

### Dishwasher

The source currently named **Kitchen** is treated as dishwasher power; the
custom dashboard also labels that entity Dishwasher. Confirm circuit mapping.

Two parallel detectors exist:

- Legacy state: >40 W starts a 30-second running timer; <10 W cancels it and can
  start a 3-minute completion timer. A 10-minute run-enough latch gates clean.
  Contact and temperature branches also participate in status/announcements.
- `29bbcc343e24ac09` ('theMagic') drives `input_select.dishwasher_new` using rolling
  statistics and sample-count debouncing. It hardcodes `SAMPLE_SECS = 5`, although
  the actual source normally arrives every 10 seconds. Its named 4-minute window
  is 48 samples (about 8 minutes today, 48 seconds at 1 Hz). Fast running uses six
  high samples; the 120-minute lookback is also sample-count based.

Not ready for an unmodified 1 Hz input. Convert windows and dwell to sample
 timestamps/elapsed time, define gap behavior, and emit service calls only on
state transitions. The newer classifier currently returns a state every sample,
with branches that can call HA services repeatedly for the same state.

### Dryer / laundry indicators / outage warning

Washer Dryer tab `0711e9167e62e249` uses col8, a 50 W threshold, a 1-minute high
 timer and 35-second low timer, with resets and latches. Timers are elapsed-time
based. High power turns `input_boolean.dryer_status` OFF, while completion turns
it ON unless the recent-door guard suppresses it: this helper acts as a completion
indicator, not a simple running Boolean. Door contact and laundry LED/notification
consumers must keep their existing semantics. Washer has its own source path.

`dryer_outage_detector` alerts after **two samples >=100 W** during a grid outage;
rearm requires <20 W for 2 minutes. At 1 Hz its initial alert would become much
faster. Replace the two-sample rule with an intentional elapsed-time dwell and
freshness/gap checks. Preserve a separate path for prompt outage decisions.

### EV charging control and alerts

Tesla tab `7fa25727b15db1f0`, state trigger `42b634721e8471ed`, watches
`sensor.iotawatt_ev_charging_power` plus overrides, Powerwall SOC and grid outage.
Current-state nodes fetch solar, SOC, charger power, and total home load.
`b0b4f535c19d2ed7` calculates:

`available watts = Powerwall solar - (total home including heat pump - EV watts)`.

It retains start/run hysteresis (120-second solar qualification, 20-minute minimum
solar run), but its normal output directly posts to OpenEVSE `/override` through
`d8423915df88b573`. No command deduplication/rate gate is present on that route.
One-second measurement must not imply one-second HTTP commands. Cache live values,
check source timestamps, evaluate at a deliberate cadence (e.g. 5–10 seconds), and
send changed targets with a minimum command interval/limited keepalive. Grid outage
and manual disable should bypass the normal wait. Verify controller lease/keepalive
requirements before choosing the final interval.

Current cached inputs have no per-source age limit. A 1 Hz EV value mixed with
older total-home/solar readings can briefly corrupt the subtraction. Use matched
measurement windows or explicit age/skew constraints. HA total-home is the sum
of main IotaWatt total and IotaWattSolar heat-pump power; 16 branch CTs do not cover
the full panel and cannot substitute for the main total.

Separate IotaWatt-tab 'Tesla not charging' alert reads `global.Tesla` and
`global.solarExcess`, has a 5-minute trigger and 12-hour rate limit. Preserve or
explicitly migrate both sources rather than changing only the HA EV entity.

## History and derived consumers

- 16 HA integration helpers directly consume IotaWatt/IotaWattSolar entities;
  an additional integration consumes Total Home and Heat Pump. Utility meters
  then consume these energy helpers. Retargeting them to 1 Hz power can cause
  derived energy states to update frequently even when raw power is excluded.
  Prefer device-integrated energy published every 60 seconds; retain recorder
  history for energy and minute-average watts, not live copies or fast derivatives.
- Energy MQTT Sensors polls total-home/Powerwall every 15 seconds, publishes
  effective usage and writes EnergyPowerRaw; heat-pump input is IotaWattSolar.
- Existing IotaWatt SQL raw ingestion, five-minute rollups, hourly archives,
  Grafana/monthly trends remain separate. Tesla Auto Tonight uses seven days of
  historical total/non-EV energy, not a one-second trigger.
- `/home/pi/dashboard_webapp/app/config.py` names IotaWatt sensors for the live
  breakdown (including Kitchen labeled Dishwasher), and total-home for usage.
- No IotaWatt references found in HA automation YAML or searched home-agent /
  voice-orchestrator source. HA UI-configured helpers do contain the dependencies
  above. This is not proof of absence in remote systems or external subscribers.

## Isolated replay evidence

Ran the exact retrieved dishwasher and dryer-outage function bodies in a Node.js
VM with synthetic timestamps and mocked Node-RED context/output. No live services
were reachable from the replay. Constant high load starting at t=0 produced:

| Input interval | Dishwasher first running | Dryer first outage alert |
| --- | --- | --- |
| 10 seconds (current nominal) | t=50 s | t=10 s |
| 5 seconds (classifier assumption) | t=25 s | t=5 s |
| 1 second (proposed) | t=5 s | t=1 s |

These are elapsed times after the first sample, not physical load-to-alert latency.
Reproduce with `node emporia-vue3/audit-20260927/replay.js`.

## Recommended migration boundary

ESPHome 1-second averaged power → HA native API live entities → Node-RED source
adapter (circuit, watts, source, timestamp, availability) → appliance-specific
state machines and command gates. Recorder exclusions do not remove live HA state
or automation events. HA state-change events are not guaranteed every second when
a value is unchanged; dwell must use clocks, and health must use availability /
last report rather than treating last value change as a heartbeat.

Keep IotaWatt and Vue identities separate for comparison; explicitly select one
source per decision path. Do not merge two writers into the same MQTT topics or
allow both to actuate. Do not reuse the SQL/discovery fan-out as the 1 Hz ingress.
Feed separate 60-second history/energy outputs to recording consumers. Handle
unknown/unavailable explicitly, never convert missing power to zero.

Before actuation, run the Vue classifiers in shadow mode with bounded transition
logs, compare behavior across appliance cycles and dropouts, then switch one
consumer at a time via scoped Node-RED Admin API deployment. HA restart for recorder
configuration requires prior user approval. None is requested or performed by
this audit.

References:
- https://www.home-assistant.io/integrations/recorder/
- https://www.home-assistant.io/integrations/esphome/

## Offline phase-two proposal prepared later on 2026-09-27

See `emporia-vue3/phase2/README.md`, reference logic, tests and migration plan.
Fresh Admin API inspection confirms washer has its own smart-plug/motion/contact
path; do not replace it with the shared washer/bidet CT. EV automation-disable
yields control; only grid outage overrides it with an active stop. Earlier generic
wording about a manual-disable bypass must not be read as a forced-stop command.
No phase-two live changes were deployed.
