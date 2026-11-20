# Emporia Vue 3 commissioning and circuit additions

## Current state — labeled deployment, 2026-09-27 evening

This section supersedes the earlier commissioning configurations below.
All 16 branch identities were established by manual events; see
`emporia-vue3/circuit-map.csv` and `emporia-vue3/manual-tests-20260927.md`.

The deployed config retains raw internal readings and stable numbered API
sensors. Labeled consumption copies apply the observed active-load sign, clamp
negative noise to zero, and double power only for inputs 2 Dryer, 3 AC, 4
Range Oven, 11 EV Charger and 12 Claire Mini Split. Current is never doubled.
Branch references remain BLACK/L1 provisionally; sign agreement does not prove
physical leg. Single-leg doubling is an estimate for mixed 120/240 V appliances,
not a calibration or proof of balanced legs. No IotaWatt-derived calibration used.

All branches have labeled 1-second power/current and independent 60-second
averages. Existing Fridge entity IDs and its daily energy are preserved. Other
branch energy counters are not introduced by this deployment. The existing
Fridge Daily Energy counter resets at firmware reboot (`restore: false`).
Mains A/B have positive consumption power copies (1x), with their existing current
entities; signed diagnostics are retained. No whole-home total or subtraction
balance is introduced, and outdoor/service-entry loads remain outside these mains.

New fast entity IDs end `_live` and match the existing active recorder exclusion.
The old Fridge Power and Fridge Current retain their explicit exclusions.
Minute averages remain eligible for history. 69 duplicate commissioning entities
(numbered branch streams, mains signed power and old input-1 signed reference)
were disabled through HA's entity-registry WebSocket API, leaving the native
ESPHome raw stream available to the commissioning logger. Raw sensor identities
and historical meanings are preserved; no old signed/one-leg history is rescaled.
In particular, input 10 was moved from the UPS circuit to dishwasher around
16:02–16:05 MDT; its earlier readings remain UPS data.

No HA restart or Node-RED change is part of this deployment. Existing automations
still use their previous sources; source migration, elapsed-time gates and service
call deduplication are explicitly phase two. See the consumer audit.

Firmware and prior YAML backup are recorded in
`emporia-vue3/labels-20260927/deployment.json`. The native verification, HA registry
snapshots, recorder checks and credential-redacted diff live in that directory.
`emporia-vue3/labeled-streams.json` maps raw ports to labels, sign and multiplier.

## Device and source

- YAML: `/home/pi/esphome/config/emporia-vue3.yaml` (contains credentials; do not commit).
- Ethernet/API: `192.168.10.226`, device `emporia-vue3`, HA already paired.
- Builder: container `esphome-esphome-1`, ESPHome 2025.8.3.
- External component pinned to `emporia-vue-local/esphome` commit
  `79e2747371b70370b96ef919f7c1e556f461344a` for reproducibility.
- Pre-commissioning YAML and firmware:
  `/home/pi/esphome/config/backup_vue3_20260926_093324/`.
- User reports split-phase voltage connections L1/L2, with L3 and neutral
  connected to neutral, and one branch CT in the first 1/2 connector.
  No main CT measurements have been configured.

## Commissioning configuration

L1 (BLACK) and L2 (RED) voltage, line frequency and L2 phase angle are exposed.
Input 1 was provisionally identified as the fridge: six simultaneous samples were
about -150 W signed on Vue versus 154–155 W on IotaWatt. Input 2 was unused and
produced phantom power/current, so it was removed after the diagnostic upload.

Final configuration exposes `Fridge Power`, `Fridge Current`, `Fridge Daily Energy`
and `Input 1 Signed Power L1 Reference`. The corrected internal power uses
`max(-x, 0)` on unthrottled samples; published watts average five seconds, and
energy publishes once per minute in kWh. Daily energy resets at midnight and
reboot (`restore: false`); it begins with this deployment, not earlier today.

**Provisional mapping:** physical leg and circuit identity still need independent
confirmation (e.g. matching a fridge load transition). The L1 reference with sign
inversion agrees within about 3% at the observed running load. Do not interpret
negative power as proof of L2: CT orientation also affects sign. No calibration
was tuned to force agreement with a single operating state. L3 is not configured
as an energized phase. No main CTs or total-house energy are configured.

| Vue input | Label | Reference / correction | Status |
| --- | --- | --- | --- |
| 1 | Fridge | BLACK/L1, invert sign, 1x | Provisional; ~150 W versus IotaWatt ~154 W |
| 2–16 | Unused | None | Omitted |

