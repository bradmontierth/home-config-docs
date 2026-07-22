# Business Hours Intent (Google Places) Plan

**Status:** BUILT + DEPLOYED 2026-07-21. Cloud keys and hard quotas,
orchestrator place/hours intents, cache/fallback, and the kitchen display's
Google-backed map evidence view are live and visually validated on the kiosk.
**Where:** `home_config/voice-assistant/orchestrator/` (deployed as
`voice-orchestrator` via `/home/pi/voice-pipeline/docker-compose.yml`, port
8785) plus `/home/pi/dashboard_webapp`. Satellite and Node-RED are untouched.

## Goal

"What time does Home Depot close?" / "When does Costco open?" answers in ~2–3s from the Google Places API instead of falling back to the slow (3–7s) LLM+web-search `ask` path. Authoritative hours including holiday overrides — restores the Google Home behavior.

Decisions made 2026-07-20 (Brad): use Google Places API (New) via a personal Cloud project · billing exposure must be provably $0, guarded by quota caps, not just budget alerts · clone the sports intent pattern (fast API, silent fallback to `ask`).

## Pricing reality (drives the guard math)

- March 2025 pricing: no more $200 credit; per-SKU free monthly allowances — Essentials 10K, Pro 5K, **Enterprise 1K**.
- `regularOpeningHours` / `currentOpeningHours` are **Enterprise-tier fields** (confirmed on the Place Details (New) field-mask docs). Request bills at the highest SKU of any requested field → each hours lookup = one **Text Search Enterprise** call: **1,000 free/month, ~$35/1K (~3.5¢) past that**.
- Expected real usage: ~8–10 asks/month, less after caching. ~1% of free tier.

## Phase 0 — Cloud project + guard stack

Order matters: quota cap and key restrictions go in **before** the key ever reaches the orchestrator.

