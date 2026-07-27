# IotaWatt Performance, Retention, and Grafana Migration Plan

Status: Phases 1–3, including Grafana and non-Grafana consumer migrations, were implemented and verified by 2026-07-22. Guarded pruning is enabled; the initial cleanup completed successfully and daily maintenance is active.

Last investigated: 2026-07-22 (America/Denver)

## Objectives

1. Eliminate the five-minute CPU spikes caused by the current IotaWatt rollups.
2. Retain fine-grained data only for a useful recent window.
3. Preserve permanent hourly energy history suitable for daily, monthly, and yearly trends.
4. Make Grafana transparently display recent and historical data in the same panels.
5. Make aggregation and pruning observable, idempotent, and safe to retry.

## Verified findings

### Five-minute CPU spike

The spike is caused by the local Docker Node-RED `IotaWatt` flow, specifically the inject node:

```text
iotawatt_rollup_5m_inject — 5 min DB rollups
```

Its solar branch runs this chain:

```text
IotaWattHoursSolarbyMinute
  -> NetUsage
  -> SolarCostTrend
  -> recent Net calculation
```

During a live sample, MariaDB spent approximately 19 seconds executing the `REPLACE INTO NetUsage ... SELECT` statement. MariaDB used roughly 82–95% of one core while local Docker Node-RED reached approximately 135% CPU. On a four-core host, those processes account for the observed host-level spike.

The Blue Iris Node-RED instance on `192.168.10.49` was not the source of this five-minute event. Its active ingestion schedule is one minute, and the captured long-running statement was the IotaWatt `NetUsage` query.

The user crontab also runs `teslausb-manifest.sh` every five minutes, but it was waiting on an offline Pi and did not consume meaningful CPU during the sample.

### Indexes survived the migration

The relevant live indexes exist:

```text
IotaWatt.Time                         PRIMARY KEY
IotaWatt.Time                         IotaWatt_created_index (redundant secondary index)
IotaWattSolar.Time                    PRIMARY KEY
IotaWattHoursbyMinute.DateDTS         PRIMARY KEY
IotaWattHoursSolarbyMinute.DateDTS    PRIMARY KEY
NetUsage.DateDTS                      PRIMARY KEY
SolarCostTrend.DateDTS                PRIMARY KEY
```

The expensive behavior is an optimizer-plan problem, not a missing-index problem.

MariaDB 11.4.2 plans the current `NetUsage` join as:

```text
IotaWatt       ALL     ~23.8 million rows
IotaWattSolar  eq_ref  one primary-key lookup per IotaWatt row
```

The current CTE boundary is not turned into an indexed range for the driving table. Even adding the equivalent range predicate to both tables while retaining the CTE still produced a full index scan.

This scalar-bound form produces the desired plan:

```sql
WHERE s.Time BETWEEN
      (SELECT MAX(Time) FROM IotaWattSolar) - INTERVAL 15 MINUTE
  AND (SELECT MAX(Time) FROM IotaWattSolar)
```

Verified plan:

```text
IotaWattSolar  range   PRIMARY(Time)  ~181 rows
IotaWatt       eq_ref  PRIMARY(Time)  one row per source row
```

The indexed `MAX(Time)` subqueries are optimized away and are inexpensive.

### Current scale

Approximate current table sizes:

| Table | Rows | Data size |
|---|---:|---:|
| `IotaWatt` | 23.8 million | 1,992 MB plus 238 MB indexes |
| `IotaWattSolar` | 24.7–25.5 million | 935 MB |
| `NetUsage` | 24.4–25.1 million | 1,556 MB |
| `IotaWattHoursbyMinute` | 2.15 million | 164 MB |
| `IotaWattHoursSolarbyMinute` | 2.16 million | 101 MB |
| `SolarCostTrend` | 2.13 million | 69 MB |

The actual one-day source window contains about 16,500 samples per source table.

### Existing retention patterns

The mature existing pattern is the weather retention system documented in `mysql-retention-rotation-guide.md`:

- 14 days of raw `weather` samples.
- Permanent hourly min/max/average rows in `weather_hourly`.
- Idempotent `ON DUPLICATE KEY UPDATE` rollups.
- Batched raw deletion.
- Maintenance logging in `maintenance_runs`.
- Grafana unions recent raw values with historical hourly values.

There is also an older `long_energy` daily archive with 12 energy categories per day from 2022-05-15 through 2025-07-10. It has no active producer, primary key, or confirmed current consumer. It is useful as evidence of the earlier design idea, but it should not be copied as-is.

### Scheduler warning

The existing MariaDB events are enabled in metadata, but the live server reports:

```text
@@event_scheduler = OFF
```

