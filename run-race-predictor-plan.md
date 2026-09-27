# Running race predictor — plan (drafted 2026-09-26)

Goal: a personal, course-aware race-time predictor trained on Brad's own running history
that (a) does not get dragged down by stroller runs / steep Utah trail runs, (b) handles
net-downhill and net-uphill courses, and (c) recovers quickly after a season of cycling.

## Data inventory (verified 2026-09-26)

- `/home/pi/trainer_max/companion/strava-proxy` imports **cycling only**
  (`CYCLING_TYPES` in strava_import.py) and pulls streams time/watts/hr/cadence/velocity/
  altitude/moving — no latlng, distance or grade_smooth. rides.db has 1,838 Strava rides
  (2001–2026-09-24). Only the refresh token is stored (`data/tokens.json`); Strava rotates
  refresh tokens, so no second process may refresh it — any run importer must live inside
  the trainermax container (or the container must mint access tokens for it).
- `/home/pi/strava/storage/database/strava.db` (stopped robiningelbrecht/strava-statistics,
  Feb 2025): 297 Runs 2023-05-20 → 2025-02-06, full activity JSON incl. `best_efforts`,
  `splits_standard`, `laps`; HR stream for 152 runs, Garmin running power for 91. No gear
  (shoes) and `workout_type` never set → race/shoe/stroller labels must be added by hand.
- Best efforts in that DB: mile 5:55 (2023-06-19), 5K 20:44 (Jordan River, 2023-11-10),
  10K 41:46 + 15K 64:13 + 20K 88:20 (Snow Canyon Half 2023-11-18, big net downhill),
  HM 1:39:24 (Utah Valley Half 2024-06-01, net downhill), marathons St George 3:37:25
  (2023-10-07, net −2,600 ft) and Ogden 3:41:05 (2023-05-20, net −1,100 ft), Mid Mountain
  50K 6:58 (2024-08-17, trail). Peak weeks 38–41 mi, median run-week 17 mi, 86 run-weeks.
  Nearly every race point is net downhill → flat-normalisation is not optional.

## Architecture

1. **Run importer** (inside trainermax container, same resumable/rate-limited job pattern,
   own `runs.db`): sport types Run/TrailRun/VirtualRun; streams time, distance, latlng,
   altitude, grade_smooth, velocity_smooth, heartrate, cadence, watts, temp, moving; store
   summary JSON (best_efforts, splits, laps, gear_id, workout_type, device). Pull athlete
   gear list for shoe names. ~2 requests/run.
2. **Labels** (SQLite table + tiny UI): race / workout / stroller / treadmill / carbon shoe /
   exclude, seeded by name rules ("stroller", "jogger", "treadmill"), TrailRun type, and
   Strava gear once it exists. Labels drive observation weight, never deletion.
3. **Grade model**: per-second flat-equivalent speed via Minetti cost-of-running curve
   C(i)=155.4i⁵−30.4i⁴−43.3i³+46.3i²+19.5i+3.6 (J/kg/m), then a *personal* correction fitted
   from his own HR–speed–grade bins (steep hiking grades > ~+15% are where Minetti/GAP
   undercount effort). Trail runs then become usable observations instead of noise.
4. **Fitness observation per run**: aerobic efficiency = flat-equivalent speed ÷ %HR-reserve
   on steady, moving, |grade|<3%, non-stroller, drift-controlled segments (first ~40 min,
   temperature-adjusted). Hundreds of observations → this is the statistical backbone.
5. **Fitness state**: Kalman-style trajectory over the efficiency series with a decay prior;
   plus Banister-style impulse response on running load (GAP-based rTSS) and a *separate*
   cycling-load input with a transfer coefficient (prior ~0.5 to the aerobic term, 0 to
   economy). Two tiers: acute state + slow "trained ceiling" anchored at lifetime best.
6. **Race curve**: personal critical-speed / Riegel exponent fitted to flat-normalised
   known efforts (mile → marathon); predicted flat time by distance = f(fitness state).
7. **Course adjustment**: apply the grade model mile-by-mile to a course profile (GPX or a
   past activity), with capped downhill benefit and a late-race eccentric-damage penalty for
   long downhill marathons; carbon-plate factor (~2–4 %, smaller at slow paces); race
   altitude and forecast temperature terms.

