# Home Control Intent (Curated Buttons) Plan

**Status:** Phase 1 DEPLOYED 2026-07-22 — Node-RED tab "Voice Buttons" (`294429bac2b766ff`), all 16 `button.voice_*` entities live in HA. Smoke-tested by API press: sink + slider blinds close/reopen, dinner mood on/exit (WLED preset + whites off → exact circadian restore), brighten wire (BriCurveAdj 150/BriCurveDisable true) + back-to-normal reset, HA logbook entries confirmed. Remaining Phase 1: Brad's eyes-on pass (left/right/small/glare/all buttons; dinner preset changed to 3 "red blue" slow-shift per Brad 2026-07-22).

**Phone alias editor DEPLOYED 2026-07-22** (commit `fea45aa`): mobile page at `http://192.168.10.217:8785/home-commands/ui`, tile "Voice Commands" on the homelab homepage. Phrase tester dry-runs the matcher (`GET /home-commands/match` — never presses); on a miss, one-tap add to the closest command. Live table now at `/home/pi/voice-pipeline/data/home_commands.json` (rw dir mount, seeded from the repo copy baked in the image; the `:ro` single-file mount was removed — it would go stale on host inode swaps from git ops). Repo copy = versioned seed/default; phone-added aliases live only in /data, worth syncing back to the repo occasionally. Validation server-side: cross-command duplicate aliases rejected, last alias not removable. Fun fact from testing: "darken the kitchen" scores 79 vs "brighten the kitchen" — one under threshold, opposite actions; the strict 80 earns its keep.

**Phase 2 DEPLOYED 2026-07-22** (commit `8c09321`, container rebuilt): `home_control` intent + `home_control.py` + `home_commands.json` (bind-mounted `:ro`, mtime hot-reload). **Deviation from plan:** matcher is full-string `fuzz.ratio` at threshold 80 after politeness-filler strip, NOT `WRatio` 85 — WRatio's partial/token heuristics scored "turn on the sprinklers" ≈ "close the sink blind" at 52→ok but "what a nice dinner we had" ≈ "dinner mood" at 86 and "are the blinds closed" ≈ "close the blinds" at 82; with plain ratio, misses top out ≈68 and cleaned hits score ≥86. Pin words (left/right/sink/sliding/slider/big/small/little) restrict candidates so near-identical blind aliases can't cross. 7 unit tests (`test_home_control.py`); E2E: sink close/reopen by text `/command`, garage + front-door refusals, brighten/normal round trip, "are the blinds closed"→ask, press 15–18ms. Live-voice test (wake word → satellite) pending Brad.
**Where:** orchestrator (`home_config/voice-assistant/orchestrator/`) + MQTT discovery buttons (created by Node-RED) + Node-RED flows (Beelink, existing tabs/globals). Satellite untouched. Companion plan: `business-hours-places-plan.md` (same build session candidate).

## Goal

Very limited voice home control — a handful of curated *commands*, not general device control. "Close the sink blind," "brighten the lights," "set the mood for dinner." No exposure of the general Zigbee2MQTT/ESPHome entity space to the voice layer; the four kitchen blinds are addressable only through a fixed alias table.

Decisions made 2026-07-20 (Brad), amended 2026-07-22:

- **Voice = another Pico button.** All logic and state live in Node-RED (all-custom flows, globals, existing "button push → temporary override → 60-min time-based return" machinery). Voice is just one more entry point into those flows.
- **Mechanism: one MQTT discovery `button` per command, created by Node-RED** (2026-07-22 — supersedes the HA `input_button` helper idea). This is the house convention for creating HA entities (template sensors need an HA restart; MQTT discovery doesn't), and it's a strictly better fit here: a press from *any* source (voice via HA service call, HA dashboard, phone) makes HA publish to the button's `command_topic`, and a single Node-RED `mqtt in` node on that topic namespace is the one entry point — no events-state nodes, and HA-UI presses and voice presses are literally the same code path. Every press still lands in the HA logbook.
- No HA scripts — logic would be duplicated outside Node-RED. Orchestrator always goes through HA (`button/press`), never straight to MQTT/Node-RED — keeps HA visibility and the logbook audit trail.
- **Blast radius = the button list.** Worst case of any parse error / fuzzy mismatch / false wake: a blind moves or a lighting flow Brad wrote runs at an odd time. Locks, garage, alarm excluded entirely (not a v1 confirmation-tier thing — just absent).
- **Misses do NOT fall back to `ask`** (unlike sports/weather/places). A control phrase that doesn't match says "I don't control that" — never web-search "open the blinds."
- **Blinds: no sun-azimuth logic, ever** (2026-07-22). Glare is predictable in principle but hard to compute (reflections, sun peeking through) and there are only 4 kitchen blinds. Instead: a per-blind alias table, plus a "glare" combo that closes the two blinds that actually ever matter (kitchen left + sink).

## The four kitchen blinds

| Name | Entity | Notes |
|---|---|---|
| Kitchen left | `cover.kitchen_left_shade` | one of the two glare offenders |
| Kitchen right | `cover.kitchen_right_shade` | |
| Sink | `cover.sink_shade` | the other glare offender |
| Sliding door ("the big one") | `cover.kitchen_sliding_kitchen_door` | duplicate `cover.kitchen_sliding_door_shade_windowshade` exists (other integration) — RESOLVED 2026-07-22: chosen entity verified live by close/reopen via the voice button. |

(`cover.all_blinds` is a house-wide group — do not use.)

## MVP command set

Blind buttons come in open/close pairs; targets and starter aliases:

| Target | Covers | Close aliases (starter) | Open aliases (starter) |
|---|---|---|---|
| `blind_left` | left | "close the left blind", "close the left kitchen blind" | "open the left blind", … |
| `blind_right` | right | "close the right blind", … | … |
| `blind_sink` | sink | "close the sink", "close the kitchen sink", "close the sink blind" | "open the sink blind", … |
| `blind_slider` | sliding door | "close the sliding door", "close the big one", "close the big blind" | "open the sliding door", … |
| `blind_small` | left + right | "close the small blinds" | "open the small blinds" |
| `blind_glare` | **left + sink** | "fix the glare", "sun's in the kids' eyes" | — (no open; "open the blinds" covers it) |
| `blinds_all` | all 4 | "close the blinds", "close the kitchen blinds" | "open the blinds", "open the kitchen blinds" |

(Whether bare "close the blinds" should mean all-4 or the glare pair is a pure alias-table edit later — hot-reload JSON, no redeploy. Starting literal: all 4.)

Plus the three lighting commands, unchanged from the original plan:

| Command | Aliases (starter) | Node-RED flow behavior | Exit |
|---|---|---|---|
| `kitchen_brighten` | "brighten the lights", "brighter", "bump up the lights" | new trigger into the **existing** override flow; stepping logic (dim→50%, bright→100%) lives there | existing 60-min return |
| `dinner_mood` | "set the mood for dinner", "dinner mode", "dinner lights" | WLED preset (kitchen fixture color strips top+bottom) + ESPHome white strips off | 60-min return or back-to-normal |
| `lights_normal` | "back to normal", "normal lights", "reset the lights" | cancel active overrides, resume circadian baseline | — |

Total: 12 blind buttons + 3 lighting buttons = 15. Naming convention `button.voice_*` (via discovery `object_id`) so the voice-exposed surface is greppable in HA.

## Brighten rework — as built (2026-07-22 evening, Brad's staged design)

Old brighten (link into the Pico cabinet path) was a live no-op: the Pico "increment 50" floors to BriCurve, and in the evening the cabinets already sit AT BriCurve — plus the cross-tab link-out **never delivered at all** (one-sided `link out` — see gotchas in `nodered-flow-agent-guide.md`; this also means the original lights_normal repaint had been a silent no-op).

New behavior (all in Node-RED "Voice Buttons" tab, `brighten stage 1/2` function):
- **Press 1:** cabinets **+50pp** from current (HA `api-current-state`), fixture **both sides +50pp** from current combined level (mega calc's `kitchenfixture_levels` global), **cans ON at 50%**. Sets `BriCurveAdj`/`BriCurveDisable` (house override holds cabinets + all while-on kitchen strips), new global **`FixtureBriAdj`** (fixture boost level), `fixtureBothOverride=true`, `KitchenBrightenStage=1`; fires the Global CT repaint; starts a 90-min stoptimer.
- **Press 2 (within the window):** everything to 100 (cabinets/fixture/cans), timer restarts, stage=2.
- **Return:** timer expiry or `lights_normal` → `brighten reset`: stage 0, `FixtureBriAdj` cleared, BriCurve override released, `fixtureBothOverride=false`, cans OFF, repaint restores circadian (fixture returns to the normal evening top-only blend).

Making the fixture boost SURVIVE took guards in three places (the fixture has multiple competing writers; each was found by catching a live revert ~5s after boost):
1. Update CT tab: the 2 fixture `build values` use `FixtureBriAdj || BriCurve`; `both/top` forces the both-sides branch while boosted; **`Global CT In` link-in now lists the voice tab's link-out** (the one-sided-link fix).
2. Kitchen Motion tab: motion path `build values` (af03d9…) stands down during boost; the two both-sides `build values` use the boost level.
3. Subflow defs `272ada…` ("build api request top") and `766751e…` **"Kitchen Fixture Dynamic"** (evening top-only driver, `inputValue <= 8` → top-only): both return null while `FixtureBriAdj` is set. Deployed via full `/flows` deploy (nodes-type doesn't rebuild subflow instances; rate-limiter queues also leak stale pre-boost messages — the def-level guard is what finally held).

Verified live 2026-07-22 ~21:10: stage-1 boost held both sides at target through motion and a 5-min repaint tick; stage-2 goes to 100; normal restores everything. Repaint trigger payloads are `{}` (strings tripped ValidationError on old walk nodes with input overrides). Flows backups: `flows.json.backup_before_brighten_rework_*`, `backup_before_topsubflow_guard_*`, `kitchen-motion-tab.backup_before_boost_guards_*`.

## Phase 1 — as built (2026-07-22)

Tab **"Voice Buttons" `294429bac2b766ff`** (node ids `cafe*`; flows backup `flows.json.backup_before_voice_buttons_*`). Deviations/discoveries vs the plan below:

- **Return timer is 90 min, not 60** — the house's actual `stoptimer-varidelay` convention (Pico brighten uses 90); dinner mood uses the same 90-min auto-exit.
- **Brighten** = link-out into `Under Cab Adj IN` (`a7e05d5f415f49c9`, Kitchen Motion Lighting) — the identical path as Kitchen Garage Entry Pico button 1: zigbee2mqtt-get Cabinet LEDs → "increment 50" (sets `BriCurveAdj`, `BriCurveDisable=true`) → 90-min reset.
- **Dinner mood** = POST `{"on":true,"ps":3}` (preset 3 "red blue" slow shift — was 5, changed per Brad 2026-07-22) to WLED `192.168.30.37/json` + `light.turn_off` the 5 `light.kitchenfixture_*` channels (transition 2) + `KitchenDinnerMood` global + 90-min stoptimer → exit. **No gate flag needed in existing flows:** every repaint path ("Update CT Values while On", motion, circadian) gates on the `kitchenFixtureStatus` global and only re-drives sides already ON. Known benign edges that relight whites mid-dinner: physical fixture wall-switch press (explicit intent) and the Misc-tab TV-off-after-8pm path.
- **Dinner exit / back-to-normal relight** reuses the house subflow `7d78ee4552a640a7` "kitchen fixture both" with the standard msg shape (`payload=sunPercentMod`, `switch:"on"`, `level=BriCurve`, `transition`).
- **Back to normal** = replicate "Reset BriCurveAdj" (`BriCurveAdj=BriCurve`, `BriCurveDisable=false`) + link-out into `Global CT In` (`1fa5c29431b8582b`) to repaint ON lights + stop dinner timer + dinner exit if `KitchenDinnerMood`. NOTE: the repaint path is gated by a separate `BrightnessOverride` global (set from Misc Automations) — deliberately left untouched; if it's ever true, repaint no-ops.
- **Blinds** = one function mapping command → `{target:{entity_id:[...]}}` into two v7 call-service nodes (`cover.close_cover`/`cover.open_cover`, `blockInputOverrides:false`) — input target override confirmed working live.
- Discovery inject has `once:true` (5s after deploy), so redeploying the tab republishes retained configs — that's the whole entity-management story.

## Phase 1 — original plan (superseded by "as built" above)

1. **Discovery setup flow (Node-RED):** one inject → function → mqtt-out that loops a command list and publishes retained configs to `homeassistant/button/voice_<cmd>/config`:

   ```json
   {
     "name": "Voice: close left blind",
     "unique_id": "voice_blind_left_close",
     "object_id": "voice_blind_left_close",
     "command_topic": "voice/button/blind_left_close",
     "payload_press": "PRESS",
     "device": {"identifiers": ["voice_buttons"], "name": "Voice Buttons"}
   }
   ```

   All 15 under one "Voice Buttons" device so they group in HA. Re-running the inject after editing the list is the whole entity-management story (retained configs; publish empty payload to a config topic to delete a button).

2. **One `mqtt in` node on `voice/button/#`** → switch on topic tail:
   - **Blind targets** → a single function node mapping command → `{service: close_cover|open_cover, entity_id: [covers]}` → HA call-service node. Trivial; no override machinery.
   - **Brighten** → wire into the existing Pico/Zooz override entry point — a wire, not new logic.
   - **Dinner mood** → WLED preset call + ESPHome strips off + an exit path matching the house override pattern.
   - **Back to normal** → cancel/expire override state, re-apply circadian values.
3. **Validate with zero voice involvement:** press each button from the HA UI, watch the room. Flows are done when the buttons work by hand. This also settles the sliding-door duplicate-entity question.

Node-RED deploys via Admin API per `nodered-flow-agent-guide.md` (same as bedtime rework); backup flows.json first as usual.

## Phase 2 — orchestrator intent

Smaller than the Places intent (~40-line handler, one code path).

### 1. Command reference JSON

`home_config/voice-assistant/orchestrator/home_commands.json` (versioned with the code, mounted into the container):

```json
{
  "blind_glare_close": {
    "aliases": ["fix the glare", "sun's in the kids' eyes"],
    "entity": "button.voice_blind_glare_close",
    "confirm": "Closing the left and sink blinds."
  },
  "blind_sink_close": {
    "aliases": ["close the sink", "close the kitchen sink", "close the sink blind"],
    "entity": "button.voice_blind_sink_close",
    "confirm": "Closing the sink blind."
  }
}
```

- One entry per command; `confirm` is the full spoken response (no LLM involvement in phrasing).
- **Hot-reload on file mtime** — editing aliases or adding a command never needs a container restart.
- Keep this file and the Node-RED discovery list in sync by hand (15 entries; acceptable for MVP).

### 2. `intent.py`

- Add `home_control` to `INTENTS` (`intent.py:16-21`); prompt rule: commands *to change something in the house* (lights, blinds, modes) → `intent: home_control`, verbatim-ish phrase into `query`. Explicitly distinct from `music_control` and from questions *about* the house.
- Coercion in `_validate` (`intent.py:155`).

### 3. New `orchestrator/home_control.py`

- `async def handle(parsed) -> dict | None`: rapidfuzz `WRatio` of `parsed["query"]` against all aliases — **threshold ~85, stricter than sports' 78** (tiny vocabulary; wrong action beats wrong answer, so prefer misses). Below threshold → `None`. With left/right/sink/slider now in the vocabulary, sanity-check that near-collisions ("close the left blind" vs "close the right blind") resolve correctly at this threshold — the differing word is short; if flaky, exact-substring match on the target word (left/right/sink/slider/small/big) before fuzzy.
- On match: `POST {HA_URL}/api/services/button/press` `{"entity_id": ...}` — token via the existing `weather.py:_token()` pattern (same mounted `ha_token`, `config.py:40`; consider extracting `_token()` to a shared helper rather than a third copy).
- Return `{"response": entry["confirm"], "ok": True}`. Optimistic confirmation — no state verification (service call returns before blinds move; that's correct).

### 4. `app.py`

- `elif intent == "home_control":` block: `None` / exception → speak **"I don't control that."** — no `ask` fallback (contrast with sports at `app.py:543-560`). Success → normal `_finalize`; no `ask_mod.remember()` needed (nothing to follow up on). Response reaches dashboard via the generic `response` event.

### 5. Compose

- `voice-pipeline/docker-compose.yml`: bind-mount `home_commands.json` (`:ro`) so live alias edits in the repo reach the container without rebuild. No new secrets — HA token already mounted.

## Testing

1. Phase 1 gate: all 15 buttons work from HA UI by hand (blinds move, lighting flows fire); sliding-door duplicate resolved.
2. `POST /command` text bypass (`app.py:813`): each canonical alias + paraphrases ("make it brighter in here", "shut the blinds", "close the little blinds"); left-vs-right disambiguation both directions; "open the garage" and "unlock the front door" **must** miss with "I don't control that."; "close the blinds" while music plays (confirm `music_control` doesn't collide).
3. False-positive sweep: a few question-shaped phrases ("are the blinds closed") — should NOT route to `home_control` (v1: they'll go to `ask`; acceptable).
4. Live voice: full round-trip latency (expect fastest intent — one LAN call, no filler needed).
5. HA logbook shows each voice press (audit trail sanity check).

## Non-goals / later

- ~~Sun-azimuth glare flow~~ — **killed 2026-07-22**, replaced by the `blind_glare` combo (left + sink). Not deferred; deleted.
- Individual device control beyond the fixed blind alias table, brightness numbers by voice, room-scoped general control — deliberately never.
- Locks / garage / alarm — excluded, including any confirmation-tier scheme.
- State queries ("are the blinds closed?") — different intent (read-path), only if wanted later.
- State verification after service call.
- Non-kitchen blinds (family room, bedrooms) — add rows to the alias table + discovery list only if actually asked for by voice in practice.

## Addendum 2026-08-18 — blind percentages (`cover_set`)

Reverses one line in Non-goals above ("brightness numbers by voice … deliberately
never" stays dead; **blind positions do not**). Brad asked for it directly: the
afternoon sun glares off the west kitchen windows, and `blind_glare` — the combo
that replaced the killed azimuth flow — closes left + sink *fully*, which makes
the kitchen very dark. The right answer is a partial position, and all four
kitchen shades have reported `supported_features: 15` (open|close|**set_position**
|stop) since day one. Nothing was wired to ask.

- **Not a button and not a classifier intent.** A value can't ride a payload-less
  button press, and a button per percentage is not a table anyone maintains. The
  grammar *is* the intent (`intent.fast_parse_cover_level`), so it costs no LLM
  round trip: **187–245 ms** measured live vs. the 3–5 s a classifier turn takes.
- **Direction is the verb.** `open … to 80` → position 80; `close … to 80` →
  position 20; bare `set … to 80` → 80 (openness, as HA and the dashboard show
  it). The spoken reply always ends "…percent **open**", because confirming
  "close it 80 percent" with "setting it to 20 percent" sounds like a mishear.
- **An explicit level is required**, so the curated open/close buttons are
  untouched — the grammar cannot fire on "close the blinds".
- **Exact target match, not fuzzy.** `home_control` can afford `fuzz.ratio`
  because a miss presses nothing; here a near-miss would move a different window.
  Table lives in `covers.py` (code, not the phone-editable JSON — the alias editor
  at :8785/home-commands/ui does **not** see these targets).
- Direct `cover.set_cover_position` call, bypassing Node-RED. Narrow exception to
  "all behavior lives in Node-RED": closed entity list, one clamped number, no
  logic to drift out of sync with a flow.
- Room-scoped like `home_commands.json`: "the blind" is the tub window from the
  master closet, Simon's own from his room, all four from the kitchen.
- **Open:** `blind_glare` still closes fully. Brad is finding the position he
  likes by voice first; bake that number into the glare button afterward.

## Simon's room — fan + fun-color brightness (2026-08-22)

- **Blind percentage** ("close the blind to 70", "open the blinds halfway") already worked in Simon's room — `covers.py` has had `blind_simon` (`cover.boys_room_baby_blind`, sats `simon`) since the 2026-08-18 percentage build. Nothing changed.
- **Ceiling fan** (`fan.boys_room_ceiling_fan`, `percentage_step` 25): five new buttons in the Voice Buttons tab — `voice_simon_fan_on/off` (`fan.turn_on/off`) and `voice_simon_fan_low/medium/high` (`fan.set_percentage` 25/50/100), handled in the existing `Simon room command` function. Aliases in `home_commands.json` (seed + live `/data` table), `sats: ["simon"]`. Live-pressed: 100 → low 25 → high 100.
- **Matcher guard:** "turn on the fan" vs "turn on the lights" differ by one noun and sit near the 80 threshold, so `home_control._EXCLUDE_WORDS` drops `simon_lights_*` when the phrase says "fan" and `simon_fan_*` when it says "light(s)" — same idea as the blind pin words. Tests in `test_home_control.py`.
- **Fun color brightness:** effects inherit the color strip's last brightness, and after the 22:00 bedtime flow that is the 3% night red — so a daytime "give me a cool color" ran the animation at 3%. The button now sends `brightness_pct` computed exactly as the crown white CT builders do (`BriCurveStair ?? BriCurve`, clamped 1–100) alongside `effect`. Effect allow-list also synced with the four 2026-08-22 effects (Ocean Waves, Pac-Man, Thunderstorm, Campfire). Live-pressed 2026-08-22 ~17:40: Ocean Waves at brightness 181 (≈71%, the CT level) instead of the inherited value.
- **Named effects:** 15 buttons `voice_simon_fx_<slug>` (one per crown effect, `None` excluded), resolved by a `namedEffects` map in the same function with the same `crownBrightness()`; aliases are kid-speak ("pac man"/"pacman"/"pack man", "waves", "dino stomp"/"dinosaurs", "stars", "storm", "fire"...), Simon-room only. Live-pressed Pac-Man + Dino Stomp at bri 178. Note `GET /home-commands/match` is unscoped (ignores room) — use `home_control._match(q, sat)` to dry-run room scoping.

## Claire's room — second Voice PE (2026-08-25)

Twin of Simon's room on the same firmware/bridge (`voice-assistant/voice-pe/`,
device `claire-voice-pe` 192.168.30.60, `claire-voice-bridge` on :8795, zone
`claire` → `ma_claire_room` / snap client `claire_room`, policy = quiet hours
20:00–07:00, no guard entity). Entities are from Dashy's
Claire's Room section — the `adrienne_office_*` ids are stale names for her
room, not another room.

Sixteen buttons `button.voice_claire_*`, all handled by the `Claire room
command` function on the Voice Buttons tab (router rule 6, `claire_`):

| Command | Does |
|---|---|
| `story_time` | `light.claire_lamp` 10% @ 2000 K, fan lights + closet off, blind closed, `claireLightOverride=true` held by a 1 h stoptimer on the tab (released by `story time override release`) |
| `bedtime` | lamp + fan lights + closet off, blind closed, override cleared / timer stopped |
| `blind_close` / `blind_open` | `cover.adrienne_office_bali_shades_windowshade` (close is idempotent — "if it is open" needs no check) |
| `lights_on` / `lights_off` | lamp + `light.claire_fan_1` + `light.claire_fan_2_2` at `BriCurve`/`CTWide` (on also releases the override); off adds the closet light |
| `fan_on/off/low/medium/high` | `fan.claire_fan_js` (`percentage_step` 33 → 33/66/100) |
| `too_hot` / `too_cold` | `climate.clairehvac2_my_heat_pump` via `Claire mini split now` → `Claire mini split decide`: if cool/heat/auto, setpoint ∓2 °F (clamped 61–79); if off/dry/fan_only, `set_temperature` with `hvac_mode` cool/heat at current temp ∓2 |
| `hvac_cool` / `hvac_heat` / `hvac_off` | "turn on the AC" / "turn on the heat" / "turn off the AC" |

Orchestrator: `home_commands.json` `claire_*` (sats `["claire"]`),
`covers.py` `blind_claire`, `_EXCLUDE_WORDS` extended to `claire_lights`/`claire_fan`,
and both `_ROOM_WORDS` maps learned `simon`/`claire` (+ possessives) — the
kid-name words are stripped before scoring, so "close Claire's blind" from
the kitchen matches her room's "close the blind".

Smoke test by API press 2026-08-25: fan low 33 → off; too hot 71.5→70 (device
rounds to whole degrees) → too cold → 71.5; story time (lamp on, then re-set to 10 % per Brad,
override true) → bedtime (all off, override false); blind open 100 → close 0;
text `/command` "turn off the fan" as `sat=claire` → `claire_fan_off`, reply
broadcast to room `claire` at 15. Gotchas: the lamp reports ~13 s late and the
fan bounces back on if told off within a few seconds of `set_percentage` — do
not chain presses in tests faster than the devices settle. Live-voice pending.

## Kid rooms — staged "turn on the lights" / "brighter" + quiet hours off (2026-08-25)

The 2 a.m. hands-full case (Brad): lights must work by voice in both kid rooms
at any hour, and a second ask should blast them.

- **Quiet hours + guards disabled** in both bridges (`QUIET_START == QUIET_END`)
  and `satellite_policies.json` (same trick, guard entities removed). Code
  paths intact; re-enable notes + two follow-ups (speaker-ID pass-through,
  "quiet time" sound) in `voice-assistant-backlog.md`.
- **Staging** lives in one function, `kid room lights stage`, fed by an
  `api-current-state` per room (`Simon lights now` = `light.simon_fan_lights`,
  `Claire lamp now` = `light.claire_lamp`): reference light off → the room's
  globals (Simon `BriCurveStair`, Claire `BriCurve`, both `CTWide`); reference
  light already on, or the `*_brighter` button → 100 %. No timer/return: the
  next 5-minute circadian tick takes them back, same as a Pico press.
- **Simon's "lights"** = `light.simon_fan_lights` (group of the two fan bulbs,
  one of them mis-named `light.master_bed_lamp_ct`) + `light.simon_lamp_ct` +
  crown: colour channel off, then CT channel on. `simon_lights_off` now turns
  off all of those (crown colour included, so a running effect stops).
- **Claire's "lights"** = lamp + `light.claire_fan_1` + `light.claire_fan_2_2`
  (the two bulbs in the fan). Off adds the closet light.
- New buttons `simon_brighter` / `claire_brighter`; aliases deliberately
  overlap `kitchen_brighten` ("brighter", "turn up the lights", "it's too
  dark") — the room-local pool wins in the kid rooms, the kitchen keeps its
  own from anywhere else (tested).
- Live-pressed 2026-08-25: Simon 194→255→off→194 (crown white on, colour
  off); Claire off→255→255→off (her BriCurve was 100 at the time).

## Apostrophes + classifier-miss rescue (2026-08-25, live from Claire's room)

"Okay computer, it's story time" failed twice: the wake-strip normaliser turned
`it's` into `it s`, the exact-alias fast path missed, and the LLM classifier
returned `none` (its prompt has no idea "story time" is a home command).
Fixes: `verify._normalize` now drops apostrophes instead of spacing them
(`its story time`); `home_control._fold` makes alias matching apostrophe-blind
and repairs the `it s` artefact; and `app.py` rescues a non-follow-up `none`
through `home_control.fuzzy_match` (room-scoped, same 80 threshold) before
apologising. Text `/command "It's story time."` as `sat=claire` →
`claire_story_time`. Lesson: every curated phrase that is not obviously a
device command needs either an exact alias or this rescue — the classifier
prompt is not the alias table.

## Family room commands + paired-mic room evidence (built 2026-08-25)

**Shipped:** `fr_fan_on/off/low/medium/high` (`fan.family_room_ceiling_fan`, 25/75/100 —
Brad: 75 is "medium", 50 has no name; bare "on" restores last speed),
`fr_cans_on/off` (`light.family_room_can_group`, the four BR30s — "the cans" means
the family room even though the kitchen has `light.kitchen_can_dimmer`), and the
blinds split: `blinds_kitchen_*` (`sats:["kitchen"]`, the four), `blinds_family_*`
(`sats:["familyroom"]`, `cover.family_room_family_room_serena` = Lutron-direct, NOT
the Hubitat mirror `cover.family_room_serena_windowshade`, + `cover.family_room_small_shade`
FYRTUR), `blinds_all_*` (`sats:["kitchen","familyroom"]`, all six — entity kept,
Node-RED target list grew). All fan/cans/six-blind commands are scoped to the two
open-space mics; kids' rooms keep their own fans, the master has none.

**Matcher changes** (`home_control.py`): `"family"` in `_ROOM_WORDS` (named room beats
the mic); fan↔cans in `_EXCLUDE_WORDS` (`fuzz.ratio("turn on the fan","turn on the cans")`
= 90); `_ALL_WORDS` = {all, every, everywhere} skips the local-first pass — otherwise
"close ALL the blinds" (89 vs the room's own "close the blinds") never reached the
six-blind command; `sat=None` now reads as `DEFAULT_SAT` like the rest of the app. Live
`/data` table migrates itself (pre-split `blinds_all_*` are replaced by the seed's).
`covers.py` gained `blinds_family` for the percent grammar ("open the family room
blinds to 30", or bare "the blinds" from the family-room mic).

**Node-RED** "Voice Buttons" tab: 13 new discovery buttons, `blind cmd -> covers`
targets `blinds_kitchen/blinds_family/blinds_all`, router rule 7 `fr_` → new
`Family room command` function (id `cafe000000000020`). Backup of the pre-change tab
in the session scratchpad; the deployed tab is the source of truth.

**Why "whichever mic responded" is not a room signal (30 days to 2026-08-25):** 104 of
169 verified open-space wakes were heard by both mics; the kitchen won 85 (82%). It runs
`HOP_MS=192` on the faster box vs the family-room Pi's 320, so it reaches `/verify`
first by construction. Expect bare "close the blinds" from the couch to close the
kitchen four most of the time; "family room blinds" is the reliable phrase until v2.

**Paired-mic evidence (logging only, v2 not built):** loudness IS a distance signal,
so `/verify` now measures each pre-roll (`orchestrator/loudness.py`: RMS of the loudest
500 ms window, dBFS, computed in a background task after the response — nothing on the
chime path) and the satellite passes its stage-1 peak on `/verify?peak=` (the loser
never posts `/telemetry`). New `turns` columns: `wake_rms_db` on every wake row;
loser rows get `arb_turn_id` (the winner's turn); the winner's row gets
`other_sat/other_stage1/other_rms_db`. Parakeet's "score" (`wake_score`) is text
similarity to the wake phrase and saturates at 100 on both mics — useless for this;
stage-1 peak is acoustic but also saturates near the mic; RMS is the primary signal.
Query once a couple of weeks have accumulated:

```sql
select datetime(at,'unixepoch','localtime') t, sat, wake_rms_db, stage1_score,
       other_sat, other_rms_db, other_stage1, command
from turns where other_sat is not null order by at desc;
```

The v2 chooser (attribute the room for `blinds_kitchen/blinds_family` by the louder
mic, at command time, with a per-array gain offset — XVF3800 vs Pi4 ReSpeaker) is a
`home_control.handle` change once the pairs show a usable margin. Log line to grep:
`arb evidence winner=… rms=… | loser=… rms=…`.

**Cans are staged (2026-08-25 eve, Brad):** a bare `light.turn_on` restored the last
state (cool and very bright). Now `fr_cans_on` reads the group first (`family room
cans now` → `family room cans stage`, ids `cafe000000000021/22`): off → `BriCurveCans`
% at `CTCans` K, the same values the 5-min "Update CT Values while On" tick writes;
already on, or `fr_cans_brighter` ("make the cans brighter" / "turn up the cans" /
"cans up") → 100 % with `familyRoomCansOverride=true` so the tick leaves them, released
by a 90-min stoptimer (`cafe000000000023/24`) or by `fr_cans_off`. Bare "brighter" /
"make it brighter" stays the kitchen's staged brighten from both mics (it was already
house-wide; scoping it per mic would just re-enter the hop-race problem). Press-tested
off→on (4 %/2500 K) → brighter (100 %, override) → off → on.
