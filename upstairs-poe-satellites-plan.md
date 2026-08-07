# Upstairs Voice Satellites — Two Bookshelf Rooms Now, Ceiling Later

Status: **plan only, nothing built.** Drafted 2026-07-30 as a PoE/ceiling
hardware POC; **rescoped 2026-08-07** (Brad) to two shelf-mounted satellites,
with the entire ceiling program deferred 2–3 years.

## The 2026-08-07 decision

Two satellites, built independently, in either order. Neither blocks the other.

| | **Path A** | **Path B** |
| --- | --- | --- |
| Room | Master closet | Simon's room |
| Device | The freed `.24` Pi + the Jabra knock-off USB mic | The Home Assistant Voice PE already on hand |
| Network | Cat6 drop through the attic to the closet shelf | Wi-Fi |
| Hardware cost | $0 | $0 |
| Stage-1 wake | On-device, `assistant.py` unchanged | Server-side, over an ESPHome-protocol bridge |
| New code | None on the device | The bridge — this is the real work |
| Answers | Does anyone actually reach for a bedroom mic? | Can ESPHome + a bridge be a first-class satellite? |

Why this shape, recorded so it doesn't get relitigated:

- **The kids can't trigger it yet.** Every command in scope is adult-spoken for
  the next 2–3 years. That removes the reason to rush the ceiling.
- **A finished appliance beats a bare board on a shelf** in a kid's room: no
  wires to grab beyond USB-C, a visible mute switch, and it looks like it
  belongs on a bookshelf.
- **A closet shelf is not a ceiling.** The thermal argument that rules a Pi out
  of an insulated joist bay does not apply to open conditioned air, so the
  Path A Pi is a *permanent* answer for that room, not a probe to be retired.
  The master closet may never need to be one of the five ceiling drops.
- **The ceiling becomes a conversation in 2–3 years**, when both kids want it
  and cleaning the shelves up is the actual motivation.

### What this decision freezes

Everything in **Part 4** is on ice: the ReSpeaker XVF3800 + XIAO purchase, the
T1/T2/T3 bench, the ~$215 of parts, the Step 0 multimeter session.

**Do not buy the $54.50 ReSpeaker.** Its entire justification was "nothing is
thrown away" on the way to the ceiling; with the ceiling years out, that
argument expires long before the board gets used. Hardware bought now should be
judged as "a good bookshelf device for three years" — which is the HAVPE and the
Pi already sitting in this house. If a third room wants one, buy a second HAVPE
(~$60), one room at a time, as demand proves itself.

---

## Part 1 — the shared foundation (built once, both paths need it)

The hardware was never the bottleneck. These five items are, and they are the
same work whichever device is in the room.

**1. Per-satellite target map.** `sat_id → {ma_player, ha_player, playback,
music_policy, quiet_hours}`, hot-reloaded from JSON like `home_commands.json`
and `broadcast_rooms.json` already are. Nothing today is per-room.

**2. Room × state intent allowlist — design the state axis in now.** Not just
`room → [intents]` but `room × state → [intents]`, because Path B's core
requirement is state-dependent (below). Retrofitting a state dimension onto a
room-only table later is the annoying version of this job. Reading the state is
already solved: `find_phone.py:138` GETs `/api/states/{entity}`, and
`input_boolean.simonalarm` is already a real HA entity — **no Node-RED work
needed for the gate.**

**3. Per-room music policy.** `music.py` targets the kitchen jukebox queue
unconditionally (`config.py:239`, `MA_QUEUE_ID`) and deliberately shuffles
anything resolving to an artist or playlist (`music.py:568`). So "play Raffi"
today means endless Raffi on the kitchen speakers. Needs
`{queue_id, single_track}` per room, where `single_track` forces resolution to a
*track* rather than an artist.

**4. Reply routing — ON the critical path for both paths (Brad, 2026-08-07).**
An earlier draft of this document called this optional on the grounds that each
device could use a local speaker. **Rejected.** Both rooms speak out of the
installed whole-home zones: the HAVPE's own speaker is poor, and the closet Pi
was never going to have one worth listening to. **Both satellites are therefore
mic-only**, the same shape as the family-room satellite. Appendix A items 1–3
are a prerequisite, not a follow-up.

Three consequences that change the build order:

- **Do item 3 (the `msg.ttsUrl` bypass) up front, not as an optimization.**
  Verified against the live Admin API, 2026-08-07. Kokoro *is* the house's fast
  path, but it lives in **tts-router** (`:8891`), not in the subflow:
  `main.py:1186` treats a `fast:` or `kokoro:` voice prefix as forced-Kokoro,
  and the orchestrator already speaks with `TTS_VOICE=fast:doorbell`
  (`config.py:29`) — that is the doorbell voice. Inside Amp Speakers, though,
  `Prepare ungroup + wake + TTS` defaults to **`tts.openai` with voice
  `picard:calm`** — a cloud round-trip in a different voice. It honours
  `msg.ttsEntity` / `msg.voice` overrides, so `tts.kokoro` looks like a
  one-property fix — except **the `tts.kokoro` entity is `unavailable`** as of
  this writing, so that override would fail today. The bypass sidesteps both
  problems: the orchestrator has already rendered the audio in the right voice,
  and the subflow just pads, wakes, and announces it.
- **Item 2's `forceBedroom` is now required, not a nicety.**
  `DisableBedroomAnnouncements` and `adrienneWorkingDisableAnnounce` exist to
  silence the master zone — and that zone is now the *reply* path. Without the
  override, a question asked in the closet gets answered into a muted zone and
  the user hears nothing, with no error anywhere. This is the most likely
  day-one "it just doesn't work" failure of Path A.
- **The 3-second amp wake is genuinely cheap, as designed.** Kick it at stage-2
  verify and the gate elapses under ASR + intent + TTS, which is ~1.5–4 s on an
  ordinary turn and far longer on an ask. It costs nothing on the paths that are
  already slow.

**4a. The wake chime is the casualty — decide this before building.** In the
kitchen the local chime fires the instant `/verify` confirms, and that sub-second
"I heard you" is what stops people repeating themselves. Routed through a cold
amp it would land ~3 s late, which is worse than no chime at all. Options:

- **Simon's room: the HAVPE LED ring.** Native, bridge-drivable, silent, and
  strictly better than a chime in a room where a child is going to sleep.
