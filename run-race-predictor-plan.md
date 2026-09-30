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

## Plan v2 (2026-09-27) — generated by `trainer_max/companion/runmodel/make_plan.py`

Brad's review: v1 had four 20–22 mi runs in a row, long runs > ½ the week, 3+ h at his pace, and
undefined interval sessions. v2: long runs alternate big/medium (16·4MP, 18, 12, 14·6MP, 18·8MP,
half, 3:00, 15, 3:00, 16·6MP); only two runs reach ~20 mi and both are prescribed by TIME
(3:00 cap: 100 easy + 80 MP ≈ 19.5 mi; 75 easy + 105 MP ≈ 19.8 mi dress rehearsal); long ≤ ~45 %
of the week. Every session is segment-built with reps, recoveries, miles and duration; I volume
≤ ~8 %, T ≤ ~10 % of weekly miles (Daniels). Peak 43 mi, 927 mi total. Plan page rows expand to
the day-by-day prescription. Regenerate with the ev-planner venv, then docker cp plan.json.

## Fitness history page (2026-09-27) — `:8093/fitness`

`GET /api/runs/fitness`: per-run adjusted efficiency, 28-day rolling median (≥ 5 runs in window),
weekly miles, long-run drift, and every road race as a flat SLC half-marathon equivalent next to
the model's pre-race half estimate. Reading: 2023 climb 3.65 → 4.1 tracks the 35 mi/wk months;
plateau 4.0–4.1 since; best 28-day 4.34 (Jun 2025). The 2025 race dots sit ~2–4 % BELOW the
model line at its peaks → the time ∝ 1/efficiency translation overshoots when efficiency spikes
(possible heat over-correction in summer). Treat peaks with caution; races are the ground truth.

## Intensity correction + explicit race-day terms (2026-09-27, metrics v5)

- Linear intensity slope over-credited hard runs (~+4 % at HRR ≥ 0.78). Replaced by a fitted table
  (INTENSITY_KNOTS/LOGADJ in runmodel.py; iterated vs a ±30-day local training level on 577
  non-race non-carbon runs). All HRR bands now within ±2 % of the line.
- Race-day efficiency then sits +2.1 % above the training line (was +8 %). Attributed to carbon
  (CARBON_GAIN 2 %). TAPER_GAIN 0: untapered 5K–15K races were 1.6 % FASTER vs fitness than tapered
  halves/marathons, so no taper effect is detectable (literature says ~2 %; revisit with more races).
- Recalibrated: RACE_K 25353 = half-eq × state-before over 10 clean races (±2.1 %);
  REF_STATE = RACE_K/6160 ≈ 4.116. In-sample mean abs error 1.3 % (optimistic; LOO curve was 2.0 %).
- predict(state, alt, carbon=, tapered=); review adds Boise marathon in regular shoes (+2 %).
- Race notes (label `note`) on the four 2025 races; a note starting "COMPROMISED" draws the race
  hollow on /fitness. SLC run series 5K (wind, reran 5K 21:00 next day) and RUN SLC (6 days after
  the half) are compromised and excluded from calibration.
- Live 2026-09-27: state 4.07 (365-d fallback), SLC half 1:43:53, Boise marathon 3:51:25
  (3:40:55–3:56:46), regular shoes 3:56:02.

## Coach phase 0: replay + grading (2026-09-27 eve)

Goal: a post-run Pushover "coach" note for Brad, Adrienne and later Brad's sister that never becomes Strava Athlete Intelligence ("you ran 0.1 mi more — great progress"). Design rule: **code decides what is significant, the model only judges and words it.**