IotaWatt at `192.168.40.211` identifies `Fridge` on channel 11, a 50 A CT with no
multiplier. `GET /config.txt` and `GET /status?inputs=yes` provide its configuration
and live watts. HA entity: `sensor.iotawatt_fridge_power`. The `phase: 1` field is
CT phase correction, not L1. Its single voltage reference and automatic reversal
cannot establish the physical Vue L1/L2 mapping. Initial reading was about 141 W.

Sources:
- https://emporia-vue-local.github.io/docs/tutorial/configuration/
- https://docs.iotawatt.com/en/master/split-phase.html
- https://docs.iotawatt.com/en/master/CTconfig.html

## Build, OTA and verify

Back up the YAML and last successful firmware before each change. Keep build
parallelism at `esphome.compile_process_limit: 2` on this shared host.

```bash
docker exec esphome-esphome-1 esphome config /config/emporia-vue3.yaml
docker exec esphome-esphome-1 esphome compile /config/emporia-vue3.yaml
docker exec esphome-esphome-1 esphome upload /config/emporia-vue3.yaml --device 192.168.10.226
docker exec esphome-esphome-1 timeout 25 esphome logs /config/emporia-vue3.yaml --device 192.168.10.226
```

Use `esphome clean /config/emporia-vue3.yaml` only if stale build artifacts cause
errors. The first commissioning build had a duplicate ESP-IDF efuse target; clean
resolved it. PlatformIO registry was unreachable during the clean build, so the
already cached exact libraries (`noise-c` 0.1.10 and `libsodium` 1.10020.7) were
copied from another build into this device's `.piolibdeps/emporia-vue3` directory.

For a simultaneous one-minute comparison without credentials in command output:

```bash
docker cp /home/pi/home_config/emporia-vue3/compare.py esphome-esphome-1:/tmp/vue3-compare.py
docker exec esphome-esphome-1 timeout 80 python /tmp/vue3-compare.py
```

The script reads the API key from the mounted YAML and prints sensor values only.
Verify device reconnect, plausible voltages/frequency, no I2C errors, and HA entity
updates after every upload. OTA uses Ethernet; another serial flash is unnecessary
while the device remains reachable.

## Adding circuits

1. Record the Vue numbered input, circuit label, voltage, physical L1/L2 mapping,
   CT orientation and comparison source. A paired connector has two independent
   CT inputs; do not add or sum both for one CT.
2. Add only installed inputs under `ct_clamps`. Set each `phase_id` to the verified
   voltage reference. Keep signed diagnostics during commissioning.
3. Compare watts against a known reference over changing load states. Confirm
   that both meters are measuring the same conductor/circuit before calibration.
4. After confirming mapping/sign, give the raw power sensor a stable internal ID
   and no name. Apply the verified sign correction there. Expose a `copy` sensor
   with `throttle_average: 5s` and a clear circuit name.
5. If energy is wanted, integrate the unthrottled corrected power, not its five-
   second display copy. For `total_daily_energy`, add Home Assistant time, use
   `restore: false`, publish every 60 s, and multiply Wh by 0.001 for kWh. Daily
   energy resets at midnight and device reboot with this policy; it is not a
   persistent lifetime counter.
6. Validate, compile, OTA, read logs and compare again. Update the circuit map here.

Do not automatically apply a 2x multiplier: a 120 V fridge needs none. Record
240 V circuits separately; circuits with unequal leg loads need both measurements.
Do not add total-house/balance calculations until main CTs are installed and verified.

## Deployment verification — 2026-09-26

Both diagnostic and final configurations validated, compiled and uploaded over
Ethernet successfully. Final device build reports `Sep 26 2026, 15:41:27` (UTC).
HA discovered and recorded live states for:

- `sensor.emporia_vue3_fridge_power` (~150 W)
- `sensor.emporia_vue3_fridge_current` (~1.78 A)
- `sensor.emporia_vue3_fridge_daily_energy` (first minute ~0.002518 kWh)

Initial six-sample comparison: Vue mean 149.89 W, IotaWatt mean 154.00 W,
Vue 2.67% lower. Final samples varied around 150 W versus 152–157 W. Voltage
references were ~123 V and ~180 degrees apart. Reported Vue frequency was
59.79 Hz versus IotaWatt ~60.02 Hz; no frequency calibration was attempted.
No I2C/component failure appeared in captured post-OTA logs. One 64 ms API
operation warning occurred while connecting diagnostic clients.

`emporia-vue3/commissioning-20260926.jsonl` preserves the initial comparison;
`emporia-vue3/final-20260926.jsonl` preserves final measurements.
Temporary input-2 diagnostic entities may remain unavailable in HA's entity
registry after removal from firmware; they are not part of the active configuration.

## One-second trial preparation — 2026-09-27

