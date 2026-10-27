# Aillio Bullet R2 — custom logger / controller / auto-roast plan

Status 2026-10-13 eve: roaster ordered, not delivered. **Phase 1+2 software BUILT against a
simulator** in repo `/home/pi/roaster` (local commit, no GitHub remote yet): driver, byte-exact
simulator, USB→MQTT bridge + Pi install script, FastAPI server + SQLite, phone PWA with live
chart/controls/roast history, HA discovery. Deployed on the Beelink at :8806 with the
simulated bridge (`docker compose --profile sim`). Phase 0 (real-frame validation) still gates
everything. See the repo README for the MQTT/HTTP contract.

## 0. What the research actually says (facts, with sources)

**The protocol is already decoded and open.** Artisan added Bullet R2 support in
v3.1.2 (April 2025, contributed by GitHub user mikefsq). The driver is
`src/artisanlib/aillio_r2.py` in the Artisan repo (AGPL-3). A local copy is in
this session's scratchpad; re-fetch from
`https://raw.githubusercontent.com/artisan-roaster-scope/artisan/master/src/artisanlib/aillio_r2.py`.
Key facts from that file:

| Item | Value |
|---|---|
| USB | VID `0x0483` PID `0xa4cd`, configuration 1, interface 1, one bulk IN + one bulk OUT endpoint, pyusb/libusb-1.0, no kernel driver (detach + claim) |
| Frames | Roaster **streams** 64-byte frames unsolicited (the R2 loop never sends a status request; the R1 driver polled at 10 Hz). Frame type in byte 0: `0xA0` temperatures/state, `0xA1` fans/errors, `0xA2` power electronics |
| A0 frame | IBTS bean temp + IBTS RoR, IBTS **ambient**, bean-probe temp + RoR, energy this roast, differential air pressure, exhaust fan RPM, inlet air temp, hot air temp, exhaust temp, **absolute barometric pressure, humidity**, roast clock min/sec, **`button_crack_mark`, `fc_sample_index`, `fc_number_cracks`**, PSet/FSet/HASet/DSet (power/fan/?/drum setpoints), `roasting_method` (Manual/Recipe), state machine byte |
| A1 frame | duty + RPM for coil fans, induction fans/blower, IBTS fan, **drum RPM**, error codes, button bitmask |
| A2 frame | power setpoint W, line Hz, IGBT temps, Vrms, Irms, active/reactive/apparent power |
| Commands | 4-byte opcode + CRC32 (custom table, big-endian words, little-endian CRC appended). Heater/fan/drum are **relative** `INCR`/`DECR` (`0x34/0x31/0x32`, `01`=up `02`=down). PRS = `30 01 00 00`. Preheat setpoint = `35 00 hi lo`. No absolute "set power 7" command exists |
| Ranges | heater 0–10 (R2) / 0–14 (R2 Pro), fan 1–12, drum 1–9, preheat 20–350 °C |
| States | `00` off, `02` preheat, `03` stabilizing, `04` charge/ready, `06` roasting, `07` cooldown, `08` cooling/back-to-back, `09` shutdown, `0B` power-on reset. PRS cycles off→PH→charge→roasting→cooldown |

Other facts:

- R2 Wi-Fi (2.4 GHz) is for **front-panel firmware updates only**. Users on the
  Roast.World forum confirm RoasTime still talks over USB; there is no LAN
  protocol and no public API. Bluetooth: none.
- Artisan warns that running RoasTime and another logger on the same USB device
  at once causes instability. With a Pi permanently plugged in, RoasTime on the
  laptop is simply never used (firmware updates can go over Wi-Fi from the panel).
- The R2 front panel has a **first-crack mark button** (single press since firmware
  v79) and the mark is in the A0 frame. So FC marked at the roaster reaches our
  logger with no phone involved. `fc_number_cracks` looks like a crack counter;
  what increments it is unknown until we watch it live.
- The R2 already measures ambient temp (IBTS ambient), humidity, barometric
  pressure, inlet air temp. Outdoor temp/humidity already flow from the Ecowitt
  station into Node-RED/MQTT. Ambient logging costs nothing.
- RoasTime "recipes" = replay of recorded power/fan/drum changes keyed on time
  and bean temperature. That is what Artisan's "Playback Events" does too. Neither
  closes the loop on RoR.