The last weather maintenance ran on 2026-07-03 and the last device-monitoring prune ran on 2026-07-04. The compose file specifies `--event-scheduler=ON`, but the running container command is only `mariadbd`, indicating that the live container was not recreated with the compose command.

Do not depend on an unmonitored MariaDB event scheduler for the new retention system.

Resolution recorded 2026-07-22: weather and DeviceMonitoring retention were migrated to
the guarded Node-RED workflow documented in `mysql-retention-rotation-guide.md`; both legacy
MariaDB events are now explicitly disabled.

### Timestamp warning

The MariaDB container operates in UTC, while the historical IotaWatt `DATETIME` values are local wall-clock times. Retention cutoffs must not use naive database `NOW()` against IotaWatt timestamps.

Use a source-relative cutoff derived from `MAX(Time)`, or use explicit and tested timezone conversion. Preserve current local-time semantics for historical IotaWatt data, including DST validation.

## Phase 1: eliminate the recurring CPU spike

Phase 1 should be completed and verified independently of retention work.

### Implementation record — 2026-07-21

Phase 1 was deployed to the live Docker Node-RED `IotaWatt` tab `8123861fc119b96b` through the Admin API. The final API comparison against the pre-change tab showed exactly four changed fields: the `template` field on these nodes:

```text
afc433ae107c33be    IotaWattHoursbyMinute
3310f8173efb0e83    IotaWattHoursSolarbyMinute
64663cc87deff872    NetUsage
0b2842604f0fc81a    SolarCostTrend
```

The frequent queries now use a 15-minute scalar `MAX(timestamp)` range and `INSERT ... ON DUPLICATE KEY UPDATE`. The existing one-day correction chain after the 00:06 nightly solar import was left unchanged.

`SolarCostTrend.DateDTS` is defined as `TIMESTAMP ... ON UPDATE CURRENT_TIMESTAMP`. Its upsert therefore explicitly includes:

```sql
DateDTS = VALUES(DateDTS)
```

This prevents updates to several historical buckets from trying to move their primary keys to the same current timestamp.

Final `EXPLAIN` results:

```text
IotaWattHoursbyMinute         range PRIMARY, ~181 rows
IotaWattHoursSolarbyMinute    range PRIMARY, ~181 rows
NetUsage IotaWattSolar        range PRIMARY, ~181 rows
NetUsage IotaWatt             eq_ref PRIMARY, 1 row per solar row
SolarCostTrend                range PRIMARY, ~181 rows
Scalar MAX subqueries         tables optimized away
```

Two post-deploy five-minute cycles completed successfully. All four target tables advanced together. In the second execution window MariaDB averaged 0.7% of one core and peaked at 4% in the one-second samples, with no long query or insert backlog. Boundary-aligned result checks found all expected rows (`14/14` completed minute buckets, `181/181` NetUsage rows, and `16/16` SolarCostTrend buckets), with differences limited to the destination column rounding precision. Node-RED logged the scoped tab update and no MySQL errors after the final deploy.

Primary pre-change backups:

```text
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json.backup_before_iotawatt_phase1_20260721_203810
/home/pi/nodered/data/projects/nodered_n100_mini/iotawatt-tab-8123861fc119b96b.backup_before_phase1_20260721_203810.json
```

### 1. Back up and pull the live flow

Follow `nodered-flow-agent-guide.md`.

1. Create a timestamped backup of the current project flow file.
2. Identify the live `IotaWatt` tab through the Admin API.
3. Pull only that tab with `GET /flow/8123861fc119b96b` (verify the ID again at implementation time).
4. Modify and deploy only that tab with `PUT /flow/TAB_ID`.

### 2. Change the five-minute rollup window

Change the normal five-minute rollups from a full day to a 15-minute overlap. Fifteen minutes covers multiple poll cycles and permits small delays without rewriting an entire day.

Use scalar indexed boundaries, not the current CTE boundary, for joined queries:

```sql
WHERE s.Time BETWEEN
      (SELECT MAX(Time) FROM IotaWattSolar) - INTERVAL 15 MINUTE
  AND (SELECT MAX(Time) FROM IotaWattSolar)
```

Apply the same principle to the non-solar source and derived rollups.

### 3. Replace `REPLACE` where practical

Prefer:

```sql
INSERT INTO ...
SELECT ...
ON DUPLICATE KEY UPDATE ...
```

This updates existing primary-key rows without `REPLACE` delete-and-insert behavior.

### 4. Preserve a correction pass

Keep a separate, off-peak correction job that recalculates a wider window after nightly IotaWatt imports. The frequent job should remain limited to 15 minutes; the correction job can safely revisit the last day once per night.

### 5. Verify before deployment

For every revised query:

1. Run `EXPLAIN` against the exact final SQL.
2. Require `range` access on the timestamp-driving table.
3. Require `eq_ref` on the joined timestamp primary key.
4. Reject any plan showing `ALL` or a full `index` scan across a multi-million-row table.

### 6. Validate after deployment

1. Re-read the tab from the Admin API.
2. Check Node-RED logs.
3. Capture the next two five-minute boundaries with process-level CPU and MariaDB process-list sampling.
4. Confirm the output rows match the old calculation for the overlap window.
5. Confirm normal 10-second inserts no longer back up behind the rollup.

## Phase 2: raw retention and permanent hourly history

### Implementation record — 2026-07-21

Phase 2 was implemented additively. No source row was deleted, updated, or compacted. The full reproducible database migration is:

```text
/home/pi/home_config/iotawatt-phase2-hourly-archive.sql
```

The live archive objects are:

```text
IotaWattHourly             37,069 hourly rows
IotaWattSolarHourly        36,632 hourly/derived rows
EnergyPowerHourly           1,269 rows
IotaWattRetentionState          1 safety/configuration row

v_iotawatt_daily
v_iotawatt_solar_daily
v_energy_power_daily
v_iotawatt_pruning_readiness

archive_iotawatt_hours(start,end)
archive_iotawatt_solar_hours(start,end)
archive_energy_power_hours(start_utc,end_utc)
maintain_iotawatt_archive()
assert_iotawatt_pruning_ready()
```

Live inspection showed that a single two-table source cutover would misrepresent the data. The implementation therefore uses three explicit archives:

- `IotaWattHourly` stores legacy local-wall-time circuits. Wh comes from the minute table; raw samples provide true hourly min/max/average, voltage, frequency, and sample counts.
- `IotaWattSolarHourly` stores legacy local-wall-time grid/solar/heat-pump values plus Tesla allocation and the existing tariff-cost calculation.
- `EnergyPowerHourly` stores the newer UTC feed using a UTC primary key plus local bucket, local date, and UTC offset. This preserves both repeated 1:00 a.m. buckets on future fall-back DST days.

EnergyPower provenance is split as follows:

```text
2026-03-05 12:00 UTC through 2026-06-01 12:00 UTC
    89 daily-only Powerwall solar totals
    SourceGrain=daily, StatsQuality=daily_total_only

Beginning 2026-06-02 18:34 UTC
    continuous minute/raw EnergyPower data
    1,180 archived UTC hours at implementation time
    SourceGrain=minute+raw, StatsQuality=raw_15s
```

The daily-only rows are deliberately not presented as real hourly measurements. Their original UTC timestamps and `daily_total_only` provenance remain visible.

The 52-month idempotent backfill completed as maintenance run `42`. Each month was constrained by primary-key ranges and a 60-second statement timeout; observed batches completed in approximately 2.6–9.4 seconds. The archive tables occupied approximately 40 MB plus indexes at implementation time.

Validation results:

- IotaWatt minute-covered history reconciled exactly: `70,879,607.67 Wh` and `2,189,697` minute rows on both sides.
- Every individually tested circuit reconciled exactly. An initial combined nullable-expression comparison was misleading; per-column checks were zero-difference.
- Legacy solar reconciled exactly: grid `67,015,478.92 Wh`, solar `48,705,123.80 Wh`, and heat pump `4,696,118.00 Wh`.
- Raw counts reconciled exactly at the archive boundary: IotaWatt `26,425,860`, IotaWattSolar `25,532,632`, and EnergyPowerRaw `282,560`.
- EnergyPower solar, panel, and battery totals reconciled after the three-day correction pass. Full-history heat-pump difference was `0.0108 Wh`, within accumulated four-decimal hourly storage rounding.
- Selected days and complete months, including March/November DST months, matched their source totals exactly.
- The legacy local `DATETIME` sources already collapsed the repeated fall-back hour and cannot recover it. Spring-forward days correctly contain 23 hours. A synthetic fall-back test proved that `EnergyPowerHourly` preserves 25 distinct UTC buckets and both local 1:00 a.m. labels.
- Existing low-coverage hours remain present and explicitly flagged. They were not silently filled or discarded.
- There were 226 raw-only IotaWatt hours with missing minute rollups. They now use an explicit `SourceGrain=raw` five-second integration fallback, preserving energy that was absent from the old minute table.
- The solar archive labels 1,101 older minute-only hours without raw extrema as `SourceGrain=minute`, and 29 NetUsage/cost-only hours as `SourceGrain=derived`.

Archive-only maintenance was called twice with unchanged row counts and totals, proving idempotence. It completes in about one second and reprocesses the last three days so late source corrections converge.

The live Node-RED maintenance tab is:

```text
97e23c7dbbaa481c    IotaWatt Retention
```

Its saved source is:

```text
/home/pi/home_config/iotawatt-phase2-nodered-flow.json
```

At Phase 2 completion it called only `maintain_iotawatt_archive()` daily at 03:40 America/Denver. An hourly watchdog sent a Pushover alert if the latest success was older than 36 hours, with a 12-hour repeat cooldown. The manual inject path was triggered through the Admin API and produced successful maintenance run `46` in one second. The live tab was re-read and exactly matched the saved JSON; Node-RED logs contained no related errors. Phase 3 later replaced this archive-only call as recorded below.

Pruning was intentionally blocked at this Phase 2 checkpoint. The state at that time was:

```text
GrafanaMigrated=1
ConsumersMigrated=1
PruningEnabled=0
ReadyToPrune=0
```

`assert_iotawatt_pruning_ready()` was tested and correctly raises an error. Raw 14-day and minute 30-day retention values are recorded. Both migration gates now pass, but deletion will not be implemented or enabled until pruning is separately designed, reviewed, and approved.

### Recommended retention tiers

| Tier | Data | Initial retention |
|---|---|---:|
| Raw | `IotaWatt`, `IotaWattSolar`, `EnergyPowerRaw`, recent `NetUsage` | 14 days |
| Minute | Existing `*HoursByMinute` tables | 30 days |
| Hourly | New hourly aggregate tables | Indefinite |
| Daily | SQL view over hourly data | Indefinite |

Thirty days for minute data is a starting recommendation. It remains small enough to be inexpensive while preserving useful recent detail. It can later be reduced to 14 days.

### Hourly table design

Grafana inspection favors two wide hourly tables aligned with the existing source schemas rather than a normalized metric table that would require repeated pivots:

```text
IotaWattHourly
IotaWattSolarHourly
```

Each row represents one complete local hour.

`IotaWattHourly` should include:

- `BucketStart` primary key.
- `SampleCount`, `FirstSample`, `LastSample`, and a coverage indicator.
- For each circuit: hourly `MinW`, `MaxW`, `AvgW`, and `Wh`.
- Voltage and frequency min/max/average; no Wh value for those measurements.
- Provenance/source and `UpdatedAt` fields.

Circuits currently include:

```text
Total
WaterHeater
Furnace
Fridge
Kitchen
ACTotal
DryerTotal
EVTotal
RangeTotal
AllOther
UpstairsBeds
Office
UtilityCloset
ClaireMini
```

`IotaWattSolarHourly` should include equivalent statistics and Wh totals for:

```text
TotalGrid
TotalSolar
TotalHeatPump
```

It should also include the final approved derived energy-balance fields where appropriate:

```text
grid_import_wh
grid_export_wh
solar_self_consumed_wh
tesla_total_wh
tesla_green_wh
cost
```

### Resolve source overlap before backfill

There is overlap between the older IotaWatt sources and the newer `EnergyPowerRaw` / `EnergyPowerHoursByMinute` data. Existing Grafana queries already prefer newer EnergyPower minute rows when present and fall back to IotaWatt history.

Before implementing the archive, record and approve:

1. The reliable cutover date for `EnergyPowerRaw`.
2. The canonical source for solar, total load, grid, heat pump, battery, and EV values after that date.
3. The meaning of `TotalGrid` (total consumption versus utility-grid flow).
4. The intended import/export and tariff calculations.

Hourly rows should retain source/provenance so historical changes are explainable.

### Backfill strategy

1. Create the hourly tables and indexes without enabling deletion.
2. Backfill energy Wh primarily from the existing minute tables rather than rescanning all raw rows.
3. Sum minute Wh values into hourly Wh.
4. Derive historical average watts from minute energy where necessary.
5. Treat historical min/max from minute data as minute-average extrema unless raw backfill is explicitly justified.
6. Use newer EnergyPower data after its approved cutover and IotaWatt history before it.
7. Use idempotent upserts so the backfill can be resumed or rerun.

### Validation gates

Before any deletion:

1. Compare hourly-derived daily kWh with current minute-derived daily kWh.
2. Validate total load, solar, EV, AC, heat pump, and major circuits.
3. Validate selected days, complete months, and full-history totals.
4. Establish an acceptable rounding tolerance; energy totals should otherwise match.
5. Check sample counts and coverage for every archived hour.
6. Test local DST days containing 23 or 25 hours.
7. Verify no gaps or overlap at the old/new source cutover.
8. Verify Grafana recent, historical, and boundary-spanning ranges.

The old tables remain untouched until all validation gates pass.

### Maintenance procedure

Keep the data-local work in stored procedures, but trigger the daily call from a visible Node-RED maintenance flow unless the MariaDB event scheduler is repaired and monitored.

The maintenance procedure should:

1. Derive an hour-aligned cutoff from source `MAX(Time) - INTERVAL 14 DAY`.
2. Aggregate complete hours older than that cutoff.
3. Upsert hourly rows.
4. Verify every raw hour being removed has a corresponding hourly row and matching sample count.
5. Abort pruning if aggregation or validation fails.
6. Delete raw rows in primary-key order and bounded batches.
7. Pause briefly between batches.
8. Log start, finish, status, rows aggregated, rows deleted, cutoff, and error details to `maintenance_runs`.
9. Use a SQL exception handler so failures are logged rather than leaving a permanent `running` record.

Add a watchdog that alerts when the most recent successful maintenance run is older than 36 hours.

### Initial pruning

The initial cleanup is materially larger than steady-state daily maintenance.

1. Take a verified backup first.
2. Prune one table at a time.
3. Delete 25,000–100,000 rows per batch in timestamp-primary-key order.
4. Set a maximum row count or wall-clock duration per maintenance window.
5. Monitor CPU, I/O, locks, query latency, and backup impact.
6. Stop automatically if validation or health thresholds fail.
7. Do not run `OPTIMIZE TABLE` automatically. Deleted InnoDB space becomes internally reusable; physical compaction requires a large table rebuild and should be a separately reviewed maintenance operation.

## Phase 3: guarded pruning rollout

### Implementation record — 2026-07-22

Phase 3 is implemented, verified, and enabled. The reproducible database and Node-RED artifacts are:

```text
/home/pi/home_config/iotawatt-phase3-pruning.sql
/home/pi/home_config/iotawatt-phase3-nodered-transform.jq
/home/pi/home_config/iotawatt-phase3-nodered-flow.json
```

Before deletion, a full single-transaction dump of `hubitat_logging`, including routines, events, and triggers, was created and verified with both `sha256sum -c` and `zstd -t`:

```text
/home/pi/backups/iotawatt-prune-20260722_203533/hubitat_logging_pre_prune.sql.zst
SHA-256: bf38f1be61d479f677ea45d3e21e9f05a279f2f84bafc433ab834f963d09054d
Compressed size: 1,745,718,774 bytes
Uncompressed size: 12,253,635,910 bytes
```

The migration added per-table validation, dry-run and bounded-delete procedures, `IotaWattPruneAudit`, prune timestamps/status in `IotaWattRetentionState`, and a daily archive-then-prune wrapper. Deletion requires all three independent gates and exact archive validation. The live state after rollout is:

```text
GrafanaMigrated=1
ConsumersMigrated=1
PruningEnabled=1
ReadyToPrune=1
RawRetentionDays=14
MinuteRetentionDays=30
```

The first dry run exposed two validator edge cases rather than data loss: historical solar-minute hours that legitimately used raw heat-pump fallback when the minute source was `NULL`, and tiny double-rounding differences in raw heat-pump averages. The validator was narrowed to the actual archive semantics and tolerance, then rerun across all eight tables. The approved dry run reported zero missing and zero mismatched archive groups. The two earlier `blocked` audit rows are preserved as evidence of the guard working; no execute run failed.

The initial cleanup removed exactly 83,010,745 source rows:

| Table | Rows deleted | Fixed cutoff | Runtime |
|---|---:|---|---:|
| `EnergyPowerRaw` | 207,459 | 2026-07-09 02:00 UTC | 2 s |
| `SolarCostTrend` | 2,109,768 | 2026-06-22 20:00 local | 16 s |
| `EnergyPowerHoursByMinute` | 28,964 | 2026-06-23 02:00 UTC | <1 s |
| `IotaWattHoursbyMinute` | 2,148,026 | 2026-06-22 20:00 local | 18 s |
| `IotaWattHoursSolarbyMinute` | 2,151,863 | 2026-06-22 20:00 local | 16 s |
| `NetUsage` | 24,852,725 | 2026-07-08 20:00 local | 173 s |
| `IotaWattSolar` | 25,308,818 | 2026-07-08 21:00 local | 159 s |
| `IotaWatt` | 26,203,122 | 2026-07-08 21:00 local | 282 s |

Every table finished with zero remaining eligible rows, zero missing archive groups, and zero mismatched archive groups. The cleanup used 25,000-row timestamp-primary-key batches and did not run `OPTIMIZE TABLE`. Deleted InnoDB pages are available for reuse, but filesystem free space is not expected to increase unless physical compaction is separately approved.

Post-prune validation covered the real read paths after the old source rows were physically absent:

- All 46 migrated Grafana SQL targets returned frames for recent and January 2024 ranges.
- Seven representative boundary-spanning compatibility-view queries returned data successfully.
- A complete Grafana scan found zero direct reads from pruned source tables.
- Rebuilt Monthly Trends cells matched the unified view exactly: 54 solar and 54 electricity cells, with `0.0000 Wh` maximum delta.
- The Tesla recent-history query still returned seven complete qualifying days.
- Current ingestion and five-minute derived data continued advancing with no retention-related MariaDB or Node-RED errors.

The steady-state wrapper was then run directly and through the live Node-RED tab `97e23c7dbbaa481c`. Both completed successfully. The Node-RED tab now calls `maintain_iotawatt_retention()` daily at `03:40 America/Denver`; its hourly watchdog checks both archive and enabled-prune success and alerts after 36 hours. The final Admin API re-read exactly matched the saved Phase 3 flow. Primary rollback copies are:

```text
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json.backup_before_iotawatt_phase3_20260722_211115
/home/pi/nodered/data/projects/nodered_n100_mini/iotawatt-retention-tab-97e23c7dbbaa481c.backup_before_phase3_20260722_211115.json
```

The last manual end-to-end Node-RED execution was maintenance run `66`, from `2026-07-23 03:12:39` to `03:12:41` UTC. Its archive child run `67` and all eight prune audits succeeded. A subsequent audit found 24 successful execute records, zero execute failures, and zero anomalies in the latest eight-table run.

### Validator precision hotfix — 2026-07-26

The daily wrapper failed safely on 2026-07-25 and 2026-07-26 because the
`EnergyPowerRaw` validator independently rounded a source average and its
`DECIMAL(18,4)` archive value to two decimal places. For the
`2026-07-10 19:00:00 UTC` bucket, the source `PanelUsageW` average was
`6314.794979` and the stored archive value was `6314.795000`; their actual
difference was only `0.000021 W`, but independent rounding produced
`6314.79` versus `6314.80`.

The live `validate_iotawatt_prune_table()` routine and the reproducible
`iotawatt-phase3-pruning.sql` source now use null-safe absolute-difference
checks with a `0.01 W` tolerance for the three `EnergyPowerRaw` averages.
Before deployment, all 62 eligible archived hours were confirmed within
`0.000050 W`, with none exceeding `0.01 W`.

After the scoped routine update, all eight tables validated with zero missing
and zero mismatched archive groups. The manual Node-RED maintenance run
`106` completed from `2026-07-26 23:58:06` to `23:58:09 UTC`; archive run
`107` and prune audits `65` through `72` all succeeded, leaving zero eligible
rows. The routine backup is:

```text
/home/pi/home_config/iotawatt-retention-routines-backup-20260726_1732.sql
```

## Grafana unified current-and-history views

### Implementation record — 2026-07-21

The Grafana migration was deployed and verified. Seven strict-boundary database views now combine permanent hourly history with retained current data:

```text
v_iotawatt_power_all
v_iotawatt_solar_power_all
v_iotawatt_energy_all
v_iotawatt_solar_energy_all
v_energy_power_energy_all
v_solar_cost_trend_all
v_iotawatt_solar_accounting_all
```

The reproducible view/backfill SQL and dashboard transformation are:

```text
/home/pi/home_config/iotawatt-grafana-compatibility-views.sql
/home/pi/home_config/grafana-iotawatt-migration.jq
```

`SolarSelfConsumedWh` was backfilled from the original sample-level `NetUsage` condition before pruning, and `archive_iotawatt_solar_hours` now maintains it. This preserves the Solar Utilization numerator at hourly grain rather than approximating it from hourly averages.

Five dashboards were backed up and changed through the Grafana API. Only the SQL of the affected targets changed; dashboard layout, panels, transformations, variables, and datasource metadata were preserved:

| Dashboard | UID | SQL targets | New version |
|---|---|---:|---:|
| IotaWatt Energy | `aebnyja8531moa` | 13 | 4 |
| IotaWatt Live | `cebnyj1w2a680f` | 12 | 6 |
| Solar | `debnyj9w02t4wa` | 11 | 3 |
| Thermostat | `cebnyj6gvkzk0a` | 3 | 4 |
| Trends | `aebnyjaoxeha8b` | 7 | 9 |

Rollback backups, intended POST payloads, API save responses, re-read live dashboards, and query-validation responses are in:

```text
/home/pi/home_config/grafana-dashboard-backups/iotawatt-migration-20260721_220356
```

The separate `IotaWatt Double Pole Test` dashboard reads its own verification table and was not changed. A post-deploy scan of every live Grafana dashboard found zero remaining direct reads of `IotaWatt`, `IotaWattSolar`, the minute tables, `EnergyPowerHoursByMinute`, `NetUsage`, or `SolarCostTrend`.

All 46 changed targets succeeded through Grafana's datasource API for recent and archive-only ranges. Seven representative queries spanning the 14-day raw and 30-day minute boundaries also succeeded. Validation examples:

```text
January 2024 IotaWatt total Wh:       exact match
January 2024 solar Wh:                exact match
January 2024 solar cost delta:        -$0.000057
January 2024 self-consumed Wh delta:  +0.0003 Wh
2026-06-10 EnergyPower solar Wh:      exact match
Recent IotaWatt energy:               exact match
```

MariaDB plans use timestamp-primary-key `range` access in the applicable archive/current branch, with indexed scalar `MAX()` boundaries optimized away. No migration-related Grafana or MariaDB error remained after validation. The safety state at this pre-prune checkpoint was:

```text
GrafanaMigrated=1
ConsumersMigrated=1
PruningEnabled=0
ReadyToPrune=0
```

No telemetry had been deleted or pruned at this checkpoint.

### Verified dashboards and API access

Live Grafana is reachable at `http://127.0.0.1:3001` and reported version 11.5.0 during inspection.

The unauthenticated API returned both dashboards with `canEdit=true` and `canSave=true`:

| Dashboard | UID | Default range |
|---|---|---|
| IotaWatt Energy | `aebnyja8531moa` | Last 24 hours |
| IotaWatt Live | `cebnyj1w2a680f` | Last 1 hour |

No dashboard writes were performed during planning.

### Compatibility views

Do not embed a large union independently in every Grafana panel. Create database views that provide stable query contracts:

```text
v_iotawatt_power_all
  historical IotaWattHourly AvgW rows
  UNION ALL
  current IotaWatt raw rows

v_iotawatt_solar_power_all
  historical IotaWattSolarHourly AvgW rows
  UNION ALL
  current IotaWattSolar raw rows

v_iotawatt_energy_all
  historical IotaWattHourly Wh rows
  UNION ALL
  current IotaWattHoursbyMinute rows

v_iotawatt_solar_energy_all
  historical IotaWattSolarHourly Wh rows
  UNION ALL
  current IotaWattHoursSolarbyMinute rows
```

Each view must enforce one strict boundary:

```sql
historical_timestamp < boundary
current_timestamp   >= boundary
```

The boundary can be derived from the minimum timestamp retained in the current table, which is an inexpensive primary-key lookup, or stored explicitly in a retention-state table and advanced only after successful aggregation and pruning.

Test each view with `EXPLAIN` to ensure Grafana time filters are pushed into indexed branches. If MariaDB materializes a view or fails to push down predicates, retain the views as simple branch contracts and place branch-specific `$__timeFilter` predicates in the dashboard SQL.

### IotaWatt Energy mapping

The dashboard currently contains:

- Stat panels for total usage, Tesla, AC, heat pump, furnace, dryer, and all other.
- A power breakdown time series.
- Usage-by-hour bars.
- Total power and cumulative-energy series.

Migration mapping:

- Stat totals -> energy compatibility views.
- Usage by Hour -> energy compatibility views.
- Breakdown -> power compatibility views.
- Current/total power -> power compatibility views.
- Cumulative Wh -> energy compatibility views.

Wh is additive, so a result containing recent minute Wh and historical hourly Wh remains mathematically valid for sums and cumulative windows.

The dashboard's stat panels use Grafana's `lastNotNull` reducer. They will continue to show the newest current value when the selected time range includes both hourly history and raw/minute data.

### IotaWatt Live mapping

The dashboard currently contains:

- Current usage, voltage, and frequency stats.
- Circuit bar gauges.
- Circuit and solar power breakdowns.
- Calculated amperage series.
- Combined watts, voltage, frequency, and solar series.

Migration mapping:

- Recent period -> five-second raw data.
- Historical period -> hourly average watts.
- Voltage/frequency history -> hourly averages.
- Amps -> the same 120 V / 240 V calculations applied to hourly average watts.
- Optional future enhancement -> display hourly min/max as a historical band.

The default one-hour view remains entirely raw, so its normal live behavior does not change. Longer ranges automatically transition to hourly history.

### Grafana deployment workflow

Only perform this after the hourly tables, views, and validation are complete:

1. `GET /api/dashboards/uid/aebnyja8531moa`.
2. `GET /api/dashboards/uid/cebnyj1w2a680f`.
3. Save timestamped backups of both returned dashboard JSON documents.
4. Modify only the target SQL strings that reference IotaWatt data.
5. Preserve dashboard UIDs, panel IDs, layout, transformations, variables, and folder metadata.
6. Save with `POST /api/dashboards/db` using the current dashboard version and overwrite semantics.
7. Re-read both dashboards and verify version increments.
8. Test a recent-only range, a historical-only range, and a range spanning the retention boundary.
9. Validate sums against direct SQL checks.
10. Inspect Grafana and MariaDB logs for errors or unexpectedly expensive plans.