- **Master closet: a tiny speaker used *only* for the chime.** A ~$10 speaker
  playing a 200 ms tone is not competing with the installed speakers for
  anything that matters — replies and music still come out of the zone. This
  keeps sub-second feedback without compromising the reason for the decision.

**4c. NOT a blocker — the reply path is MA-native and never resolves an HA
entity.** An earlier revision of this document (commit `0be8ba5`) called the
missing `media_player.master_bedroom` and `media_player.shower` entities a hard
blocker for Path A. **That was wrong, and the wrong fix would have broken a
working path.** Corrected here per Brad's challenge, with the mechanism read out
of the live subflow:

- `msg.players` carries HA entity-id **strings used only as keys** into
  `MA_PLAYER_MAP`. Nothing looks them up in HA.
- Every audio command is addressed by **MA player id**:
  `players/cmd/play_announcement` with `player_id: ma_shower`, and
  home-audio-adapter `/v1/isolate` with the same id.
- The only HA call in the subflow is `tts_get_url`, which is not
  player-specific.
- **All five MA players are live and available**, verified against the MA API
  2026-08-07: `ma_loft`, `ma_simon_room`, `ma_claire_room`,
  `ma_master_bedroom`, `ma_shower` — all `available=True`, none synced.

So the master and shower reply paths are fine, and **the broadcast intercom to
those rooms is untested, not broken** — the earlier claim that it had been
quietly failing is retracted. **Do not "fix" `MA_PLAYER_MAP` by repointing it at
`_snapcast_client` entities.** That would swap a working MA-native path for a
different integration and break the three rooms that work.

Two real notes survive the correction:

- **The HA entities were never missing — they carry legacy entity ids.**
  Resolved by Brad, 2026-08-07: `media_player.speaker_pi` is the master bedroom
  and `media_player.speaker_pi_2` is the master shower. Both are online, and
  both carry the MA-supplied friendly names ("Master Bedroom", "Shower"), which
  is why an entity-id search finds nothing and a friendly-name search finds them
  instantly. **Search this house's media players by friendly name, not entity
  id.** This fills Appendix A item 1's `ha_player` field for both rooms.

  **Recommended cleanup: rename them to `media_player.master_bedroom` and
  `media_player.shower`.** Nothing references the current ids — verified across
  the live Node-RED flows, `home_config`, HA dashboards and automations,
  `dashboard_webapp`, `homepage` and `homebridge`; the only hits are HA's own
  entity registry, `core.restore_state` and the log. The rename is functionally
  inert (the announce path is MA-native either way) but it makes the two room
  maps literally correct instead of coincidentally string-keyed, restores
  symmetry with loft/claire/simon, and removes the exact trap that produced the
  false blocker above.

- **Grouping is still why the ungroup step exists.** Grouped players cannot be
  announced to individually, which is what the per-player `/v1/isolate` call is
  for. But note these entities do *not* vanish when grouped — they expose a
  `group_members` attribute (currently empty). Don't rely on presence/absence of
  an entity as a grouping signal.
- **`forceBedroom` is confirmed required, from the code.** The subflow filters
  on the literal string `media_player.master_bedroom` whenever
  `DisableBedroomAnnouncements` or `adrienneWorkingDisableAnnounce` is set, and
  `msg.forceBedroom === true` is the only override. Without it, a closet reply
  is dropped by that filter with nothing but a `node.warn`.

**4b. Playback muting is promoted to day-one work.** Appendix A's note that the
mic hears its own reply from the ceiling with no AEC reference now applies to
both rooms immediately: the server must suppress that room's detection for the
reply duration + ~500 ms. It knows the padded WAV length, so this is arithmetic,
but it has to exist on day one rather than after the first feedback loop.

**5. Arbitration — record the trigger, don't do the work yet.** `_ARB`
(`app.py:160`) is one house-global holder with `ARB_SUPPRESS_S=3`. Kitchen,
master closet, and Simon's room are far enough apart that a single "okay
computer" won't reach two mics, so the existing race is tolerable. **The
proximity-group rework (Appendix A item 5) becomes mandatory the moment two mics
land within earshot of each other** — e.g. adding the master *bedroom* alongside
the closet, or the loft alongside Simon. Don't add such a pair casually.

---

## Part 2 — Path A: the master closet Pi

### Hardware and network

The `.24` kitchen Pi, freed by the `.251` mini-PC cutover. **Before it rejoins
the network, disable its `voice-assistant` and `squeezelite` units** — it must
never contend with the kitchen box for the wake path or the Pi player MAC.

Mic: the Jabra knock-off. Known to be poor at kitchen-across-the-room distances
and entirely adequate at closet distances — one person, arm's length, quiet
room, hard surfaces. This is the case it is fine for.

Network: a Cat6 drop through the attic to the closet shelf. Wired is the whole
point — it removes Wi-Fi from the failure surface of the only path that has no
new firmware in it. **This run is also the pilot for the eventual ceiling
program:** it proves the attic route, the switch ports, and the PoE budget for
$0 extra. Pull two or three while up there.

Playback: **none locally except the chime.** Replies and music both go to the
master bath / shower zone through the Amp Speakers subflow, addressed as the MA
player `ma_shower` — see Part 1 items 4, 4a and 4c. The MA player is live and
available; the missing `media_player.shower` HA entity is not in this path.

The Pi needs only a ~$10 speaker for the wake chime (`PLAYBACK_DEVICE` already
defaults to `plughw:CARD=Headphones`).

**Shelf tidiness — the real objection (Brad, 2026-08-07).** The top shelf
already carries the big TRC metal power-supply enclosure and the LED controller
for the upstairs LEDs, so this is joining an existing equipment cluster rather
than starting one, and mains power is already there. Two ways to keep the cable
count down:

- **PoE + splitter: one cable, not two.** The Cat6 run is happening anyway; an
  802.3af splitter hands the Pi 5 V and RJ45 off that single drop, so no wall
  wart and no second cable to the shelf. The parts list in Part 4 already
  contemplated splitters — this is the one place to buy one now.
- **A USB-audio speaker rather than 3.5 mm.** Power and audio ride the same
  cable back to the Pi, instead of a barrel supply plus an audio lead.
- **Best case — no speaker at all:** if the "Jabra knock-off" is a *speakerphone*
  puck (mic **and** speaker, like the Jabra 510 it imitates) then the chime plays
  out of the device already sitting there and the whole question evaporates.
  **Confirm the model before buying anything.**

