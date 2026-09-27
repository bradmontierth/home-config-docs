# TrainerMax companion analytics

## Ownership and paths

- Host: this Beelink, `192.168.10.217`; no remote SSH is needed.
- Repository: `/home/pi/trainer_max`.
- Companion: `/home/pi/trainer_max/companion/strava-proxy`.
- Compose service: `strava-proxy`; container: `trainermax-strava-proxy`.
- Web UI: `http://192.168.10.217:8093` (container port 8080).
- SQLite: `companion/strava-proxy/data/rides.db`, WAL mode.
- Credentials: companion `.env` and `data/tokens.json`; do not print their contents.
- Product/data-flow guide: `/home/pi/trainer_max/companion/WEB_COMPANION_PLAN.md`.
- Current implementation/runbook: `/home/pi/trainer_max/companion/strava-proxy/README.md`.

The same container owns Strava OAuth/uploads, Android ride sync, outdoor imports,
analytics and static web pages. Android owns locally recorded rides; Strava supplies
imported rides. Restarting this container briefly interrupts all those endpoints.

## Altitude comparison status

**Deployed 2026-09-14.** The companion image and additive SQLite metadata schema are live.
The follow-up UI revision is also live: the laps/intervals table has a compact **aNP**
column beside NP. Reference elevation/acclimatization controls are under **⚙ Settings**;
tapping an aNP value opens elevation, coverage and calculation details. The standalone
altitude section is removed. Phone and desktop browser checks passed, including the
live latest ride's Lap 2 value of 269 W. The subsequent NP/pause fix (method version 2)
is also deployed: NP and aNP share the original lap importer's partial-startup rolling
window method, recorded zero watts are retained, and supported recording gaps are
excluded from coverage. The table and detail popup use the same NP baseline. No
additional schema change or stored lap-metric rewrite was needed for this fix.
All 33 backend/API tests passed; the live phone UI, health endpoint and real ride
comparison passed without browser errors. Brad's explicit historical lap-timing
backfill was started and is processing in rate-limited batches. New rides continue
importing within that job's request budget; this avoids blocking normal syncing for
the duration of a long backfill.

Deployment recovery artifacts:

- Consistent SQLite backup and manifest: `/home/pi/backups/trainermax/20260914T190817Z/`.
- Backup `quick_check`: `ok`; 1,842 rides, 9,070,882 samples, 9,966 interval rows.
- Prior image tag: `trainermax-companion:pre-altitude-20260914t190817z`.
- Current image: `sha256:355bb438b70fd9c1e09336e9b1d5190a1c9eb67b858332ae569fa5decf2e4212`.
- NP/pause fix backup: `/home/pi/backups/trainermax/20260915T001422Z-np-pause/` (UTC timestamp; deployment was September 14 local time). SQLite quick check passed.
- NP/pause rollback tag: `trainermax-companion:pre-np-pause-20260915t001422z`.
- UI revision backup: `/home/pi/backups/trainermax/20260914T192746Z-anp-ui/` (SQLite quick check `ok`).
- UI rollback tag: `trainermax-companion:pre-anp-ui-20260914t192746z`.
- The historical timing job was explicitly resumed after the UI deployment; previously
  saved timing is retained and skipped on the next batch.
- Existing lap metrics compared with the backup: no differences.

Latest ride `strava-20175105632` synced during deployment. Lap 2 (45:08) has 100%
paired coverage and average elevation 6,601 ft: 259 W measured → 267 W equivalent
at 5,000 ft; NP 261 → 269 W under the acclimatized model. Under method 2 the three
laps show NP/aNP 211/212, 261/269 and 163/163 W. Lap 3 has 2,607 paired seconds and
622 inferred paused seconds (10:07 at the summit, plus 0:15 later); its coverage is
100%, and the whole ride's coverage is also 100%. Every recorded sample has altitude.

Pause inference requires both gap endpoints to have not-moving flags, zero power,
speed <=1 m/s, and elevation within 15 m. Only missing seconds are excluded; recorded
stopped samples remain in NP. Unknown gaps and actual missing channels still count
against coverage. The elapsed-time chart is unchanged. The privacy-minimized ride
fixture and synthetic contradictory-evidence tests cover these cases.

The new comparison uses time-weighted altitude and sample-level power adjustment
relative to 5,000 ft by default, with selectable acclimatization assumptions. Both
measured and estimated average/NP use matched valid time. Missing data is flagged;
indoor/virtual rides use physical trainer elevation. Exact lap offsets and environment
metadata are saved on new imports. Existing laps need the explicit
metadata backfill described in the companion README.

