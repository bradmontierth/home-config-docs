# Phase-two power migration — LIVE, 2026-09-27

Implemented at the user's explicit request **without shadow mode**. The old main
IotaWatt is not a failover source. No Home Assistant restart was performed.

The earlier design is preserved in [proposal-20260927.md](proposal-20260927.md).
Its offline/shadow statements describe that earlier proposal, not the running system.

## Live consumers

Five existing Node-RED tabs on port 1880 were updated via per-tab Admin API PUTs:
IotaWatt (`8123861fc119b96b`), Washer Dryer (`0711e9167e62e249`), Dishwasher
(`ee0f1588760b9b3b`), Tesla (`7fa25727b15db1f0`), Energy MQTT Sensors
(`afea81d223c193eb`). Each was re-read and checked after deployment.

- Range/oven: Vue Range Oven watts drive Range, OvenOn, the existing toe-kick WLED
  actions, and oven_status. Range activates at 500 W; its off dwell is 60 seconds.
  OvenOn requires >100 W for 180 seconds and releases after five minutes below
  the legacy 500 W off threshold. Away/bedtime reminders and Ecobee's fan gate
  continue to use the same globals. Outputs occur on indicator transitions.
- Dryer: 50 W for 60 seconds qualifies running; 35 seconds below 50 W qualifies
  completion after a running state. The existing completion-helper polarity,
  recent-door guard, laundry notifications and lighting remain in place. During
  a confirmed outage, >=100 W for ten seconds emits the existing dryer warning;
  low power <20 W for two minutes or restored grid rearms it.
- Dishwasher: the correctly identified dishwasher CT drives the legacy status
  (>40 W for 30 seconds, <10 W for three minutes, ten minutes observed running
  before clean). The richer running/drying/clean classifier is also LIVE, on its
  existing separate dishwasher_new helper. Four-minute time-weighted windows
  replace sample counts; its features run every five seconds. Existing contact,
  temperature, dirty-state and announcement branches remain. Service gates cover
  all existing writers to these two helpers.
- Washer: retains the smart-plug, movement and contact sources. The shared
  washer/bidet CT is displayed but is not substituted for a dedicated washer source.
- EV: Vue mains A + mains B + separate outdoor heat-pump power form total home;
  subtract Vue EV watts once. Branch power is already scaled in ESPHome. Existing
  ASAP, low-SOC recovery, tonight, TOU, SOC hysteresis, two-minute solar qualification
  and twenty-minute solar run policies remain. Missing SOC cannot become zero and
  falsely trigger recovery. Normal evaluation is every five seconds. Override and
  outage events can evaluate immediately.
- Compatibility globals AC, Furnace, Tesla and dishwasherPower refresh at five
  seconds. The separate Tesla-not-charging alert requires a fresh Vue EV reading;
  its outdoor IotaSolar excess calculation and notification limits remain.
- Energy MQTT/SQL: fresh Vue mains plus separate outdoor load replace old main
  Iota total. The existing 15-second effective-usage and EnergyPower SQL cadence
  remains. Discovery is retained and sent once per node deployment, not per sample.
  The EnergyPower calculation uses one fresh shared snapshot for panel, solar,
  outdoor load and signed Powerwall flow.

Main-Iota polling, the old latest-row control/discovery fanout, and its recovery
watchdog were detached. Separate outdoor IotaSolar ingestion and historical data
were preserved. The old main-Iota raw SQL series is not populated with fabricated
Vue rows or renamed columns. Older standalone reference paths remain on the tabs
but cannot invoke the selected Vue charging policy.

## Timing, freshness, and actuation

HA native ESPHome state events are ingested as they arrive. A selected-source HA
REST template snapshot every five seconds supplies last_reported timestamps even
when values do not change. This bypasses stale unchanged-value caches. There is
one cheap local one-second deadline clock, not one service call per entity per
second. Per-node runtime lives in the persistent JavaScript sandbox's globalThis,
not the per-message Node-RED node wrapper and not high-frequency disk context.

Appliance input older than 20 seconds is invalid: it does not mean zero power or
completion. Invalid data resets qualification; healthy observation is needed to
resume. Runtime windows are bounded in memory. Restarting a tab resets observed
runtime, so an in-progress dishwasher cycle must earn its clean qualification
again. This intentionally avoids inventing runtime during a gap.

For EV balance, the adapter retains sixty seconds of reports and integrates every
source over the **same five-second interval**, ending at the oldest latest source
report. All inputs must still be fresh and the whole window covered. Slower
Powerwall/outdoor reporting therefore adds latency without repeatedly resetting
solar qualification merely because its timestamp differs from the 1 Hz CTs.

Appliance action gates deduplicate by semantic target and acknowledged result,
retry failures at ten-second intervals up to three attempts, and expire old
power-derived intents after 45 seconds. Opposing new intents replace pending
ones; no delayed FIFO of old readings is played back.

EV writes are serialized, require acknowledgement, and are sent only when the
requested override changes. Normal changes have a ten-second minimum interval.
Outage/TOU/stale-solar stops bypass that cooldown while respecting the in-flight
request (three-second request timeout). Read-only /override reconciliation runs
every 30 seconds and permits a corrective write on mismatch. Automation-disable
yields control; a known outage retains its absolute stop priority. An automation-
owned solar session with invalid required inputs holds for 30 seconds, then stops.
Unknown grid state cannot start charging. Confirmed manual charging intents retain
their policy behavior without requiring solar balance data.

