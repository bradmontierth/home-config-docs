# Daily energy, HA dashboards, Grafana and SQL — deployed 2026-09-27

This migration is live. Home Assistant was not restarted. Configured helpers and
Lovelace/Energy preferences were changed through HA APIs; Node-RED changes used
per-tab Admin API GET/PUT/re-read; Grafana changes used its HTTP API.

## Daily counters and dashboards

Ten existing integration helpers now consume their matching Vue **Power 1m Average**
entities: fridge, dryer, AC, range/oven, furnace, water heater, dishwasher, EV charger,
Claire mini split, and office. Their cumulative values, entity IDs, units and daily
utility-meter connections were preserved. The before/after snapshot confirmed all
ten cumulative totals and daily totals were exactly unchanged at the switch.
Existing integration methods were preserved; no timer was added to extrapolate
energy during missing reports. Installed HA also handles unchanged state reports,
so a steady minute-average load still advances the counter.

Six new cumulative/daily pairs cover microwave, washer/bidet/laundry, garage,
master bedroom/closet, basement bathroom, and loft/kids bathroom/whole-house fan.
The old Upstairs Beds and Utility Closet counters remain historical; they were
not assigned to unrelated new circuits. `ha-migration-result.json` records the
complete 16-circuit entity map and the changed helper options. Dishwasher energy
friendly names were corrected while retaining the old `kitchen_energy` IDs.

The existing HA view at `/dash-with-sections/iotawatt` is now **Power & Energy**.
Its existing URL was preserved. It displays both mains, total home including the
outdoor heat pumps, solar, battery flow, all sixteen circuits, daily energy,
minute-average mains history, both supply voltages and frequency. All retired
main-Iota sensor references in this Lovelace dashboard were replaced. HA's built-in
Energy device list now contains these sixteen energy counters plus the outdoor
heat pump counter; its grid and solar source settings were preserved.

The custom kitchen/dashboard application's Today list was also migrated to all
sixteen circuits plus outdoor heat pumps, matching its Current list. It obtains
energy units from the entities. Only that dashboard container was rebuilt.

**Today is a partial migration day.** Existing counters continued from their old
values and new counters began at creation. Earlier gaps were not retroactively
filled. No energy was fabricated to make today's totals look complete.

## MySQL ingestion and history contracts

Database: `hubitat_logging` in `mariadb-mariadb-1`.

- `VuePowerMinute`: one row per channel/minute, eighteen power channels plus L1/L2
  voltage and frequency. A unique `(BucketStartUTC, Channel)` key makes retries
  idempotent. Power rows store the observed 60-second average and `W / 60` Wh.
  `ReportedAtUTC` preserves the actual source report timestamp. Diagnostic rows
  have no Wh value and cannot create empty energy-only buckets.
- The collector checks a selected HA snapshot every ten seconds but writes only
  new minute reports. It rejects unknown/stale values and the initial partial
  windows after a firmware reboot. SQL acknowledgement commits its deduplication
  state; a failed insert can retry. It never applies another 240 V multiplier.
- `VuePowerHourly`: scheduled permanent hourly archive with average values,
  observed seconds and summed Wh. Re-running an hour updates rather than doubles
  it. Existing retention jobs/tables were preserved; this migration deletes no
  history and introduces no new raw-data pruning.
- Existing `EnergyPowerRaw` and `EnergyPowerHoursByMinute` continue the previously
  migrated 15-second full-home/solar/Powerwall feed. Circuit logging is not 1 Hz SQL.

`v_iotawatt_power_all_legacy` and `v_iotawatt_energy_all_legacy` preserve the original
history contracts. Their public compatibility views append the new Vue series,
with a strict cutover boundary and local-time representation for existing SQL
consumers. Old tables were not populated with synthetic IotaWatt rows.

Canonical Grafana contracts `v_home_power_all` and `v_home_energy_all` use UTC
and expose the newly measured circuits as separate columns. New Vue timestamps
remain UTC directly, avoiding an ambiguous UTC→local→UTC round trip at the autumn
DST change. Old local-time history is converted using America/Denver. Old Upstairs
Beds/Utility Closet columns are NULL in new Vue rows, not invented zero readings.
The new remainder subtracts all sixteen measured branches from the two mains;
it may include measurement differences and its coverage differs from old Other.

The first circuit SQL bucket is **2026-09-28 00:12 UTC / September 27 18:12 MDT**.
The full-home feed was already verified in phase two; its first complete migration
minute is **2026-09-27 23:48 UTC / 17:48 MDT**. `whole-home-views.sql` uses that explicit
boundary for current household consumption, Powerwall solar and outdoor load.

The old NetUsage/SolarCostTrend ETL required an exact-time join to the removed main
IotaWatt. It had stopped at 09:14 local time and was disconnected. Its historical
rows remain. Active solar accounting/cost reports now use normalized energy views
backed by the fresh EnergyPower feed. Flat-rate energy-value estimates retain the
existing $0.05/$0.10 per-kWh model; panels are labeled as estimates, not battery-aware
utility bills. Direct solar/load overlap is a minute-level estimate after cutover.

Tesla Auto Tonight's historical demand query now reads the extended energy view
and requires measured total/EV values for coverage qualification. Monthly Trends'
four solar-energy query references now use the updated local-calendar contract.
Their decision/reporting policies were otherwise retained.