- Audio first-crack detection, open source and usable today:
  - `syamaner/coffee-first-crack-detection` (Hugging Face, Apache-2.0): Audio
    Spectrogram Transformer fine-tuned on 1,435 ten-second 16 kHz chunks from 21
    roasts (Hottop, cheap USB mics: FIFINE, ATR2100x). Test set: 98 % accuracy,
    FC recall 97.6 %, precision 89 %, 5 FP / 261 negatives. INT8 ONNX runs in
    ~2.45 s per 10-s window on a Pi 5, ~56 ms on an M3. Companion dataset
    `syamaner/coffee-first-crack-audio` (Apache-2.0). In real roasts the author
    needed a debounce (≥3 positive windows within 30 s) and landed "within 20 s
    of manual FC". **Second crack is not labelled anywhere public.**
  - `aderusha/RoastLearner`: Windows-only, PyAudioAnalysis classifiers → Artisan.
    Reference only.
  - `Xbjornsen/RoastCompanion`: Android app, on-device amplitude/spectral +
    small classifier. Reference only.
- Artisan itself is a Qt desktop app. It can run on a Pi but its UI is not
  phone-usable; its WebLCDs page shows numbers only, no control.

## 1. What I would build (and why it differs from the ask)

Brad's description: Pi on the roaster's USB, logs + controls, auto-FC from a
mic, ambient logging, auto-roast from a profile, phone web app, "modify Artisan
or use it as reference".

Decisions:

1. **Use Artisan as reference, do not fork or run it.** Our driver is ~300 lines
   of struct unpacking + 6 commands. Artisan's analysis tools stay useful on the
   laptop: we export every roast as CSV in Artisan's import format so nothing is
   lost. Licence call for Brad: vendoring `aillio_r2.py` verbatim makes the
   roaster repo AGPL-3 (irrelevant for a private homelab repo, but it is a
   choice). Recommendation: write our own driver from the frame layout above and
   credit mikefsq/Artisan in the README.
2. **Same architecture as the rest of the house.** Pi at the roaster runs one
   small bridge (USB → MQTT + local SQLite + WebSocket). Web app + DB live on the
   N100/beelink like voice-ops/cookmode/thermal_viewer. Home Assistant gets a
   handful of entities via MQTT discovery (state, IBTS, RoR, FC event) so
   Node-RED, the kitchen display captions and the voice assistant can say
   "first crack" for free. Grafana gets the time series for after-the-fact
   comparison. Phone UI = PWA first; a thin APK through the existing F-Droid
   pipeline only if the PWA annoys him.
3. **Closed-loop on the roaster's own setpoints, not optimistic.** Because
   heater/fan/drum commands are relative steps, the bridge must read PSet/FSet/
   DSet back from the A0 frame after each step and retry, never assume
   (Artisan's driver writes `self.heater = value` optimistically; that is how a
   lost USB packet turns "power 6" into "power 7" for the rest of the roast).
4. **First crack: record first, detect in shadow, arm later.** Every roast records
   mic audio to WAV alongside telemetry from day one, with Brad's manual FC mark
   (panel button or phone). That builds a labelled dataset on *his* roaster, mic
   and environment for free. Two detectors then run in shadow and are compared
   to the manual mark on the dashboard; auto-declare only when it has matched
   within ~10 s over several roasts (same shadow→arm pattern as the stop model
   and the claire-is-up veto):
   - **Pop-rate detector (Pi, cheap, explainable):** per-roast adaptive noise
     floor of the drum+fan (learned during the first minutes after charge),
     transient/onset detection in the ~1–8 kHz band, count pops per 30 s. FC
     start = pop rate crosses a threshold; FC rolling / end come from the same
     rate curve. Second crack = same detector plus the temperature gate below.
   - **AST model (GX10):** the syamaner INT8/ONNX model on 10-s windows streamed
     from the Pi, same ≥3-of-N debounce. The GX10 already serves the voice
     pipeline over LAN; a 16 kHz mono stream is nothing. Fine-tune on our own
     recordings once we have ~15–20 roasts.
   - **Temperature gate on both:** no FC candidate is accepted below an IBTS
     floor (≈185 °C, tune from data) and none is accepted twice. Second crack
     needs IBTS well above FC temp and some minutes after FC. This single rule
     removes most false positives (charge clatter, chaff tray, someone talking).