Installed OpenEVSE gateway v5.0.0/controller 8.2.2 was queried read-only. The exact
v5.0.0 source shows auto_release tied to session completion, not a timed lease;
unchanged charging commands do not need periodic keepalive writes. Recovery reserve
writes also use acknowledgement and a per-recovery-cycle deduplication key.

## Dashboard, total energy, and recorder

`/home/pi/dashboard_webapp/app/config.py` now displays all sixteen correctly named
Vue circuits plus the separate outdoor heat pumps. Current usage uses the new
`sensor.emporia_vue3_home_power_live`, published every 15 seconds from fresh mains
and outdoor readings, expiring after 45 seconds without valid updates. All 17
breakdown sources were verified present in HA. Only the dashboard container was
rebuilt/recreated; HA and Node-RED were not restarted.

The existing Total Home and Heat Pump helper was updated through HA's supported
options-flow API to sum the two **one-minute mains averages** plus outdoor
heat-pump power. Its entity ID and downstream integral/daily helper are preserved.
This avoids driving that integral from 1 Hz mains. The total helper may also update
when the slower outdoor input changes; it is not itself strictly one-minute.

Active recorder exclusions continue to cover all Vue *_live entities and the
original fridge Power/Current IDs. Verification after cutover found zero live
measurement rows, history for all 36 minute-average entities, and one recorded
running transition per dishwasher helper. The new live-total entity matches the
same exclusion glob. See [live/recorder-verification.json](live/recorder-verification.json).

**Subsequent energy migration is now complete.** Individual circuit integrals/daily
counters, HA Lovelace and Energy device views, the custom Today breakdown, Grafana
and MySQL consumers were migrated in the following task. See
[the energy migration record](../energy/README.md) for source mapping, history
boundaries, verification and remaining legacy-only circuits. Meter phase/scaling
assumptions remain those recorded during commissioning; this is not calibration.

## Verification and operations

- 33 reference-model tests, plus 8 production-function/wiring tests passed.
  Production tests model Node-RED's fresh node wrapper on each invocation and
  cover mixed-cadence windows, overrides/outage/TOU, stale solar, missing SOC,
  recovery, serial commands, retries, appliance dwell and rich-classifier startup.
- Live dishwasher_status changed to running at 17:47:40 MDT and dishwasher_new at
  17:47:48. Both were driven by the actual running dishwasher, not synthetic input.
- OpenEVSE acknowledged the selected disabled override and readback agreed;
  subsequent identical decisions produced no extra writes during observation.
- Final continuous observation: 28 post-warmup checks over approximately 140 seconds
  had valid aligned EV tuples with zero invalid windows. OpenEVSE remained at one
  successful write, one ACK, zero errors and matching readback throughout. See
  [live/cadence-verification.json](live/cadence-verification.json).
- All selected power sources were fresh. Five-second snapshot reconciliation was
  necessary to avoid missing unchanged reports between slower source updates.
- Logs showed no new Vue function/service errors after corrections. Existing
  unrelated Blue Iris polling errors remain. A charger GET reset occurred during
  the initial Tesla-tab restart; subsequent command ACK/readback succeeded.
- Complete new range/dryer/appliance end-of-cycle behavior was tested in simulation;
  not every physical appliance was exercised during deployment.

Read-only Node-RED diagnostics:
`GET http://127.0.0.1:1880/vue3-power/status` and
`GET http://127.0.0.1:1880/vue3-ev/status`.
They report source ages, appliance transitions, aligned EV tuple and command counts.

Private pre-change and deployed tab snapshots, plus the dashboard source backup,
are in `/home/pi/home_config/.local/vue-phase2-20260927_172732` (excluded locally
from Git). They may contain pre-existing embedded credentials and must remain
private. Source adapters/builders/tests are in `live/`. Builders target those
commissioning snapshots; future edits must start from a fresh per-tab Admin API GET,
not blindly regenerate old tab versions. Deploy checks refuse unexplained live drift.

To undo, use per-tab Admin API PUTs from the private backups, then re-read and check
logs. Restoring old Iota controls will not restore measurements from removed CTs.
The total helper's prior sources were sensor.iotawatt_total_power and
sensor.iotawattsolar_total_heat_pump_power. Do not edit deployed flows.json or
restart HA as a substitute for API changes.

## 2026-09-30 fix: mid-session 6 A parking

Symptom: with PW >90 % and ~4 kW surplus the charger sat at 6 A for exactly
2 minutes at a time (TeslaMate `charges`: 13:26, 13:43, 13:57). Cause: the
policy re-applied the 120 s solar *start* qualification during a running
session; one invalid telemetry tick reset it — observed trigger 13:56–13:57
and 14:03 was the outdoor IotaWattSolar heat-pump input (reports every
~16–21 s against the 20 s limit → 'aligned window warming / outdoor'; Powerwall
solar at 12–15 s is also close). The reset then fell through to the 6 A hold. PW in the
89–91 % hysteresis band did the same indefinitely. Fix: a running solar
session follows live surplus while PW ≥ stop threshold; the qualification
gates only starts. Stale-telemetry safety stop lengthened 30 s → 90 s to avoid
needless relay cycles. Deployed via Tesla-tab PUT 13:59:14; files, replay test
and deploy script in [live/fix-20260930](live/fix-20260930/).
