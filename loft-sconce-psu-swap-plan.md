# Loft sconce rewire + LED PSU cascade plan

Decided 2026-08-31 (conversation w/ Claude). Status: **planned, nothing bought/built yet.**

## Summary of decisions

1. **Loft sconce**: retire the in-fixture Dig Uno + An Penta Mini. New **An Penta Plus #2 in Simon's closet** (Ethernet), ~10 ft run to the sconce. The fixture becomes dumb (strips + one multi-core cable, no controllers/Wagos inside).
2. **Simon's closet power**: the master-closet **LRS-200-24 moves here** (with its TRC enclosure) and feeds both Simon's crown Penta Plus and the new loft Penta Plus. Retire the 4 A desktop brick.
3. **Master closet power**: replace with **HLG-480H-24** (fanless, potted, IP67 — needs no enclosure) + metal handy-box splice to a plug-in appliance cord. Sized for the future **master crown molding** project.
4. Old 25-ft 16/2 (master closet → loft) is **abandoned in place** as future pathway. Do NOT pull it out.

## Why (engineering rationale)

- The in-fixture-controller layout wasn't buying what it was built for: with low-side
  switching, the 25-ft feed still carries 20 kHz chopped current. The real remote-controller
  risks are (1) digital data integrity — fine at ~3 m through the Penta's level shifter,
  (2) voltage drop — ~0.25 V worst case on 18 AWG at 2 A, (3) PWM EMI — negligible at 10 ft
  with conductors bundled in one jacket (loop area is what radiates).
- Crosstalk from analog PWM negatives onto the digital data line in a shared jacket is a
  non-issue at 10 ft: QuinLED slows gate edges, coupled spikes ≲1 V vs 5 V logic thresholds,
  level shifter drives low-impedance. Data MUST travel with its own ground in the same
  jacket (tight loop = the immunity). Failure mode if wrong = visible pixel sparkle;
  fixes: spare conductor as 2nd data-ground, sacrificial pixel, slower data rate.
- **LRS-350 has a 60 mm fan** (LRS series is fanless only ≤200 W) — that's the noisy garage
  unit's problem; off the table for a bedroom closet. UHP-500/750 are fanless but have
  exposed screw terminals (only 3D-printed covers, no TRC steel enclosure exists) and cool
  by conduction through the baseplate (want an aluminum mounting plate). HLG-480H needs
  neither: 90 °C case rating, free-air convection, screw it to a board.

## Load math

**Loft sconce**: max 2 A @ 24 V (48 W). 3 white analog channels + 1 digital strip.

**Simon's closet (LRS-200-24, 8.8 A / 211 W)**
- Crown: capped 4 A today (13×11 room, 48 ft perimeter; caps exist because mixing
  white+color hits ~12 W/m and the old 4 A brick was the wall)
- Loft: 2 A
- Total 6 A = 68 %. Crown cap can be raised 4 A → ~5 A (80 % total); beyond that too tight.

**Master closet (HLG-480H-24, 20 A / 480 W)**
- Future crown: perimeter 2×(15.5+14) + 8 + 4 bump-outs = 71 ft ≈ 21.6 m.
  Strips: 5 W/m white, ~7 W/m color, ~12 W/m uncapped mix → **uncapped worst ≈ 260 W**.
  No software cap needed at this supply size.
- Master bath vanity: 2 m × 20 W/m = 40 W
- Stair fixture: 5 pendants × 2 ft × 8 W/m ≈ 24 W peak (run at 70 % but PWM ⇒ size to peak)
- Worst case ≈ 325 W = 68 % of 480 W. (LRS-200 would have been 82 % on the *tame* crown
  scenario with zero mix headroom — hence the upgrade.)

## Wiring — Simon's closet → loft sconce

One pull of **18/8 thermostat/security wire (CL2/CL3)**, ~12 ft (~$1.33/ft):

| Conductor | Use |
|---|---|
| 1 | analog +24 V (shared, sized fine: ~0.25 V drop worst case) |
| 2–4 | white ch 1/2/3 switched negatives |
| 5 | digital +24 V |
| 6 | digital data |
| 7 | digital ground |
| 8 | spare — land as 2nd digital ground |

Fallback if pixel sparkle ever appears (not expected): sacrificial pixel, lower data
rate, or move data+gnd to its own small cable (two self-contained circuits in two
jackets is also electrically sound).

