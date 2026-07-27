# MySQL Retention / Rotation / Historical Tables

Reference for the data-retention scheme on the **`hubitat_logging`** database. The point of
this scheme is to stop "write-once, read-never" telemetry from filling the disk while keeping
the long-term history that's actually useful.

_Last verified: 2026-07-22._

## Where it runs

- **Host:** this device, `192.168.10.217` (the old `.250` mini PC was decommissioned; all tables
  were migrated here). This is also the MySQL all the Grafana dashboards now point at
  (datasource `n100mini`, uid `eebnvy9j8hmgwb`).
- **Container:** `mariadb-mariadb-1`, defined in `/home/pi/mariadb/docker-compose.yml`.
  The live container currently reports `event_scheduler=OFF`. Weather/device retention no
  longer depends on the MariaDB event scheduler; it is called and monitored by Node-RED.
- **Database / creds:** `hubitat_logging`, user `hubitat_logger` (root pw and user pw are in the
  compose file).

```bash
# quick shell into the DB
docker exec -it mariadb-mariadb-1 mariadb -uhubitat_logger -p hubitat_logging
```

## Guarded weather and device retention

The production entry point is:

```sql
CALL maintain_weather_device_retention();
```

The live Docker Node-RED tab is:

```text
c518fa408132989c    Weather & Device Retention
```

It calls the procedure daily at **03:20 America/Denver**. An hourly watchdog sends a
Pushover alert if a complete successful run is more than 36 hours old, if pruning is
disabled, or if the safety state is not ready.

The legacy MariaDB events remain present for provenance but are explicitly `DISABLED`:

Confirm their disabled status and historical last-execution timestamps:

```sql
SELECT EVENT_NAME, STATUS, INTERVAL_VALUE, INTERVAL_FIELD, STARTS, LAST_EXECUTED
FROM information_schema.EVENTS WHERE EVENT_SCHEMA='hubitat_logging';
```

| Legacy event | Last execution | Current status |
|---|---|---|
| `ev_maintain_weather_retention` | 2026-07-03 03:20 UTC | `DISABLED` |
| `ev_prune_device_monitoring` | 2026-07-04 03:00 UTC | `DISABLED` |

Every complete Node-RED-triggered run is logged to **`maintenance_runs`** as
`maintain_weather_device_retention`. Every archive, validation, and prune stage is also
logged to **`WeatherRetentionAudit`**. Check recent activity:

```sql
SELECT job_name, started_at, finished_at, status, rows_affected, note
FROM maintenance_runs ORDER BY id DESC LIMIT 20;

SELECT *
FROM WeatherRetentionAudit
ORDER BY id DESC LIMIT 20;
```

### Implementation record — 2026-07-22

The prior events looked enabled in metadata but silently stopped because the live MariaDB
container was restarted without the compose file's newer `--event-scheduler=ON` command.
The weather job had not run since July 3.

The guarded rollout:

- Archived and exactly validated 9,120 missing weather hour/metric groups.
- Removed 1,693,499 weather rows beyond the 14-day boundary.
- Removed the 1,762,010-row initial DeviceMonitoring backlog, plus newly eligible rows
  encountered by the final steady-state tests.
- Left zero weather rows beyond the fixed 14-day cutoff.
- Validated all remaining source groups with zero missing or mismatched archives.
- Successfully ran the wrapper directly and through Node-RED.
- Successfully ran all 41 weather SQL targets across 11 Grafana dashboards for both
  archive-only and recent ranges after physical pruning.

Reproducible implementation and live-flow copy:

```text
/home/pi/home_config/weather-device-guarded-retention.sql
/home/pi/home_config/weather-device-retention-nodered-flow.json
```

Primary Node-RED rollback backup:

```text
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json.backup_before_weather_retention_20260722_213538
```

Grafana validation requests and responses:

```text
/home/pi/home_config/grafana-dashboard-backups/weather-retention-post-prune-20260722_213713
```

The verified full database backup taken immediately before the IotaWatt/weather retention
work contains every weather row eligible in this cleanup:

```text
/home/pi/backups/iotawatt-prune-20260722_203533/hubitat_logging_pre_prune.sql.zst
```

### 1. Weather — downsample, validate, then prune

`maintain_weather_device_retention()` calculates one fixed UTC cutoff, snapped to the
top of the hour at `UTC_TIMESTAMP() - INTERVAL 14 DAY`, then:

1. Upserts every eligible source group into `weather_hourly`.
2. Validates every source `name`/hour against the archive: sample count, minimum,
   maximum, average, first timestamp, and last timestamp.