Grafana migration is a mandatory gate before pruning historical raw or minute rows used by existing panels.

## Node-RED and other consumer migration

### Implementation record — 2026-07-22

The only long-history non-Grafana consumer requiring a query migration was the live Docker Node-RED `Monthly Trends` tab:

```text
7b4ea8c23b72358e    Monthly Trends
```

The tab was pulled and deployed through the Node-RED Admin API. Exactly four fields changed, replacing `IotaWattHoursSolarbyMinute` with `v_iotawatt_solar_energy_all`:

```text
f418849c7b171125    Solar Build the query       func
b3c1445085023a40    Solar Distinct Years        template
7739ac6bbacc8aa2    Electricity Build the query func
d10e75b26972d98d    Electricity Distinct Years  template
```

The saved post-migration tab and primary rollback backups are:

```text
/home/pi/home_config/iotawatt-consumer-monthly-trends-flow.json
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json.backup_before_iotawatt_consumers_20260722_081146
/home/pi/nodered/data/projects/nodered_n100_mini/monthly-trends-tab-7b4ea8c23b72358e.backup_before_iotawatt_consumers_20260722_081146.json
```

Preflight found the old and unified sources produced the same 51 available year/month buckets with zero solar or grid Wh difference. After the scoped deploy, the live tab was re-read and exactly matched the saved JSON. The manual solar branch was injected through the Admin API, which rebuilt both `SolarMonthlyTracking` and `ElectricityMonthlyTracking`. All 54 completed month/year cells in each resulting table matched the unified view exactly with a maximum delta of `0.0000 Wh`.

The recent-only consumers were validated and deliberately left on the retained fine-grained tables:

- `Tesla Auto Tonight` uses a 10-day minute-data lookback to select seven complete local days. Its read-only production query returned seven qualifying days; minute retention remains 30 days.
- The three-minute `NetUsage` calculation returned all 36 expected five-second samples, spanning 175 seconds with zero source lag.
- IotaWatt and EnergyPower ingestion and rollups are producers of retained current data, not historical consumers.
- The live MQTT sensors consume current flow messages rather than database history.

The host Node-RED instance on port 1881, Blue Iris Node-RED at `192.168.10.49`, host scripts/cron, and MariaDB events/triggers had no additional consumers. An old `IotaWattSolar` subflow definition contains legacy one-day SQL but has no deployed instances; it is a cleanup candidate, not a runtime consumer or pruning blocker.

Node-RED logged the scoped `Monthly Trends` update and no related MySQL or runtime errors. The safety state at this pre-prune checkpoint was:

```text
GrafanaMigrated=1
ConsumersMigrated=1
PruningEnabled=0
ReadyToPrune=0
```

No telemetry had been deleted or pruned at this checkpoint.

### Review outcome

The completed review covered:

- Node-RED `Monthly Trends` queries reading `IotaWattHoursSolarbyMinute`.
- Node-RED `Tesla Auto Tonight`, which reads ten days of recent energy data.
- The recent three-minute `NetUsage` automation.
- IotaWatt live MQTT sensors.
- Grafana dashboards: IotaWatt Energy, IotaWatt Live, Solar, Thermostat, and Trends.
- IotaWatt Double Pole Test uses a separate verification table and is likely unaffected.

The ten-day Tesla query and three-minute automation remain on recent data when raw/minute retention is at least 14 days. Monthly and long-range consumers should move to hourly data or a daily view over hourly data.

## Execution order

Implementation steps 1–8 and 10–11 are complete. Step 9 is ongoing post-rollout observation; step 12 remains an optional, separately reviewed maintenance decision.

1. Review and approve this plan.
2. Implement and verify Phase 1 query changes.
3. Decide source semantics, EnergyPower cutover, and retention durations.
4. Create hourly tables, retention-state/audit structures, and procedures.
5. Backfill hourly history without deleting anything.
6. Validate totals, coverage, timezone behavior, and source cutover.
7. Create and `EXPLAIN` compatibility views.
8. Migrate Grafana and Node-RED historical consumers.
9. Observe the new read path for several days.
10. Enable daily aggregation with maintenance-health alerting.
11. Begin gradual, guarded pruning.
12. Review whether physical table compaction is worth a separate maintenance window.

## Approved configuration

```text
Raw retention:             14 days
Minute retention:          30 days
Hourly retention:          indefinite
Daily representation:      SQL view over hourly rows
Frequent rollup overlap:   15 minutes
Wide correction pass:      once nightly
Maintenance trigger:       Node-RED calling stored procedures
Maintenance cutoff:        source-relative, local-hour aligned
Failure alert threshold:   36 hours since last successful run
Pruning:                   guarded, audited, and bounded
```