5. **Auto-roast in three deterministic steps, no LLM in the loop.**
   - Step 1 *Replay*: a saved roast's control events replayed keyed on **IBTS
     temperature** (not time). Colder ambient/charge → changes happen later, same
     bean temperature. Equivalent to a RoasTime recipe but ours and phone-driven.
   - Step 2 *Track*: a bounded PI correction on heater to follow the reference
     IBTS curve / RoR (heater is integer 0–10, so rate-limit to one step per N
     seconds and clamp to ±2 steps from the reference). Fan/drum follow the
     reference only.
   - Step 3 *Finish*: auto-DROP on target IBTS, or target development-time ratio
     after the (auto or manual) FC mark, whichever Brad picks per bean. DROP =
     PRS press → cooldown; the roaster does the rest.
   - Guardrails in every step: any front-panel or phone input instantly
     disarms auto mode; hard caps on IBTS and roast time force cooldown; if the
     bridge or USB drops, the roaster just keeps its last settings (it is a
     standalone machine with its own protections), so the phone shows a loud
     "link lost" state. Auto mode is armed per roast from the phone, never on by
   default, and the roaster is never left unattended.
6. **Ambient / lot data:** log roaster ambient, humidity, pressure, inlet temp,
   Vrms (A2 frame) and outdoor Ecowitt temp/humidity per roast. Per-roast form on
   the phone: bean/lot, green weight, roasted weight (→ weight-loss %), notes,
   score. Weight loss and FC time are the two numbers that actually tell you if
   two roasts matched.

## 2. What I would NOT build

- A fork of Artisan or Artisan-on-a-Pi with VNC/noVNC (phone-hostile, and Artisan
  would hold the USB device we need).
- Roast.World sync / RoasTime compatibility. No API. Export CSV for Artisan instead.
- A native Android app up front. PWA first.
- Charting from scratch. uPlot (fast, tiny) for live and replay curves.
- An LLM/"agent" controller. The published autonomous-roast experiment that used
  one overshot its own development-time target; this is a control problem.
- Second-crack detection before we have our own recordings of second crack.
- Anything over the R2's Wi-Fi. It is firmware-update only.

## 3. Hardware

- **Pi at the roaster.** The retired Pi 4 (.24, master-closet satellite, unplugged)
  is enough for the bridge + WAV recording + pop-rate detector. If on-Pi AST
  inference is wanted (no GX10 dependency), that needs a Pi 5. Recommendation:
  Pi 4 now, GX10 does the model. USB-A → USB-C cable to the roaster. Wi-Fi or
  Ethernet to the LAN; where the roaster lives (garage? patio?) decides which.
- **Mic.** Two channels from roast one, compared offline:
  1. *Air mic* = the existing Tonor USB condenser, mounted on the roaster cart
     (same geometry every roast), ~30 cm from the trier/door side, foam
     windscreen for the porch. This matches what the public AST model was
     trained on (cheap USB condensers), so it feeds the GX10 model as-is.
  2. *Trier-port probe* = the Bullet owners' "stethoscope mod" with a mic on the
     end: high-temp silicone stopper in the trier port, ~2 in of 1/2 in ID
     silicone tube ending flush with the stopper face (not into the drum, or
     bean strikes clatter), a cheap omni electret lavalier capsule at the outer
     end into a USB audio dongle. Owners report "every crack, even at the back
     of the drum" through this path; the capsule sits in stagnant air outside
     the hot metal. Costs the trier port while mounted; fallback placements are
     the bean-chute plug or under the drop door where chaff falls (~85 % as good).
     This channel is what makes the pop-rate detector easy.
  3. *Piezo contact mic on the chassis*: cheap experiment only; cracks are
     airborne transients inside the drum and the chassis carries drum/bearing
     rumble. Record it if a $10 piezo is lying around, do not plan on it.
  Decided 2026-10-13: Pi 4 + GX10 model + USB→MQTT bridge. Roaster lives in the
  office, wheeled to the porch to roast → Wi-Fi for the Pi.
- **Scale** (optional, later): any BLE/USB scale for green/roasted weights, or
  type the numbers.

## 4. Phases

**Phase 0 — day the roaster arrives (½ day).** udev rule for 0483:a4cd, raw-frame
dumper that logs every 64-byte frame with timestamps to disk. Confirm frame
rate, confirm the A0 decode matches the panel display, watch what
`button_crack_mark` / `fc_number_cracks` / `roasting_method` do during one
manual roast, and confirm the roaster streams unsolicited. This is the
protocol-validation gate for everything else.