That puts the realistic shelf load at: Pi, one Cat6, the mic puck, and possibly
nothing else.

Note the timing quirk this creates: she rides in the **afternoon**, so the
"don't wake a sleeping spouse at 6 a.m." objection to using the master zone does
not apply. Quiet-hours handling in the per-satellite target map still should —
a 2 a.m. answer blasting the master zone is a different matter.

### Command set

| Say | Does | Entity / mechanism | Status |
| --- | --- | --- | --- |
| "close the bathroom blind" | Master bath blind | `cover.upstairs_bath_blind` → new `button.voice_*` in the Voice Buttons tab | new row in `home_commands.json` |
| "keep the lights on" | Suppresses the motion-off timer while doing laundry | see below | **the only real new logic in Path A** |
| "play Raffi" | Master bath / shower zone | `media_player.shower_snapcast_client` | needs Part 1 item 3 |
| "what's the weather" | Already built | — | needs Part 1 item 1 only |
| timers / lists / reminders / ask | Already built | — | needs Part 1 item 1 only |

### "Keep the lights on" — the only real new logic in Path A

Standing still while folding laundry lets the motion timer expire and drops you
into the dark. The fix is a hold. Three things make it less trivial than it
sounds.

**There are four lights, and they move in tandem** (Brad, 2026-08-07). There is
no case for lighting the closet alone, so this is one group command, not four:

| # | Light | Control path |
| --- | --- | --- |
| 1 | Master closet LEDs | `closetleds` ESPHome controller (`light.closetleds_1`…`_5`) |
| 2 | Ceiling bulb (Zooz) | `light.master_closet_light_switch` — Hubitat device 44 |
| 3 | Master bath LED fixture | **same controller as #1** — confirm which channels are closet vs bath |
| 4 | Master toilet light | `light.master_toilet_light` (zigbee) |

**Three control technologies, and the live flows are Hubitat.** Verified against
the Node-RED Admin API (not `data/flows.json`, which is stale on this host —
see `nodered-flow-agent-guide.md:48`): the "Upstairs Bathroom" tab drives this
group through **Hubitat** — motion is device 911, the switch is device 44 — plus
four `function` nodes named "closet LEDs". So the hold is a Node-RED job against
Hubitat, not an HA-entity job, and the HA entity ids above are mirrors, useful
for state reads and voice-button targets but not the control surface.

**Design it as a timed hold, not a toggle:** a `masterLightsHold` global with a
window (start at 30 min), the same shape as the staged-brighten 90-minute
window, auto-expiring. A toggle you have to remember to clear will be left on
and will silently defeat the motion automation forever. State the window out
loud in the confirm — "Holding the lights for thirty minutes" — so an
unattended hold is never a surprise.

**The hold is only as good as the number of off-paths it gates.** More than one
branch can darken this group: closet motion (Hubitat 911),
`binary_sensor.master_bath_motion_occupancy`,
`binary_sensor.master_shower_motion_motion`, and scene flows — the "Sleeping In
or Nap" tab touches the closet too. **Enumerate every branch that can turn any
of the four off and gate them all on the one global.** Miss one and the lights
still go out, and the feature reads as broken rather than partial.

**Also confirm before building:** that `cover.upstairs_bath_blind` is the master
bath blind and not a kids'-bathroom blind. This house uses "Upstairs Bath" for
both the master closet LED flow and this cover, which is suggestive but not
proof.

---

## Part 3 — Path B: HAVPE in Simon's room

### Why Simon's room is the right first HAVPE

It is the lowest-stakes possible first deployment of a brand-new client type:
adult-triggered, at conversational distance, with a four-command vocabulary, in
a room where a false accept at 2 a.m. wakes one sleeping child rather than both
adults. If the bridge misbehaves, the blast radius is one room.

### Architecture: the aioesphomeapi bridge

The ESPHome native API lets any client subscribe as *the* voice-assistant peer
on port 6053 — Home Assistant is merely one such client, and standalone Python
bridges against stock firmware are a demonstrated pattern. **Constraint: exactly
one subscriber**, so the device's assist-satellite entity in HA must be disabled
before the bridge connects.

**Run it in continuous-stream mode** (`use_wake_word` / `start_continuous`),
which streams 16 kHz mono to the subscriber and puts wake detection on the
server. This is the mode that composes with what we already have:

- **Our exact `okay_computer.onnx` and `okay_google.onnx`**, our exact
  thresholds, full pre-roll — so stage-2 verify (`verify.py`) works unchanged
  and behaviour matches the kitchen.
- **No second wake-model training stack.** microWakeWord is TFLite + JSON
  trained through a different pipeline with its own score scale; after the
  stop-model score-scale burn, a new scale is a real cost, not a footnote.
- **Most of `assistant.py` is reusable as-is.** The satellite consumes the mic
  as a plain byte stream (`arecord.stdout`, `assistant.py:1391`); everything
  downstream — ring buffer, stage-1 scoring, Silero endpointing,
  `capture_command`, follow-ups — just reads from it. The bridge swaps the audio
  *source* and the playback *sink*. The mic/playback split is already proven in
  production: the family-room satellite is mic-only and relays all audio via
  `PLAYBACK_RELAY_URL`.
- **Playback is not the bridge's problem at all.** Brad, 2026-08-07: the HAVPE's
  own speaker is poor and the room already has installed speakers worth using.
  So the device is **mic-only** — replies and Raffi both go to
  `media_player.simon_room` via the Amp Speakers subflow, exactly like the
  family-room satellite. This *simplifies* the bridge: no `audio_http` path, no
  speaker component, no I2S playback contention (ESPHome cannot listen and play
  on the same I2S bus anyway, which would otherwise have forced a stop-listening
  window on every reply).
- **The LED ring replaces the wake chime.** It is native to the device, drivable
  from the bridge, and silent — which is strictly what you want in a room where
  a child is falling asleep. See Part 1 item 4a.

Where the process runs: the Beelink. **That means no Linux box in Simon's room
at all** — no SD card to corrupt, no boot flash to lose (pw_pi and the `.24` fan
are both cautionary). The cost is stage-1 CPU moving onto a box already at load
~1.9; ~0.25 core for one mic with a lazy hop is fine.

