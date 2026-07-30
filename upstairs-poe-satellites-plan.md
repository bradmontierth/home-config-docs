# Upstairs PoE Voice Satellites — Hardware POC Plan

Status: **plan only, nothing built** (drafted 2026-07-30, rescoped same day).

End state: a PoE-powered ReSpeaker in each of the five upstairs rooms that
already have whole-home audio zones (loft, Simon, Claire, master, shower),
streaming audio to the Beelink, with replies coming out of that room's own amp
zone.

**This POC exists to answer one question: can the XVF3800 + ESP32 stack be
given wired PoE Ethernet without the SPI bus and the I2S bus fighting over
pins, and will it stream reliably for weeks?** The software side (server-side
wake detection, in-room reply routing) is understood work and is summarized in
Appendix A, not staged as a proof.

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

### T1 — XIAO in the ReSpeaker socket + bare W5500 module (recommended)

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
  pin-budget question at all.
- **Cons:** ESP32 classic, not S3 — formatBCE's XVF3800 driver targets the S3
  and would need porting/verification. Bigger board. ~$29 vs ~$20.

### Fallback — USB ReSpeaker + PoE splitter + Pi

Zero new firmware, zero soldering; ~$115/room and five more Linux boxes with
five more filesystems to corrupt. Keep it in the back pocket; do not build it
unless all three ESP topologies fail.

---

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