**Phase 1 — logger MVP.** Bridge service (pyusb, systemd, auto-reconnect) →
MQTT + SQLite. Roast boundaries from the state machine (charge→roasting =
roast start, →cooldown = drop). Phone PWA: live IBTS/probe/RoR/power/fan/drum/
state/clock, event marks (charge, FC, drop) with the panel FC mark reflected,
roast list, replay chart, CSV export. WAV recording per roast with telemetry-
aligned timestamps. HA entities via MQTT discovery. "First crack marked" on
the kitchen captions + voice (one Node-RED tap, same pattern as announcements).

**Phase 2 — control from the phone.** Power/fan/drum ± and absolute pickers,
preheat setpoint, PRS with confirm, all closed-loop on the reported setpoints.
Big, glove-friendly buttons. Verify step-command reliability (lost packets,
rate limiting; Artisan sleeps 100 ms between steps).

**Phase 3 — FC in shadow.** Pop-rate detector on the Pi + AST on the GX10,
candidates logged against the manual mark, compare view on the dashboard.
Arm auto-declare once it matches within ~10 s on ~5 consecutive roasts.
Fine-tune AST on our WAVs when we have 15–20 roasts.

**Phase 4 — auto-roast.** Replay-by-IBTS → PI tracking → auto-drop, each gated
behind the previous working on real roasts. Profile comparison view (this roast
vs reference, RoR overlay, FC/drop markers, weight loss).

**Phase 5 — polish.** Bean/lot library, roast scoring, Grafana dashboards,
second-crack detector once we have recordings, APK if the PWA is annoying.

## 5. Risks / unknowns

- Protocol was decoded against 2025 firmware; a firmware update could move
  fields. Phase 0's raw-frame logger stays in the bridge permanently (dump on
  decode mismatch).
- Relative step commands + lost packets → setpoint drift. Mitigated by
  closed-loop verification (decision 3).
- USB contention: the Pi owns the device; RoasTime on the laptop requires
  unplugging the Pi. Accepted.
- PRS is a cycle, not a target: a mis-counted press during cooldown walks the
  state machine. `set_state` in Artisan refuses unknown states; ours does the
  same and additionally confirms the new state from the next A0 frame before
  pressing again.
- Outdoor noise (wind, kids, cars) for the mic. The temperature gate + debounce
  handle most of it; the contact-mic experiment is the fallback.
- Pi 4 + WAV + pop detector + MQTT is fine; do not also run the AST on it.

## 6. Trier-port probe mic — shopping list (2026-10-13)

Port size: Bullet owners seat a **20 mm** tapered high-temp silicone plug in the
trier port (stethoscope mod), and 3/4 in OD silicone tube also fits it as a
friction plug. So the port is ≈19–20 mm. Measure on arrival before drilling.

| # | Part | Why | Link |
|---|---|---|---|
| 1 | Swpeet 100-pc high-temp tapered silicone plug kit (1/16–3/4 in) | the stopper; pick the one that seats at 20 mm, drill a hole for the tube | https://www.amazon.com/dp/B07KJYPGT5 |
| 2 | Food-grade silicone tubing, 1/2 in ID × 3/4 in OD, 1 ft | the ~2 in acoustic tube through the stopper (3/4 in OD also works alone as the plug, per owner reports) | https://www.amazon.com/s?k=silicone+tubing+1%2F2+ID+3%2F4+OD+high+temp |
| 3 | Movo M1 USB lavalier, omni electret, 20 ft cable, foam windscreens | the capsule; USB-direct so no dongle/bias questions, confirmed working out of the box on Debian | https://www.amazon.com/s?k=Movo+M1+USB+lavalier |
| 4 | (alt to 3) any 3.5 mm TRS omni lav + Pi-friendly USB audio dongle; PiHut-style cheap CM108 dongles gave the hottest mic drive in BirdNET-Pi testing, UGREEN ones were quiet | only if the Movo is out of stock | https://www.amazon.com/s?k=lavalier+microphone+3.5mm+TRS+omnidirectional ; https://thepihut.com/products/usb-audio-adapter |
| 5 | Short USB-A → USB-C cable, 3 ft | roaster → Pi 4 (R2 may ship one; have a spare) | https://www.amazon.com/s?k=usb+a+to+usb+c+cable+3ft |
| 6 | Lisle 52750 mechanic's stethoscope (optional) | the owners' human-ear version; handy to verify the acoustic path before trusting the mic | https://www.amazon.com/s?k=Lisle+52750 |

