# Modeled UV index (sunscreen timing)

Purpose: know *when* in the day sunscreen is warranted for the daily bike/run/kid
outing, without trusting the Ambient Weather station's absolute UV number.

## What it is

A clear/typical-sky **climatology** model, not a measurement:

    UVI = 11.15 * sin(solar_elevation) ^ 2.32

`A=11.15`, `P=2.32` were fitted against Salt Lake City's published monthly maximum
UV; the exponent matches the ~2.4 in published simplified UV models. Solar
elevation comes from the NOAA equations (no library). The WHO protection
threshold UVI ≥ 3 therefore reduces to one number: **sun elevation ≥ 34.6°**.
Solar noon here is ~13:20 MDT / 12:20 MST (SLC sits 27 min west of the 105°W
meridian). By this fit the noon peak stays under 3 from **Nov 3 to Feb 9**.

What it does NOT know: clouds, ozone (fall runs ~10% above spring at the same
sun height), smoke, snow reflection. Treat values as ±15%. Broken cloud can push
real UV *above* clear sky for minutes, so the recommendation always uses the
modeled clear-sky value; the measured station value is shown only as
"actual right now".

### Sensor check (2026-09-23, hourly stats since 2026-07-27, clearest-quartile days)

| Sun elevation | Station (hourly mean) | Model | Ratio |
|---|---|---|---|
| 20–30° | 1.4 | 1.4 | 1.07 |
| 30–40° | 2.9 | 2.9 | 0.99 |
| 40–50° | 4.6 | 5.0 | 0.92 |
| 50–60° | 5.1 | 6.9 | 0.73 |
| 60–70° | 6.0 | 8.6 | 0.70 |

The station agrees with the fit around the UVI-3 threshold and reads 25–30% low
at high sun (integer resolution, flat spectral response). Its solar-radiation
channel also tops out ~830 W/m² on clear August days (true clear sky ≈ 1000), so
neither absolute number is trusted; ratios to their own clear-day envelope are.

## Service

`home_config/uv-model/` — `uv_model.py` + `test_uv_model.py` + Dockerfile + compose.
Container `uv-model` (host network, restart unless-stopped) on the beelink.

```bash
cd /home/pi/home_config/uv-model
python3 test_uv_model.py          # geometry + window + payload checks (no pytest needed)
python3 uv_model.py --once        # print the payload, no MQTT
python3 uv_model.py --day 2026-12-21
docker compose up -d --build      # deploy
docker logs uv-model --tail 5
```

Publishes every 5 min (aligned) to the anonymous broker `192.168.10.217:1883`:

- `uv_model/state` (retained JSON): `uvi`, `uvi_peak`, `level`, `protection`,
  `solar_elevation`, `solar_noon`, `uv3_start`, `uv3_end`, `threshold_elevation`,
  `curve` (15-min modeled UVI 05:00–21:00 local), `model`.
- `uv_model/status` online/offline (LWT) — entities go unavailable if it dies.
- HA MQTT discovery under `homeassistant/…/uv_model/*/config` (device "Modeled UV",
  `object_id` pins the entity ids). **No HA restart needed**, ever.

| Entity | Source |
|---|---|
| `sensor.modeled_uv_index` | `uvi` (attrs: level, solar_elevation) |
| `sensor.modeled_uv_peak_today` | `uvi_peak` (attrs: solar_noon, uv3_start/end, curve — the dashboard reads these) |
| `binary_sensor.uv_protection_recommended` | `uvi >= 3` |
| `sensor.uv_protection_start` / `_end` | timestamps, `unknown` in winter |

The curve/window live on the *peak* sensor on purpose: they change once a day, so
the recorder stores one attributes row instead of one per 5-min tick.

Tunables are compose env: `UV_LAT/UV_LON`, `UV_A/UV_P`, `UV_THRESHOLD`,
`UV_INTERVAL`, `MQTT_HOST`, `TZ`. To recalibrate `UV_A`, compare clear-day
station peaks to the model (see table) — remember the station under-reads at high
sun, so don't fit A to it blindly.

## Kitchen display

`dashboard_webapp`: two chips on the Indoor Climate tile next to AQI/Ozone —
`☀ UV Now` and `☀ UV Peak` — coloured by WHO band (<3 green, 3–5.9 yellow, 6–7.9
orange, 8–10.9 red, ≥11 purple), tooltip = today's sunscreen window. Tapping
either opens the trend modal in a UV-specific mode: dashed modeled curve for the
day, solid weather-station line so far today, yellow UVI-3 line with the
"sunscreen" band shaded above it, "now" marker, and a summary line
`Now · Station · Peak at HH:MM · Sunscreen HH:MM – HH:MM`.

Code: `app/config.py` (`AQI_ENTITIES` uv_now/uv_peak, `UV_MEASURED_ENTITY`,
`HISTORY_SERIES.uv_measured`), `app/static/app.js` (`uvClass`, `renderUvTrendModal`,
`uvMeasuredToday`), chip icon CSS in `styles.css`, band/caption CSS in `editorial.css`.
Deploy: `cd /home/pi/dashboard_webapp && docker compose up -d --build`, then reload
the kiosk Chromium (kitchen-dashboard-display-guide.md → Deploy / Update).