**Known escape hatch, banked not built:** at four-plus bookshelf rooms,
server-side stage-1 approaches a full core. If the fleet grows past ~3, train an
"okay computer" microWakeWord model and push stage-1 back onto the devices —
a GX10 night, the same shape as okay_google. Not a surprise; a card to hold.

### Command set and the armed-state gate

| Say | Does | Notes |
| --- | --- | --- |
| "goodnight" | Bedtime scene for the room | new Voice Button |
| "open / close the blind" | Simon's blind | **confirm which cover** — `cover.boys_room_baby_blind` vs `cover.babyblind_windowshade` vs `switch.babyblind` |
| lights | `light.simon_fan_lights`, `light.simon_room_crown_*` | existing entities |
| "play Raffi" | `media_player.simon_room`, **one track, no shuffle** | needs Part 1 item 3 |

**The gate is the interesting requirement.** Once `input_boolean.simonalarm` is
on, the room is asleep and the satellite must stop taking requests — otherwise
"play Raffi" becomes an infinite bedtime-stalling device the moment Simon is old
enough to say it.

Implementation: `room × state → allowlist`, with the armed state resolving to an
**empty** allowlist — log the turn, play nothing. Recommend **silent** rather
than a spoken refusal: a reply is a reward for asking, and a spoken "no" at
bedtime is worse than nothing. Accept that this is briefly confusing for an
adult who forgot the alarm is armed; the shadow log makes it diagnosable.

**The better version is already available.** Speaker ID has been armed in
production since 2026-07-27 (`SPEAKER_MODE=active`, brad/adrienne profiles), so
"when armed, honour recognized adults only" is a near-term option rather than a
someday one. Ship the empty-allowlist version first; add the speaker-scoped
version once Adrienne's enrollment is fattened past nine clips.

### Gates — what "proven" means for Path B

- **B1 — bridge audio.** Continuous 16 kHz mono arrives from stock firmware with
  HA's assist-satellite entity disabled. Dump to WAV; confirm rate, channel,
  and that it is genuinely continuous, not post-wake bursts.
- **B2 — stage-1 parity.** Existing ONNX models over the bridge stream trigger
  at comparable rates to the kitchen on the same spoken phrase, with pre-roll
  intact and `/verify` passing on real utterances.
- **B3 — reply playback.** Orchestrator WAV URL plays through `audio_http`;
  measure wake→chime and reply latency against the kitchen's numbers.
- **B4 — 7-day soak.** No dropped stream, no bridge restarts, no memory growth,
  no unexplained wake gaps. Watch Beelink load across the week.

**If continuous mode turns out not to work outside Home Assistant**, the
fallback is on-device microWakeWord — and then the blocking question becomes
whether the streamed audio includes pre-wake-word audio, because without it
`verify_and_extract` never sees "okay computer" and stage-2 cannot run as
written. Measure that before designing around it; don't assume either way.

---

## Part 4 — DEFERRED: the PoE ceiling program

**Everything below this line is on ice until roughly 2028–2029**, when both kids
want a mic and the shelves need cleaning up. It is preserved intact because the
analysis is sound and re-deriving it would be waste — but nothing here should be
bought, ordered, or bench-tested on the strength of this document today. Read
Part 1's item 5 first: the arbitration rework is the piece that actually gates
growing the fleet.

### Why the ceiling (Brad, 2026-07-30) — and what it rules out

Not aesthetics. The reasoning, recorded so nobody re-opens it:

- We want **Ethernet data** at each device. Once a drop is being pulled, ending
  it in the ceiling is strictly less work than attic → top plate → LV box on the
  wall → patch cable back up to the device.
- A Pi 4 + PoE + array **will not fit** any ceiling LV box.
- The device **cannot live in the attic above the insulation** — it hits 140 °F+
  in summer.

Consequences, which decide the hardware:

- **The permanent endpoint cannot be a Raspberry Pi.** A Pi 4 is rated to 50 °C
  ambient and dissipates 3–5 W into an insulated cavity. An ESP32-S3 is rated to
  85 °C and dissipates well under a watt. In a ceiling pocket surrounded by
  insulation, that is the whole ballgame. The ESP is the *correct* endpoint here,
  not a cost compromise.
- **The array and the electronics need not share a box.** Put the mic array at
  the ceiling surface behind an acoustically open grille and the ESP + PoE front
  end a few inches away in the joist bay on a short harness. This turns the
  "two boards on a harness" property of T2/T3 from a drawback into an advantage,
  and it means only the array has to fit the ceiling opening. **It also demotes
  T1**, whose entire appeal was stacking onto the ReSpeaker socket.
- **Keep the whole assembly below the insulation plane**, sealed, grille open to
  conditioned room air. At ~1 W that is thermally uneventful even with a 140 °F
  attic above. Verify in G6 anyway.

### Non-negotiable: data over copper, not just power

Every topology below carries **Ethernet data and PoE power on the same single
Cat6 run**. Wi-Fi is not a fallback and is not an option — if a run is going
into the attic anyway, the radio buys nothing and costs reliability. All three
candidates are 10/100 (two pairs for data, PoE on the spare pair or phantomed
on the data pairs), so one Cat6 per room does both jobs with pairs left over.

Corollary for the firmware: **disable the Wi-Fi radio outright.** In ESPHome
the `ethernet` and `wifi` components are mutually exclusive anyway. This is
deliberate — a dead link must be a loud failure, not a mic that silently
degrades onto a congested 2.4 GHz channel and starts dropping utterances. It
also means a dead link is a dead mic, which is why per-client stream health
with a Pushover alert is a work item, not a nice-to-have.

---

## 1. Headline: the pin budget closes, with one pin to spare

The conflict is real but it is a *default-assignment* conflict, not a silicon
one. Both SPI and I2S on the ESP32-S3 route through the GPIO matrix, so either
bus can be moved to any free pin in firmware. The question is only whether
enough pins are physically free and physically reachable.

XIAO ESP32S3 header ↔ GPIO, annotated with what the ReSpeaker consumes:

| XIAO pin | GPIO | Used by ReSpeaker XVF3800? | Notes |
| --- | --- | --- | --- |
| D0 | 1 | **free** | |
| D1 | 2 | **free** | |
| D2 | 3 | **free** | ESP32-S3 strapping pin (JTAG select) — use for a signal that idles high, e.g. CS |
| D3 | 4 | **free** | |
| D4 | 5 | I2C SDA → XVF3800 control | |
| D5 | 6 | I2C SCL → XVF3800 control | |
| D6 | 43 | I2S mic data (XVF3800 → ESP) | |
| D7 | 44 | I2S speaker data (ESP → XVF3800) | **we don't need this** — see §2 |
| D8 | 7 | I2S LRCLK / WS | |
| D9 | 8 | I2S BCLK | |
| D10 | 9 | **free** | |