## Evaluation

- Leave-one-race-out CV: hide everything from race day onward, fit, predict, compare.
  Report MAE in % and seconds per distance; compare with Riegel-from-last-effort and
  (where remembered) Garmin's number at the time.
- Stability test: predicted flat 10K must not move after a stroller/trail run.
- Trail check: Mid Mountain 50K with and without the personal grade curve.
- Honest caveat: ~9 race points; CV is a sanity check, not proof.

## Detraining / re-entry

Don't model a year of cycling theoretically. Calibration protocol on return: 2–3 easy
steady runs (feeds the efficiency estimator) + one 20-min or 5K flat effort; the state
model then overrides the prior within a week. Cycling load enters only through the
transfer term.

## Phases

0. Run importer + runs.db + labels UI (container rebuild + multi-hour backfill — needs approval).
1. GAP + personal grade curve + efficiency trend page (answers "am I fitter?").
2. Race curve + course-specific predictor with profile input.
3. CV report page.
4. Decay/transfer term + calibration protocol.

Garmin Connect: skipped for now (official API needs developer approval; VO2max/predictor
are derived from the same raw data we already get; revisit for FIT-level running dynamics).

## Phase 1 first results (2026-09-26, offline scripts `trainer_max/companion/runmodel/`)

Run with `/home/pi/ev-planner/.venv/bin/python analyze.py` / `fit.py` (numpy/scipy there; read-only on runs.db).

- 12 races labelled by Brad (2023-05 → 2026-02). Flat-equivalent (Minetti) times: Ogden 3:42→3:51,
  St George 3:38→3:58, Snow Canyon 1:33→1:46, Utah Valley 1:40→1:45; Boise River Marathon 2025 (flat)
  3:52→3:53. Downhill-corrected 2023 marathons bracket the real flat marathon → Minetti road-downhill
  credit is about right.
- Predictor v0 = personal Riegel (b≈1.12) + 28-day efficiency-state term, LOO CV on 11 road races:
  MAE 2.8 % vs 7.0 % for Riegel-from-last-race. BUT the state coefficient is ~0 — the curve alone
  carries it; race form has been very stable 2023–2026.
- Efficiency series (flat-eq speed / HR-reserve, steady segments, HR lag 30 s): 3.5 (Aug 2022) →
  4.0 (fall 2023) → 4.25 (early 2025) → 4.3 (Dec 2025) → 4.06 (early 2026); summer dips = heat
  confound. Strava average_temp exists only for 2022 → next: join Open-Meteo/Ecowitt temperature.
- Naive grade-bin check is lag-confounded (uphill looks cheap, downhill dear); sustained-grade
  version has too few segments at 5 % bins → next: 30 s grade smoothing + 3 % bins.
- Flat predictions from the 365-day state: 5K 20:54, 10K 45:34, half 1:45, marathon 3:50.

## Phase 1 second pass (2026-09-26 eve): weather, altitude, carbon, grade curve, Boise

- `runmodel/weather.py` caches Open-Meteo archive hourly temp/RH/wind per run in `runmodel/weather.db`
  (589 runs; no key needed). `fit2.py` / `curvecheck.py` hold the analysis.
- Efficiency regression (547 runs, quarter FE): temp −0.34 %/°C above 10 °C; intensity −7.7 % per
  +0.1 HRR; altitude −9.5 %/1000 m (confounded: low-altitude runs = trips/races); carbon +9 % even among
  hard runs = race-day bundle (taper/flat/competition), NOT a shoe estimate — needs carbon on easy runs.
- Adjusted quarterly fitness: 3.67 (2022) → 3.86 (2023Q2) → 4.08 (2023Q3) → plateau 4.0–4.1 through
  2026Q1. Race form stable for the same reason.
- Race model + altitude: +0.75 %/1000 ft (≈ +7 s/mi marathon SLC-valley vs Boise; matches Brad's felt
  5–10 s/mi); temp collinear with altitude in the 12 races, can't separate yet.
- Personal grade curve (HR-based, 31 s smoothed grade, 3 % bins, lag 30 s) vs Minetti: downhill gives
  Brad LESS benefit (−4.5 %: 0.90 vs 0.78), moderate uphill costs LESS (+4.5 %: 1.16 vs 1.27), steep
  ≥12 % costs MORE (2.17 vs 1.94). Race self-consistency: Minetti LOO 2.9 %, personal 2.8 %, 50/50
  blend 2.5 % (best) → use the blend for road, personal for steep trail.
