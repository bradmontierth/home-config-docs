# Home Control Intent (Curated Buttons) Plan

**Status:** PLANNED 2026-07-20. Nothing built — no HA helpers, no Node-RED flows, no orchestrator code.
**Where:** orchestrator (`home_config/voice-assistant/orchestrator/`) + HA helpers + Node-RED (Beelink, existing tabs/globals). Satellite untouched. Companion plan: `business-hours-places-plan.md` (same build session candidate).

## Goal

Very limited voice home control — a handful of curated *commands*, not general device control. "Close the blinds," "brighten the lights," "set the mood for dinner." No room disambiguation, no device names, no exposure of individual Zigbee2MQTT/ESPHome entities to the voice layer.

Decisions made 2026-07-20 (Brad):

- **Voice = another Pico button.** All logic and state live in Node-RED (all-custom flows, globals, existing "button push → temporary override → 60-min time-based return" machinery). Voice is just one more entry point into those flows.
- **Mechanism: one HA `input_button` per command.** Orchestrator only ever calls `input_button/press`; Node-RED events-state nodes pick up presses like any wall button. Buttons are also pressable from HA dashboards/phone, and every voice action lands in the HA logbook.
- No HA scripts — logic would be duplicated outside Node-RED. No direct orchestrator→Node-RED HTTP — bypasses HA visibility for no gain.
- **Blast radius = the button list.** Worst case of any parse error / fuzzy mismatch / false wake: a flow Brad wrote runs at an odd time. Locks, garage, alarm excluded entirely (not a v1 confirmation-tier thing — just absent).
- **Misses do NOT fall back to `ask`** (unlike sports/weather/places). A control phrase that doesn't match says "I don't control that" — never web-search "open the blinds."

## MVP command set

| Command | Aliases (starter) | Entity | Node-RED flow behavior | Exit |
|---|---|---|---|---|
| Fix the glare | "close the blinds", "fix the glare", "sun's in the kids' eyes" | `input_button.voice_kitchen_blinds_close` | v1: close all 4 kitchen blinds. v2: sun-azimuth logic picks the 1–2 glaring blinds | open command / manual |
| Open the blinds | "open the blinds", "open the kitchen blinds" | `input_button.voice_kitchen_blinds_open` | open all 4 | — |
| Brighten | "brighten the lights", "brighter", "bump up the lights" | `input_button.voice_kitchen_brighten` | new trigger into the **existing** override flow; stepping logic (dim→50%, bright→100%) lives there | existing 60-min return |
| Dinner mood | "set the mood for dinner", "dinner mode", "dinner lights" | `input_button.voice_dinner_mood` | WLED preset (kitchen fixture color strips top+bottom) + ESPHome white strips off | 60-min return or back-to-normal |
| Back to normal | "back to normal", "normal lights", "reset the lights" | `input_button.voice_lights_normal` | cancel active overrides, resume circadian baseline | — |

Naming convention `input_button.voice_*` so the voice-exposed surface is greppable in HA.

## Phase 1 — HA + Node-RED (flows are the real work)

1. Create the 5 `input_button` helpers in HA.
2. Node-RED: events-state node per button (press = state timestamp change) wired into flows:
   - **Brighten**: attach to the existing Pico/Zooz override entry point — should be a wire, not new logic.
   - **Blinds open/close-all**: simple cover group calls. (Sun-azimuth glare selection = v2; needs Brad's window-orientation input.)
   - **Dinner mood**: WLED preset call + ESPHome strips off + an exit path matching the house override pattern.
   - **Back to normal**: cancel/expire override state, re-apply circadian values.
3. **Validate with zero voice involvement:** press each button from the HA UI, watch the room. Flows are done when the buttons work by hand.

Node-RED deploys via Admin API per `nodered-flow-agent-guide.md` (same as bedtime rework); backup flows.json first as usual.

## Phase 2 — orchestrator intent

Smaller than the Places intent (~40-line handler, one code path).

### 1. Command reference JSON

`home_config/voice-assistant/orchestrator/home_commands.json` (versioned with the code, mounted into the container):

```json
{
  "blinds_close": {
    "aliases": ["close the blinds", "fix the glare", "close the kitchen blinds"],
    "entity": "input_button.voice_kitchen_blinds_close",
    "confirm": "Closing the blinds."
  }
}
```

- One entry per command; `confirm` is the full spoken response (no LLM involvement in phrasing).
- **Hot-reload on file mtime** — editing aliases or adding a command never needs a container restart.

### 2. `intent.py`

- Add `home_control` to `INTENTS` (`intent.py:16-21`); prompt rule: commands *to change something in the house* (lights, blinds, modes) → `intent: home_control`, verbatim-ish phrase into `query`. Explicitly distinct from `music_control` and from questions *about* the house.
- Coercion in `_validate` (`intent.py:155`).

### 3. New `orchestrator/home_control.py`

- `async def handle(parsed) -> dict | None`: rapidfuzz `WRatio` of `parsed["query"]` against all aliases — **threshold ~85, stricter than sports' 78** (tiny vocabulary; wrong action beats wrong answer, so prefer misses). Below threshold → `None`.
- On match: `POST {HA_URL}/api/services/input_button/press` `{"entity_id": ...}` — token via the existing `weather.py:_token()` pattern (same mounted `ha_token`, `config.py:40`; consider extracting `_token()` to a shared helper rather than a third copy).
- Return `{"response": entry["confirm"], "ok": True}`. Optimistic confirmation — no state verification (service call returns before blinds move; that's correct).

### 4. `app.py`

- `elif intent == "home_control":` block: `None` / exception → speak **"I don't control that."** — no `ask` fallback (contrast with sports at `app.py:543-560`). Success → normal `_finalize`; no `ask_mod.remember()` needed (nothing to follow up on). Response reaches dashboard via the generic `response` event.

### 5. Compose

- `voice-pipeline/docker-compose.yml`: bind-mount `home_commands.json` (`:ro`) so live alias edits in the repo reach the container without rebuild. No new secrets — HA token already mounted.

## Testing

1. Phase 1 gate: all 5 buttons work from HA UI by hand.
2. `POST /command` text bypass (`app.py:813`): each canonical alias + paraphrases ("make it brighter in here", "shut the blinds") to check LLM→fuzzy pipeline; "open the garage" and "unlock the front door" **must** miss with "I don't control that."; "close the blinds" while music plays (confirm `music_control` doesn't collide).
3. False-positive sweep: a few question-shaped phrases ("are the blinds closed") — should NOT route to `home_control` (v1: they'll go to `ask`; acceptable).
4. Live voice: full round-trip latency (expect fastest intent — one LAN call, no filler needed).
5. HA logbook shows each voice press (audit trail sanity check).

## Non-goals / later

- Sun-azimuth glare flow choosing which 1–2 blinds to close (v2 of Phase 1; needs window-orientation mapping from Brad).
- Individual device control, brightness numbers by voice, room-scoped general control — deliberately never.
- Locks / garage / alarm — excluded, including any confirmation-tier scheme.
- State queries ("are the blinds closed?") — different intent (read-path), only if wanted later.
- State verification after service call.