3. Aborts with a SQL error if any archive group is missing or mismatched.
4. Deletes raw weather in complete UTC-hour units. Whole-hour progress preserves exact
   revalidation and safe resume after interruption.
5. Records the cutoff, eligible rows, archive groups, mismatches, deleted rows, remaining
   rows, status, and runtime in `WeatherRetentionAudit`.

Net result — the boundary is clean, **no overlap and no gap** (rollup writes `created < cutoff`,
prune deletes `created < cutoff`, same cutoff):

| Table | Grain | Retention | Columns of interest |
|-------|-------|-----------|---------------------|
| `weather` (raw) | every sample | **last 14 days only** | `name, value, created` |
| `weather_hourly` (aggregate) | 1 hour per `name` | **full history (back to 2021)** | `name, bucket_start, sample_count, min_value, max_value, avg_value, first_created, last_created` |

> `weather_hourly.first_value` / `last_value` columns exist but the rollup leaves them **NULL** —
> don't rely on them. Use `min_value` / `max_value` / `avg_value`.

So historical weather is **not lost** — it lives in `weather_hourly` at hourly min/max/avg grain.
Only the raw per-sample detail older than 14 days is dropped.

Safety configuration and health timestamps are stored in `WeatherRetentionState` and
exposed through `v_weather_retention_readiness`. Pruning requires:

```text
ConsumersMigrated=1
PruningEnabled=1
ReadyToPrune=1
```

### 2. Device monitoring — guarded hard purge

`prune_device_monitoring_to_cutoff()` deletes `DeviceMonitoring` rows older than the fixed
90-day cutoff in timestamp/ID order, in bounded 50,000-row transactions. It uses the same
enable gate, audit table, wall-clock/row limits, failure handler, and daily wrapper.

There is **no rollup / aggregate table** for DeviceMonitoring—telemetry older than 90 days
is gone for good, intentionally.

## What this means for Grafana dashboards

Any panel that queries raw `weather` over a window **longer than 14 days** will go blank for the
older part unless it also reads `weather_hourly`. The fix (already applied to all weather panels,
2026-06-21) is a row-level UNION so the outer aggregation sees both raw samples and hourly buckets —
this keeps **true** daily highs/lows and avoids double-counting the one boundary day:

```sql
FROM (
  SELECT name, created, value AS vmin, value AS vmax, value AS vavg
    FROM weather WHERE $__timeFilter(created)
  UNION ALL
  SELECT name, bucket_start AS created, min_value AS vmin, max_value AS vmax, avg_value AS vavg
    FROM weather_hourly WHERE $__timeFilter(bucket_start)
) weather
```

Then rewrite the outer aggregates to read the matching column:
`max(value)` → `max(vmax)`, `min(value)` → `min(vmin)`, `avg(value)` → `avg(vavg)`.

Dashboards touched: Home (+ the file-based landing page `/home/pi/grafana/dashboards/home.json`),
Humidity, Room Comparison, Thermostat, Thermostat by Hour, Trends, Trends Weather, Weather,
Weather Trends, Weather Trends by City, Weather by City.

> The `/` landing page is served from the static file `home.json` (compose env
> `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH`), **not** the DB. It's a separate copy from the
> DB dashboard `debnyj589w9a8f` — edit both when changing the Home dashboard.

## Inspecting / changing the jobs

```sql
SELECT * FROM v_weather_retention_readiness;
SELECT * FROM WeatherRetentionAudit ORDER BY Id DESC LIMIT 20;

-- Archive only; never deletes:
CALL archive_weather_guarded();

-- Validate only; never deletes:
CALL dry_run_weather_retention();

-- Production archive + validate + guarded prune:
CALL maintain_weather_device_retention();

-- Emergency pruning lock:
UPDATE WeatherRetentionState
SET PruningEnabled=0,
    LastNote='Pruning manually paused'
WHERE Id=1;
```

After pausing, archive and dry-run validation remain available. Re-enable only after reviewing
the audit and resolving the reason for the pause.

Do not re-enable the legacy MariaDB events while Node-RED owns the schedule. The MariaDB
`event_scheduler` may remain `OFF`.

## Applying the same pattern to another table

To downsample another high-volume table, use the guarded pattern in
`weather-device-guarded-retention.sql`: one fixed cutoff, idempotent archive, exact
source-to-archive validation, enable gate, bounded deletion, audit records, failure handlers,
visible Node-RED scheduling, and a stale-success watchdog. Then update Grafana consumers to
combine recent and hourly history.