Prepared firmware retains the existing Fridge Power / Fridge Current entity IDs,
but changes their display averaging to 1 second, along with the signed diagnostic.
New Fridge Power 1m Average and Fridge Current 1m Average sensors independently
average the raw internal streams for 60 seconds. Energy still integrates corrected
unthrottled power and publishes every 60 seconds. Voltage diagnostics remain at
5 seconds. Only branch input 1 is configured; no main CT inputs are enabled.

HA `configuration.yaml` now excludes these exact IDs from recorder:

- `sensor.emporia_vue3_fridge_power`
- `sensor.emporia_vue3_fridge_current`
- `sensor.emporia_vue3_input_1_signed_power_l1_reference`

The future naming pattern `sensor.emporia_vue3_*_live` is also excluded. All minute
averages, daily energy, and existing IotaWatt entities remain eligible for recording.
Existing energy helpers and appliance controls continue using their IotaWatt sources.
Do not retarget HA integration helpers to the 1 Hz stream without addressing their
own derived write frequency.

Backups:
- `/home/pi/esphome/config/backup_vue3_1sec_20260927_085703/` (YAML and old firmware)
- `/home/pi/homeassistant/config/configuration.yaml.before_vue3_1sec_20260927_085703`

Deployment order: validate/build → user-approved HA restart → verify recorder
exclusions → OTA → API cadence and HA database verification. Preparation alone does
not activate recorder filters or change the installed firmware. **HA restart still
requires user approval.** Use `verify-cadence.py` inside the ESPHome container for a
130-second native API cadence sample; follow with a bounded recorder query to
confirm live entities produce no new state rows while minute/energy rows do.

### One-second trial deployed and verified

User approved the HA restart, performed at 2026-09-27 14:59:46 UTC. HA returned
RUNNING; live API readings advanced while excluded recorder entries stopped at
14:59:45 UTC and voltage entries resumed. Only after that check, the compiled
firmware (`Sep 27 2026, 14:58:16`) uploaded successfully over Ethernet.

A 130-second native API observation received 131 reports each for fridge watts,
current and signed watts: median interval **1.005 s**, maximum **1.097 s**.
HA REST observations independently confirmed one-second state updates. Minute
averages produced an initial cached/partial report followed by ~60-second intervals;
energy produced ~60-second updates. The recorder query spanning this test found
**zero new state rows** for all three fast entities and new rows for both minute
averages and daily energy. HA state discovery can add startup/unavailable rows in
addition to the periodic measurements.

Evidence:
- `emporia-vue3/cadence-check-20260927.jsonl`
- `emporia-vue3/ha-live-check-20260927.jsonl`
- `emporia-vue3/recorder-check-20260927.json`

No Vue-derived HA integration helpers exist, and IotaWatt automation sources and
Node-RED flows were not changed. This verifies initial cadence/storage behavior,
not a 24–48 hour stability run or independent calibration. Daily energy restarted
from zero at OTA as expected (`restore: false`). Physical leg and fridge circuit
identity remain provisional pending load-transition confirmation.

Startup logs reported recovery of an unfinished recorder session, and warnings
for existing non-Vue energy-helper statistics/units. Recorder resumed normal writes;
those unrelated helper issues were not modified in this deployment.

## Full-panel commissioning — 2026-09-27

User installed 16 branch CTs plus two mains CTs and clarified that the IotaWatt CTs
are removed: no simultaneous reference readings are available. User confirms the
original fridge CT has not moved: **input 1 is Fridge**. Preserve those existing
fridge entities. Inputs 2–16 remain numbered until historical-pattern matching or
controlled load changes confirm their identities.

Prepared firmware enables A/B and 1–16. Every port has Signed Power Live and
Current Live (1-second averages) plus separate 1m Average power/current sensors.
The recorder exclusion `sensor.emporia_vue3_*_live` already covers all new fast
entities. Existing fridge exclusions remain. No HA restart is required for this
expansion, and no Node-RED automation source is migrated by it.

Branch power uses BLACK/L1 provisionally. Main A uses BLACK/L1 and B uses RED/L2
provisionally. The signed measurements deliberately preserve orientation/reference
uncertainty. No other branch energy counters, absolute-value totals or main total
are published until mapping/signs are verified. Individual current measurements
and power patterns support mapping without pretending the physical leg is known.

Mapping table: `emporia-vue3/circuit-map.csv`. Appliance labels and physical phase
are separate fields. Do not infer L1/L2 solely from signed power.

A Vue-only commissioning capture runs separately from HA recorder, collecting one
snapshot per second with value ages and connection state. It stops after 48 hours
or 512 MiB, whichever comes first; hourly compressed JSONL files are retained for
analysis. Samples older than 10 seconds or received while disconnected are marked
null rather than silently reused. No IotaWatt endpoints are polled. Its historical
data remains the comparison source, not an accuracy standard.