- Boise River Marathon 2025: 7:55–8:05 through mile 13 (3:30 pace), fade 14–18, blow-up 19+. Riegel 1.06
  from the Feb half = 3:30 (Garmin's number); personal b≈1.12 = 3:40; full model LOO = 3:49; actual 3:53.
  Weather 12 °C/48 % RH — near ideal. Verdict: pace ~9 % above fitness, not weather.
- 13 races now labelled (15k 2023-10-28 added).

## Boise re-examination + two-segment curve (2026-09-26 late)

- Prior-races-only fit (blend curve) for Boise 2025 = 3:38–3:41 regardless of recency half-life
  (90 d → none); the earlier "LOO 3:49" used Minetti + later races. Actual 3:53.
- Pairwise exponents: 5K→half 1.04–1.12, half→marathon 1.17–1.20 → marathon-specific fade.
  Two-segment curve (b_short 1.10, b_marathon 1.17): LOO MAE 2.0 % (Boise 3:49 vs 3:53 → cold ≈ 1–2 %).
  Altitude term unstable (+0.12…+0.8 %/kft) — collinear with temp; keep ~0.5 %/kft as a prior.
- Marathon builds were 26–30 mi/wk with ≤2 twenty-milers → steep fade is conditional on that volume.
- Boise miles 2–10 HR-efficiency 4.0–4.07 = normal race-day level → no HR evidence of the head cold early;
  drift to 3.89 by mile 13 then collapse at 19.

## Training program (2026-09-26) — Boise River Marathon Sat 2027-05-01

Source of truth: `trainer_max/companion/strava-proxy/static/plan.json` (rendered at
`:8093/static/plan.html`, shows actual weekly miles vs target). 31 weeks from Mon 2026-09-28,
897 mi total, peak 42. Phases: Re-entry wks 1–9 (12→22 mi, all easy + strides, bike 2–3×;
BASELINE = Boise Turkey Trot 5K Thu 11/26, model re-derives paces) · Base wks 10–18 (24→36,
one quality: tempo/hills alternating, long 10→16) · Build wks 19–26 (38→42, intervals or
threshold + long with MP 4→10 mi; TUNE-UP HALF Sat 3/6 locks the marathon range) · Specific
wks 27–28 (22 & 20 mi long with MP 12/10) · Taper 80/60/40 %. Paces (SLC, cool): easy
9:30–10:15, MP 8:35–8:45, threshold 7:35–7:45, intervals 6:45–6:55, strides 20 s ~6:00 feel.
Rules on the page (10 %/wk, 2 quality max, long ≤ ⅓, back-off on efficiency −4 % or RPE ≥ 7,
durability gate on like-intensity long-run drift, log RPE in Strava, carbon only for half/MP12/race).

TODO for the drift metric: compare like-intensity segments only (Brad's 4/12 20-miler ended with
a 3-mi tempo pickup → the −10 % "drift" was the intensity term, not durability).

## Phase 2 in container (2026-09-27)

Backfill finished: 799 runs 2015-10 → 2026-08 (782 with streams), 1.97 M samples, hourly quick sync
live. `runmodel.py` shipped (see companion guide): per-run adjusted efficiency, blended-curve
flat-eq distance, like-speed long-run drift, weather cache, weekly review endpoint + review card on
the plan page. Drift definition v4: late 25 % vs miles 2–6, only late samples whose 61 s-smoothed
flat-eq speed is within ±10 % of the early speed and not within 120 s after a surge (>8 % faster);
falls back to like-speed only. RESULT: the pickup was NOT the explanation — Brad's 2025 long runs
drift −6.5 (16 mi), −7.3 (18), −10.0 and −10.1 (20 mi) at like speed; 2023 builds −9 to −13.
Durability is the real marathon limiter; the plan's MP-progression gate uses this number.
Live review 2026-09-27: state 4.04 (365-d fallback, no runs in 28 d), SLC half 1:42:55,
marathon 3:51 (range 3:40–3:56), Boise 3:49 (3:38–3:54).