## Master closet HLG-480H install (code-compliant 120 V)

Handy box beside the supply; HLG factory input lead in one ½" KO, appliance cord in the
other, both via **strain-relief cord grips** (not NM clamps). Splices inside: brown→black
(L), blue→white (N), grn/yel→green (G) + green pigtail to box ground screw. Blank cover,
plug into existing top-shelf outlet. Check HLG lead OD vs grip range (Arlington
LPCG50 .200–.485" is the safe pick; Halex .260–.375" fits the 16/3 cord).

## Shopping list

- An Penta Plus (#2) — loft, in Simon's closet
- HLG-480H-24 (plain or -A suffix; NOT -B — its dimming input is redundant w/ Penta) ~$110–140
- ~12–15 ft 18/8 CL2/CL3 t-stat/security wire
- [RACO 8660 handy box](https://www.homedepot.com/p/4-in-H-x-2-in-W-x-1-7-8-in-D-Steel-Gray-1-Gang-Drawn-Handy-Box-with-Ten-1-2-in-KO-s-and-Raised-Ground-1-Pack-8660/100560024) + [RACO 860 blank cover](https://www.homedepot.com/p/RACO-1-Gang-Handy-Box-Blank-Cover-860/202056194)
- 2× cord grips: [Arlington LPCG50](https://www.amazon.com/Arlington-LPCG50-10-Electrical-Connector-10-Pack/dp/B00303FYKA) or [Halex 21692](https://www.amazon.com/Halex-21692-2-Inch-Strain-Connector/dp/B000VYK6YK)
- [Southwire 6 ft 16/3 SJT appliance cord](https://www.homedepot.com/p/Southwire-6-ft-16-3-SJTW-13-Amp-125-Volt-Replacement-Power-Supply-Cord-Black-9706SW8808/301132790) (13 A vs HLG's ~5 A draw)
- Master crown: LEDs already bought; **molding not yet purchased**

## Sequencing

1. Buy parts. Master crown molding project is the driver for the HLG but doesn't block
   the loft/Simon's work.
2. Simon's closet: install Penta Plus #2 + Ethernet, pull 18/8 to loft, gut sconce
   controllers, land strips.
3. Move LRS-200 (+ TRC enclosure) master → Simon's; retire 4 A brick. Both crown + loft
   now on it.
4. Master closet: HLG-480H + handy box; vanity + stairs move over; crown lands here later.
5. Leave the 16/2 in the wall.

## 2026-09-08 — Penta Plus #2 flashed and bench-verified (before any wiring)

**Board found + flashed:** the spare An-Penta-Plus was on Ethernet at **192.168.10.34**
running QuinLED's factory WLED 0.15.0-b7. ESPHome `~/esphome/config/loft-sconce.yaml`
(+ `wled_fx.h`, reuses `crown_fx.h`) was compiled in the `esphome-esphome-1` container and
uploaded through WLED's `/update` page (`firmware.ota.bin`, 563 KB). It rebooted straight
into ESPHome at the same IP (Ethernet, hostname `loft-sconce`, API + web UI on :80).
Caveat: the flash still carries WLED's partition table (works fine for ESPHome OTA from
now on); a one-time serial flash of `firmware.factory.bin` over the board's USB-C would
normalise it — optional.

**Analog channel plan (Brad's rewire decision):** outies-only is the nicer look at eye
level, so keep innie+outie for BOTH whites and drop amber innie entirely.

| Terminal | GPIO | Channel | HA entity (after adoption) |
|---|---|---|---|
| L1 | 33 | cool white **outie** | `light.loft_sconce_cool_white_outie` |
| L2 | 32 | cool white **innie** | `light.loft_sconce_cool_white_innie` |
| L3 | 12 | warm white **outie** | `light.loft_sconce_warm_white_outie` |
| L4 | 4 | warm white **innie** | `light.loft_sconce_warm_white_innie` |
| L5 | 2 | amber **outie** | `light.loft_sconce_amber_outie` |
| Digital | 5 (+ relay 13) | pixels | `light.loft_sconce_color` |

18/8 conductor use: 1 shared +24 V, 5 switched negatives (L1–L5), pixel data, pixel
ground = 8. The pixels' V+ therefore **shares the analog +24 V conductor** (the analog
negatives are PWM-switched so nothing else can be shared) — which means the Penta's
digital power relay (GPIO13) is bypassed and the pixels are always powered; the relay
logic in the YAML stays but is cosmetic. Trade if ever wanted: drop cool-white innie to
free a conductor for relay-switched pixel V+.

**Colour map decided 2026-09-08 (Brad's 18/8 colours) — land the Penta end first:**

| Colour | Penta Plus terminal | Fixture end |
|---|---|---|
| **red** | any LED **V+** screw terminal | common +24 V Wago (all white anodes) **and** both pixel strips' V+ |
| **white** | **L1** (cool white outie) | cool-white outie negatives Wago — split from today's single CW Wago |
| **blue** | **L2** (cool white innie) | cool-white innie negatives Wago |
| **yellow** | **L3** (warm white outie) | warm-white outie Wago |
| **brown** | **L4** (warm white innie) | warm-white innie Wago |
| **orange** | **L5** (amber outie) | amber outie Wago; amber **innie** negatives get capped, not joined |
| **green** | digital **DATA** terminal | DIN of the first (top) strip; jumper its DOUT → second strip's DIN |
| **black** | digital **GND** terminal | both strips' GND |
| — | digital V+ terminal | leave empty (pixels fed by red) |

Mnemonic: white/blue = cool pair, yellow/brown = warm pair, orange = amber; the lighter
colour of each pair is the outie. Red power, black ground, green data.
Pixel check: `Count Markers` effect → pixel 0 red at the top strip's start, blue at the
last pixel, green pair at the top→bottom junction (19/20 if the strips really are 20+20).

**Interim Wi-Fi (2026-09-08 15:30):** Brad pulled the attic Ethernet but hasn't crimped
the RJ45, and wants to live-test the wiring first. ESPHome can't build Wi-Fi + Ethernet
together, so `loft-sconce-wifi.yaml` (package overlay: `ethernet: !remove` + wifi with
fallback AP/captive portal, same device name + entities) was OTA'd over the Ethernet link.
Board is now **on Wi-Fi at 192.168.30.32** (−57 dBm, web UI :80, API :6053). When the
RJ45 is done: `esphome compile /config/loft-sconce.yaml` then
`esphome upload /config/loft-sconce.yaml --device 192.168.30.32` (both yamls share the
build dir `.esphome/build/loft-sconce`, so recompile before upload). Ethernet build
comes back on the .10 LAN (was 192.168.10.34).

**Digital side, what the old WLED actually ran** (dumped from 192.168.30.22):
40 px configured as 2 outputs × 20 (GPIO16, GPIO3), WS2812-type, **RGB** colour order,
segment 0..36. Presets in use: 2 "red plasma" (Simon armed — Node-RED `red loft` on the
Simon Room tab, bri = BriCurve), 6 "green plasma" (`green loft`), 5 Fire 2012 (2 mirrored
halves), 3 Pride 2015, 1 Colortwinkles, 4 Wipe Random; `on random` picks any preset.
All six are reimplemented as ESPHome effects on `light.loft_sconce_color`:
`Plasma` (colour = the light's RGB, so red/green plasma = same effect, different colour),
`Fire 2012`, `Pride 2015`, `Colortwinkles`, `Wipe Random`, plus `Count Markers` (bench
helper: px0 red, every 5th dim white, middle pair green, last blue).
With one data line the two 20-px strips must be **chained** in the fixture; `num_leds`
substitution is 40 — verify with Count Markers and trim to the real count.

**Node-RED changes needed at cutover** (flows untouched today, old loft still live):
- `build api request` (loft CCT mixer, 0–100 → amber/warm/cool): rename the 5 entity ids
  and add cool-white innie/outie handling (today cool white is one channel; per Brad,
  ≤17 = amber→warm outies only, >17 = warm→cool on all; with the split he can run
  outies-only under cool white too — decide the innie rule then).
- `red loft` / `green loft` / `off` / `on random` HTTP nodes → HA `light.turn_on`
  `light.loft_sconce_color` with `effect: Plasma`, `rgb_color: [255,0,0]` / `[30,255,0]`,
  `brightness_pct: BriCurve`, `transition: 1`; `off` → `light.turn_off`.
- "Update CT Values while On" bri push (`set new bri`) → `light.turn_on` brightness only.
- `kitchen fixture wled` tab's `presets.json` GET + `loftPresets` global become moot.
- HA: adopt the discovered ESPHome device `loft-sconce` (Settings → Devices → Discovered),
  then retire `loftsconce` (Penta Mini) and the WLED integration entry for 192.168.30.22.

**Bench test 2026-09-08 (no strips attached):** every effect selectable, whites drive,
relay = ON + "frame re-sent" 60 ms after pixel turn-on, OFF 750 ms after turn-off
(so the 700 ms fade completes), 2 s watchdog quiet, uptime steady. Web API note: the
ESP-IDF web server rejects POSTs without `Content-Length` (curl `-H "Content-Length: 0"`).

**Crown side-finding while porting:** ESPHome scales every pixel written from an
`addressable_lambda` by the light's brightness itself (`ESPColorCorrection.local_brightness`,
set in `AddressableLight::update_state` even while an effect runs). The crown's effects
also multiply by brightness → **crown effects run at brightness² today** (50 % slider ≈
25 % light). The loft effects deliberately do NOT multiply. Fixing the crown = pass 255
to `crownfx::finish` / drop the `* brightness` in each effect; not done, Brad to decide
(his BriCurve values were tuned against the current behaviour).

## 2026-09-08 evening — wired, flicker chased down, data booster in

**As-built at the fixture:** 18/8 is the full **25 ft** (not pulled tight; ~0.3 V drop at 2 A,
left alone). Colour map as in the table above. Pixels: **four strips, four DINs** (upper
sconce = 2 × 1.5 m, lower = 2 × 1 m), no DOUT access, so all four inputs are **paralleled**
(mirrored frames), not chained. Chain-later plan: solder one strip's DOUT, route back through
the existing analog-jumper hole into the next DIN.

**Flicker (any effect/fade, whites off too):** NOT PWM crosstalk (whites off = no edges),
NOT Wi-Fi/RMT starvation (`rmt_symbols: 512` changed nothing), NOT chip timing. Isolation
by pulling strips off the Wago one at a time: **lower strip clean, upper strip garbage** →
the upper's DIN had less margin, exposed by 25 ft of unterminated single-ended data plus a
branch. **Fix = QuinLED Data-Booster at the fixture** (Brad had one): 24 V + GND from the
red/black Wagos, data in = green, booster data-out → all strip DINs; strips keep V+/GND on
the Wagos. First attempt without the booster ground failed; **common GND (black) is
mandatory** — fixed, flicker gone. Config left at WS2812 timing + rmt_symbols 512 (harmless)
+ Plasma dither off (ESPHome scales after us, dither was pointless).
Open: pixel count via Count Markers (upper strips are longer than lower; with mirroring set
`num_leds` to the longest), Ethernet build once the RJ45 is crimped (`esphome upload
/config/loft-sconce.yaml --device 192.168.30.32`), Node-RED cutover, HA adoption.

**Later 2026-09-08 (booster in, counted, staging):** pixel count via `RGB Count` effect =
**upper strips 20 px, lower strips 13 px** → `num_leds: 20` (mirrored, lowers show the first
13). `Wipe Random` sped up to one colour per 3 s (WLED speed 220). **White↔colour staging
in firmware** (not Node-RED, so every caller is covered): PWM on the shared 25 ft cable
still corrupts pixel data while any white is lit, so a colour request while a white is lit
or fading parks the request, cuts the strip (0 ms), fades whites off in 250 ms, waits
350 ms, replays the request (HA shows the colour light blink off/on ~0.4 s); a white
turning on kills the colour instantly. Verified via API sequence test: white→Plasma
replayed 0.39 s later, amber-on killed Plasma. Also: `api.reboot_timeout: 5min`,
`rmt_symbols` back to default, `RGB Count` effect kept as a counting tool.
The 20-min "board wedged" scare was NOT the board: nine of Claude's `timeout docker exec …
esphome logs` readers were still alive inside the container holding API slots (see memory
esphome-logs-zombie-readers). Still open: Ethernet build after RJ45, Node-RED cutover
(old loftsconce/WLED flows), retire old devices in HA, chain the strips one day.

## 2026-09-08 late — Node-RED cutover DONE (full deploy rev 7b25c93d…)

Backup: `nodered/data/projects/nodered_n100_mini/flows.json.backup_before_loft_penta_plus_20260908_205230`.
All old Penta-Mini entity ids and every `192.168.30.22` WLED call are gone from live logic:

| Where | Was | Now |
|---|---|---|
| subflow **loft sconce** → `build api request` | 5 `light.loftsconce_*` (amber in/out, ww in/out, cw combined) | **outies only** (Brad 2026-09-08): ≤17 amber→warm outie, >17 warm→cool outie; both innies driven OFF every call |
| **Update CT Values while On** | `change ip=30.22` → `wled status` subflow (GET /json/state) | `api-current-state light.loft_sconce_color` → `switch colour on?` (out0 on, out1 off; old "error" output folded into off) |
| same tab `set new bri` + http POST | WLED `bri=BriCurve` | `light.turn_on loft_sconce_color brightness_pct=BriCurve transition 5` via api-call-service |
| **Simon Room** `red loft` / `green loft` / `off` (+ http nodes) | WLED presets 2 / 6 / on:false | `light.turn_on` effect **Plasma** rgb [255,0,0] / [30,255,0] brightness_pct BriCurve transition 1; `light.turn_off` |
| **Bedtime Routine** | `change ip` → `wled status` | current-state + switch, same as above |
| **kitchen fixture wled** (Loft Pico 2 link) | hourly `presets.json` poll → `loftPresets` global; `on random` = random preset; `off` | poll+store+inject **disabled (d:true)**; `on random` picks Colortwinkles / Pride 2015 / Wipe Random / Fire 2012 @100 % or red/green Plasma @11 % (preset bri 29); `off` = `light.turn_off` |
| HA helper `light.loft_sconce_group` | 5 old loftsconce entities | 5 new `light.loft_sconce_*` channels (options flow via REST) |

Live test: `POST /inject/9fdba037c70df0c4` (Simon Room manual red) → HA `light.loft_sconce_color`
on / Plasma / rgb 255,0,0 / bri 89 (BriCurve 35). Deprecation warning in the NR log:
`domain and service input properties are deprecated… use action` — same legacy payload style the
old mixer used; harmless until node-red-contrib-home-assistant-websocket 1.0.
Still open: Ethernet build after RJ45; remove the dead HA entries (ESPHome `loftsconce` Penta Mini,
WLED integration `Loft-Sconce-WLED` 192.168.30.22); Pico button live test; first Global-CT
repaint with the outies-only mixer.

**Pico re-test flicker (2026-09-08 night) → staging hardened.** Root cause: the Pico/Node-RED path
sends the colour request and FIVE separate white `turn_off` calls (transition 1 s) within ~100 ms;
any turn_off arriving after the firmware's 250 ms fast fade restarted that white's fade to 1 s, and
the fixed 350 ms replay streamed pixels over live PWM. Now `stage_colour_on` waits for every white's
`current_values` to reach off (`wait_until`, 450 ms cap), gives stragglers a second 150 ms fade
(300 ms cap), hard-cuts only what still survives, then replays after 80 ms. `colour_yields_to_white`
also fires when the colour is merely mid-fade-out (current/transformer), not just commanded on.
Emulated Pico sequence (aioesphomeapi, /tmp/pico_test.py in the esphome container): colour
replayed 0.69 s after the button with no hard cut; earlier build needed 4 hard cuts.
Log capture rule: `docker exec esphome-esphome-1 timeout N esphome logs …` (timeout INSIDE).

**Aggregate White light (2026-09-08 night, Brad's office-lights idea).** The Pico re-test showed the
whites' fade-off still ended in a visible hard cut most times: five separate white turn_off calls
kept straddling the firmware's fast-fade window. Fix = ONE white entity, one command:
- Firmware: `light.loft_sconce_white` (`color_temperature` platform, 2700–6500 K) over two template
  outputs → `loft_apply_white` renders amber/warm/cool OUTIE levels (input=(1-mix)*100, split 17, same
  curve as the old mixer). The three outie mono lights are gone (ESPHome dropped their HA entities
  on reconnect); `light.loft_sconce_cool_white_innie` / `_warm_white_innie` remain, parked off.
  Staging white-set = white + 2 innies.
- Node-RED (full deploy rev 9f2ecc64…): subflow mixer → single `light.turn_on` with
  `brightness_pct` + `color_temp_kelvin` (office `cctInputToKelvin`, linear in mireds) or
  `light.turn_off`, `action/target/data` payload; wire ORDER changed so whites-off fires before the
  colour call (Loft Pico out2, Simon alarm-true branch).
- HA: group `light.loft_sconce_group` = white + 2 innies; dashboard /29 entities list + a White tile
  with brightness and colour-temp sliders.
Emulated Pico (white off 1 s, then colour): staging replayed at +0.42 s, no second fade, no hard cut.
Red-loft inject re-fired afterwards to restore Simon's armed indicator.

**Ethernet cutover (2026-09-09 15:52).** Attic RJ45 crimped. `esphome compile /config/loft-sconce.yaml`
then `esphome upload … --device 192.168.30.32` (OTA over the interim Wi-Fi). Board answered on
192.168.10.34 five seconds after reboot; HA's ESPHome entry re-homed to that host by itself; entity
states restored (white on = daytime mixer state). Note for next time: under the Wi-Fi overlay the
jack/switch show NO link lights — `ethernet: !remove` means the ESP never drives the LAN8720's
50 MHz clock (GPIO17 CLK_OUT), so a cable can only be tested with the Ethernet build loaded.
`loft-sconce-wifi.yaml` stays as a recovery overlay. Remaining: delete the dead HA entries
(ESPHome `loftsconce` = old Penta Mini, WLED `Loft-Sconce-WLED` 192.168.30.22), Pico live test,
first outies-only Global-CT repaint.

**Close-out (2026-09-09 eve).** Brad confirmed the Pico path and the outies-only colour temperature
live. Deleted the legacy HA config entries (ESPHome `loftsconce` = old Penta Mini at 192.168.30.14,
13 entities; WLED `Loft-Sconce-WLED` at 192.168.30.22, 27 `dig_quad_v3_*` entities) after confirming
no live dashboard, Node-RED or yaml references; HA needed no restart. Controller side of item 4 is done.
Left: the PSU cascade and, someday, chaining the strip DOUT→DIN.

## 2026-09-17 — Simon's closet PSU step DONE (Brad's report)

Brad added an **outlet inside Simon's closet** (cord tidiness) and moved the **LRS-200-24** in.
It now feeds both Penta Plus boards — Simon's crown and the loft sconce — and is working well.
The 4 A desktop brick is out of the picture. Sequencing step 3 is complete; what is left of the
cascade is the master-closet side (HLG-480H-24 + handy box, vanity + stairs on Wago pair A,
pair B parked for the master crown).

**Punch-list item (Simon's crown, from the original install):** one solder joint on a white
NEGATIVE pad (cw or ww) has let go and the wire is only resting on the pad. Assessment:
- A −wire touching the + pad is a short ACROSS the channel MOSFET, not through the strip — the
  strip's resistors are bypassed and protect nothing. Only conducts while that channel is on;
  the LRS-200 hiccups at ~10–12 A (and the Penta's input fuse is in the loop). Outcome = both
  crown and loft blink out, worst case a dead FET. Not the fire scenario.
- The real risk is the resting contact itself: a resistive/intermittent joint carrying an amp or
  two heats at a pinpoint and can micro-arc (24 V DC is just enough to sustain it). Note the
  fault budget went UP with this swap: the brick folded at 4 A, the LRS will happily push 8.8 A.
- Verdict: not urgent, but fix it — reflow with a cordless iron (pad and wire are already
  tinned: flux + one touch), or if that point is a redundant injection, remove and cap the wire.
  Do NOT glue a loose contact in place.

**Correction, same day:** the loose joint is the JUMPER across the mini-split gap, and the crown
perimeter is fed from BOTH ends — so it is a redundant link in a ring, not a load-carrying joint.
Current diverts to the other feed as the contact degrades; voltage across the joint is capped at
the leg-to-leg drop difference (~1 V, well under 0.5 W, cannot arc). No visible flicker (Brad).
Downgraded to "fix only if already up there": reflow, or just snip + cap that one wire (the ring
doesn't need it; removes the short-to-+ nuisance case). Only cost of an open joint = that white
marginally dimmer near the mini split on the longer leg.
