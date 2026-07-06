# MySQL Retention / Rotation / Historical Tables

Reference for the data-retention scheme on the **`hubitat_logging`** database. The point of
this scheme is to stop "write-once, read-never" telemetry from filling the disk while keeping
the long-term history that's actually useful.

_Last verified: 2026-06-21._

## Where it runs

- **Host:** this device, `192.168.10.217` (the old `.250` mini PC was decommissioned; all tables
  were migrated here). This is also the MySQL all the Grafana dashboards now point at
  (datasource `n100mini`, uid `eebnvy9j8hmgwb`).
- **Container:** `mariadb-mariadb-1`, defined in `/home/pi/mariadb/docker-compose.yml`.
  Started with `--event-scheduler=ON` so MariaDB scheduled EVENTS can run.
- **Database / creds:** `hubitat_logging`, user `hubitat_logger` (root pw and user pw are in the
  compose file).

```bash
# quick shell into the DB
docker exec -it mariadb-mariadb-1 mariadb -uhubitat_logger -p hubitat_logging
```

## The two retention jobs (MariaDB EVENTS, run daily)

Confirm they're enabled / when they last ran:

```sql
SELECT EVENT_NAME, STATUS, INTERVAL_VALUE, INTERVAL_FIELD, STARTS, LAST_EXECUTED
FROM information_schema.EVENTS WHERE EVENT_SCHEMA='hubitat_logging';
```

| Event | Runs | Calls | What it does |
|-------|------|-------|--------------|
| `ev_maintain_weather_retention` | daily @ 03:20 | `maintain_weather_retention()` | rolls up + prunes the `weather` table |
| `ev_prune_device_monitoring`    | daily @ 03:00 | `prune_device_monitoring(90)`   | hard-deletes `DeviceMonitoring` rows older than 90 days |

Every run is logged to the **`maintenance_runs`** table
(`id, job_name, started_at, finished_at, status, rows_affected, note`). Check recent activity:

```sql
SELECT job_name, started_at, finished_at, status, rows_affected, note
FROM maintenance_runs ORDER BY id DESC LIMIT 20;
```

### 1. Weather — downsample, don't delete

`maintain_weather_retention()` does two steps, both keyed off a 14-day cutoff
(snapped to the top of the hour, `NOW() - INTERVAL 14 DAY`):

1. **`rollup_weather_hourly(14)`** — for every raw `weather` row **older than 14 days**, insert/update
   an hourly aggregate into **`weather_hourly`**: one row per metric `name` per hour bucket
   (`ON DUPLICATE KEY UPDATE`, so it's idempotent / re-runnable).
2. **`prune_weather_raw(14)`** — deletes those same >14-day rows from raw `weather`
   (batched `DELETE ... LIMIT 100000` with a brief sleep to avoid lock storms).

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

### 2. Device monitoring — hard purge

`prune_device_monitoring(90)` just batch-deletes `DeviceMonitoring` rows where
`Created < NOW() - INTERVAL 90 DAY`. **No rollup / aggregate table** — telemetry older than
90 days is gone for good (intentional; it has no value past 90 days). Nothing for dashboards to
combine here; they simply can't show >90 days.

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
-- read a procedure body
SELECT ROUTINE_DEFINITION FROM information_schema.ROUTINES
WHERE ROUTINE_SCHEMA='hubitat_logging' AND ROUTINE_NAME='rollup_weather_hourly';

-- procedures present: maintain_weather_retention, rollup_weather_hourly,
--                     prune_weather_raw, prune_device_monitoring

-- run a job manually (e.g. to backfill the rollup or force a prune)
CALL maintain_weather_retention();
CALL prune_device_monitoring(90);

-- pause / resume a job
ALTER EVENT ev_maintain_weather_retention DISABLE;
ALTER EVENT ev_maintain_weather_retention ENABLE;
```

To change a retention window, edit the parameter the event passes (e.g. `prune_device_monitoring(90)`
→ `(120)`) via `ALTER EVENT ... DO CALL ...`, or change the `14` in `maintain_weather_retention()`.
If the scheduler ever appears dead, check `SHOW VARIABLES LIKE 'event_scheduler';` (should be `ON`)
— it's set by `--event-scheduler=ON` in the mariadb compose command.

## Applying the same pattern to another table

To downsample a different high-volume table instead of hard-purging it: create a `<table>_hourly`
aggregate (mirror `weather_hourly`), write a `rollup_<table>_hourly(days)` + `prune_<table>_raw(days)`
pair modeled on the weather procedures, wrap them in a `maintain_<table>_retention()` proc, and add a
daily `ev_maintain_<table>_retention` EVENT. Log each run to `maintenance_runs`. Then update any
Grafana panels to UNION raw + hourly as shown above.