- `strava-proxy/coachfacts.py` (in container): per-run session class (easy/steady/tempo/intervals/strides/long/long+quality/race + label kinds stroller/treadmill/trail; cached in `coach_run_class`, labels applied on read) and facts computed from **history before the run only**, each marked `significant` against the athlete's own spread: efficiency vs 28-day (≥ max(4 %, 2σ)), 28-day fitness change (≥ 2.5 %), 7-day volume vs prior 4-week avg (ratio ≥ 1.5 and +8 mi, or ≤ 0.5), return after ≥ 6 days, hard-day density / back-to-back, easy-not-easy (≥ 20 min above 151 bpm), longest in 6 weeks, long-run drift vs 6-month median (≤ −6 % or ±3 pts), rep fade (≥ 4 %), race vs model (> 3 %) and race split, heat/cold/wind, carbon on training runs. Boise 2025 build: 34 of 72 runs have a significant fact, 38 are ack-only.
- Profile per athlete in runs.db meta `coach_profile:<athlete>` (about, dated goals with setOn, dated notes → time-scoped so a replay never sees hindsight). Source file `trainer_max/companion/coach/profiles/<athlete>.json`, pushed with `coach.py profile`.
- Generator `trainer_max/companion/coach/coach.py` runs on the HOST with the machine `claude` login (subscription, no API key; same pattern as jobs/nightly_digest.py): `claude -p --json-schema` → {message, kind ack|note|warning, focus, reasoning}. Prompt `coach/prompts/system_v1.md` (one point max, banned praise list, bad/good examples). Model used: claude-opus-5-5, ~8–25 s per message.
- Store `coach_messages` (batch, prompt_ver, grade good|filler|wrong + note). API `/api/coach/{facts/<run>,replay-runs,recent,messages,messages/<id>/grade,profile}`; grading page `:8093/coach` (in main nav).
- First replay batch `v1-boise25` = Jan 1 – May 10 2025. Early read: coach is too naggy about volume and treats its own previous messages as agreed instructions ("we said 25 miles") — tuning waits on Brad's grades.
- Next: tune to ≥ 90 % "good" on graded replay → Adrienne live via Pushover → sister (Funnel + per-person login still to design/approve). Adrienne's Strava connect link: `:8093/strava/authorize?athlete=adrienne` (tests the app's athlete capacity).

### Coach v2 (2026-09-28 early)
- Per-athlete HR zones (`coachfacts.athlete_zones`, as-of the run date): LTHR = median of the last ≤4 clean road races' avg HR ÷ distance factor (5K 1.02, 10K/15K 1.01, half 1.00, marathon 0.93; 10K–half preferred), HR max = p99 of run max HR (Brad 190; the single 207 is a strap spike). Zones as % LTHR: easy <85, steady 85–90, marathon 90–95, threshold 95–102, hard >102 → Brad today LTHR 177: 150/159/168/180, flat paces from the efficiency model at current fitness (easy slower than ~9:05, threshold 8:12–7:35). Jan 2025 LTHR was 171 (Utah Valley downhill half at 168 drags it; consider excluding net-downhill races). The old fixed %HRR bands called marathon effort "threshold" (Boise 2025: 164 "threshold" minutes).
- Payload now has per-mile splits + per-lap rows (pace, gap from our grade model, ft up/down, HR p10/p50/p90 with the first 30 s of laps skipped), a `work` summary line, and up to 3 comparable prior sessions (same group: intervals / tempo / long / race, 120 days). Session kinds add marathon-effort and treadmill-<kind>.
- Prompt v2 (`coach/prompts/system_v2.md`): judge pace by gap, HR only from hrZones (no invented caps), plan=null ⇒ no plan talk, replay context ⇒ earlier notes were never delivered, made-up example numbers flagged.
- Batch `v2-select` (16 hand-picked runs). Headline: "Don't slip" mile repeats — v1 said they faded 7:05→7:46; by gap they got faster (last rep climbed 100 ft), and v2 compared it to the Christmas Eve session (25–30 s/mi quicker). Remaining pattern for Brad to grade: volume warnings dominate long-run days, "pounding" line repeats.
- Gotcha: `docker cp` of a module does NOT reload the running uvicorn — rebuild/restart before generating through the API (a first v2 batch was generated on stale facts and deleted).
- 2026-09-28: `/coach` now shows the athlete profile (zones today with paces, races behind LTHR, about/goals/notes) and, per message, the exact system prompt + run JSON sent (`coach_messages.system_text`, backfilled for v1/v2 batches; `GET /api/coach/zones`).
- Brad's v2 feedback (don't overfit): less Boise-centric (general running fitness, PRs, fitness gains are worth naming), more human / understanding-not-condescending (the day-after-5K "try again" run deserved acknowledgement, not a lecture), less jargon ("gap", "threshold zone", "late fade %"), and plan flexibility — a swapped day (intervals on a planned easy day) is fine if the week's hard-day budget holds.
- TODO portal for Adrienne + sister (Rachel): calendar of the plan with actual runs synced onto it, fitness/predicted-pace side panel, coach chat (can propose plan changes; code-enforced guardrails), and the athlete profile (zones, resting HR entry, goals).