Seeed's own W5500 adapter example uses SCK=D8, MISO=D9, MOSI=D10, CS=D1 —
i.e. **D8 and D9 land squarely on I2S LRCLK and BCLK.** That is why the two
boards can't naively coexist. But:

- Free pins with the ReSpeaker in place: **D0, D1, D2, D3, D10 — five.**
- W5500 needs SCK, MOSI, MISO, CS = **four**, plus an optional interrupt pin.
- **Five free, four required, one spare for INT.** Reset can be tied off.

So the wiring is a remap, not a redesign. That is the good news. What the POC
must actually establish is *physical*: are those five pins broken out anywhere
on the ReSpeaker board, and does the resulting two-board sandwich work
electrically and mechanically.

Seeed documents that "Unused IO Pads (XIAO) — additional I/O solder pads
connected to XIAO module" exist, and that there are "exposed headers for I2C
and I2S communication." **Neither is enumerated anywhere in their wiki, wiki
GitHub source, or the CNX/Hackster coverage.** No schematic PDF is published
for this board. That undocumented breakout is the single blocking unknown.

---

## 2. Two things that make this easier than it looks

**We don't need the playback direction.** The reply comes out of the ceiling
speakers via the amp zone, not out of the ReSpeaker's 5 W amp or its 3.5 mm
jack. So the I2S speaker line (D7/GPIO44) is dead weight — it can be dropped,
and if the pin budget ever gets tight, that's a sixth free pin. It also means
the ReSpeaker's power draw is just the XMOS DSP and mics; no amp load. An
802.3af budget (12 V/1.1 A ≈ 13 W) is enormous overkill.

