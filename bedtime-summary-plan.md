# Bedtime Summary Rework Plan

**Status:** DEPLOYED 2026-07-20 (~12:09). Daytime inject test passed twice (pushover delivered, formatter output verified via flow context). Audio path untested midday ("no target amp players" — amp off; expected). Live audio validation = tonight's real run. Backup: `flows.json.backup_before_change_20260720_120413`. OpenAI key still needs revoking (in git history).
**Where:** Node-RED (Beelink docker, `node-red-container-main`), tab **Bedtime Routine** (`ab965bba13d85fcb`). Flow file: `/home/pi/nodered/data/projects/nodered_n100_mini/flows.json`; deploy via Admin API `PUT /flow/ab965bba13d85fcb` per `nodered-flow-agent-guide.md`.

## Goal

Replace the 10–20-second checklist-of-non-events with an exception-based summary:

- **Audio (bedroom ceiling speakers):** warnings only; all-clear night is just *"The home is secure. Goodnight."*
- **Pushover:** compact 3-liner — secure summary, one ✅ roll-up, one weather footer; warnings inserted on top when present.
- **Guarantee kept:** "Home is secure" is emitted **only** when doors + locks + HSM all positively check out. Missing/stale source data becomes a visible warning ("door status unavailable"), never a silent drop. This is strictly safer than today, where a missing `DoorList` global reads as "All doors are closed."

Decisions made 2026-07-20 (Brad): water warn-only ≥30 min non-irrigation · compact 3-liner pushover · weather as pushover-only FYI footer · drop ChatGPT reword + DALL-E entirely (deterministic text only).

## Current chain (for reference)

```
inject 80b4df73 / http-in 27295736 (bedtime trigger)
→ hsm state c5a2e85c → HSM Check 5fb9c945 → Check Doors 76e3fa8d → Lock Check dadfaf1f
→ Check Windows b9eb229e → Downstairs Window Check 6f00c705 → check gates ea0a3f3d
→ Weather Forecast subflow 5cac8deb → Oven 735baaab → Washer 6417efbc
→ water state 89471e77 → water fn ad72b37c → Tesla Plug Check d9da6917
→ Check No response 93e2da9c → Heater a0f55685 → Dishwasher 65451f2d
→ simonalarm state 448428f5 → simon alarm check 7b924104
→ Combine messages 231c306f
   ├─ ArtMode switch e09e1e6c → (DALLE path 39122c1c/5afd23e9/9e2bdb00/…) or pushover e91478d3
   ├─ delay 15234373 → announcements-enabled check 382e6c53 → … chatgpt-mode switch d85b13b8
   │    → (Build ChatGPT Query 14ca4b46 → openai → msg.alexa 429ab3c1 → mysql log) or bypass
   │    → … set speakers fa6bdf78 → Amp Speakers subflow b9216c82
```

Each check node appends a full sentence (`msg.doors`, `msg.lock`, `msg.HSM`, …); Combine joins them in fixed order → clunky happy path.

## New design

### 1. Structured facts instead of sentences

Each check node pushes onto `msg.facts` (array, created by the first node in the chain):

```js
msg.facts.push({
  id: "doors",              // stable key
  level: "ok" | "warn",     // warn = must surface everywhere
  okLabel: "doors closed",          // short fragment for roll-up line
  warnText: "There are 2 doors open: back door, garage entry.",  // full sentence, pushover
  audioText: "the back door and garage entry are open",          // spoken fragment (warn only)
});
```

Rules:
- **Unavailable data = warn.** If a source global is missing/empty/stale, push `level:"warn"` with text like "Door status unavailable." Never default to OK.
- Checks that are silent when OK (oven, washer, dishwasher, heater, gates, downstairs windows, water) push an `ok` fact with `okLabel:null` (excluded from roll-up) or a `warn`.

### 2. Per-check spec