## Running mirror (runs.db) — race-predictor groundwork

**Deployed 2026-09-26.** Separate SQLite `companion/strava-proxy/data/runs.db` (never
touches rides.db) fed by `run_import.py` / `runstore.py` inside the same container —
it must live there because only a Strava refresh token is stored and Strava rotates it
on every refresh; a second refresher would break the live proxy.

- Imports Run / TrailRun / VirtualRun: detailed activity JSON (best_efforts, splits,
  laps, gear, device, workout_type) + streams time, distance, latlng, altitude,
  grade_smooth, velocity_smooth, heartrate, cadence, watts, temp, moving. Two requests
  per run, budget 70 per 15-min window (leaves room for the hourly ride sync), own
  job/lock, resumable. Gear (shoes) fetched once per id.
- Labels (`run_labels`): race, stroller, treadmill, carbon, exclude, shoe, note. Seeded
  from Strava name/trainer/workout_type; a manual label always beats a re-seed.
- UI: `http://192.168.10.217:8093/runs` (phone-checked). API: `GET /api/runs?athlete=`,
  `GET /api/runs/{id}`, `PUT /api/runs/{id}/labels`, `POST /import/strava/runs?athlete=`
  (explicit backfill; never auto-started), `GET /import/strava/runs/status`. The hourly
  loop only runs a quick run sync once `meta.full_walk_done:<athlete>` exists.
- Tests: `test_runs.py` (copy into the container with fixtures, run with throwaway
  `RUNS_DB`/`RIDES_DB`). Rollback image `trainermax-companion:pre-runs-20260926t191305z`,
  rides.db backup `/home/pi/backups/trainermax/20260926T191305Z-runs-import/`.
- Model (2026-09-27): `runmodel.py` computes per-run metrics into `run_metrics` (version-stamped;
  bump `METRICS_VERSION` to force a recompute) with Open-Meteo weather cached in `run_weather`.
  Coefficients are FROZEN from the offline fits (companion/runmodel/*.py via the ev-planner venv);
  refit offline and paste. `GET /api/runs/review` = state, 7-vs-28 delta, last long-run drift,
  flags, predictions (SLC + Boise), current plan week; `POST /api/runs/metrics/recompute`.
  The review route must stay ABOVE `/api/runs/{run_id}`. Metrics recompute runs in a background
  thread 90 s after start and after every import batch. Plan page `/static/plan.html` +
  `static/plan.json` (regenerate by hand; no route needed). Rollback image
  `trainermax-companion:pre-runmodel-20260927t135509z`. Tests: `test_runmodel.py` (run modules
  separately — they share one RUNS_DB per process). Image now includes numpy.
- Plan and modelling design: `run-race-predictor-plan.md`.

## Read-only troubleshooting

```bash
docker ps --filter name=trainermax --format '{{.Names}} {{.Status}} {{.Ports}}'
curl -fsS http://127.0.0.1:8093/health
curl -fsS http://127.0.0.1:8093/import/strava/status
docker logs --tail 80 trainermax-strava-proxy
```

For direct SQLite inspection use a `file:…?mode=ro` URI and `PRAGMA query_only=ON`.
Do not import `app.py` against the live database for inspection: module import runs
schema initialization and starts the auto-sync thread. Frontend dashboard/history
pages POST a sync nudge; use API GETs for strictly read-only inspection.

## Rollout procedure

This rollout and Brad's backfill were explicitly approved and performed on
2026-09-14. For future disruptive operations, follow the user's current approval rules.

1. Take a consistent SQLite backup with the SQLite backup API (include live WAL
   contents; copying only the main `.db` file is insufficient). Retain the old image
   ID for rollback. Avoid backing up or exposing tokens in review artifacts.
2. Build the companion image; recreate only the `strava-proxy` Compose service.
   Startup adds nullable metadata columns. No Android build or APK publication is needed.
3. Verify health, schema, a real ride comparison and logs. Regular auto-sync resumes
   under its existing schedule; it does not opt into historical lap timing backfill.
4. If separately included in approval, explicitly start
   `POST /import/strava/altitude-backfill?athlete=brad`, verify the returned job has
   `altitudeBackfill: true`, and monitor completion/error state. It may wait through
   Strava rate-limit windows. Existing lap power metrics remain intact.

Rollback can use the prior image with the additive schema left in place; the old code
ignores the new columns. If restoring a database backup becomes necessary, stop the
writer first and assess rides imported since the backup before replacing data.