Build: pick the plug that seats firmly at the port, drill a hole ~1 mm under the
tube OD, push ~2 in of tube through so the inner end is flush with the stopper
face (nothing into the drum), stuff the lav capsule + foam into the outer end,
strain-relieve the cable on the cart. Capsule ends up 2–3 in outside the hot
metal in stagnant air. Tonor stays as the air mic on the cart; both recorded.

## 7. Field notes + next build (2026-10-27, first real roasts)

**Phase 0 is effectively passed.** Roaster arrived 10-17 with a broken IBTS-data pogo pin; Aillio
replaced the whole Control Module Set (C FW 116 / I FW 28). On the first two seasoning roasts the
server auto-recorded both roasts from the state byte (charge → roasting, PRS → cooldown = drop) and
the **panel F1 first-crack press arrived** as `crack_mark=2` for one frame (roast 2, 203.3 °C, 7:03).
`fc_cracks` / `fc_sample_index` stayed 0. Found while roasting:

- `drum_rpm` (A1) reads 0 at D9 → field unpopulated or mis-offset; don't use it.
- `energy_kwh` decodes as garbage (4294967.5) → probably u32, not f32.
- `inlet/hot_air/exhaust_temp` read 0.0 on the R2.
- First `roasting` frame can carry IBTS 25.0 → `charge_ibts` wrong; take the charge temp from the last `charge` frame.
- IBTS RoR is jumpy (15–29 °C/min while the probe RoR is smooth); display a smoothed RoR.
- Outdoor outlet sags 123 V → 114 V at 11–12 A even without an extension cord.
- Back-to-back starts the next roast at F1, and preheat/charge stop the drum: dump at D9 in cooling, then F1.
- Someone already drove `/api/cmd` from a phone during roast 2 (192.168.10.225).

**Brad's asks (10-27), what exists vs what's new:**

| Ask | Status |
|---|---|
| Phone live chart, RoR, mark FC | EXISTS (PWA :8806); needs smoothed RoR + outdoor/glove-friendly big buttons |
| Thin Android APK | NEW: WebView wrapper, F-Droid publish (same as other com.local.* apps) |
| Green coffee inventory: 3–4 lots, ~20 lb each, photo, pick from a dropdown | NEW: `greens` table (name, origin/region, process, decaf, purchase date, lbs bought, photo) + roast.green_id; remaining lbs = bought − Σ green_g |
| Weigh in / weigh out | Fields exist (`green_g`, `roasted_g`); add a post-roast prompt + weight-loss % |
| Compare stats across lots / varietals / regions | NEW: per-roast metrics (charge temp, TP, dry end, FC temp + time, DTR, drop temp, total time, loss %) + compare/overlay view |
| Maintenance windows by roasted weight | NEW: cumulative green kg from `roasts`; intervals from the R2 manual below; prompt shown **at drop** (the "do it now while it cools" moment), also before preheat if overdue; Pushover |
| Phone alerts during a roast | NEW (Phase 1): Pushover at FC window, drop-temp target, and ~20 s before the 160 °C presence alarm (A-02), with an "I'm here" action that sends a harmless F2-equivalent nudge. Do NOT auto-defeat A-02 silently. Need to confirm a USB command resets the presence timer. |

**Maintenance intervals (R2 Operation Manual pp. 29–31):**

| Interval | Task |
|---|---|
| Every roast session or every 2–3 kg | Empty chaff collector (knock ribs, vacuum plug); check chaff filter, soak + scrub if dark; empty cooling tray + check its filter |
| Every 30 kg | Clean impeller (H2 set screw, aluminum: mild soap only); vacuum under the drum, clear stuck beans. No compressed air at the front (mica tube) |
| Annually | Inspect exhaust pipe (6 front-plate screws), bottle brush; never remove the pipe |
| As needed | Clean IBTS lens when FC temps drift below 195 °C — the app can detect this from logged FC marks |

**Built 2026-10-27 eve (roaster commit 460745d, Android `/home/pi/roaster-android` 94b0297):** everything in
the table above except the "I'm here" nudge. Turning point moved to the probe (IBTS reads the cold beans
at charge and only rises). The presence warning watches real panel presses counted by the bridge; a USB
command as a nudge is still untested and deliberately not built. Details in the roaster README.