| Check | OK behavior | Warn condition | Notes |
|---|---|---|---|
| Doors | fold into secure line | any open → list names | same source (`DoorList`) |
| Locks | fold into secure line | any unlocked → list; staleness >24h → warn | staleness becomes a warn, not a trailing sentence |
| HSM | fold into secure line | not `armedNight` | |
| **Secure line** | `doors ok && locks ok && hsm ok` → "Home is secure — doors closed, locks locked, armed." | any of the three warn → no secure claim; show warnings | computed in formatter, not a check node |
| Water | **silent** | runtime ≥ **30 min** AND not irrigation (`flumeRunTimeObject.classification !== "irrigation"` and not `sprinkler_active`/`sprinkler_guard_active`) | threshold const at top of the function for easy tuning |
| Tesla | `okLabel:"Teslas plugged in"` | existing home-and-unplugged logic unchanged | |
| Gates | silent | any open → list names | |
| Windows (all) | `okLabel:"windows closed"` | any open → list names | drop the separate downstairs-check sentence from the message; downstairs fan-speed logic elsewhere on the tab is untouched |
| Sensor refresh | `okLabel:"sensors reporting"` | any >23h silent → list names | drop the always-on "All door sensors are responding." |
| Simon's alarm | `okLabel:"Simon's alarm armed"` | not armed | exception-only in audio (as today); OK state lives in the roll-up line only |
| Dishwasher | silent | dirty >12h (existing logic) | "status not available" stays a warn |
| Oven / Washer | silent | on / wet clothes (existing logic) | |
| Heater (winter) | silent | forecast low ≤10° → "Heat the bathroom tonight." | kept |
| **"Open a window tonight" / "Keep windows closed"** | **deleted** | — | whole-house fan replaced it; remove Weather Forecast subflow instance `5cac8deb` from the bedtime chain (subflow itself stays — used elsewhere) |

### 3. Formatter (replaces Combine messages `231c306f`)

Reads `msg.facts`, emits:

**Pushover (`msg.payload`):**
```
⚠️ <warn sentence>            ← 0..n lines, warnings always first
🌙 Home is secure — doors closed, locks locked, armed.   ← or "⚠️ Home is NOT secure" framing if any core warn
✅ Teslas plugged in · windows closed · sensors reporting · Simon's alarm armed
Low 70° · AQI 12 (good) · 0% rain
```
Footer reads globals directly (`forecastTemp`, `OutdoorAQILocal`, `RainForecastforBed`) — no dependency on the weather subflow; if a global is missing, omit that fragment (footer is FYI-only, no fabricated zeros).

**Audio (`msg.alexa`):**
- No warns: `The home is secure. Goodnight.`
- With warns: `Heads up: <audioText fragments joined with "and">. Otherwise the home is secure. Goodnight.` (if a core security warn: drop "otherwise secure").

Keep `flow.set("BedtimeMessage", Text)` for the dashboard/log consumers.

### 4. Removals

- **ChatGPT emotion reword path:** chatgpt-mode state `e1632a5d` + switch `d85b13b8`, Build ChatGPT Query `14ca4b46`, its http request `7187e5c4`, `msg.alexa` `429ab3c1`, INSERT INTO log `259223f3` (+ mysql node if unused elsewhere), stoptimer nodes tied to it. Wire announcement path straight through.
- **DALL-E path:** ArtMode switch `e09e1e6c`, Build DALLE Query `39122c1c`, http request `5afd23e9`, Build Message ×3 (`9e2bdb00`, `f9f87d54`, `27e02f69`), jimp `a7cc2a2b`, pushover-with-image `165facb4`, Build Log Entry `5f5b0c69` + file node. Combine→pushover `e91478d3` becomes the only pushover path.
- **🔑 Hardcoded OpenAI API key** lives in `14ca4b46` and `39122c1c` inside flows.json (git-backed). Deleting the nodes removes it from HEAD but **it stays in git history — revoke the key at platform.openai.com** (it's old `sk-` format, likely dead, revoke anyway).

### 5. What does NOT change

Everything else on the tab: mode/HSM arming calls, Z-Wave off subflows, blinds/fan/mini-split logic, vacuum kick-off, Simon crown/bed globals, volume path (`set volume`, age/brad/guest switches), Amp Speakers subflow, "disable bedroom announcements" gate, BI profile call, stair pixel strip.

## Build & test steps

1. Backup: `cp data/projects/nodered_n100_mini/flows.json data/projects/nodered_n100_mini/flows.json.backup_before_change_$(date +%Y%m%d_%H%M%S)`
2. `GET /flow/ab965bba13d85fcb`, edit nodes as above, `PUT` back (single-tab deploy).
3. Daytime test: manual inject `80b4df73` with pushover intact; expect compact message. Force-test warn paths by temporarily setting a fake global (e.g. `gateList` entry open) via a scratch inject, or just review formatter unit cases in the function's on-message comments.
4. Verify audio path fires (or temporarily wire audio to debug to check `msg.alexa` text without waking the room).
5. Live validation = tonight's real run; compare pushover against old format expectations.
6. Revoke the OpenAI key.

## Open items / later ideas (not in scope now)

- Optional: trend flavor ("3rd night in a row the small gate was left open") from the bedtime MySQL log.
- Optional: severity-based pushover priority (warn nights ping, all-clear nights silent notification).
