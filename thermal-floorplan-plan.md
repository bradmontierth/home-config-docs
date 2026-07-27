# Home Thermal Floor Plan — Build Plan

Full-screen thermal view for the kitchen dashboard: three stacked SVG floor plans with
each room tinted by its live temperature, KPI row per floor, and a tap-through 24-hour
trend chart overlaid against outdoor temperature.

_Drafted 2026-07-26. **All phases (0–5) built and live 2026-07-27.**_

> **Build note (2026-07-27):** this ships as its own project at `/home/pi/thermal_viewer`
> (FastAPI, Docker, host network, port **8779**) rather than inside `dashboard_webapp`.
> Phase 5 becomes an integration step — tile → iframe or a port of the view — instead
> of building inside the dashboard from the start. See that repo's README.

## Source material

- `floor.png` — builder sheet ("Fairfax", 2,373 SF): **upper** + **main** levels, with
  handwritten dimensions. Two option insets on this sheet are **not** our build.
- `lower.png` — **lower** level, L-shaped: two bedrooms + bath + utility across the top,
  rec room bottom-right, cold storage off the back, stairs center.
- We do **not** have the 4th-bedroom option (that space is the loft).

## Where it lives

Host app is the existing kitchen dashboard — see `kitchen-dashboard-display-guide.md`.

| | |
|---|---|
| Repo | `/home/pi/dashboard_webapp` |
| Backend | FastAPI, `app/main.py` |
| Frontend | `app/static/index.html` + `app.js` + `styles.css` |
| Runs | Docker on Beelink, `http://192.168.10.217:8777/` |
| Display | kiosk Chromium on `display-pi` |
| HA token | `/home/pi/cecret_lake/dashboard_webapp/ha_token` |

The overlay pattern already exists — `#overlay` with a `← Back` button, plus `#places-view`
and `#list-view` as full-screen takeovers. The thermal view copies that; no new pattern.

**Code is baked into the image.** Deploy is `docker compose up -d --build`, not a refresh.

## Data source decision

**HA REST API only.** Single auth, single naming scheme, ~10 days of raw history — more
than a 24 h chart needs.

MySQL (`hubitat_logging`) was evaluated and **deliberately left out of scope**. It does hold
long-term data for outdoor, indoor, attic, basement closet, fridge/freezer and ~6 Zigbee
rooms, but **7 of our rooms have no history there at all** (Simon's, loft, laundry, master
bath, entry bath, bike room, utility closet), and its `displayName` scheme is a legacy
Hubitat subset that has silently drifted — two devices stopped logging years ago unnoticed.
Wiring it in means two data paths plus a hand-maintained name-reconciliation table, for
partial coverage. Revisit only if we want multi-year comparisons.

## Room → sensor mapping

Primary drives room fill color. Window sensors are **chart traces only** — never map color,
because a window reading is solar gain on the glass, not room air, and which windows are hot
changes with season and time of day.

### Upper level
| Room | Primary | Window trace |
|---|---|---|
| Master Bedroom | `sensor.master_bed_motion_temperature` | `master_bed_window` |
| Master Bath | `sensor.master_bath_motion_temperature` | `master_bath_window` |
| Simon's Room | `sensor.simon_motion_sensor_temperature` ⚠️ battery | `simon_room_window` |
| Claire's Room | `sensor.adrienne_office_motion_temperature` | `adrienne_office_window` |
| Loft | `sensor.loft_motion_sensor_temperature` | `loft_window` |
| Laundry | `sensor.laundry_motion_temperature` | `laundry_window` |
| Attic | `sensor.weather_station_extra_temperature_sensor_7` | — (unconditioned scale) |

Claire's room is `adrienne_office_*` — offices were shuffled when the kids were born.
Do not "fix" this name.

### Main level
| Room | Primary | Window trace |
|---|---|---|
| Kitchen | `sensor.weather_station_indoor_temperature` | `kitchen_sink_window` |
| Family Room | `sensor.my_ecobee_current_temperature` | `family_room_window` |
| Office | `sensor.office_motion_sensor_temperature` | `office_window` |
| Entry Bath | `sensor.entry_bath_motion_temperature` | — |
| Entry | — (no interior sensor) | `front_door` |
| Garage | `sensor.garage_motion_temperature` | — (unconditioned scale) |

- `kitchen_multisense_temperature` is **dead** — last state change 2026-07-21. Do not use.
- Ecobee thermostat is physically in the family room. Known to read ~1–2 °F high; that is a
  constant offset and does not distort trend shape. Ship it, revisit later.
- Kitchen weather-station console likely reads 1–2 °F high from self-heating. Same reasoning.

