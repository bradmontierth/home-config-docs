# Slideshow "Memories" Plan — Same-Age Comparison + On This Day

Two new special-slide types for the kitchen slideshow (`/home/pi/immich-slideshow`, :9021):

1. **Same-age** — side-by-side of the kids at the same age: a recent Claire photo next
   to Simon at the age Claire was in that photo ("here's what he looked like at her age").
2. **On this day** — photos from this calendar date in prior years.

Design stance (agreed 2026-07-18): the magic is serendipity, so both types get
**injected sporadically into the normal rotation** by default. On-demand browsing comes
later via tap gestures (age-caption tap → same-age mode; badge tap → on-this-day mode).
No persistent mode/toggle — everything transient and stateless, nothing to un-toggle.

## Feasibility facts (verified against the live DB 2026-07-18)

- Birthdays synced from Immich people: **Simon 2023-02-03, Claire 2025-07-14** (she just
  turned 1). Feed already computes per-photo age labels from these (`curate._age_label`).
- The hourly sweep indexes the **whole library** — `MIN_TAKEN_AT=2023-01-01` is only a
  serve-time filter in `db.candidates()`. Old photos are already in the sidecar DB and
  `/img/{id}` can fetch any of them. Special queries just bypass the filter — **no new
  sync lane needed** (the "archive lane" worry was unfounded).
- Simon's entire life is ≥2023, so the cutoff never bites for same-age anyway:
  Claire at 12 mo → Simon in Feb 2024 → ~692 tagged Simon photos that year.
  573 Simon photos in 2023 cover the newborn end. Claire has 400+ photos in 2026.
- Pre-2023 photos for on-this-day: 2,177 family-bucket + 1,277 faces-bucket rows
  (parents are tagged even before the kids existed). Exact-date test: July 18 has
  8 prior-year family/faces photos — enough, but thin; threshold-gate the feature.
- Deployed env (`/home/pi/cecret_lake/immich_slideshow/.env`): family-only mix
  (`MIX_FAMILY=1.0`), `SHOW_VIDEOS=1`, satellite audio relay on.

## Shared plumbing (built once, in Phase 1)

**Special-slide framework.** `curate.pick_batch()` gains injectors that append marked
slides; feed items gain two fields:

- `special`: `"same_age"` | `"on_this_day"` (absent for normal slides)
- `specialLabel`: eyebrow text the viewer renders above the caption, e.g.
  "AT THE SAME AGE" / "ON THIS DAY · 2023"

Viewer (`static/viewer.html`): render `specialLabel` as a small eyebrow line above
`caption-main` (same text-shadow treatment, ~22px fullscreen / 13px widget, letter-spaced
caps, maybe `#fbbf24`-adjacent warm tint). Same-age items arrive as a pre-built pair
(2 items in one slide) and must **not** be re-paired or split by `nextSlide()`'s portrait
pairing — pass them through as an intact slide. Landscape members of a special pair still
render as `fit` panes (existing pair path already does this).

**DB helpers** (`db.py`), all deliberately ignoring `MIN_TAKEN_AT`:

- `person_photos_between(name, start_iso, end_iso)` — rows where
  `people LIKE '%"<name>"%'` (quoted-name match against the JSON text; exact-token safe
  for our names) AND `taken_at` in range AND `excluded=0` AND `media_type='IMAGE'`.
  Range queries on `taken_at` are index-friendly string compares (existing pattern).
- `on_this_day(month_day, before_year)` — `substr(taken_at,6,5)=?` AND year `< ?` AND
  `excluded=0` AND `media_type='IMAGE'`, bucket family/faces only. Full scan of ~27k rows
  is fine; no new index.

**Selection & repeat suppression.** Reuse `_sample()`/`_weight()` and `record_shown()` —
special picks go through the same history table, so the same archive photo won't recur
day after day (HIDE_HOURS/DAMP_DAYS apply as-is). Videos are excluded from special
slides in v1 (poster/pairing/audio-relay complexity for zero benefit).

**Config** (env, with defaults in `config.py`):

```
SAME_AGE_PER_BATCH=1      # slides injected per 30-slide batch (~1 per 10 min at 20s dwell)
SAME_AGE_WINDOW_DAYS=21   # ± age-match window; auto-widen 21→42→70 until ≥5 candidates
SAME_AGE_RECENT_DAYS=45   # how recent the younger kid's photo must be
OTD_PER_BATCH=2           # max on-this-day slides per batch
OTD_MIN_PHOTOS=3          # today needs ≥ this many prior-year photos or OTD stays silent
```

## Feature A: Same-age comparison

**Generator** (in curate, runs per batch when both kids have birthdays):

1. Pick the "now" side: weighted-sample one Claire photo from the last
   `SAME_AGE_RECENT_DAYS` days (favorite boost + repeat suppression as usual).
2. Compute Claire's age **at that photo's `taken_at`** (not "today" — history swipes and
   stale queues stay honest) in days; target = Simon's birthday + that many days.
