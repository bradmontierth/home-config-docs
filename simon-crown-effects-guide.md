# Simon crown effects

## Device and build ownership

- Controller: QuinLED An-Penta-Plus, `simon-room-crown`, Ethernet **192.168.10.56**.
- 220 WS2811 RGB pixels, GPIO5, GPIO13 digital power relay; separate CWWW white
  light on GPIO33/GPIO32. White and color families are mutually exclusive.
- Source: `/home/pi/esphome/config/simon-room-crown.yaml`.
- Shared integer helpers: `/home/pi/esphome/config/crown_fx.h` (also used by loft).
- Builder: running container `esphome-esphome-1`, ESPHome **2025.8.3**.
  Use this container's existing toolchain. Do not use a throwaway Compose build.
- API port 6053; OTA port 3232. No web server is configured on this controller.
- Config is outside the home_config Git repository. Preserve timestamped source
  and firmware backups before editing/building.

## Palette Swoosh — added 2026-09-10

New effect: **Palette Swoosh (Limited)**. Implementation lives in
`/home/pi/esphome/config/palette_swoosh.h`; the YAML adds its include and one
16 ms addressable lambda. Existing effects and shared helpers are unchanged.

- Current revision: **Ocean Waves motion and shading between solid-color wipes**,
  cycling blue, amber, purple, teal, rose, green themes. Each theme has deep
  colored troughs, colored faces, pale crests, and near-white foam. Blue uses
  Ocean Waves' exact four RGB shade stops; other themes use corresponding tints.
- Four forward swells at 11 pixels/second and five counter-swells at 7
  pixels/second, mixed 5:3, match Ocean Waves' motion on the 220-pixel crown.
- Waves ease to solid color during the 1.5 seconds before each wipe, remain
  completely absent during the wipe, and return over 1.5 seconds afterward.
  This keeps the wipe between just the two solid base colors. Motion phase
  continues internally while hidden. The first selection begins with waves.
- First palette holds for 30 seconds. Wipes begin at 30, 60, 90... seconds after
  selection; each takes 1800 ms. An eased 40-pixel edge replaces the old palette
  in increasing pixel order. Illumination continues throughout.
- Effect selection resets the timeline. Elapsed time accumulates unsigned
  `millis()` differences into 64 bits, preserving motion across timer rollover.
- Fixed-point subpixel positions, 16-bit color values, and wide integer
  intermediates throughout; no floating-point arithmetic in this effect.
- Integer wave counts close the swells/ripples around the 220-pixel ring.
  During a wipe, old/new palette boundaries are intentional.
- Reuses `crownfx::finish(p, 255, false, 0, i)`: existing power cap, one final
  quantization, no temporal dithering. Wave shades are normalized to a 304
  channel-sum ceiling before compositing, leaving rounding headroom below the
  shared 306 cap. Solid colors total 288. Brightness is not perceptually calibrated.

### Brightness convention

The installed ESPHome `AddressableLight::update_state()` applies light brightness
through `ESPColorView`, including during effects. The older crown effects also
multiply by brightness, giving approximately brightness squared. Palette Swoosh
passes 255 to the helper so ESPHome applies user brightness once. It can therefore
look brighter than older effects at the same slider setting. Do not silently
rewrite older effects or retune BriCurve as part of adding a new one.

The LED output remains 8-bit: 16-bit intermediate math avoids premature rounding,
but does not create a 16-bit LED driver. Temporal dithering is disabled to avoid
introducing shimmer; visual low-brightness smoothness still needs human review.

## Validation and build

Host regression test uses the actual new effect and shared helper headers, with
a minimal RGB Color container in place of ESPHome's hardware-dependent headers:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -Werror -fsanitize=undefined \
  -I/home/pi/home_config/diagnostics/crown-swoosh \
  -I/home/pi/esphome/config \
  /home/pi/home_config/diagnostics/crown-swoosh/test.cpp \
  -o /tmp/crown-swoosh-test-bin
/tmp/crown-swoosh-test-bin