### Lower level
| Room | Primary | Notes |
|---|---|---|
| Rec / Family Room | `sensor.weather_station_extra_temperature_sensor_4` | "Closet Temp"; add `basement_family_motion` as 2nd chart trace |
| Bike Room | `sensor.bike_room_motion_temperature` | |
| Basement Bath | `sensor.basement_bath_motion_temperature` | |
| Utility | `sensor.utility_closet_water_sensor_temperature` | prefer water over door sensor |
| Cold Storage | `sensor.cold_storage_water_sensor_temperature` | prefer water over door sensor |
| Guest Bedroom | — (no interior sensor) | window-only; leave blank |

Door/contact sensors in this house read **5–7 °F high** (cold storage door vs water: +5.1;
utility door vs water: +6.7). Prefer motion/water sensors as primaries wherever both exist.

The basement closet sensor has a **−0.98 correlation with outdoor temp** — it gets colder as
the day gets hotter, because it tracks AC duty cycle rather than solar gain. It also runs
2–3 °F below the room itself (thermally buffered closet) but reports 7× more often. Hence:
closet as primary for a clean line, room sensor as a second trace for the truth.

### Excluded
`*_internal_temperature` (chip temps, 107–146 °F), fridge/freezer (appliances — a 34 °F box
would wreck the color scale), `weather_station_extra_temperature_sensor_1` (unidentified —
see Open Questions).

Baseline for all charts: `sensor.weather_station_outdoor_temperature`.

Mapping lives in `app/data/thermal_rooms.json`, hot-reloadable, so remapping a sensor is a
JSON edit and a container restart — never a redraw.

## Phase 0 — Node-RED discovery fix ✅ DONE 2026-07-27

Node-RED tab **"Weather Station"**, function node **`62e5318ceff3f60e`** ("config/state")
publishes all 14 weather-station sensors via MQTT discovery. It sets neither `device_class`
nor `state_class`, which is why these entities get **no long-term statistics** and why the
outdoor sensor doesn't even register as a temperature entity.

Add both fields to each entry and pass them through in `configPayload`:

```js
{ sensor: "temp1f", name: "Extra Temperature Sensor 1", unit: "°F",
  value_path: "temp1f", icon: "mdi:thermometer",
  device_class: "temperature", state_class: "measurement" },

// in the builder:
...(config.device_class && { device_class: config.device_class }),
...(config.state_class  && { state_class:  config.state_class  }),
```

Redeploy republishes the retained configs; HA updates entities in place. **No HA restart.**
Statistics accumulate from that moment forward — insurance for a future "this July vs last
July" without needing a second data path.

Gotchas for editing flows live: see `nodered-flow-agent-guide.md`.

**What actually shipped.** Two nodes needed the fix, not one. `62e5318ceff3f60e` carries
the extra WH31 pucks (attic, rec-room closet); the outdoor baseline and the kitchen
indoor sensor live in a second `config/state` node, **`6436adf570f12f1e`** (Ambient base
station). Both were patched — 24 discovery topics now carry `device_class` +
`state_class`. Rain sensors were deliberately skipped: unit `in` is ambiguous between
accumulation and intensity, and a wrong `device_class` breaks entity setup.
`temperature_co2` also had its unit corrected from `F` to `°F`, which `device_class:
temperature` requires.

**The redeploy does _not_ republish the configs.** The config branch is gated behind
globals (`weatherStationConfig2`, `weatherStationAmbientConfig`) that are persisted to
disk, so they stay `true` across a deploy and the retained payloads never re-emit. The
24 retained topics were instead read back, patched and republished directly to the
broker. HA updated the entities in place, no restart, no errors in the log.

## Phase 1 — Backend ✅ DONE 2026-07-27 (`/home/pi/thermal_viewer`)

New `app/thermal.py`, mounted from `main.py`.

- `GET /api/thermal/current` → per-room `{room_id, label, temp, sensor, stale, floor}`,
  plus KPIs: outdoor, and time-weighted mean per floor.
- `GET /api/thermal/history?room=<id>&hours=24` → room series + window trace (if any) +
  outdoor series.

Rules:
- **Step interpolation, not linear.** Zigbee sensors report on change-threshold, not a
  clock — `master_bed_motion` logs ~21 points/24 h while outdoor logs ~750. Drawing straight
  lines between sparse points invents smooth ramps that never happened.
- **Time-weighted floor averages.** A naive mean lets a chatty sensor dominate.
- **Staleness:** if `last_changed` older than 3 h, mark `stale: true`. Frontend renders gray
  with a "last seen" label rather than a confident wrong number. This is what would have
  caught the dead kitchen sensor.
