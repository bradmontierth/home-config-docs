# Flight Watch (SLC + BOI → OGG fare watcher)

Purpose: catch a cheap week for the January/February 2027 Maui trip for both
families (us from SLC, sister's family from BOI) without checking Google Flights
by hand, and make it obvious when the nightly scan silently stops working.

Container: `home_config/flight-watch/` (`docker compose up -d --build`), page
http://192.168.10.217:8783 (Homepage → Vibe Coded Apps → Flight Watch).
Data: `flight-watch/data/flightwatch.db` (SQLite, WAL). Config: `config.yaml`
(mounted read-only, re-read before every scan, no restart needed).

## How it works

- **Source**: Google Flights, scraped with `fast-flights` 3.1 (builds the
  base64 protobuf `tfs` URL param, parses the server-rendered `ds:1` blob).
  No API key, no login. Google has no public flights API since 2018.
- **Grid**: every departure `depart_from..depart_to` × `nights` list × origin,
  round trip, 1 adult economy, USD. Each leg carries Google's own
  `max_duration_minutes` filter (layovers included), so overnight-in-SAN
  "1-stops" never enter the DB. 320 queries/night, `pace_seconds` apart
  (~25–30 min).
- **Round-trip caveat**: Google returns *outbound* options, each priced with
  its cheapest matching return. The DB stores one row per outbound option, so
  "price" = best round trip for that outbound, not every return combination.
- **Elapsed time** is arrival − departure across zones (winter offsets table in
  `scanner.py`); the leg-duration sum hides layovers and is not used.
- **Rules** (`rules.py`, pure functions, `test_rules.py`), evaluated after each run:
  `nonstop_max`, `connecting_max` (cheapest qualifying date pair per origin,
  message lists the top 3), `new_low` (beats the trailing
  `new_low_window_days` minimum by `new_low_pct`), `combined` (cheapest
  SLC+BOI for the *same* dates ≤ `combined_max` or a new low). Each rule key
  re-fires only after a further `realert_drop` or `realert_days`.
- **Notify**: Pushover (creds via `env_file` from `cecret_lake/pushover/.env`,
  device from `PUSHOVER_DEVICE`, never hardcoded) with a deep link to those
  dates in Google Flights. MQTT on the HA broker: `flightwatch/status`
  (retained run summary), `flightwatch/best/<ORIGIN>` (retained),
  `flightwatch/alert` (per alert) for Node-RED / kiosk use.
- **Rate-limit tell**: `blocked_after_consecutive_errors` consecutive
  parse/HTTP failures abort the run with status `blocked`, a Pushover warning
  (max once per 20 h), and a red bar on the page. A captcha page shows up as a
  parse error (no `ds:1` script), not an HTTP error.
- **Schedule**: `scan_at` local time nightly; on container start it scans
  immediately if the last completed run is older than `catch_up_hours`.
  `POST /api/scan` or the page's *Scan now* button triggers one by hand.

## Page

Tiles (last/next scan, run status, best nonstop and best in-cap connecting per
origin), a per-run line chart (solid = nonstop, dashed = connecting; blue SLC,
orange BOI), run-health bars (height = queries answered, colour = status), one
heat map per origin (departure × nights, darker = cheaper, ● = a nonstop exists),
tap a cell for the itineraries + that pair's price history + Google Flights link,
and the alert log. A page that stops gaining a bar per night = the scan is dead.

## Baseline (first pass 2026-09-24)

Delta is the only SLC–OGG nonstop; BOI has none. Nonstop was $1,000–1,950 for
every week probed (we usually pay $500–600 mid-Jan to early Feb), the in-cap
connecting floor was ~$719 SLC (Alaska via HNL) and ~$573 BOI (United via SFO),
both on Jan 30 → Feb 6. Presidents' Day week (Feb 13) is the priciest.

## Fallback if Google blocks us

Swap `scanner.build_query`/`get_flights` for SerpApi's Google Flights endpoint
(same fields, ~$50 per 5k searches ≈ two weeks of nightly passes) or add a
proxy via `get_flights(q, proxy=...)`. Amadeus Self-Service (`nonStop=true`)
is free but its GDS prices don't match what Delta sells.