3. Pick the "then" side: weighted-sample one Simon photo from
   `target ± SAME_AGE_WINDOW_DAYS`, widening 21→42→70 days until ≥5 candidates; if still
   empty, skip injection this batch (no error, no filler).
4. Emit as one 2-item slide, Claire left / Simon right, `special: "same_age"`,
   `specialLabel: "AT THE SAME AGE"`. Existing captions already carry
   "Claire (12 mo)" / "Simon (12 mo)" + month/year, which is exactly the payoff —
   no caption override needed.
5. Insert at a random position in the batch (not first — let it surprise).

**Later variant** (backlog, not v1): occasionally pick a random shared age ≤ Claire's
current age instead of "Claire now" — e.g. both kids as newborns. Same generator with a
different target; add once v1 feels right.

**API**: `GET /api/same-age?months=&n=` returning alternating/paired slides for that age.
Built in Phase 1 as the test hook (curl + browser check without waiting for rotation
luck); becomes the browse-mode backend in Phase 3.

## Feature B: On this day

**Generator** (per batch, using server-local date):

1. Query `on_this_day(today's MM-DD, current year)`. If < `OTD_MIN_PHOTOS`, inject
   nothing — silence on empty days is the feature ("a badge that's always there is
   furniture").
2. Weighted-sample up to `OTD_PER_BATCH`, favoring favorites and family bucket, spread
   across distinct years when possible (don't let one 2018 photo-dump own the day).
3. Single-item slides, `special: "on_this_day"`,
   `specialLabel: "ON THIS DAY · <year>"`; caption-sub gains a relative line, e.g.
   "3 years ago today". Old photos predate the kids → captions show parents' names or
   location/date, which is fine (ages only render for people with birthdays).
4. Repeat suppression handles "same photo twice today" automatically (HIDE_HOURS=24).

Exact month-day only (no ±1 fudging — "on this day" should mean it). Feb 29: matches
only on leap years; acceptable. `GET /api/on-this-day?n=` as test hook / future badge
+ browse backend; feed response gains `onThisDay: {count, years}` summary for the
Phase 3 badge.

## Phase 3 (later): on-demand browsing + badge

Deliberately deferred — injection alone delivers most of the value; gestures need a
dashboard_webapp change and new viewer mode machinery.

- **Tap coordinates.** The kiosk iframe never sees touches; `dashboard_webapp/app/static/app.js`
  (~line 1923) posts `{type:"slideshow-tap"}` with no position. Add normalized overlay
  coords (`x`,`y` ∈ 0..1). Viewer maps to viewport px and hit-tests
  (`document.elementFromPoint`) against caption / badge rects before falling through to
  the existing `onTap()` (video-play / fullscreen-toggle) behavior. Native click/touch
  path (direct viewing) uses real event coords — same hit-test.
- **Age-caption tap → same-age browse.** Tapping the caption on any slide showing a kid
  with an age enters a transient sub-queue from `/api/same-age` for that age: banner
  ("Simon & Claire at 14 months — tap to exit"), swipes navigate within it, tap or ~3 min
  idle exits back to live rotation (drop remaining sub-queue, refill). No persisted
  state anywhere.
- **On-this-day badge.** Small pill (fullscreen only; widget hides chrome) shown when the
  feed's `onThisDay.count ≥ OTD_MIN_PHOTOS`. Tap → chronological-by-year browse of
  `/api/on-this-day`, same transient sub-queue mechanics. Badge appearing only a few
  days a week is the invitation.

## Phasing & verification

| Phase | Contents | Touches |
|---|---|---|
| 1 | Special-slide framework, DB helpers, same-age generator + injection, eyebrow rendering, `/api/same-age` | immich-slideshow only |
| 2 | On-this-day generator + injection, `/api/on-this-day`, `onThisDay` feed summary | immich-slideshow only |
| 3 | Tap coords, caption-tap browse, OTD badge + browse | + dashboard_webapp |

Verify per phase: curl the new API endpoints (candidate counts, window-widening, empty-day
silence); load `:9021/` in a desktop browser to eyeball eyebrow/pair rendering in both
widget and fullscreen sizes; then live kiosk watch for injection-rate feel (tune
`*_PER_BATCH` envs — env-only, no redeploy of code). Deploy = compose rebuild on the Pi
(repo still has no GitHub remote — commit locally regardless; git is the backup).

## Risks / notes

- `people` is JSON text; match `'%"Name"%'` with quotes to avoid substring collisions.
- Special queries must skip `MIN_TAKEN_AT` **on purpose** — comment it in code so a
  future cleanup doesn't "fix" it.
- Batch over-pick for the `types=` test-hook path (`main.py /api/feed`) multiplies `n`
  by 5 — injectors should run on the final batch size, not the over-picked pool, or
  video-filter requests would get 5× the specials.
- Claire-side pool is currently rich (400+ photos this year); if a slow photo month makes
  the recent window empty, the generator skips silently — acceptable.
- Parents have no `birthDate` in Immich, so same-age only covers the kids. Setting the
  parents' birthdays in Immich would unlock "dad at 30" comparisons someday — noted, not
  planned.