docker exec esphome-esphome-1 esphome config /config/simon-room-crown.yaml
docker exec esphome-esphome-1 esphome compile /config/simon-room-crown.yaml
docker exec esphome-esphome-1 esphome upload /config/simon-room-crown.yaml --device 192.168.10.56
docker exec esphome-esphome-1 timeout 30 esphome logs /config/simon-room-crown.yaml --device 192.168.10.56
```

Put `timeout` **inside** the container; timing out only the docker client can
leave orphan API log readers that exhaust the device's connection slots.
Validation output may include resolved secrets: do not paste it unfiltered.
When selecting an effect through the API, omit transition/flash parameters.

Initial 2026-09-10 host checks: 2,887,720 pixel frames; RGB channel sum 258..289
(cap 306); largest channel change between 16 ms frames 27. Verified exact wipe
start/end colors, monotonic coverage, palette cycling, spatial wrap continuity,
effect reset, timer rollover, and long runtimes with undefined-behavior sanitizer.
Firmware compiled successfully: 519,422 bytes flash (28.3%), 17,364 bytes static
RAM (5.3%). These memory numbers exclude runtime heap use.

### Deployment verification — 2026-09-10 14:08–14:11 MDT

OTA succeeded; API reported the new build (Sep 10 2026, 20:07:56 UTC), the same
device identity, and 17 effect entries including None and Palette Swoosh. The
controller logged a successful boot after 60 seconds. A 66-second live effect
run at the existing color brightness (56%) covered two scheduled wipes. API
requests remained responsive, the white/color interlock and relay worked, and
the run showed no slow-loop, watchdog, or reboot warnings. This verifies device
operation, not the appearance of the physical light; human visual review remains.

The initial verification script used an unavailable APIClient attribute and
restored the prior lighting early; the corrected full run passed. Its final
disconnect logged CONNECTION_CLOSED as expected. Both log readers used an
in-container timeout and exited. Final lighting was restored: color off/None,
white on at 76.08%, 6500 K.

New OTA SHA-256:
`e1331e36f0ef4c4a16fa3106354202ee4d13b0dc220911f40b1b2ac1d3ccc9b0`

### Moving-wave revision — 2026-09-10

User approved revising the existing effect and reflashing after the first
version looked nearly static, with color differences resembling voltage drop.
Only `palette_swoosh.h` changes firmware behavior. The existing effect name,
30-second interval, 1.8-second wipe duration, brightness convention, power cap,
relay safety, and all other effects remain intact. The wipe feather is widened
from 28 to 40 pixels to soften the stronger color contrast.

Regression checks now also require four distinct crest bands, at least 80
pixels reaching the primary and 40 reaching the secondary color, and substantial
one-second color movement across every palette at multiple wave phases. Full
test passed: 2,887,720 pixel frames, RGB sum 287..289, maximum 16 ms channel
step 27. Firmware compiled successfully: 519,302 bytes flash, 17,364 bytes
static RAM. These tests verify numerical behavior, not subjective appearance.

First-version source and firmware backup:
`/home/pi/esphome/config/backup_before_swoosh_waves_20260910_141835/`

Revised OTA SHA-256:
`e55fdd69d81653d63d8155a29d27628ff26f3ec44a6444a7d0c80d4849fd2cb4`

OTA and the 66-second live check passed at 14:20–14:21 MDT. Device reports build
Sep 10 2026, 20:19:26 UTC; boot success logged, API responsive through both
scheduled wipes, white interlock intact, no runtime slow-loop/watchdog warnings.
The final API-client disconnect produced the expected CONNECTION_CLOSED log.
The user already had Palette Swoosh selected before this revision; it was left
running at 56.08% brightness, with white off, for visual review.

### Single-hue subtle-movement revision — 2026-09-10

Visual feedback rejected the contrasting crests: they looked like little
segments, and two old plus two new colors made the wipe hard to follow. The
user asked to build on the simple single-color wipe with subtle movement.
Current behavior is described at the top of this guide. Fixed-point math,
single brightness application, power limiter, 30-second timing, and 1.8-second
wipe remain. Only `palette_swoosh.h` changes firmware behavior.

Regression checks now verify a single hue (constant RGB channel ratios) at
all positions between wipes, two broad brightness crests, restrained but
nonzero movement over one second, and the existing wipe/rollover/seam checks.
Passed 2,887,720 pixel frames: RGB sum 244..289, maximum 16 ms channel step 27.
The output remains 8-bit and subjective smoothness requires visual feedback.

Previous contrasting-wave source and firmware backup:
`/home/pi/esphome/config/backup_before_swoosh_subtle_20260910_143716/`

Build and OTA passed: 519,174 bytes flash, 17,364 bytes static RAM. Device
reports build Sep 10 2026, 20:37:31 UTC. The resumed live check at 18:24–18:25
MDT ran for 66 seconds through two scheduled wipes. API remained responsive,
white stayed off, and no slow-loop/watchdog warnings were observed; the final
test-client disconnect logged the expected CONNECTION_CLOSED. Palette Swoosh
was left selected at the existing 56.08% brightness for visual review.

Single-hue 85–100% revision OTA SHA-256:
`0dbef078c21c3ddf533a89733b3a4713dbbeeb3193c76dc9e1570bfce5e0632d`

### Ripple contrast adjustment — 2026-09-10

User could not see the 85–100% ripple and approved increasing its range to
60–100%. This changes only the intensity floor and amplitude in
`palette_swoosh.h`; hues, speed, wave width, wipe, and brightness handling stay
the same. The 60% trough is relative to the user's brightness slider, not an
absolute slider value. Tests retain the single-hue, two-crest, timing, seam,
rollover, and power-cap checks, with new intensity/movement expectations.
2,887,720 pixel frames passed: RGB sum 172..289 (cap 306), maximum 16 ms
channel step 27. This verifies output arithmetic; physical appearance remains
subject to user review.

Pre-adjustment source/firmware backup:
`/home/pi/esphome/config/backup_before_swoosh_contrast_20260910_185642/`

Build and OTA passed: 519,174 bytes flash, 17,364 bytes static RAM. Device
reports build Sep 11 2026, 00:56:56 UTC. At 18:57–18:58 MDT, the 66-second
live run passed through two wipes with API responsive and white interlock
intact. Boot success was logged, with no slow-loop/watchdog warnings. The
test-client disconnect logged the expected CONNECTION_CLOSED. Left running
at the existing 56.08% slider brightness for visual review.

60–100% ripple revision OTA SHA-256:
`09475ff72f4df75bd2d7f22c9024a547ea06a13b4653783a242685a65997cae8`

### Ocean shading with solid wipe — 2026-09-10

User still could not see the brightness-only motion and approved using Ocean
Waves' shading, with waves settling before and returning after each wipe.
Only `palette_swoosh.h` changes firmware behavior. Current behavior is at the
top of this guide; earlier visual experiments above are deployment history.

Host checks passed 2,887,720 pixel frames: RGB sum 66..305 (cap 306), maximum
16 ms channel step 27. Tests verify the motion against the existing Ocean
Waves formula (within fixed-point rounding), deep troughs/pale foam/movement
for every theme, monotonic settle/return envelopes, spatially uniform fields
throughout every wipe, exact wipe endpoints, seam continuity, reset, long
runtimes, and timer rollover with undefined-behavior sanitizer.

Pre-change source and firmware backup:
`/home/pi/esphome/config/backup_before_swoosh_ocean_20260910_190143/`

Build and OTA passed: 519,794 bytes flash, 17,364 bytes static RAM. Device
reports build Sep 11 2026, 01:02:33 UTC. The 66-second live check at 19:03–19:04
MDT covered two scheduled wipes plus wave return, with API responsive and
white interlock intact. Boot success logged; no slow-loop/watchdog warnings.
The test-client disconnect logged the expected CONNECTION_CLOSED. Left running
at the existing 56.08% slider brightness for the user's visual assessment.
Device checks confirm operation; they do not verify the appearance of the LEDs.

Current OTA SHA-256:
`560ca6b1e473ae668bbf21b011ec5c4c693b281dac59c3e4322be25cd73261a1`

Select **Palette Swoosh (Limited)** on the crown color light to try it. Existing
voice named-effect and random-effect allow-lists were not extended in this
firmware change.

## Rollback reference

Pre-change sources and firmware are preserved in:

`/home/pi/esphome/config/backup_before_palette_swoosh_20260910_140631/`

Original OTA SHA-256:
`1177f46753682d5749b06c508cf5358b5ec15af6dd45c474eaaacf0757496453`

Rollback is disruptive and requires approval unless already authorized. Restore
the backed-up YAML and rebuild/upload with the same container, or use the
preserved OTA binary with the supported uploader. Do not restore only the YAML
and then upload a newer stale build artifact.

## Construction Site — added 2026-09-10 (evening)

Simon loves construction trucks, so the ring became a job site: one scene per
wall. Implementation is `/home/pi/esphome/config/construction_site.h` (float
state, `crownfx::finish(px, 255, …)` so brightness is applied once, channel
sum pre-capped at 304 so the helper's rounding never lands above 306). The
browser simulator is the choreography reference:
https://claude.ai/code/artifact/9998a413-b999-4f35-9041-f4bd745bb7d3
(source in the session scratchpad; republish from there).

Three new effects (20 entries total now):

- **Construction Site (Limited)** — walls are the spans between three corner
  pixels. Scenes: *Dig site* (excavator scoops a brown pile into a dump truck,
  3 scoops per load, truck backs in with white reverse blink, hauls, turns,
  backs up to the far pile, dumps; dirt is conserved so when the source pile
  is gone the ends swap and the excavator tracks across), *Road crew* (paver
  lays hot asphalt over gravel, roller smooths, striper paints yellow dashes,
  three cars drive the finished road, fade to gravel, repeat), *Mixer pour*
  (barber-pole drum, pours 7-px slabs that cure from wet blue-grey to light
  grey), *Flagger* (cones + two paddles STOP/SLOW, cars queue and take turns
  through a one-lane zone), *Dark*.
- **Site Swatches (Limited)** — 11 static colour blocks (dirt, gravel, hot
  asphalt, rolled asphalt + dashes, wet concrete, cured concrete, yellow, amber,
  cone orange, truck bed with lights, ground) for judging the palette on the
  ceiling wash.
- **Pixel Ruler (Limited)** — px 0 red, every 10th blue, every 50th white,
  last pixel green; count ticks per wall to find the corner indices.

Config entities (ESPHome template number/select, `restore_value`, no reflash
needed to change them; the effect resets its scenes when any of them changes):
`Site Corner 1/2/3` (defaults 55/110/165 — placeholders until counted),
`Site Wall 1..4` scene selects (defaults Flagger / Mixer pour / Dig site /
Road crew — Brad's wall notes: 1 = door bump-out, 2 = above bed, 3 = cleanest
run, 4 = mini-split), `Site Dirt Pixels` (24), `Site Scoops Per Load` (3).
Corners are validated in the header (each wall ≥ 3 px).

Host regression: `home_config/diagnostics/crown-construction/test.cpp`
(build like the swoosh test, add `-fsanitize=undefined,address`). 2,362,500
frames over 7 corner layouts × 6 scene sets, 15 min each: no NaN, channel sum
≤ 306, dig site 35 loads / 5 end-swaps in 15 min on a 55-px wall, road crew 10
cycles; rollover, 100 s stall and live reconfig all fine.

Build: 547,422 B flash (29.8 %), 42,788 B static RAM (13.1 % — the scene
buffers). Backup: `backup_before_construction_20260910_194828/`. OTA at ~19:55
MDT, uptime reset confirmed via HA, effect list 20. Left running Construction
Site at the existing 56 % (Simon asleep with lights on, Brad OK'd). OTA
SHA-256 `5e9ffe5d78e5d186da3eb307d61e88306db56b47160646efd083baff6e38faa8`.

Open: Brad's read on brown/grey through the ceiling wash (he expects the
flagger to be hard to follow as ambient light); count corners with the ruler
and set the Site Corner numbers; ring direction vs. wall numbering; voice
aliases ("construction", "dump truck") on the Node-RED Voice Buttons tab
(completed 2026-09-13; see below).

### Construction Site voice wiring — 2026-09-13

Added `button.voice_simon_fx_construction_site` discovery and the Simon command
mapping to `Construction Site (Limited)` through the Voice Buttons tab Admin
API. The command uses the existing crown brightness logic. Simon-only aliases
in both the repository seed and persistent live table are `construction site`,
`construction trucks`, `construction`, `dump truck`, and `dump trucks`.
Existing verb normalization supports “give me”, “show me”, “put on”, and
“switch to”; these aliases bypass the classifier through the existing path.

Verified HA button registration, effect availability, seven dispatch phrases,
nine other-room exclusion cases, and the Node-RED discovery/action payloads.
Light actions were mocked. The initial dispatch check inadvertently sent seven
spoken confirmations through room broadcast; corrected checks mock that output
too. Checks and before/after API snapshots are in
`diagnostics/construction-voice/`. No physical effect activation was tested.

### Zone model + measured corners — 2026-09-10 (later)

Brad counted the Pixel Ruler (Voice Notes note 342, "Wall paint strip color
sequence"): pixel 0 red at the wall 4 / wall 1 corner, then along wall 1;
first white (50) sits on the door bump-out jog; blues 60, 70 finish wall 1;
white 100 is directly above the bed on wall 2; blue 110 is "pretty much" the
wall 2/3 corner; white 150 is the middle of wall 3; blue 180 is barely on
wall 4; the strip physically jumps past the mini-split before blue 190; then
white 200, blue 210, green 219. Decoded: corners ≈ 75 / 113 / 179, bump-out
≈ 45–55, mini-split gap ≈ 180–186 (all ± a few px).

Brad's calls: colours "okay-ish"; the truck with its dark bed and the
flashing red reads well; cut the flagger (too abstract as ceiling wash); the
prime runs are wall 4 after the mini-split + wall 1 to the bump-out (one run
across the pixel-0 corner) for dirt or road, and wall 3 for the other.

The 3-corner / 4-wall model could not express a run that wraps through pixel
0, so the config is now **four zones**, each `Site Zone X Start` / `Site Zone
X End` (0–219; end < start wraps; end == start disables) plus `Site Zone X
Scene`. Pixels outside every zone are off. Defaults: A 186→45 Dig site (79
px, the truck rounds the corner), B 113→179 Road crew (66 px), C 75→113
Mixer pour (38 px, above the bed), D 0→0 Dark. The bump-out remainder
(46–75) and the mini-split stub stay dark. `Site Corner 1-3` and `Site Wall
1-4` entities are gone (HA will list them as unavailable until deleted).

Bug caught by the host test while converting: a disabled zone (L < 3) is
forced to Dark, and comparing the *effective* scene against the requested one
made the config look changed every frame → constant scene resets. The zone
now stores `requested` separately. Test (7 zone layouts incl. wrap, reversed,
zero-length and out-of-range values × 6 scene sets, 15 min each): 2,362,500
frames, 0 failures, channel sum ≤ 306, dig 27 loads / 3 end-swaps in 15 min
on the 79-px wrap zone, road 9 cycles, bump-out remainder verified dark.

Build 548,642 B flash (29.9 %), 42,820 B static RAM. OTA ≈ 20:35 MDT, SHA-256
`6140fe01e93c3807947daac94d3f9d540861db9303513376d9ce23d5c215d388`. Same
backup directory as the first flash tonight (`backup_before_construction_
20260910_194828/` holds the pre-construction state). Simulator artifact
updated to the zone model with the measured room.