- Cache current state ~30 s; the kiosk polls continuously.
- HA history API note: send **timezone-aware** timestamps. Naive ones get parsed as local
  and silently shift the window by the UTC offset.

**What actually shipped.** All 30 entities in the mapping were confirmed to exist in HA
first. Live numbers back the plan's claims: master bedroom logs 25 points/24 h against
the weather station's 818, and the rec-room closet puck logs 173 against the room
sensor's 26 while running ~2.7 °F cooler. The staleness rule caught the known dead
Simon's-room sensor (~10 days) **and a second one the plan did not know about — the
basement bath, ~3.7 days stale.** Floor averages exclude stale and unconditioned rooms.

`GET /api/thermal/rooms` was added beyond the plan (serves the mapping to the frontend),
and the mapping hot-reloads on mtime change, so a sensor swap needs no restart at all.

## Phase 2 — SVG floor plans ✅ DONE 2026-07-27

Three hand-authored SVGs in `app/static/floorplans/` — `upper.svg`, `main.svg`, `lower.svg`.
Traced from `floor.png` / `lower.png` using the handwritten dimensions for proportion.

- One `<path>` per room carrying a stable `id` matching `thermal_rooms.json`.
- Shared `viewBox` scale across floors so they read as one building.
- Separate layer for walls/labels above the fill layer.
- Rooms with no sensor still get a path — rendered with a hatch fill.

Lower level is the least certain (traced from a lower-res sheet); expect one correction pass.

**What actually shipped.** Scale is 1 ft = 10 user units with an identical
`viewBox="0 0 391 420"` on all three floors. The interior-wall layer is *generated*
from the room paths (`scripts/wallgen.py`) so walls cannot drift from the shapes they
outline; `tests/check.py` fails if they do, and also fails if any room in
`thermal_rooms.json` has no shape or vice versa. `y 380–415` is an off-plan strip for
the attic, which has no footprint. `/` serves a plain preview (no colour) so the plans
and the mapping can be checked by eye before anything is wired to colour.

**The plans are MIRRORED** (Brad, 2026-07-27): our house is the flipped version of the
printed sheet, so every x is `391 − x` of the plan as drawn — garage right, family room
left, rec room bottom-left.

**Wall positions are measured, not eyeballed.** The source PNGs were scanned for dark
rows/columns per region and wall centres converted straight into SVG units. That found
real deviations in the first pass (upper closet depth off by 1.7 ft, master-bath top
by 1.8 ft, mid-floor wall by 0.9 ft, lower level's whole top band by 1.6 ft, garage
depth by 0.8 ft). Caveat for anyone repeating this: the blue handwritten dimension
arrows scan as "dark" too — several apparent walls mid-loft are annotation lines.

**The lower level is drawn unfinished.** Its bedrooms, bath and W.I.C. are *dashed*
future partitions on the sheet, not built walls; only the utility room, mechanical box
and L-shaped shell are solid. They are drawn solid here on the assumption they were
built as proposed — worth confirming with the rest of the eyes-on pass.

**Room identities CONFIRMED by Brad 2026-07-27** — both guesses were right:
- Upper: Bedroom 2 (upper right) = `simon_room`, Bedroom 3 (lower right) = `claire_room`.
- Lower: smaller bedroom (top-right) = `bike_room`, larger (top-left) = `guest_bedroom`.

**Adjacent room edges must be the same coordinate or ≥ 0.6 ft apart.** A first pass
left eight pairs 0.1–0.4 ft apart, which render as one smudged double line instead of
a wall (Brad spotted it on the lower level). `tests/check.py` now fails on any such
pair, and on unfilled slivers between rooms.

## Phase 3 — Map view + KPIs ✅ DONE 2026-07-27

- Stack order **upper → main → lower**, top to bottom. Reads as a cross-section of the
  house, and puts the hottest floor at the top where heat actually goes.
- KPI row above each floor: floor average, plus outdoor + whole-house average in the header.
- **Two color scales:**
  - *Conditioned* rooms: 60–80 °F ramp.
  - *Unconditioned* (attic, garage): separate wider ramp, visually distinguished by hatch or
    dashed border. The attic hit **130 °F today and 140 °F historically** — on a shared ramp
    it pegs the scale and makes an 80 °F bedroom look temperate.
- Legend showing both ramps.
- Load the `dataviz` skill before picking actual palette values.

