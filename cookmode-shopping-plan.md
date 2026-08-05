# Recipe → Shopping List Plan (cookmode)

**Status: BUILT + DEPLOYED 2026-08-05**, same day as scoping. Phases 1–4 all
shipped; live on the real library. Not yet done: Brad's own pantry pass (only 5
terms are ticked, from a smoke test), the APK is **built but not published**,
and nothing has been used on a real phone. See "What changed during the build"
at the end — several design details moved, one of them load-bearing.

Commits: cookmode `1ac2093`, voice-notes-android `a55da9f`, home_config
`1f484e6`.

Supersedes the older scoping in `voice-assistant-backlog.md` ("Recipe → shopping
list", scoped 2026-07-27) in two ways: **there is no voice path**, and **full
quantity parsing is not a prerequisite**. Read this file, not that section.

**Where:** cookmode (`/home/pi/cookmode`, `:8786`) owns everything new — the
pantry vocabulary, the tier classification, the staging endpoint, the desktop
review page, and the phone-width grid. The orchestrator's existing
`lists.add_from_text` is the only write path into the household shopping list;
Mealie stays stock. Voice Notes APK gets one button and one WebView activity.

## Goal

You are planning the week. You pick a recipe and its ingredients land on the
household shopping list — **without** dumping two dozen rows, most of which are
things you already own.

The naive version ("add every ingredient") is unusable: falafel alone is ~18
lines, and salt, cumin, olive oil and water are never things you need to buy.
The useful version splits ingredients three ways and only asks about the middle
one.

## Decisions (Brad, 2026-08-05)

- **No voice path.** Not in v1, and not as a follow-up in this shape. Reasons
  below — the blocker is title collisions, not title length.
- **Review page is desktop web**, served by cookmode. Not the kitchen display —
  cleanup is a sit-down task, not a standing-in-the-kitchen task.
- **Fail closed.** An ingredient nobody has reviewed goes on the shopping list.
  Unreviewed → Buy, always. The system may never silently drop something.
- **Phone is the entry point**, using the pictures. Grid of recipes, tap one,
  triage, done.
- Shopping stays **household-shared**, not per-person — consistent with the
  existing read and write paths (`voice-assistant-backlog.md`, per-person to-do
  item: "shopping stays household-shared, per Brad — it's the household we shop
  for").

## The three tiers

| Tier | Meaning | Behaviour |
|---|---|---|
| **Buy** | We don't habitually stock this | Pre-checked, goes on the list |
| **Check** | We stock it, but it runs out | Listed separately: "do we have enough?" |
| **Hidden** | We stock it and it lasts | Collapsed to a count: "+ 7 staples" |

Brad's framing verbatim: *"add these to the shopping list, high probability that
you need them. And then something like verify that you have enough. And some
things would be never — salt, for example."* Tofu is the Check case that
motivated this: a main ingredient, definitely stocked-ish, definitely perishable.

## Do NOT reuse cookmode's existing `staples` field

`enrich.py` already emits `staples`, and it will be tempting. It answers a
different question — *"can the cook ignore this while cooking?"* — and the two
most recent `ENRICHMENT_VERSION` bumps deliberately pushed it **further** from
shopping semantics ("salt and pepper are staples even when measured", "stop
calling a dressing's oil a staple").

| Ingredient | `enrich.staples` | Shopping tier | |
|---|---|---|---|
| Tofu | no (it's the main) | Buy | agrees |
| Cumin, baking powder | no (measured flavouring, ranked high) | Hidden | **disagrees** |
| ¼ cup olive oil in a dressing | no ("the oil IS the dressing") | Hidden | **disagrees** |
| Parsley, for garnish | yes | Buy | **disagrees, inverted** |

Three of four disagree. Separate field, separate prompt, separate cache column.

## Where the tier comes from

The tier is not one judgment. It is two inputs with two different owners:

1. **`stocked` — do we habitually keep this?** A household fact. Only a human
   sets it. No model ever writes this column.
2. **`shelf_stable` — if we keep it, does it last?** A world fact. The model
   answers this **once per term**, when the term is created, and it is cached
   forever.

```
not stocked                      -> Buy
stocked + not shelf_stable       -> Check
stocked + shelf_stable           -> Hidden
```

The payoff: the only thing a human maintains is a **stock list** — "things we
always have" — which is enumerable and intuitive. Nobody will maintain a
three-way tier judgment; everybody can tick a pantry checklist.

Because the per-term `shelf_stable` is cached, **the per-recipe LLM call is only
the vocabulary match** (below). Fast, local, free.

## Matching: index-only, into a closed vocabulary

String keys do not work. Measured on the real library (see Appendix): naive
normalization collapses 276 ingredient lines to 216 — almost nothing — and it
splits exactly the items a pantry list is about.

| Should be one term | How it actually appears |
|---|---|
| salt | `salt` ×4, `kosher salt` ×3, `sea salt` ×2, `sea salt and freshly ground black pepper` ×2 |
| olive oil | `olive oil` ×7, `extra virgin olive oil` ×4, `extra-virgin olive oil` ×2 |
| garlic | `garlic` ×8, `garlic, peeled` ×3, `garlic, grated` ×2 |

Four ticks for salt, three for olive oil — and "EVOO" in the next imported
recipe still misses. String normalization gives the *illusion* of a vocabulary.

Instead, the stock list **is** the vocabulary, and the model matches recipe lines
into it by index — the same structural guarantee `enrich.py` already relies on
(indices in, indices out, so nothing can be invented or reworded):

```
INPUT   ingredient lines, indexed            STOCK TERMS, indexed
        0: extra-virgin olive oil            0: olive oil
        1: 3 cloves garlic, grated           1: garlic
        2: 14 oz extra-firm tofu             2: cumin
                                             3: salt

OUTPUT  {"0": [0], "1": [1], "2": []}
```

A line may match multiple terms (`sea salt and freshly ground black pepper`).
**A line is hidden only if every term it matched is stocked and shelf-stable**;
any unmatched or unstocked component pulls it up a tier. Errs toward Buy.

Unmatched → Buy. That default is what makes the whole thing safe: the worst case
is a redundant shopping row, the fix is one tap, and **that tap is exactly the
event that adds the term to the vocabulary.** The matcher's failure mode and the
list's growth mechanism are the same gesture — nothing to maintain separately.

## Building the vocabulary without hallucination

The review page needs rows to tick, so the vocabulary has to be derived from the
library. Same trick, one level up: send the distinct ingredient lines, get back a
**cluster id per line**, and label each cluster with its **shortest member**. The
model emits only integers; the label is always a real line from the library.

Verified against the actual data — this produces the right labels unaided:

- `{olive oil, extra-virgin olive oil, extra virgin olive oil}` → **olive oil**
- `{garlic, garlic peeled, garlic grated}` → **garlic**
- `{salt, kosher salt, sea salt}` → **salt**

One pass over 266 distinct lines on the local qwen3-next (GX10 `:8095`, free).
Re-run when new recipes are imported; existing `stocked` values are keyed by term
label and survive, and a term that loses all its members just stops appearing.

## Fail-closed, and why it's a better default than it sounds

An empty `pantry_term` table means nothing matches, everything lands in Buy —
which is exactly the dumb-but-correct behaviour.

So the feature **ships useful with zero setup**. No cold-start cliff, no seeding
chore blocking v1, and no chance of a bad seed silently dropping something you
needed. The review page then improves it monotonically. Seeding is optional
acceleration, done when Brad feels like it, not a prerequisite.

## Schema (cookmode sqlite, `app/store.py`)

```sql
CREATE TABLE IF NOT EXISTS pantry_term (
  term         TEXT PRIMARY KEY,   -- canonical label, = shortest member line
  stocked      INTEGER NOT NULL DEFAULT 0,  -- household fact; humans only
  shelf_stable INTEGER,            -- world fact; model, once, then cached
  n_lines      INTEGER,            -- library frequency, for review-page sort
  source       TEXT,               -- cluster | user
  updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS pantry_alias (
  line_norm    TEXT PRIMARY KEY,   -- normalized library line
  term         TEXT NOT NULL REFERENCES pantry_term(term)
);

CREATE TABLE IF NOT EXISTS shopping_match (
  recipe_id    TEXT PRIMARY KEY,
  mealie_updated_at TEXT NOT NULL,
  vocab_version INTEGER NOT NULL,
  match_json   TEXT                -- {ingredient index: [term indices]}
);
```

`shopping_match` cache key is `(mealie_updated_at, vocab_version, prompt
version)` — adding a term bumps `vocab_version` and invalidates matches, which is
fine because recomputation is local and free. Note the `updatedAt` canonicalization
gotcha from the enrichment cache (`...Z` vs `...+00:00`) applies here too.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/recipes/{slug}/shopping` | Tiered ingredient list for the staging sheet |
| `POST` | `/api/recipes/{slug}/shopping` | Commit the chosen rows to the household list |
| `GET` | `/api/pantry` | Review-page data: terms + frequency + toggles |
| `POST` | `/api/pantry/{term}` | Set `stocked` (the only human-written field) |
| `POST` | `/api/pantry/rebuild` | Re-cluster the vocabulary after new imports |

The commit endpoint calls the orchestrator, which owns `lists.add_from_text`.
cookmode does not talk to the companion (`:8768`) directly — one write path into
the shopping list, and it stays the one that already handles typing, dedupe and
attribution.

**Note on `add_from_text`:** it forwards free text to the companion's analyze
prompt, which keys off framing words to type each item. Committing N ingredients
means either one call with a "add X, Y and Z to the shopping list" sentence, or N
calls. Worth measuring — analyze is an LLM round trip, and 12 sequential ones
will be slow enough to need a spinner.

## Ingredient names: less parsing than the old scoping assumed

The backlog tied this feature to full CRF-quality quantity parsing. It doesn't
need it:

- **The list row should usually carry no quantity.** "tahini" is a better
  shopping row than "2 tablespoons tahini" — you buy a jar. Quantity only
  matters where it changes the purchase (3 lbs chicken, 6 lemons), and can ride
  along as a suffix in those cases.
- **Dedupe against the existing list only needs the name**, which is the reliable
  half of a span extraction.
- The guard from the old scoping still stands and is now easy to satisfy: **every
  extracted name must be a literal substring of the source line**, verified in
  code; on mismatch, fall back to adding the raw line unmerged. Worst case is a
  slightly dumb list entry, never a wrong quantity.

Do **not** use Mealie's own parse. It is lossy in ways already documented
(`6 to 8 fresh basil leaves` → `6 basil`; every "salt and pepper" line → a
salt-only row). cookmode renders `originalText` and this feature reads it too.

Servings scaling ("make it for eight") is the feature that genuinely needs the
full quantity parse. It is now decoupled and can come later on its own merits.

## Phone surface

**Not a sixth tab.** The bar is already five tabs squeezed to 12sp
(`MainActivity.tabButton`: *"12sp + tight padding: five tabs share the bar since
Intercom"*).

**Not a native picture grid either.** cookmode already renders the thumbnail grid
(480×300 WebP, disk-cached in `data/thumbs/`, warmed at startup, served
`immutable`), `CookmodeClient` already exists for the share target, and
`WireGuardControl` means the phone reaches `:8786` off-LAN today. Rebuilding image
loading and caching in a hand-rolled Java view layer buys nothing.

So: a **"＋ from a recipe" button on the existing Shopping tab**, opening a
full-screen WebView activity onto cookmode's grid at a phone breakpoint.

**On the phone, tap = plan, not cook.** The cook view is kiosk-tuned (38px
ingredients / 32px steps, two columns) and reads badly on a phone, and nobody
cooks from the phone when the display is in the room. So the phone breakpoint
makes a tile tap open the staging sheet directly. No long-press — that is a
discoverability problem this doesn't need.

Same grid, same server, different tap action per breakpoint.

### Staging sheet

- **Buy** — pre-checked rows. Uncheck to drop.
- **Check** — "do we have enough?" Unchecked. Check the ones you're out of;
  they join the Buy set on commit.
- **Hidden** — one collapsed row, "+ 7 staples", expandable.
- One commit button. Reports back "9 added to the shopping list."

**The trap:** a Check-row tap is an answer about *today's inventory*, not about
the household's habits. It must **not** write `stocked`. Learning from it poisons
the vocabulary the first time you happen to have tofu in the fridge. Inventory
answers are transient; `stocked` changes only on the desktop review page.

(Deliberately deferred: promoting a term after you've answered "have it" N times
in a row. If built, it *suggests* on the review page — never silently learns.)

## Desktop review page

One page, two jobs — initial seeding and later cleanup:

- Every term, sorted by library frequency descending (`n_lines`). Sorted that
  way the pantry items cluster at the top: garlic 8, olive oil 13 across
  variants, salt 11, cumin 5, water 4, cilantro 4, cayenne 3, lemon juice 3,
  dijon 3, tahini 3, butter 3…
- A `stocked` toggle per term. Tick ~25 rows in two minutes and stop when it
  stops being obvious. Everything untouched stays Buy forever — the safe
  direction.
- Show each term's member lines (expandable), so a bad cluster is visible.
- Show the derived `shelf_stable` with a manual override, for the cases the
  model gets wrong for this house.

## Phases

1. **Vocabulary + review page.** Cluster pass, `pantry_term`/`pantry_alias`,
   `GET/POST /api/pantry`, desktop page. Nothing else depends on Brad having
   used it — fail-closed means the rest works with an empty table.
2. **Tiering + staging endpoint.** Per-recipe index match, `shelf_stable` per
   term, `GET /api/recipes/{slug}/shopping`.
3. **Commit path.** `POST .../shopping` → orchestrator → `lists.add_from_text`.
   Measure the N-item latency before designing the spinner.
4. **Phone.** Grid phone breakpoint + staging sheet (web), Shopping-tab button +
   WebView activity (APK), F-Droid publish.

Phases 1–3 are all cookmode + one orchestrator endpoint, and are testable from a
desktop browser with no APK build in the loop.

## Deliberately not in scope

- **Voice.** See below.
- **Servings scaling.** Needs the full quantity parse; decoupled now.
- **Mealie meal plans.** "this week" lands naturally on Mealie's meal-plan API.
  Out of v1, but don't design the staging payload in a way that blocks it.
- **Quantity-aware Check** — "we stock flour, but not three cups of it." Real,
  but needs the quantity parse. Natural rider on servings scaling.

## Why there is no voice path

Not title length — `token_set_ratio` handles "the cauliflower tacos" →
*Chipotle Cauliflower Tacos with Creamy Jalapeño Verde* fine. The blocker is that
the library **collides with itself**, and structurally so: a vegetarian-leaning
library keeps reusing the same handful of headline ingredients.

| What you'd say | Matches, in an 18-recipe library |
|---|---|
| "the cauliflower one" | Chipotle Cauliflower **Tacos** · Roasted Cauliflower **Salad** |
| "the lentil one" | Lemony Lentil **Soup** · Sweet Potato Lentil **Curry** |
| "the tofu one" | Tofu **Salad** · Spicy Peanut Tofu **Bowls** |
| "the chickpea one" | Chickpea Salad **Sandwich** · …Crispy Sesame **Chickpeas** |

Four genuine ambiguities at eighteen recipes, worsening as the library grows.
Disambiguation turns ("did you mean the tacos or the salad?") are what make a
voice feature feel worse than a tap. Add the collision risk with plain
`add_items` ("add chicken to the shopping list" is an item; "add chicken parm" is
a recipe) and it isn't worth a new intent.

**The one voice phrasing worth building later:** *"okay computer, add these to
the shopping list"* while the cook view is **already open on the kitchen
display**. Zero name matching — the display knows what's on it — therefore zero
collisions. Nearly free once phases 1–3 exist: one intent that reads the
display's current recipe and calls the commit endpoint. Not v1.

## Appendix: measured baseline, 2026-08-05

Probed live against Mealie (`http://192.168.10.217:9925`). The token is
root-owned `600`, so `pi` cannot read it — probe via
`docker run --user 0 -v /home/pi/cecret_lake/mealie:/s:ro python:3.12-slim`,
per the cookmode convention.

| Measure | Value |
|---|---|
| Recipes | 18 |
| Ingredient lines | 276 |
| Distinct raw lines | 266 |
| Distinct after naive normalization | 216 |
| Singleton terms after normalization | 186 |
| Terms appearing ≥2× | 30, covering ~90 lines (**~33%**) |

Two decisions rest on these numbers:

- **266 → 216** is why the vocabulary must be semantic, not string-normalized.
  Raw-line dedupe collides only 10 times in 276; naive normalization adds only 50
  more, while still splitting salt four ways.
- **30 terms cover a third of every ingredient line**, and they are almost
  entirely pantry items. That is the ceiling on the seeding chore: a couple dozen
  toggles removes or demotes roughly a third of every future shopping list, and
  the 186-term tail is genuinely what you buy.

---

# What changed during the build (2026-08-05)

Everything above is the design as scoped. This section records where the
implementation departed from it and why, so the next session reads the code's
actual shape rather than the plan's.

## The alias table replaced the match cache, and tiers stopped being cached

The plan had a `shopping_match` table keyed on `(recipe updatedAt, vocab
version, prompt version)`. It does not exist. `pantry_alias` is keyed on the
**quantity-stripped ingredient line**, which turns out to be strictly better:

- the second recipe to call for garlic costs nothing, across the whole library;
- editing a recipe only re-matches the lines that actually changed;
- **tiers are derived at read time** from `pantry_term`, so ticking something
  stocked takes effect everywhere at once with nothing to invalidate — no
  `vocab_version`, no cache-key versioning, no sweep to re-run.

## The self-link — the one genuinely load-bearing change

A line's own extracted name is always recorded as one of its terms.

Without it the feature is silently inert, and it took a live sweep to see why:
during a sweep **nothing is stocked yet**, so the model is handed an empty
vocabulary and correctly returns no terms for anything. Every line is then
pinned to "no terms" in the alias cache forever, and ticking staples on the
review page afterwards visibly does nothing. The self-link means every line
points at *something*, so stocking a group reaches the lines already scanned.

Safe because a freshly created term is never stocked — only a person sets that.

## Grouping: three passes, not one clustering call

The plan proposed one clustering call over all distinct lines labelled by
shortest member. What shipped:

1. **Head-noun buckets** (free, deterministic). 166 terms → 97 buckets, only 34
   with more than one member. Two-thirds of the vocabulary needs no call at all.
2. **The model splits each multi-member bucket.** Small prompts, and a bad
   answer in one bucket cannot disturb another.
3. **A deterministic refinement gate over the model's merges** — see below.

Labels are still the shortest member, as planned. 167 terms → 139 groups.

## Merges must satisfy two independent judgments

This was not in the plan and is the most important thing learned.

The model over-merged in ways that would silently lose ingredients: lemon juice
with lime juice, three different cheeses, chicken with vegetable stock, whole
with coconut milk, salted with unsalted butter. Tightening the prompt with those
exact pairs as counter-examples fixed most of them — **and it kept merging lemon
with lime anyway.**

So a merge now also has to pass `refines()`: every word of the more general name
must survive in the more specific one. "olive oil" → "extra-virgin olive oil"
passes; "lemon juice" / "lime juice" merely share a word and fails.

The two checks cover each other's blind spots exactly:

| | catches | cannot see |
| --- | --- | --- |
| the model | "black pepper" ≠ "bell pepper" — different foods that look alike to a string comparison | lemon vs lime, repeatedly |
| `refines()` | lemon vs lime, chicken vs vegetable stock | that "pepper" ⊂ "bell pepper" is a category, not a refinement |

The asymmetry is deliberate and worth preserving: an over-split costs one extra
row to tick, an over-merge drops an ingredient off a shopping list the moment
the group is marked always-in-the-house.

## The commit path skips the analyze prompt entirely

The plan flagged "measure the N-item latency before designing the spinner",
expecting either one run-on sentence or N sequential LLM round trips through the
companion's analyze. Neither was necessary: the companion gained
`POST /api/items` for items whose type and text are already known. Ingredient
names were verified against their source lines upstream; handing them to an LLM
to be re-read would put that at risk for nothing. Dedupe against the active list
happens in the same call. **No spinner needed — it is a plain insert.**

Still routed cookmode → orchestrator → companion, as planned, so the kitchen
display gets its `list_updated`.

## Smaller things

- **Route ordering bites FastAPI.** `POST /api/pantry/{term}` declared before
  `/api/pantry/rebuild` swallows it and 422s on the missing body. Literal routes
  first; there is a comment in `main.py` saying so.
- **`fallback_name` needed `\b` around the unit group.** The abbreviations
  include a bare `c` for cups and alternation is ordered, so "3 cloves garlic"
  came out as "loves garlic". `normalize_line` was never affected — it had word
  boundaries already. Caught by a test, not by eye.
- **A regroup reports no recipe counts**, so the review page builds its summary
  sentence from whichever fields are present.
- **`reset_groups()` clears `shelf_stable` but never `stocked`.** Regrouping is
  our problem; the household's answers are not ours to discard.

## Measured on the real library after the build

18 recipes, 276 ingredient lines → 186 distinct after normalization (the plan's
appendix said 216; the shipped normalizer is more aggressive) → 167 names → 139
groups. Zero rejected name extractions across the whole library — every name the
model returned was a literal substring of its source line.

Falafel, with five staples ticked: **14 buy, 1 check, 3 hidden**. "sea salt" and
"extra-virgin olive oil" hid via the groups "salt" and "olive oil", which is the
self-link and grouping both working end to end.

## Still open

- **Brad's pantry pass.** Only salt, water, olive oil, ground cumin and garlic
  are ticked, and only to prove the tiers. The review page is `/pantry` on
  `:8786`; sorted by frequency, the real ones are near the top.
- **The APK is built, not published.** `app/build/outputs/apk/debug/app-debug.apk`.
  Publishing silently updates Brad's phone, and nothing has been tested on a
  real handset yet.
- **Two cosmetic vocabulary warts**, both harmless and both visible on the review
  page: "ground cayenne" and "cayenne pepper" are separate groups (different
  head-noun buckets — the safe direction), and "each of saffron and cayenne" is
  a bad name extraction from a two-ingredient line that got folded in with
  cayenne. Neither can hide anything wrongly; `leftover_ingredient` guards the
  multi-ingredient case at tiering time.
- **Long names on list rows.** "chopped fresh cilantro leaves and stems" is a
  verified substring and therefore correct, but clumsier than a shopping list
  wants. Trimming prep words is a deletion-only change if it ever annoys.
- **No way to split a bad group from the UI.** The AND-gate makes this rare
  enough to leave alone; if it does come up, the fix is a "split this out"
  affordance on the expanded row plus a pinned flag so regrouping respects it.