**There is already a working open-source ESPHome integration for this exact
board.** [formatBCE/Respeaker-XVF3800-ESPHome-integration](https://github.com/formatBCE/Respeaker-XVF3800-ESPHome-integration)
provides custom I2S and XVF3800 drivers, and Seeed's own Home Assistant wiki
page is built on it. Its pin config confirms the map above (LRCLK GPIO7, BCLK
GPIO8, mic GPIO43, speaker GPIO44) and notes **MCLK is not wired because the
XVF3800 is the I2S master** — one fewer high-speed clock line to route over
jumper wire, which matters a lot for signal integrity.

ESPHome also supports W5500 SPI Ethernet on ESP32 chips. So the electrical
bring-up can be done in **YAML in an evening** — ESPHome ethernet + ESPHome
i2s microphone, both up at once, no custom C++ — purely to prove the hardware.
The custom streaming firmware gets written afterwards, against hardware that is
already known good. That is the right order: ESPHome is the bring-up harness,
not the destination (this project deliberately does not use HA Assist).

Note the two Seeed examples disagree on I2S mastering — the Arduino I2S sketch
sets `cfg.is_master = true` on the XIAO, while the ESPHome config says the
XVF3800 masters. Resolve on the bench; it changes which clock lines are driven
in which direction over the harness.

---

## 3. Three candidate topologies

**Ranking, after the ceiling constraint above: T3 first, then T2, then T1.**
T1 was the early favourite because it stacks onto the ReSpeaker socket — but
the ceiling install *wants* the array and the electronics separated, so
stacking has become a liability rather than a feature.

### T1 — XIAO in the ReSpeaker socket + bare W5500 module

XIAO ESP32S3 sits in the ReSpeaker's socket as designed. A **bare W5500
module** (not Seeed's carrier — its socket is the whole problem) is wired to
D0/D1/D2/D3/D10 plus 3V3/GND. PoE arrives via an 802.3af splitter that feeds
5 V to the ReSpeaker's USB-C and hands its RJ45 to the W5500 module's jack.

- **Pros:** ReSpeaker mounted as designed; ~7 wires; cheapest; SPI over
  jumpers is far more forgiving than I2S over jumpers.
- **Cons:** depends entirely on the undocumented IO pads. If they aren't
  broken out, T1 is dead on arrival.
- **Kills the unknown in:** one multimeter session.

### T2 — XIAO in Seeed's W5500 PoE carrier + harness to the ReSpeaker's I2S/I2C headers

Invert it. The XIAO lives in the [XIAO W5500 Ethernet Adapter](https://www.seeedstudio.com/XIAO-W5500-Ethernet-Adapter-p-6472.html)
($19.90, 802.3af PD → 12 V → 5 V via TPS563201, W5500 on D8/D9/D10/D1), and a
harness carries LRCLK, BCLK, mic-data, SDA, SCL, 3V3, GND to the ReSpeaker's
*exposed* I2S/I2C headers. I2S gets remapped in firmware onto free carrier
pins (D0/D2/D3/D6/D7 are all free there; I2C keeps D4/D5).

- **Pros:** PoE and Ethernet are a designed, tested product — no splitter, no
  PD sourcing, no bare module. Uses documented headers instead of undocumented
  pads.
- **Cons:** two boards to mount and enclose. BCLK at 16 kHz × 32 bit × 2 ch =
  **1.024 MHz** over jumper wire — slow enough to be fine over ~10 cm with a
  good ground return, but it is now the fragile bus instead of SPI. Verify the
  adapter's PoE is genuinely 802.3af PD (the marketing text says "12 V Ethernet
  power input", which reads ambiguously against a 48 V af switch; the wiki spec
  says IEEE802.3af with 12 V/1.1 A output, so it should be a real PD front end —
  **confirm before ordering**, because passive-12V would need a different injector).

### T3 — Olimex ESP32-POE-ISO + harness to the ReSpeaker headers

Drop the XIAO entirely. [ESP32-POE-ISO](https://www.olimex.com/Products/IoT/ESP32/ESP32-POE-ISO/open-source-hardware)
(~$29) is a mature, in-production ESP32 board with a **native EMAC + LAN8720
PHY** (no SPI Ethernet at all) and proper 3000 VDC-isolated IEEE 802.3af PoE
via a TPS2375. Same 6–7 wire harness to the ReSpeaker's I2S/I2C headers, and
a large pool of free GPIO with no contention whatsoever.

- **Pros:** the most boring, most proven PoE hardware of the three. Native
  Ethernet MAC is lower-jitter and lower-CPU than SPI Ethernet. Isolated. No
  pin-budget question at all. Separate boards on a harness is exactly the
  physical layout the ceiling install wants.
- **Cons:** ESP32 classic, not S3 — formatBCE's XVF3800 driver targets the S3
  and would need porting/verification. Bigger board. ~$29 vs ~$20.

**Recommended for Exercise B.**

### The Pi — right for Exercise A, wrong for the ceiling

USB ReSpeaker on a Pi is the fastest path to a working bedroom satellite (zero
new firmware, zero soldering, code identical to the two live satellites), which
is why Exercise A uses it. But it is **not** a candidate for the permanent
endpoint: it will not fit a ceiling LV box, and a 3–5 W board rated to 50 °C
ambient does not belong in an insulated cavity under a 140 °F attic. Use it on
a shelf to answer the demand question, then retire it.

---

## 3.5. Exercise A vs Exercise B — do the cheap one first

Two separate exercises. Only one is urgent, and it is not the one this document
is mostly about.

**Exercise A — does anyone actually talk to a bedroom?** Open question, by
Brad's own reckoning: *"do I think people will use it all the time? not sure.
Maybe if it catches on and works well — open the blinds, turn off the lights,
play Raffi, remind me…"* The kids are too young to trigger it today; a 5+ year
old with a small allowlisted command set is the real prize. **This needs no
PoE, no ceiling, no enclosure, and no attic** — a satellite on a shelf with a
wall wart answers it.

**Exercise B — the permanent ceiling endpoint.** Everything else in this
document. Expensive, irreversible (five ceiling penetrations and attic runs),
and only worth building once A says yes.

Do A first because A is cheap and B is not. The risk in this project is cutting
five holes for a feature nobody ends up using — not the soldering.

### The one purchase that serves both — **SUPERSEDED 2026-08-07**

> This recommendation is dead. It rested on "nothing is thrown away" on the way
> to a ceiling build that is now 2–3 years out, and Exercise A is being answered
> instead by Path A (the `.24` Pi) and Path B (the HAVPE), both already owned.
> Kept for the reasoning only. **Do not order this board.**

Buy **one ReSpeaker XVF3800 *with XIAO ESP32S3*** ($54.50) — not the cheaper
no-ESP32 SKU. That single board:

1. Runs in **USB mode today** on the powered-off `.24` kitchen Pi, on a shelf in
   the loft or master, wall-wart powered, no holes → Exercise A running this
   weekend. (Disable that Pi's `voice-assistant` and `squeezelite` units first
   so it can never contend with the `.251` kitchen box.)
2. Is the exact board Exercise B needs — nothing is thrown away.
3. Lets Step 0 below run on hardware we own, resolving T1-vs-T2/T3 for free
   while Exercise A soaks.

Exercise A also wants the software from Appendix A items 1–3 (reply routed to
that room's amp zone), which is needed on every path anyway.

**Gate:** ~1 month of real use. If the family reaches for it, build B with T3.
If not, the cost of finding out was $55 and a Saturday.

### Per-room intent scoping — design in now, not after the incident

The kids' rooms are the highest-value rooms ("turn off my light", "play Raffi",
"how many minutes until dinner") and the highest-risk ones (a kid discovering
they can broadcast to the whole house at 2 a.m.). Add a **per-room intent
allowlist** — a small extension of the hot-reloaded table pattern already used
by `home_commands.json` and `broadcast_rooms.json`. Much easier now than as a
retrofit.

## 4. Step 0: answer the blocking unknown for $0, today

**Check whether the family-room array is the same PCB with an empty XIAO
socket.** Seeed sells the $49.99 "no ESP32" SKU and the $54.50 "with XIAO
ESP32S3" SKU; they appear to be the same board with the module populated or
not. If the array currently on `pw-poller-pi` has an unpopulated XIAO socket
and the "unused IO pads" silkscreen, then **the entire T1 question is
answerable with a multimeter in ten minutes, before anything is ordered.**

Procedure (mic comes out of service for ~20 minutes — do it when nobody is
mid-conversation; per `powerwall-pi-guide.md`, expect to physically replug it
afterwards and verify `arecord -D respeaker_ch0 -d 3 /tmp/t.wav` produces
~192 KB, not a 44-byte header):

1. Photograph both sides of the board, including all silkscreen.
2. Continuity-buzz each XIAO socket position D0, D1, D2, D3, D10 against every
   unpopulated pad and header pin. Record the map.
3. Buzz the I2S/I2C headers against socket positions D4–D9 to confirm what
   those headers actually carry, and their pitch (2.54 vs 1.27 mm).
4. Confirm whether the XIAO's own USB-C would be reachable when socketed
   (needed for the first flash and for recovery; OTA covers the rest).

**Decision gate:** D0/D1/D2/D3/D10 reachable → **order for T1**. Only the
I2S/I2C headers reachable → **order for T2 or T3**. Neither → T3 or the Pi
fallback.

In parallel, ask Seeed for the schematic (support ticket or their forum). No
schematic is published for this board and it would settle everything.

---

## 5. Parts list

### Step 0 — $0

Multimeter, phone camera, and the existing family-room array.

### Bench POC — order after the Step 0 gate

Buy **both** ESP topologies. The delta is ~$50 against a five-room rollout and
several weeks of calendar time if the first choice fails.

| Item | Qty | Est. | For |
| --- | --- | --- | --- |
| ReSpeaker XVF3800 **with XIAO ESP32S3** | 1 | $54.50 | all |
| — cased SKU alternative | — | $53.90 | check the case has room for a second board |
| XIAO W5500 Ethernet Adapter (802.3af PoE) | 1 | $19.90 | T2 |
| W5500 SPI module, bare (RJ45 + magnetics onboard) | 2 | ~$16 | T1 |
| Olimex ESP32-POE-ISO | 1 | ~$29 | T3 |
| Active 802.3af PoE splitter → 5 V USB-C + RJ45 | 2 | ~$32 | T1, spare |
| 802.3af PoE injector (bench, so you're not at the switch) | 1 | ~$18 | all |
| 30 AWG silicone wire, 2.54 + 1.27 mm headers, flux, braid | — | ~$20 | all |
| USB-C power meter (draw + brownout hunting) | 1 | ~$15 | all |
| USB-C breakout / sacrificial cable (5 V injection into the ReSpeaker) | 2 | ~$10 | T1, T3 |
| **Bench total** | | **~$215** | covers all three topologies |

Optional, high-value side experiment (~$8): an **INMP441 or ICS-43434 I2S MEMS
mic** to bench against the XVF3800 in the same room. If a bare MEMS mic clears
the two-stage wake reliably in a quiet bedroom, per-room cost drops from ~$75
to ~$35 and the whole XVF3800 line item disappears. Worth knowing even if the
answer is no.

### Rollout, 5 rooms (after the POC passes)

| | Per room | ×5 |
| --- | --- | --- |
| T1 (XVF3800+XIAO, bare W5500, splitter) | ~$78 | ~$390 |
| T2 (XVF3800+XIAO, Seeed W5500 PoE carrier) | ~$75 | ~$375 |
| T3 (XVF3800 + Olimex ESP32-POE-ISO) | ~$79 | ~$395 |
| MEMS variant of T3, if it benches well | ~$37 | ~$185 |

Cabling and infrastructure, same for all:

| Item | Qty | Est. |
| --- | --- | --- |
| Cat6 riser, 500 ft box | 1 | ~$110 |
| Keystone jacks / couplers / patch cords | 10 | ~$30 |
| Low-voltage old-work brackets or ceiling enclosures | 5 | ~$25 |
| Staples, fish tape, labels | — | ~$25 |
| Free 802.3af/at switch ports | 5 | verify — 5 × ~3 W is nothing, but confirm the ports exist |

---

## 6. Bench protocol — what "proven" means

Run on a desk, not in a ceiling. Each gate must pass before the next.

**G1 — Ethernet alone.** ESPHome, `ethernet:` W5500 (or native EMAC on T3) on
the chosen pins, no audio. Link up, static lease, ping flood 10k packets with
zero loss, survives 10 cable pulls and a switch reboot.
*Fails if:* the W5500 won't enumerate on remapped SPI pins, or the strapping
pin (D2/GPIO3) blocks boot.

**G2 — I2S alone.** ESPHome i2s microphone via formatBCE's driver, over the
harness. Capture and dump audio; confirm sample rate, bit depth, and **which
I2S channel carries the processed beam vs the AEC reference** — the USB path
puts the beam on ch0 and the raw reference on ch1, and the I2S path may differ
or may be I2C-configurable. Judge ch0 on speech, never on ambient (a quiet-room
ch1 reading hotter than ch0 is expected, not a channel swap).
*Fails if:* clocks won't lock over the harness, or channels can't be resolved.

**G3 — both at once.** This is the actual POC. Ethernet link up while I2S runs
continuously. Watch for SPI/I2S DMA contention, buffer underruns, and clock
glitching. Seeed's own MQTT streaming example only ships **~3 seconds** of
audio — nobody has publicly demonstrated sustained streaming off this board,
so treat continuous operation as unproven until it isn't.
*Fails if:* dropouts, audible clicks, or rising latency under load.

**G4 — PoE end to end.** Powered only from the injector. Measure draw at the
splitter/PD. Cold-boot 20 times. Pull power mid-stream 20 times and confirm
clean recovery. Verify the **whole assembly, including the XVF3800, loses power
when the switch port does** — the array is known to wedge in a state that
survives a USB bus reset and needs a physical power cycle, and a remote PoE
port-cycle is the only acceptable fix for a device in a ceiling.

**G5 — 72-hour soak.** Continuous stream to a throwaway listener on the
Beelink. Zero unexplained dropouts, no memory growth, stable temperature. Log
per-minute frame counts so a gap is provable, not anecdotal.

**G6 — thermals + enclosure.** Inside whatever the real enclosure will be, at
ambient. The attic hits 130 °F; a ceiling cavity is cooler but not cool. Check
the XMOS DSP and the PD/regulator, which is where the heat will be.

**G7 — one real room, 14 days.** Mount it (temporarily, no holes), point it at
the orchestrator with the real streaming firmware, and live with it. Zero
unexplained dropouts over 14 days is the bar for cutting into a ceiling.

Only after G7: pull five runs in the attic, one room at a time with a day of
soak between.

---

## 7. Risks specific to the hardware path

| Risk | Mitigation |
| --- | --- |
| **Undocumented IO pads don't exist** | Step 0 answers it for $0 → fall through to T2/T3, which use documented headers. |
| **Seeed W5500 adapter is passive-12V, not real 802.3af PD** | Confirm against the wiki spec / ask Seeed before ordering. If passive, an af→12 V splitter fixes it, or use T3. |
| **XVF3800 wedges in a ceiling** (enumerates, every read EIO, survives bus reset) | Hard requirement: PoE port power-cycles the entire assembly. Verify in G4. Design so no board holds up 5 V independently. |
| **I2S over jumper wire (T2/T3)** | BCLK is only ~1.02 MHz — forgiving. Keep the harness <10 cm, twist each clock with its own ground return, and confirm no MCLK line is needed (ESPHome's config says the XVF3800 masters). |
| **D2/GPIO3 is a strapping pin** | Assign it a signal that idles high (CS), or leave it unused and forgo INT. |
| **Sustained streaming unproven on this board** | G3 + G5 exist precisely for this. If the S3 can't hold it, T3's native EMAC has more headroom than SPI Ethernet. |
| **First flash / recovery access when socketed** | Confirm USB-C reach in Step 0; OTA after first flash; keep a pogo/serial fallback. |
| **XIAO USB-C and XVF3800 USB-C are two different ports** | Don't assume powering one powers the other; trace it in Step 0. |
| **Five mics in bedrooms is a household decision, not a technical one** | Per-room `active/shadow/off` mode and a visible LED, using the existing MQTT mode pattern. Talk to Adrienne before the attic, not after. |

---

## Appendix A — the software side (understood, not part of the proof)

Recorded so the hardware POC lands on a known target, not so it gets built
first.

> **2026-08-07:** this appendix is no longer deferred material — Part 1 is the
> near-term subset of it. Items 1–3 (amp-zone reply routing) are explicitly
> *not* blockers for Path A or Path B, both of which have a local speaker; item
> 5 (proximity groups) is the gate on growing the fleet; item 6 (per-room mode
> switch) applies to both paths now.

**The reply path already exists.** Node-RED subflow **Amp Speakers**
(`e711d48f74f78209`) plays `amp_wake_soft_4s.mp3`, opens a **"Minimum 3s
gate"**, renders and tail-pads TTS in parallel, and fires the announcement only
when both are ready. It tracks a `wholeHomeAmpLikelyOn` global with a 14-minute
stale-clear, so a second turn inside 14 min skips the wake and drops the gate
to 1 ms. `home-audio-adapter` :8461 `/v1/isolate` ungroups the target zone;
`tts-pad-service` :8097 `/pad` fixes snapserver's tail chop. Zone player ids
are already mapped in the subflow: `ma_loft`, `ma_simon_room`,
`ma_claire_room`, `ma_master_bedroom`, `ma_shower`.

**The latency works out** if the amp wake is kicked at *stage-2 verify*, not at
reply time: the 3 s gate elapses while the user is still talking, so a cold-amp
reply lands at ~5 s with the amp already up. Consequence: no audible
confirmation ding on a cold amp, and none on a warm amp either (the subflow
skips the wake tone when `wholeHomeAmpLikelyOn`). Consider forcing a short
audible ding on the warm path so both cases feel the same.

Work items, in order:

1. Per-satellite target map (`sat_id → {playback, ma_player, ha_player,
   quiet_hours}`), hot-reloaded like `home_commands.json`.
2. Orchestrator publishes `{room, ttsUrl, volume, forceBedroom}` on reply, and
   kicks the amp wake at verify. `forceBedroom` is needed because
   `DisableBedroomAnnouncements` / `adrienneWorkingDisableAnnounce` must not
   silence a reply to someone who just spoke in that room.
3. Node-RED: new tab mirroring "Voice Broadcast", plus a ~10-line `msg.ttsUrl`
   bypass in Amp Speakers so Kokoro audio is used instead of a cloud TTS round
   trip. Deploy via the Admin API per `nodered-flow-agent-guide.md`.
4. `orchestrator/streamsat.py`: WS ingest, ring buffer, RMS gate, stage-1 loop,
   Silero endpointing, playback gating, per-client health + Pushover alert.
   Port `capture_command` / `SileroVad` from `satellite/assistant.py:598`/`:561`
   without touching the two live satellites.
5. `_ARB` (`app.py:160`) → proximity groups. It is currently one house-global
   holder; with six mics on two floors, a wake in the master would silently
   swallow a simultaneous one in the loft.
6. Per-room HA mode switch (reuse `voice-assistant/node-red/deploy_mode_switch.py`).

**Two things that will bite and are cheap to design in now:** the mic hears its
own reply from the ceiling speaker with no AEC reference, so the server must
mute that room's detection for the reply duration + ~500 ms (it knows the
padded WAV length, so this is arithmetic). And stage-1 CPU: an N100 core should
do ~40–60 ms per 2 s window, which at the kitchen's `HOP_MS=224` is ~1.3 cores
for five mics — too much on a box already at load ~1.9. Upstairs can run a lazy
hop (detection latency is free when the amp needs 3 s anyway) plus an RMS gate,
which should bring it under half a core. Measure before committing; GX10
CPU-side is the fallback.

---

## Sources

### Path B — the ESPHome bridge (2026-08-07)

- [ESPHome native API architecture (port 6053, protobuf over TCP)](https://developers.esphome.io/architecture/api/)
- [aioesphomeapi — the same client library Home Assistant uses](https://github.com/esphome/aioesphomeapi)
- [Standalone ESP32 voice bridge against stock firmware, HA out of the loop](https://blog.darrenjrobinson.com/going-direct-esp32-voice-for-openclaw/)
- [ESPHome voice_assistant component (16 kHz mono, start_continuous, TTS URL vs stream)](https://esphome.io/components/voice_assistant/)
- [ESPHome micro_wake_word (tflite + JSON, kahrendt training pipeline)](https://esphome.io/components/micro_wake_word/)
- [HAVPE stock firmware YAML (use_wake_word: false, audio_http sources, XMOS firmware)](https://github.com/esphome/home-assistant-voice-pe/blob/dev/home-assistant-voice.yaml)
- [OHF-Voice/linux-voice-assistant — ESPHome protocol implemented in Python](https://github.com/OHF-Voice/linux-voice-assistant)

### Part 4 — deferred ceiling hardware

- [ReSpeaker XVF3800 with XIAO ESP32S3 — Seeed store](https://www.seeedstudio.com/ReSpeaker-XVF3800-4-Mic-Array-With-XIAO-ESP32S3-p-6489.html)
- [Seeed wiki — XVF3800 + XIAO getting started](https://wiki.seeedstudio.com/respeaker_xvf3800_xiao_getting_started/)
- [Seeed wiki — XVF3800 + XIAO I2S test (pins GPIO7/8/43/44, 16 kHz 32-bit stereo)](https://wiki.seeedstudio.com/respeaker_xvf3800_xiao_i2s/)
- [Seeed wiki — XVF3800 + XIAO MQTT audio streaming (~3 s bursts)](https://wiki.seeedstudio.com/respeaker_xvf3800_xiao_mqtt_audio_stream/)
- [Seeed wiki — XVF3800 Home Assistant / ESPHome](https://wiki.seeedstudio.com/respeaker_xvf3800_xiao_home_assistant/)
- [formatBCE/Respeaker-XVF3800-ESPHome-integration](https://github.com/formatBCE/Respeaker-XVF3800-ESPHome-integration)
- [Seeed wiki — XIAO W5500 Ethernet Adapter (802.3af, 12 V/1.1 A, SPI D8/D9/D10/D1)](https://wiki.seeedstudio.com/xiao_w5500_ethernet_adapter/)
- [XIAO W5500 Ethernet Adapter — Seeed store, $19.90](https://www.seeedstudio.com/XIAO-W5500-Ethernet-Adapter-p-6472.html)
- [Olimex ESP32-POE-ISO — open source hardware](https://www.olimex.com/Products/IoT/ESP32/ESP32-POE-ISO/open-source-hardware)
- [ESPHome Ethernet component (W5500 over SPI)](https://esphome.io/components/ethernet/)
- [ESPHome I²S microphone component](https://esphome.io/components/microphone/i2s_audio/)
- [XIAO ESP32S3 pinout reference](https://www.espboards.dev/esp32/xiao-esp32s3/)