1. **Fresh dedicated project** (e.g. `home-voice-places`). Enable **Places API (New) only** — nothing else billable on the project. Attach billing (card required even for free tier; the steps below are why that's safe).
2. **Quota cap — the actual hard stop.** Places API (New) → Quotas →
   `SearchTextRequest per day` is set from 75,000 to **25** (confirmed in the
   live console 2026-07-21). Math: 25 × 31 = 775/month < 1,000 free. The
   225-call buffer accommodates quota-enforcement lag; don't raise toward
   32/day. The allowance is aggregated across projects on the billing account,
   so this assumes no other project consumes Text Search Enterprise calls.
3. **API key restrictions:** restrict key to Places API (New) only. Retaining the
   **IP restriction to the home public IP** is recommended defense-in-depth:
   residential IP rotation makes calls 403 and the intent silently falls back
   to `ask`, so failure mode is "slow again," not "broken." The confirmed
   25/day Cloud quota limits exposure even if Brad elects to remove the IP
   restriction. (Optional later: a public-IP-change Pushover watchdog.)
4. **Budget alert at $1** (email only, no enforcement — early warning).
5. **Secret placement:** key file at `/home/pi/cecret_lake/google_places/api_key` (raw single line or `GOOGLE_PLACES_KEY=` dotenv line, matching the `_read_key()` idiom).

Guard summary: Cloud `SearchTextRequest per day=25` is the primary stop;
API-only/IP key restrictions, the $1 alert, 24h cache, and application limit of
20/day are additional layers.

Maps JavaScript has its own project-level hard cap: `Map loads per day=25`.
The dashboard creates the Google map lazily on the first place question and
retains that map object for the kiosk browser session, so changing queries and
pins does not create another map load.

## Phase 1 — orchestrator intent

Clone the sports shape (`sports.py`, dispatch at `app.py:543-560`).

### 1. `intent.py`

- Add `business_hours` to `INTENTS` (`intent.py:16-21`).
- Prompt rule (system prompt block `intent.py:29-114`): match open/close/hours questions about a named business → `intent: business_hours`, business name into `query`, plus `hours_when`: `"open" | "close" | "now" | "today"` (default `today`; `"now"` for "is Costco open?"). Coercion in `_validate` (`intent.py:155`).

### 2. New `orchestrator/places.py`

- `async def handle(parsed) -> dict | None` — returns the spoken response plus
  a structured `places_view` payload, or `None` (can't resolve / no key / quota
  error / timeout).
- **One HTTP call per query:** Text Search (New) `POST https://places.googleapis.com/v1/places:searchText`
  - body: `textQuery` = business name, 10-mile `locationBias` circle around
    `HOME_LAT/LON`, `maxResultCount: 20`, `regionCode: "US"`.
  - The field mask also includes place ID and coordinates. One response supplies
    every pin, address, current status, and weekly schedule; there are no
    per-location Place Details calls.
  - Results are name-confidence checked, filtered to the exact 10-mile circle,
    straight-line sorted, deduplicated, and capped at eight display results.
    Exact canonical storefronts beat same-address departments (for example,
    `The Home Depot` beats `Garden Center at The Home Depot`).
  - per-call `httpx.AsyncClient(timeout=8)` like `sports.py:298`.
- **Cache:** module dict keyed by normalized business name, max **24h TTL**.
  It expires just after Google's earliest `nextOpenTime` / `nextCloseTime`, so
  cached `openNow` never survives a store-state transition. Cache hit = zero
  API calls and an instant answer.
- **Client-side daily budget:** module counter, max ~20 calls/day; over budget → return `None` (falls back to `ask`). Log when tripped.
- **Answer formatting:** from `currentOpeningHours` (fall back `regularOpeningHours`) pick today's period in `ASK_TIMEZONE` (`config.py:78`):
  - close → "Home Depot closes at 10 PM tonight."
  - open (already open) → "Costco opened at 10 AM this morning." · (not yet) → "…opens at 10 AM tomorrow."
  - now → "Yes, Costco is open until 8:30 PM." / "No, Costco is closed — it opens at 10 AM tomorrow."
  - closed all day (holiday) → say so explicitly.
  - 24h places → "…is open 24 hours."
- **Key loading:** copy `openrouter.py:_read_key()` (lines 26-51) — file path from `GOOGLE_PLACES_KEY_FILE` env, dotenv-or-raw parse, cached.

### 3. `app.py`

- `business_hours` and `place_search` share the same guarded handler. `None` or
  exception falls through to `ask_mod.handle_ask`; success is remembered for
  pronoun follow-ups. On success, `show_places` is emitted immediately before
  reply TTS, so visual evidence and speech progress in parallel. The later
  generic `response` event updates the summary without covering the map.

### 4. `config.py` + compose

- `HOME_LAT` / `HOME_LON` in `config.py` (env-overridable). Static value — no HA round-trip per query (HA `/api/config` with the existing token is the fallback source if we ever want it dynamic).
- `voice-pipeline/docker-compose.yml`: add `:ro` mount `/home/pi/cecret_lake/google_places/api_key:/secrets/google_places_key` + env `GOOGLE_PLACES_KEY_FILE=/secrets/google_places_key`, `HOME_LAT`, `HOME_LON`.

## Testing

1. Curl the Text Search endpoint directly from the Beelink with the field mask above (verifies key, IP restriction, and that hours come back for Home Depot/Costco).
2. `POST /command` text bypass (`app.py:813`): "what time does home depot close", "when does costco open", "is walmart open", "what time does el farol close" (small local place), "what time does blorbcorp close" (nonsense → must fall back to `ask` cleanly).
3. Repeat a query → confirm cache hit in logs (no second API call).
4. Console check after a day: Places API request count matches expectations (each live test = 1 Enterprise call).
5. Live voice test in kitchen; confirm latency ≈ sports intent (2–3s).
6. Follow-up test: "what time does costco close?" → "when does it open?" (falls to `ask` with remembered context — acceptable v1).

## Phase 2 — Kitchen display map evidence

- Added `place_search` for “where is Chipotle,” “show me Home Depot,” “are
  there Costcos nearby,” “closest Walgreens,” and “how far is Walmart.” Hours
  questions still use `business_hours`; both share one Places handler and one
  Text Search call.
- Google Cloud uses a separate `kitchen-dashboard-maps` browser key restricted
  to Maps JavaScript API and `http://192.168.10.217:8777/*`. The server-side
  Places key is never returned to the browser.
- Browser key and Map ID live in
  `/home/pi/cecret_lake/dashboard_webapp/google_maps_browser_key` and
  `google_maps_map_id`, mounted read-only. `/api/bootstrap` supplies only the
  referrer-restricted browser configuration.
- `show_places` renders a fullscreen evidence view: Google map and attribution,
  exact-radius circle, numbered pins, closest highlight, selectable results,
  address, open/closed status, special-hours badge, and Monday–Sunday schedule
  with today emphasized.
- The view closes explicitly or after 90 seconds; interaction extends it to
  180 seconds. A new place question reuses the map and replaces markers. Timer
  alarms and non-place answers retire stale evidence.
- The kiosk Chromium/GLES stack drew vector overlays but not the vector
  basemap. Production forces Google's raster renderer and numbered Google
  markers, restoring roads, controls, logo, and required attribution. The Map
  ID remains configured for a future vector-capable display/browser.

### Deployment validation — 2026-07-21

- Direct Beelink Text Search succeeded with the restricted key and returned
  nearby Home Depot `currentOpeningHours` + `regularOpeningHours`.
- Thirteen formatter, evidence-payload, distance/radius, canonical-store, cache,
  and ask regression tests pass in the production image.
- Text bypass passed: Home Depot close, Costco open, Walmart open-now, and El
  Farol hours today; measured latency 2.2–3.5s.
- Repeating Home Depot logged `Places cache hit` and made no second API call.
- Google corrected nonsense `blorbcorp` to Labcorp; a RapidFuzz display-name
  confidence gate now rejects that weak match (75 < 80), negative-caches it,
  and falls back to `ask` instead of speaking the wrong business hours.
- Live `Where is Chipotle?` returned seven locations inside 10 miles; repeats
  logged `Places cache hit` and made zero new Places calls.
- Live `What time does Home Depot close?` selected the actual Riverton store,
  not Garden/Rental/Pro Desk entities, spoke the 10 PM close, and showed six
  storefront pins with seven-day schedules.
- Physical-kiosk screenshots verified the raster Google basemap, Google
  attribution, circle, numbered markers, selected details, and today highlight.

## Non-goals / later

- Follow-up "when does it open?" answered *fast* from the cached place (needs last-place session state + parser rule) — v2 if the `ask` fallback feels slow in practice.
- Driving time, traffic-aware distance, directions, and Routes API calls. V1
  labels locally calculated distance as straight-line and makes no Routes calls
  or billable route-matrix elements.
- Public-IP-change watchdog cron (Phase 0 step 3 note).