### Coach v3 (2026-09-28)
- Payload adds `history`: weekly mileage (12 weeks + this week so far, runs, longest, hard sessions), previous 10 runs (title, description, session, hill-adjusted pace, HR), fitness week by week with half equivalent, races in the last year. ~10 KB per message.
- New facts: `best_effort` (Strava best efforts 1 mi–half vs the athlete's own history: all-time or fastest in a year), fitness 6-month high, `plan_vs_actual` (today's prescription + week's hard-session budget; a swapped day within budget is "fine"). Mileage-jump and fitness-trend facts fire at most once a week (repeat → not significant, noted as already flagged). Comeback weeks (prior avg < 5 mi) no longer flag volume.
- Prompt v3: an amateur who runs for love; goal race only when it changes today's advice; good news gets said; understanding-not-condescending; warnings only for injury/weeks-of-training risks; plain-language glossary (no "gap"/"zone 3"/"drift"); ≤ 2 numbers.
- `/coach`: prompt view is rendered as tables/sections (raw JSON still available); profile editable in the page (name, about lines, goals `date | name | target | set on`, notes `date | text`). `coach.py profile` now refuses to overwrite an existing profile without --force (the DB profile is the source of truth; the JSON file is a seed).
- Batch `v3-select`: 16 familiar + 11 unseen (Oct 2025–Feb 2026). Reads far more human (the day-after-5K run now: "You woke up and tried again, and it worked… fastest 5K in over a year", then a brief easy-days note). Seen issues: one invented cause ("running with faster friends" from the title "Trying to absorb some Conner"); "pounding" still recurs because it's in the profile's About line.

### Coach v4 (2026-09-28): message + optional breakdown
- Output schema adds `analysis` (stored in `coach_messages.analysis`, shown on /coach as "Full breakdown"). Prompt v4 = v3 + length by session: easy runs → message only; key workouts → 1–3 sentence message + optional 60–150-word breakdown when laps/splits tell a story; races → 2–4 sentence message about the day and the season + always a 150–300-word breakdown (pacing adjusted for hills, effort, result vs prediction, what the block did well from weekly mileage, 1–2 things for next block, recovery). Message capped at 600 chars (Pushover limit is 1024; the breakdown goes behind the notification's link).
- Batch `v4-races-workouts` (5 races, 5 key workouts, 1 easy control): races 265–447-char messages + 245–311-word breakdowns; 4 of 5 workouts got 160–215-word breakdowns, the treadmill 400s and the easy run correctly got none.

### Status + next steps (handoff 2026-09-28)
Brad's verdict on v4: "feels fantastic… the right mix… reads much better than Strava's intelligence." v4 is the working prompt.

Next (in order):
1. **Delivery:** after each run imports (run_import → metrics), call the host generator for that run (`coach.py run --batch live --prompt v4`, live context) and send the message via Pushover (cecret_lake/pushover/.env; message ≤ 600 chars, supplementary URL → the run's breakdown page). Needs a host-side trigger (the container can't run the `claude` CLI): e.g. a small host watcher polling `/api/coach/messages` gaps or a new `/api/coach/pending` endpoint. Queue + retry when the subscription limit is hit; quiet hours; one message per run.
2. **Portal** (mobile + desktop) for Brad, Adrienne, Rachel: calendar of the plan with actual runs synced onto it, run pages with the coach message + breakdown, fitness/predicted-pace side panel, coach chat (tools over the companion API; can propose plan changes, code-enforced guardrails), athlete profile (zones, resting HR entry, goals, editable About). Plan paces/HR should come from each athlete's zones (plan.json still says easy HR ≤ 145; zones say 150).
3. **Access for Rachel (remote):** separate coach service behind Tailscale Funnel with Pushover magic-link login, scoped to the athlete; Strava webhooks become possible then. Needs Brad's approval before anything goes public. Per-athlete runmodel constants (HR_MAX/HR_REST, intensity table, RACE_K) before her predictions mean anything.
4. **Adrienne:** Strava login blocked (account was Facebook-linked; Strava removed Facebook sign-in and she lost that email). Fallback = Garmin (unofficial `garminconnect` library) if Strava recovery fails. Connecting her also tests the Strava app's athlete capacity.

Everything since trainer_max 242afbe and home_config 82add0a is UNCOMMITTED.

## Athlete portal v1 (2026-09-28, LAN only)

Live at `http://192.168.10.217:8093/a/<athlete>` (brad, adrienne). Light "paper" variant of the Ember tokens (`static/portal.css`), no dark mode. Pushover deep link target: `/a/<athlete>?run=<strava id>` opens that run's day sheet (coach note + breakdown + splits/laps); `?tab=coach|races|fitness|profile`.

- **Plan tab**: Week (7 columns desktop, day list on phone), Month (7-col grid + weekly planned/actual column), Season (weekly planned vs run bars, phase bands, race flags, paces + rules, "Rebuild from today"). Goal banner: main race, countdown, goal vs predicted, this week's miles.
- **Races**: add/edit with name, date, distance chips (5K/10K/Half/Marathon/Other mi), optional goal time, optional max weekly miles, "Main goal" toggle. Any change rebuilds the plan.
- **Plan engine** `planner.py` (pure) + `portal.py` (storage). Main-goal races end blocks (two main goals = two builds, e.g. Turkey Trot then Boise); other races are tune-ups inside a block (lighter week, race replaces the long run on weekends, no quality within 3–4 days before). Start volume = last 4 weeks' average; ≤10 %/wk, every 4th week −25 %; peak by distance (5K 25 … marathon 42) or the race cap; long run ≤ share of week and ≤3 h; ≤2 hard days/week; taper 1–3 weeks; recovery weeks after a main race. Plan is STORED (meta `portal_plan:<athlete>`): past days never change; rebuild only on race/profile change or by hand. Overrides per date in `plan_overrides` (coach or manual).
- **Coach chat**: queue in `chat_messages`; host worker `coach/chatd.py` (user unit `trainermax-chatd.service`) long-polls `/api/chat/next`, which answers only the Docker host (container default gateway; LAN gets 403). One live `claude -p --input-format stream-json` process per thread, kept 55 min idle → follow-ups hit cache (test: turn 2 read 7,361 cached, wrote 453). Context (≈3k tokens) sent once per thread. Prompt `coach/prompts/chat_v1.md`: pushback rules; plan changes only as a ```proposal``` block → Apply button → code guardrails (≤2 hard days, race cap, ≤15 % over planned week, ≤3 h, no past days).
- **Fitness**: predictions, 12-month fitness line (half-equivalent), PRs (best efforts all-time + 12 months), race history. **Profile**: name, resting HR, runs/week, cross-training, "what the coach should know" lines, HR zones.
- Post-run coach now reads the portal plan (`coachfacts._plan_day` → `portal.plan_day`) and races as goals; static/plan.json + old /plan page are legacy.
- **Uncalibrated athletes** (everyone but brad, `portal.CALIBRATED`): no time predictions, effort-only plan text. runmodel HR_MAX/HR_REST/RACE_K are Brad's — per-athlete calibration is the next prerequisite for Adrienne/Rachel numbers.

Open: Pushover delivery worker (link format above), per-athlete runmodel calibration, remote access (Funnel + login) for Rachel, weekly auto-adapt of future weeks from actual training, manual day edit/move UI (chat does it today), nav link from the dark pages.
- 2026-09-28 fixes: chat context changes (races, plan, runs…) now ride along with each later message as an "updates since your last reply" block, so the start of the conversation stays unchanged and cached. The snapshot of what the coach has been told is recorded only once a reply is delivered. Claims are atomic, a claim lasts at most 5 min, and a restarted worker takes back anything left claimed (`POST /api/chat/worker-start`). The duplicate reply came from a stray hand-started worker.
- 2026-09-28: the coach applies changes itself. Prompt chat_v2: a proposal with `"apply": true` (the athlete asked for or agreed to the change) is applied as soon as the reply is saved, if it passes the guardrails, and the card shows Undo. The previous overrides are kept in `proposal.undo`. `"apply": false` (the coach's own idea) waits for the Apply button. If a change breaks a limit, the card stays open and shows why. The context now includes every later week day by day in short form, so the coach can edit weeks far ahead (e.g. around Red Mountain).

### Weekly auto-adjust + race-based fitness (2026-09-29, deployed, uncommitted)
- **Monday check-in** (`portal.adapt_week`, app.py loop every 30 min from Mon 05:00, once per week per athlete): compares last week's plan with what was run and carries the ramp forward via `history.startVol/startLong/ramp0/easyWeeks/returnTo` (planner now records `vol/lng/cut` per week). Rules: grow ≤10 % (+1.5 floor) over *achieved* volume; missed miles never made up; sick/hurt flag, or <50 % of a ≥6 mi week with no reason → one easy-only week at 50–65 % of pre-illness volume, then up to 20 %/wk back to it; travel → resume near old volume; "felt run down" → counts as the cutback; no-workout weeks don't add harder workouts, streak ≥2 → note to talk to the coach. Hand/coach overrides never touched (listed in the note). Paces: ≤2 %/wk faster, slower only after 3 Mondays running. Log: meta `portal_adjust:<a>`, state `portal_adapt:<a>` (keeps Monday's baseline so a later flag re-runs against it).
- **Week flags** (`week_flags` table; this week or last week only): chips on the Plan tab, and the coach can set them (`"weekFlag"` in a proposal, chat prompt v3). Undo restores the previous flag.
- **racefit.py**: uncalibrated athletes get fitness from races — label `race=1` or `offbike=1` (70.3 run ×0.90), `official_mi` fixes GPS-short races, Riegel 1.06, marathon ×2^1.07–1.15, results >1 yr aged +0.5 %/mo (max 8 %). HR-model pace columns hidden for them.
- **Projection** ("if training goes to plan") on the goal banner + Fitness tab: today's range minus min(7 %, 0.25 %/wk) × volume-jump factor; never used for paces.
- Adrienne: 204 runs imported, Boise added as main, Santa Cruz 70.3 → half ~2:00:31, marathon today 4:13–4:27, projection 3:55–4:21. Her HR model still needs HR on runs (41/204 have it, none this summer).

### Conversations, Team page, own best efforts, flat-half equivalents (2026-09-29, deployed, uncommitted)
- **Conversations**: `GET /api/portal/<a>/chat/threads`, `POST …/chat/threads/<t>/open` (makes it current). Coach header → "Conversations" list. Worker resumes the thread's claude session (state file); if that fails or is missing, it starts fresh with full context + the thread transcript (job.transcript, last 40 turns).
- **Team**: `GET /api/team` (connected athletes) + Team tab; teammate pages open as `/a/<them>?viewer=<me>` = read-only (no coach/profile/flags/race edit/rebuild). Note: Strava's Nov-2024 API terms say data shown to an athlete should be their own; Brad chose team visibility knowingly (friends/family, LAN, all follow each other on Strava).
- **besteffort.py** replaces Strava best_efforts for PRs: per-run speed ceiling 1.6× median (4–7 m/s), 15-s rolling median; lone spikes smoothed, ≤30 s bursts (GPS catch-up) capped, longer = car/bike → windows disqualified; runs averaging >5.5 m/s skipped; label `nobest` (✕ in the PR table) excludes a run; race with `official_mi` counts at clock time. Cache table best_efforts (VERSION 3). Adrienne's 5:56 mile was her own phone-GPS run 2024-04-22 (+ a car/bike stretch in a 2026-06-07 "run"), not Brad's data.
- **racefit** equivalents are now "worth a flat half": grade from run_metrics.flat_eq_m (runmodel blend: Brad's personal grade curve + Minetti) and altitude to 4,300 ft (K_ALT_KFT); aged value shown separately. UV half: GPS says 680 ft net drop → course worth 3:41 → flat 1:56:07; the 1:52→2:01 Brad saw earlier was the >1-yr aging, not elevation.

## Rachel onboarded; per-athlete calibration and altitude (2026-09-30)

- Rachel connected by one-time invite; 590 runs (2018→), HR from 2025-04. 15 races labelled
  (official distance, clock time); runs with Avery and pacing duties noted, not races. Boise River
  Marathon 2025 marked COMPROMISED (stomach trouble from before halfway).
- `runmodel.CALIBRATION` holds each calibrated athlete's race_k, anchor half and distance curve.
  Rachel: race_k 24820 from six halves (leave-one-out ~1.9 %; Fit One at its measured 12.99 mi via label `course_mi` — model and training use it, records keep the official 13.1), b_short 1.05 (Brad 1.097: her 5Ks
  are relatively slower), generic marathon band 1.10–1.20. HR constants and the efficiency
  pipeline are shared; the per-athlete fit absorbs the difference.
- Home altitude per athlete (`runmodel.HOME`, Rachel = Boise 2,730 ft, others Salt Lake 4,300 ft).
  Predictions, fitness chart and race equivalents are shown at home; races have a course
  altitude (`races.alt_ft`) used for their range. Fitness and Team pages have a "Show times at"
  toggle (home / Salt Lake / Boise). The altitude term is small (0.5 %/1000 ft ≈ 0.8 % SLC↔Boise).
- Retro check, Boise 2025: model said ~4:05 on the course (3:55–4:16); she ran 9:05/mi to mile 12,
  then slowed with stops, 4:18 moving / 4:33 clock.