Collector source: `emporia-vue3/capture-circuits.py`. Runtime copy lives at
`/home/pi/esphome/config/vue3-commissioning/capture-circuits.py` and uses the
existing ESPHome container's native API client. It is a bounded commissioning task,
not a permanent background service; a container restart will stop it.

When recording a manual load test, record UTC/local time, appliance, action and
which numbered traces responded. Leave ambiguous shared circuits/pairs provisional.
The older IotaWatt automation paths are not automatically migrated just because
new Vue entities appear; source switching remains a separate step after mapping.

### Full-panel deployment result

Final firmware compiled successfully (`Sep 27 2026, 16:05:51` UTC) and OTA upload
succeeded. HA discovered 36 new live entities and 36 minute-average entities;
all live entities had numeric values. After the first minute, recorder held zero
rows for the new live sensors and one fresh measurement for each of the 36 minute
averages. Input 1/Fridge remained about 149 W. Initial signed mains readings were
approximately -566 W (A) and -756 W (B); these are commissioning observations,
not calibrated or phase-verified house totals.

48-hour capture began 2026-09-27 16:06:45 UTC; expected stop around
2026-09-29 16:06:45 UTC, earlier if the 512 MiB limit is reached. Directory:

`/home/pi/esphome/config/vue3-commissioning/20260927T160216Z/`

- `metadata.json`: source/reference assumptions and known label (1=Fridge).
- `status.json`: latest heartbeat, connected state, count, time remaining.
- `samples-YYYYMMDDTHH.jsonl.gz`: one-second snapshots with per-value age.
- `process.log`: reconnect/error messages.
- `finished.json`: produced when capture exits normally.

Active gzip files can lack their final trailer; read their flushed prefix using
`zlib.decompressobj(31)` or wait for hourly rotation. Data is retained after the
capture ends. To stop early, identify only this collector PID inside the ESPHome
container and terminate it; do not restart HA or the whole container.

Pre-expansion YAML and firmware backup:
`/home/pi/esphome/config/backup_vue3_all_cts_20260927_095926/`.
No HA restart and no appliance/EV automation source changes were made for expansion.

Full-load native API cadence check (130 seconds): all 36 new live sensors delivered
130 reports each, median interval 1.004 s and maximum interval 1.107 s.
Evidence: `emporia-vue3/all-cts-cadence-20260927.jsonl`.

## Labeled deployment verification — 2026-09-27 17:10 MDT

Build `Sep 27 2026, 23:07:30` UTC uploaded successfully over Ethernet.
36 live power/current streams each delivered 130 reports in 130 seconds;
median interval 1.005 s, maximum gap 1.110 s. 1,805 aligned active-power
and current comparisons with raw streams had zero scaling error. Some
loads were idle during verification; historical active sign checks covered
all branches, and the five multiplier definitions were inspected in YAML.
All 72 labeled live/average measurement states were numeric in HA with
correct power/current units. 69 duplicate diagnostics disabled via registry
API, leaving 81 enabled device entities including diagnostics/status/energy.
After the discovery grace period, recorder had zero new rows across all
36 live measurement entities; all 36 minute averages had new history.
The bounded commissioning collector reconnected and continued capturing
72 live/diagnostic values including voltage/frequency/angle.
No HA restart, Node-RED deploy, automation-source change, or IotaWatt
calibration was performed. Log client reported host multicast membership
warnings, but direct-IP API connection and handshake succeeded; no
device I2C/component failure appeared in the bounded observation.

## Phase two live controls — 2026-09-27 evening

The user subsequently authorized live deployment without shadow mode. Range/oven,
WLED, dryer, dishwasher (both existing status helpers), EV policy input/command
gates, and Energy MQTT/SQL now use the Vue adapters. The dashboard current-power
breakdown lists all sixteen circuits and the separate outdoor heat pumps. Total
home energy uses minute-average Vue mains plus the outdoor source. Per-circuit
cumulative energy/daily-kWh migration remains separate. No HA restart occurred.

See [phase-two implementation and verification](emporia-vue3/phase2/README.md)
for timing, freshness, recorder checks, backups, and diagnostic endpoints.

## Daily energy and reporting migration — 2026-09-27 evening

All sixteen circuit daily counters are now connected: ten existing pairs preserved
their totals/IDs and six new pairs cover newly identified circuits. The HA Power &
Energy view, built-in Energy device list, custom Today breakdown, five Grafana
dashboards, SQL circuit ingestion, Monthly Trends and EV historical demand inputs
were updated. See [the complete energy migration record](emporia-vue3/energy/README.md).
No HA restart occurred. Today remains partial; earlier gaps were not backfilled.