**What actually shipped.** Conditioned rooms turned out to be *polarity* about the
setpoint, not magnitude, so they use a **diverging** blue↔red ramp (60/70/80 °F) with
a neutral midpoint; the attic and garage are plain magnitude over a wide range, so
they use a **sequential** orange ramp (50–140 °F) plus a dashed outline. Palette
values were validated against the **kitchen dashboard's own surface (#161A21)**, not
the skill's default surface — the chart's categorical trio passes all six checks
(CVD ΔE 9.4, normal-vision 26.5, contrast ≥ 3:1) and both room ramps rise
monotonically in lightness away from the midpoint. Rooms with no sensor are hatched
rather than given an invented colour; stale rooms keep the neutral fill and show
their age in the label, with a summary line at the bottom of the map.

**Deviation from the plan: floors run left→right on landscape, not stacked.** Three
stacked plans on a 1080p kiosk leave each ~300 px tall and the room labels unreadable;
side by side each plan is ~570 px wide and legible across the kitchen. Portrait still
stacks them upper→main→lower, so the cross-section reading survives where it fits.

## Phase 4 — Drill-down chart ✅ DONE 2026-07-27

Tap a room → 24 h line chart: room primary, its window sensor (if any), outdoor baseline.
Step interpolation. Back arrow returns to the map.

This is where the solar story actually lives — a window sensor spiking at 7 PM and decaying
by midnight is legible here in a way no static map badge could be.

**And it does.** Master bedroom's window sensor peaks at 86 °F around 17:00 and decays to
66 °F by 06:00 while the room itself only moves 65→73 °F. All three series share **one**
y-axis (a second scale would let the room's 8 °F swing masquerade as the outdoor's 24 °F
one). Added beyond the plan, both required by the dataviz skill: a crosshair tooltip, and
a table view of the same 24 hours as numbers. Direct labels are de-collided — two lines
ending a degree apart printed on top of each other in the first cut.

## Phase 5 — Kitchen dashboard integration ✅ DONE 2026-07-27

Tile on the main dashboard ("Temperatures") → full-screen `#thermal-view` → `← Back`.
Follow the existing `#places-view` / `#list-view` markup and CSS conventions.

Deploy: `cd /home/pi/dashboard_webapp && docker compose up -d --build`

**What actually shipped — nearly no dashboard code.** Because the viewer is its own app,
it slots into the dashboard's existing *app-overlay* mechanism (the one Mealie uses)
rather than a new `#thermal-view`. Total change to `dashboard_webapp`: one
`mdi:thermometer` glyph in `APP_ICONS`, plus a line in `.env`:

```
EXTRA_APPS=thermal|Temperatures|http://192.168.10.217:8779/|mdi:thermometer
```

The overlay already supplies the full-screen takeover and the `← Back` bar, and the
viewer uses the dashboard's own surface/ink tokens, so it reads as one app. Kiosk
Chromium was restarted (by exact PID, not `pkill`) so the tile appears on the display.

**`dashboard_webapp` is NOT committed** — the icon line sits alongside pre-existing
uncommitted grid-outage work from another session, and separating them was not mine
to do. It is deployed and live.

### Screenshotting the kiosk (useful for any dashboard work)

`chromium --screenshot` captures before first paint on display-pi, and
`--virtual-time-budget` hangs. Drive CDP instead — headless Chromium with
`--remote-debugging-port`, then `Page.navigate` / `Page.captureScreenshot` over the
websocket (`origin=None`, or Chromium 403s it). That renders at real kiosk resolution
without touching the visible session. The working script is in this session's
scratchpad as `shot.py`; `grim` still works for the live display.

## Open questions

1. **`weather_station_extra_temperature_sensor_1`** — unidentified. MySQL knows it as
   "Ecowitt Remote 1" but it stopped logging Sept 2022. Profile: high thermal mass,
   west-facing, unconditioned, shares air with outside; peaks ~3 h after the attic and stays
   ~15 °F warmer overnight. Garage is a plausible-but-unconfirmed guess (~60%). To identify:
   find the WH31 puck set to **CH1**, or freezer-test a candidate and watch the entity.
   Parked — not needed for v1.
2. **Simon's room battery** — replace; sensor is stale (~10 days as of 2026-07-27).
2b. **Basement bath sensor** — found stale 2026-07-27 (~3.7 days, last change 07-23).
   Not in the original plan; same treatment as Simon's.
3. **Entry / Guest Bedroom** — ship blank; add sensors later if wanted.
4. Lower-level room shapes need one eyes-on correction pass after first render.

## Known hazards

- Docker image bakes the code — a browser refresh will not pick up changes.
- Kiosk Chromium needs `--password-store=basic`.
- Do **not** pattern-kill processes on this box (see memory `no-broad-pkill`).
- Verify the room mapping with Brad before wiring color — he wants an eyes-on approval pass.