## Grafana

Five active dashboards were migrated, keeping their UIDs and existing layouts:

- Home Energy: `/d/aebnyja8531moa/home-energy`
- Home Power: `/d/cebnyj1w2a680f/home-power`
- Solar: `/d/debnyj9w02t4wa/solar`
- Thermostat: `/d/cebnyj6gvkzk0a/thermostat`
- Trends: `/d/aebnyjaoxeha8b/trends`

Grafana is at `http://192.168.10.217:3001`. The new **Home Energy (UTC)** datasource,
UID `vue-energy-utc`, is non-default and uses the existing DB service account.
Only the migrated energy targets use it. The original default datasource and
unrelated weather/thermostat queries keep their existing settings. Daily/monthly
energy grouping explicitly uses Denver calendar boundaries and UTC plot timestamps.
The separate double-pole hardware-test dashboard is retained and labeled **Legacy**.

## Verification

- All 16 cumulative/daily pairs are numeric and the migrated cumulative totals
  are nondecreasing. The initial ten existing pairs retained exact values.
- All 18 SQL power values matched their corresponding HA minute average for the
  same report timestamp, within the database's six-decimal precision.
- Recorder observation over five minutes: **zero live Vue measurement rows**;
  each of the 32 cumulative/daily counters recorded five updates.
- All **46 migrated Grafana SQL targets** passed for a recent two-hour range and
  January 2024: **92 successful, nonempty query results**. Queries were tested via
  Grafana's datasource API, not only directly as a DB administrator.
- Two collector tests cover units/deduplication, failed-write retry, stale inputs
  and startup partial-window rejection. An isolated temporary-table SQL test
  confirmed two 600 W minutes archive as 20 Wh/120 observed seconds, with no
  doubling on a repeated archive run. Production data was untouched by that test.
- All deployed dashboards/tabs were re-read from their APIs. Dashboard app health
  reports HA connected. Phase-two EV diagnostics still show one successful write,
  one acknowledgement, zero errors and correct TOU stop/readback.

Evidence: `verification.json`, `grafana-verification.json`, `archive-test.txt`,
`ha-states-after.json`, and the deployment results. Existing unrelated Blue Iris,
WLED-host reachability and Radon file errors were not changed by this migration.

## Maintenance and rollback

Private originals/deployed snapshots are in
`/home/pi/home_config/.local/vue-energy-20260927_180527` (locally Git-excluded).
This includes helper options, Lovelace/Energy preferences, changed Node-RED tabs,
Grafana dashboards, original SQL view definitions and custom dashboard config.
Full flow snapshots can contain pre-existing credentials: keep this directory private.

Apply SQL source files in order: `schema.sql`, generated `views.sql`, then
`whole-home-views.sql`. The final file intentionally replaces the temporary legacy-
only wrappers with the explicit whole-home cutover contracts. Builders reference
commissioning backups; future edits must start from fresh live API snapshots.
`local-calendar.py` produces the Denver grouping refinements for Trends/Thermostat.

Rollback uses the corresponding HA/Grafana/Node-RED APIs and saved view definitions;
never edit deployed flows.json or HA .storage directly. Restoring retired IotaWatt
sources would freeze live readings again, because its CTs are removed. Preserve
newly collected Vue rows and counter history even if rolling back UI changes.

### Grafana panel routing correction (2026-09-27 evening)

The initial migration updated target datasources but missed panel datasources. Home
Power consequently ran UTC-view queries through the old fixed -07:00 MySQL
session. Reproducing Breakdown with that source returned zero circuit rows and
only historical Total/HeatPump, matching the reported symptom. With the UTC source,
all 16 branches plus remainder and fresh Total/HeatPump returned.

`grafana-panel-routing.py` repairs panel routing across all five migrated dashboards.
The Thermostat panel combining gas and electric costs uses Mixed, retaining the
previous effective old datasource for gas. Other non-energy panels are untouched.
Browser-local display timezone remains appropriate; no manual time-range shift is
needed. Snapshots are in the private energy workdir's `panel-routing-*` directory.
The Amps panel now divides every new branch's watts by nominal voltage and explicitly
labels the result estimated current, not measured RMS current.

The migration generator now applies the same repair. The validator reads deployed
dashboards and checks their effective panel routing instead of forcing the UTC
source. All 92 recent/archive checks passed after repair. A separate last-hour
check confirmed all 16 branch series plus remainder, with fresh data less than two
minutes old; see `grafana-panel-routing-verification.json`. No HA restart occurred.

### Kitchen kiosk refresh (2026-09-27 20:05 MDT)

After the webapp image rebuild, the existing Chromium page retained its original
bootstrap configuration. WebSocket reconnect replaces sensor values but does not
reload entity mappings; obsolete Iota values could therefore remain frozen beside
old circuit labels. Reopened Chromium on display-pi using its existing
`/usr/local/bin/kitchen-display-chromium` launcher. Server logs confirmed a fresh
`/api/bootstrap` request and live WebSocket from 192.168.10.92. A screenshot of the
actual kiosk showed Connected and usage 2.3 kW, replacing the reported frozen
5.6 kW. The served breakdown contains 16 Vue circuits plus outdoor heat pumps,
without Utility Closet. Future entity-mapping deployments must also reload the
kiosk page; a backend restart alone is insufficient. No HA restart was needed.
