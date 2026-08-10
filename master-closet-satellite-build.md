# Master Closet Voice Satellite — Build Guide

**Purpose:** stand up Path A of `upstairs-poe-satellites-plan.md` — a voice
satellite in the master closet — on a **freshly flashed** Pi. Self-contained:
everything needed is either in this file, in this repo, or at a path named here.

Status at time of writing (2026-08-07): nothing built. PoE splitter ordered, not
arrived. Pi to be flashed clean with RPi Imager. Plan finalized, no open
questions.

---

## 0. Build in two stages — do not skip to stage 2

**Stage 1 — the Pi answers out of its own USB speaker.** No server changes at
all. Proves mic, wake, stage-2 verify, intent, and reply audio end to end. Runs
entirely on your desk.

**Stage 2 — replies move to the master bath zone.** Orchestrator + Node-RED
work (§6). Only after stage 1 is clean.

Staging matters because stage 1 has zero new server code: if something is
broken, it is the Pi. Debugging a new client and a new reply path at once is how
you end up unable to tell which layer is lying.

---

## 1. Hardware

| Item | Detail |
| --- | --- |
| Board | The freed `.24` kitchen Pi (Pi 4). **Flashing clean**, which removes the old `voice-assistant` + `squeezelite` units — the contention risk with the `.251` kitchen box disappears with the reflash. |
| Mic | The "Jabra knock-off" USB puck. **Confirmed mic-only** — it cannot play the chime. |
| Chime speaker | Cheap Amazon USB speaker already owned. USB so power + audio ride one cable. |
| Network | Cat6 drop to the closet shelf + 802.3af splitter (ordered). Until it arrives, ordinary Ethernet or Wi-Fi at the desk is fine. |
| Shelf | Already holds the TRC metal PSU enclosure and the LED controller; mains present. |

**Nothing needs to be purchased.**

---

## 2. Flash and base OS

Raspberry Pi OS (64-bit). In RPi Imager's advanced options set hostname,
user `pi`, your SSH key, and locale. Hostname: `master-closet-assist`.

### SSH access (done 2026-08-07)

The board keeps its DHCP reservation across a reflash — same NIC MAC
`e4:5f:01:67:1e:56`, so it comes back at **192.168.10.24**.

Per house convention (one dedicated keypair per host) the alias is
**`master-closet-assist`** → `~/.ssh/id_ed25519_master_closet_assist`, and the
Pi's own hostname is `master-closet-assist`. Passwordless from the Beelink,
confirmed 2026-08-07.

The old `kitchen-speaker` entry on this same address was **deleted**. It was
already recorded as a trap — it pointed at the powered-off old kitchen Pi while
the live kitchen box is `big-speaker-mini-pc` (192.168.10.251). Stale docs that
still say `ssh kitchen-speaker` (e.g. `voice-assistant-plan.md:898`) now fail
loudly rather than silently connecting to the wrong machine.

The flash also replaced the host key, so the stale `known_hosts` entry for
`.24` was removed (`ssh-keygen -R`).

### One-command provision

`home_config/voice-assistant/satellite/provision-satellite.sh` does everything
in §3 — packages, venv with pinned versions, code, sounds, models, service unit,
and a seeded `.env`. Run it **from the Beelink**:

```bash
/home/pi/home_config/voice-assistant/satellite/provision-satellite.sh master-closet-assist master
```

It deliberately stops short of two things it cannot know: the ALSA device names
(it prints the listings for you) and enabling the service. Re-running is safe,
and it never overwrites an existing `.env`. The rest of §3 documents what it
does, and is the fallback if you would rather do it by hand.

Confirm the mic and the speaker enumerate, and capture their ALSA names — you
need these for `.env`:

```bash
arecord -l && arecord -L | grep -i plughw   # mic
aplay   -l && aplay   -L | grep -i plughw   # USB speaker
arecord -D plughw:CARD=<MIC> -f S16_LE -r 16000 -c 1 -d 3 /tmp/t.wav
ls -l /tmp/t.wav      # ~96KB for 3s mono@16k. A 44-byte file = header, no samples.
```

A 44-byte WAV means the device opened but never streamed — that is a capture
failure, not a quiet room.

---

## 3. Install the satellite

```bash
mkdir -p /home/pi/voice-pipeline/{data,sounds}
python3 -m venv /home/pi/voice-pipeline/.venv
/home/pi/voice-pipeline/.venv/bin/pip install numpy onnxruntime livekit-wakeword
```

### Files to copy

From the **Beelink** (`192.168.10.217`), all paths absolute:

| Source | Destination on the Pi |
| --- | --- |
| `home_config/voice-assistant/satellite/assistant.py` | `/home/pi/voice-pipeline/assistant.py` |
| `home_config/voice-assistant/satellite/sounds/` (recursive) | `/home/pi/voice-pipeline/sounds/` |
| `home_config/voice-assistant/satellite/voice-assistant.service` | `/etc/systemd/system/voice-assistant.service` |
| `/home/pi/backups/pw_pi-20260728/blobs/okay_computer.onnx` | `/home/pi/wake-bench/okay_computer.onnx` |
| `/home/pi/backups/pw_pi-20260728/blobs/okay_google.onnx` | `/home/pi/wake-bench/okay_google.onnx` |
| `/home/pi/backups/pw_pi-20260728/blobs/silero_vad.onnx` | `/home/pi/voice-pipeline/silero_vad.onnx` |

**The `.onnx` models are not in git.** The backup kit above is the local source
of truth on the Beelink; the live copies are on `.251` in `~/wake-bench/`.
Verified 2026-08-07 — all three are byte-identical to what the kitchen runs
today (`okay_computer` `91c922ad…`, `okay_google` `633d08aa…`, `silero_vad`
`302cb198…`), so the July backup is not a stale fork. The
`stop.onnx` model in that kit is alarm-dismiss only and is **not needed here** —
alarms ring in the kitchen (orchestrator `SATELLITE_ALARM_URL` → `.251`), and
`assistant.py` logs "no stop model … ASR-only" and carries on without it.

The service unit is generic (`WorkingDirectory=/home/pi/voice-pipeline`,
`EnvironmentFile=-/home/pi/voice-pipeline/.env`) — only its `Description`
mentions the kitchen. No edit required.

### `.env` — `/home/pi/voice-pipeline/.env`

Not in git, by design. Stage-1 values:

```ini
SATELLITE_ID=master
ORCH_BASE=http://192.168.10.217:8785
MIC_DEVICE=plughw:CARD=microphone,DEV=0
PLAYBACK_DEVICE=plughw:CARD=Device,DEV=0
MODEL_PATHS=/home/pi/wake-bench/okay_computer.onnx,/home/pi/wake-bench/okay_google.onnx
SILERO_MODEL=/home/pi/voice-pipeline/silero_vad.onnx
SILERO_THRESHOLD=0.4
HOP_MS=320
ORT_THREADS=2
MODE=active
```

**Resolved devices (2026-08-07).** Both are USB, so ALSA card *names* are used
rather than numbers, which reorder across reboots.

| Role | Hardware | ALSA card |
| --- | --- | --- |
| Mic | TONOR G11 (`0d8c:0134`, C-Media) — "the Jabra knock-off" | `microphone` |
| Chime speaker | GEMBIRD Buildwin Media-Player (`1908:2220`) | `Device` |

The speaker's card name is the generic string **`Device`** — it comes straight
from a lazy USB descriptor. It is unambiguous today, but plugging in a second
no-name USB audio dongle would collide. If that ever happens, pin it with a
udev rule on `1908:2220` rather than renumbering.

Its mixer exposes a single joined-mono `PCM` control (range 0–240, set to 75%).
The chime is 44.1 kHz stereo and this dongle is mono — `plughw` (not `hw`)
does the resample and downmix, which is why the prefix matters here.

Rationale for the two tuning values, both inherited rather than invented:
`SILERO_THRESHOLD=0.4` is the kitchen's setting after the oven-corner
command-capture miss. `HOP_MS=320` is the family-room "relaxed cycles" value —
a conservative starting point, not a ceiling.

**On `HOP_MS`, corrected 2026-08-07.** The kitchen runs `HOP_MS=192` today
(verified in `.251:~/voice-pipeline/.env`), not the 224 recorded earlier. The
"192 is unsafe, thermal soft-limit 81–82 °C" conclusion was measured on *this
very board* — and Brad had removed the fan from its case. So the limit was a
missing 5 mm fan, not a Pi 4 ceiling. Two consequences:

- Do not treat 81–82 °C as a property of the hardware. It was an airflow defect.
- The **PoE hat ships with a fan**, so the permanent closet install has cooling
  the desk test does not. Start at 320, and once the hat is on, 224 or 192 is
  fair game — walk it down while watching
  `vcgencmd measure_temp` and `vcgencmd get_throttled` (expect `0x0`).

Detect lag is roughly linear in hop: the kitchen A/B measured 594–735 ms at the
old default versus 441–470 ms at 224. Worth reclaiming, but it buys latency
only — it does not improve wake accuracy, so it is a polish step, not a gate.

Do **not** set `PLAYBACK_RELAY_URL` — that is the family-room mic-only pattern
that relays audio to the kitchen. This box plays its own chime locally.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now voice-assistant
journalctl -u voice-assistant -f
```

---

## 4. Stage-1 verification

```bash
curl -s localhost:8781/health                                   # mode + liveness
curl -s -X POST localhost:8781/trigger                          # forced turn, no wake word
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"mode":"shadow"}' localhost:8781/mode                 # kill switch
```

Wake review page for this box's own clips/events:
`http://<pi-ip>:8781/review`.

Pass criteria: say "okay computer, what's the weather" → chime out of the USB
speaker → spoken answer out of the USB speaker. On the Beelink,
`docker logs voice-orchestrator` should show `verify sat=master`.

**Arbitration is fine with three mics.** `_ARB` (`app.py:160`) is one
house-global holder with `ARB_SUPPRESS_S=3`; the kitchen and the master closet
are far enough apart that one utterance never reaches both. Do not add a
second mic *within earshot* of this one without doing Appendix A item 5
(proximity groups) first.

---

## 5. Resolved entity reference

Everything below verified live on 2026-08-07. **Do not infer room membership
from entity ids in this house** — use Dashy (`lovelace.dash_with_sections`,
Upstairs view, sectioned per room) and verify against the HA API.

### Audio targets (Music Assistant player ids — this is what the amp path uses)

| Room | MA player id | HA entity |
| --- | --- | --- |
| Master bath / shower | `ma_shower` | `media_player.shower` |
| Master bedroom | `ma_master_bedroom` | `media_player.master_bedroom` |
| Loft (test target) | `ma_loft` | `media_player.loft` |
| Simon's room | `ma_simon_room` | `media_player.simon_room` |

The HA entities were renamed 2026-08-07 from `speaker_pi` / `speaker_pi_2`.
The announce path addresses MA player ids and never resolves an HA entity —
the entity strings in `MA_PLAYER_MAP` are lookup keys only.

### The five lights (one group command — they move in tandem)

| # | Light | Entity | Control path |
| --- | --- | --- | --- |
| 1 | Closet LEDs | `light.closet_leds` → `light.closetleds_4` + `_5` | ESPHome `closetleds` |
| 2 | Ceiling bulb | `light.master_closet_light_switch` | Zooz via Hubitat device 44 |
| 3 | Vanity (3) | `light.closetleds_1` / `_2` / `_3` | same ESPHome controller; subflow + rotary encoder w/ press |
| 4 | Toilet | `light.master_toilet_light` | zigbee |
| 5 | Bath floor RGB | `light.masterbathfloor_rgb_light` | ESPHome `masterbathfloor` |

Motion: Hubitat device 911, on the Node-RED **"Upstairs Bathroom"** tab.

### Covers

- `cover.upstairs_bath_blind` — master bath blind (confirmed; voice-wired
  2026-08-07 as `blind_bath_close` / `blind_bath_open`)
- `cover.boys_room_baby_blind` — Simon's blind (confirmed)

### Other

- `input_boolean.simonalarm` — Simon's room armed flag (Path B gate)
- Voice buttons follow `button.voice_*`, Node-RED tab **"Voice Buttons"**

---

## 6. Stage 2 — backend work

### Reply routing — SHIPPED 2026-08-07 (commit `5972476`)

Items 1–3 below are done. What was built:

| Piece | Where |
| --- | --- |
| `sat → {rooms, voice, volume}` table | `orchestrator/satellite_zones.json`, hot-reloaded by `orchestrator/zones.py` |
| Routing decision | `_finalize()` in `app.py`; `sat` arrives via the `_CUR_SAT` ContextVar |
| `voice` passthrough | `broadcast.send()` |
| Live table | `/data/satellite_zones.json` (seeded from the image copy) |

`master` routes to `["shower"]`, voice `fast:doorbell`, **volume 30**. 50 was
measured too loud in the bath on 2026-08-07 — the amp runs hot gain on that
zone, so the fix is a quieter announcement rather than re-gaining the amp.

**Compose env var — not in git.** `/home/pi/voice-pipeline/docker-compose.yml`
is not inside any repo, so this line exists in exactly one place on disk:

```yaml
- SATELLITE_ZONES_FILE=/data/satellite_zones.json
```

Without it the orchestrator reads the read-only copy baked into the image,
which still *works* but cannot be edited without a rebuild.

Two properties worth keeping if this is ever refactored:

- **The satellite needs no code change.** `assistant.py:811` plays a reply only
  when `audio_url` is present, so omitting it is the entire opt-out. The wake
  chime is a separate `play_file()` and keeps sounding locally — that is what
  gives "ding on the USB speaker, answer on the bath speakers" for free. The
  ask filler stays local too, which is desirable: instant acknowledgement.
- **Zone failure falls back to the satellite.** If HA/MQTT is unreachable,
  `_finalize` renders locally instead. A reply from the wrong speaker beats
  silence.

### Amp pre-wake — SHIPPED 2026-08-07

Node-RED tab **Voice Broadcast** (`e3a9d4391d545738`) gained a second chain on
topic `voice/amp_wake`: *mqtt in → Pre-wake: decide + ungroup → isolate →
build wake request → MA API*. Node ids are prefixed `vbwake`.

The orchestrator publishes it at stage-2 verify so the amp finishes waking
under the ASR + intent + TTS time instead of in front of the reply.

**Why it was needed.** The Amp Speakers subflow skips its 3 s wake gate while
`wholeHomeAmpLikelyOn` is true, and that global is only cleared after 14 min
idle. On 2026-08-07 a weather reply at **11.5 min idle** lost its opening
entirely — only the snapcast tail padding was audible. Replies at 2.7 min and
0.3 min idle were fine. The Dayton MA1240a manual says "approximately 15
minutes", with no published tolerance, so 11.5 min is not a fault; **14 was
simply never a safe threshold.**

The confidence window is therefore **10 min**, measured not spec'd:

| Idle | Behaviour | vs before |
| --- | --- | --- |
| < 10 min | no wake | unchanged |
| 10–14 min | wake | **the only new chimes** — and exactly the window where a reply is currently swallowed |
| > 14 min | wake | no extra chime; the subflow would have woken it anyway, just ~6 s later |

That last column matters: the wake chime is loud by design (it has to wake the
amp every time), so a too-eager window is a real annoyance. Tune via the
`ampPreWakeConfidenceMs` global without editing the flow.

### Self-hearing mute — SHIPPED 2026-08-07

Zone routing removed the local playback that used to keep the mic busy, so
`run_followups()` opened the mic while the answer was still coming out of the
walls. The closet satellite transcribed two of its own weather answers and
dispatched them as follow-up commands — proven by the broadcast log's `len=`
matching the follow-up transcript exactly (44 and 62 chars, verbatim).

`_finalize` now returns `mute_ms` on zone-routed replies and
`wait_out_zone_reply()` (satellite) sleeps it out then drains the mic. Absent
for locally-answering satellites, so it is a no-op there.

Estimated from text length because the render happens in Node-RED: 12 chars/s
(below the measured 12.8–14.1) + 2000 ms lead + 800 ms margin. The snapcast
tail padding is deliberately **excluded** — it is silence, and muting through
it would only delay a legitimate follow-up. ~6.5 s for a typical weather reply.

### The follow-up window needs a VISUAL cue, not an audible one

Open item, no code yet. A satellite in a room with no display leaves the user
talking into a mic with no way to know it is listening — the satellite posts
`/session/listening` for a "Listening…" badge, but that only reaches the
kitchen dashboard.

An audible tap was tried and removed the same day (2026-08-07). Two reasons it
cannot work as built:

1. The follow-up loop also spins on echo-rejected rounds, so taps landed
   **mid-reply** and read as random noise. Brad heard three per turn.
2. Placing it correctly would require knowing when the zone audio actually
   finishes — precisely the timing we deliberately stopped predicting when the
   estimated blackout was replaced with echo rejection.

**A small RGB LED on the satellite is the right answer** (Brad's call): it
carries state continuously rather than as an event, so it cannot land at the
wrong moment, and it costs nothing when you are not looking at it. Path B's
HAVPE has an LED ring natively — same problem, same shape of solution.

### Remaining items

Full rationale in `upstairs-poe-satellites-plan.md` Part 1. Items 4–5 below are
still open; 1–3 are superseded by the table above and kept for context:

1. **Per-satellite target map.** `sat_id → {ma_player, ha_player, playback,
   music_policy, quiet_hours}`, hot-reloaded like `home_commands.json` /
   `broadcast_rooms.json`. Point `master` at `ma_loft` while developing, so
   testing does not disturb the master bedroom.
2. **Reply routing — reuse the broadcast chain; no Node-RED change.**
   Verified live 2026-08-07. `POST :8785/broadcast` → MQTT `voice/broadcast` →
   the *Voice Broadcast* tab's resolver → **Amp Speakers**. The resolver
   already forwards a voice (`if (typeof p.voice === "string" && p.voice)
   msg.voice = p.voice;`), and the subflow already honours `msg.voice` and
   `msg.volume`. So the orchestrator only has to send:

   ```json
   {"rooms": ["shower"], "message": "...", "voice": "fast:doorbell", "volume": 50}
   ```

   The **only** code change is a `voice` passthrough in `broadcast.send()`
   (`broadcast.py:110`), which today takes just `(rooms, message, volume)`.
   Still kick the amp wake at **stage-2 verify** so the 3 s cold-amp gate
   elapses under ASR + intent + TTS.
3. **`forceBedroom` is not needed — do not implement it.** The subflow filters
   exactly one string, `MASTER_BEDROOM_PLAYER = "media_player.master_bedroom"`
   (verified in the live flow). `media_player.shower` → `ma_shower` is never
   touched, and the master bath speakers are the intended target anyway.

   Beyond being unnecessary, `forceBedroom: true` would be *wrong*:
   `adrienneWorkingDisableAnnounce` keys off the power draw of Adrienne's
   monitor and exists precisely to keep the bedroom quiet while she works.
   Overriding it would make the closet satellite the one device in the house
   that ignores it. Route to `shower` and the flag is respected for free.
4. **Per-room music policy.** `music.py` targets the kitchen queue
   unconditionally (`config.py:239`) and shuffles artists/playlists
   (`music.py:568`). Needs `{queue_id, single_track}`.
5. **Playback muting.** Suppress this room's detection for the reply duration
   + ~500 ms. Build it now, but see §7 — it cannot be tested from the desk.

### TTS voice — pass `msg.voice`, that is all

**`tts.openai` is not OpenAI.** It is only the *name* of the HA integration;
it points at the local **tts-router** on the Beelink (`:8891`), which forwards
to the GX10. Nothing in this path leaves the house. (An earlier draft of this
guide called it "cloud" — that was wrong.)

Kokoro is reached through that same entity by voice prefix: `main.py:1186`
treats a `fast:` / `kokoro:` base voice as forced-Kokoro. So sending
`voice: "fast:doorbell"` through the broadcast chain gets the house Kokoro
voice with **no subflow edit** — it is the same fast path the doorbell
announcements already use in production.

The subflow's defaults (`tts.openai`, `picard:calm`) apply only when nothing
overrides them; `msg.voice` wins. Do **not** chase the `tts.kokoro` HA entity —
it was `unavailable` as of 2026-08-07 and is not needed. Earlier drafts
proposed an `msg.ttsUrl` bypass so the orchestrator's already-rendered audio
could be played directly; that is unnecessary complexity given the above, and
has been dropped.

### The bath blind — SHIPPED 2026-08-07

Two lines of Node-RED and three table entries. On the **Voice Buttons** tab
(`294429bac2b766ff`), `cover.upstairs_bath_blind` joined the existing
`blind cmd -> covers` map as `blind_bath`, and the discovery function gained
`blind_bath_close` / `blind_bath_open`. No new nodes, no new routing — the
`cont "/blind"` router rule already matches the new topics.

The real work was on the orchestrator side, below.

### Room-scoped home commands — SHIPPED 2026-08-07

The alias table was flat and satellite-blind, which does not survive a second
room. Standing in the master bath, "close the blinds" scored 100 against the
kitchen's `blinds_all_close` and would have shut four blinds downstairs.

`home_commands.json` entries may now carry `"sats": ["master"]`, and
`_match()` takes the satellite id (threaded from `_CUR_SAT` at
`app.py:822`). Matching runs the room first and takes any hit outright,
falling back to the house-wide table only on a miss — so a local phrase can
never lose a three-point fuzzy race to a near-identical command elsewhere.

Naming a room overrides the room you are standing in (`_ROOM_WORDS`), which
matters more than it sounds: without it, "close the kitchen blinds" said in
the bathroom scores exactly 80 against the bath blind's own "close the
blinds" and shuts the wrong one. With it, every blind in the house is also
reachable by name from anywhere.

The upshot is that the *same* natural phrase means the local thing in each
room, instead of one room being forced into an awkward paraphrase. Aliases
may now legitimately be shared across rooms, so `add_alias()` only rejects a
duplicate when the two commands are reachable from the same place.

### The "keep the lights on" hold — SHIPPED 2026-08-07

Say **"okay computer, keep the lights on"** in the master bath or closet.
30 minutes, spoken back in the confirm.

**One guard blocks every off-path.** This looked like it needed four gates —
closet motion (Hubitat 911), `binary_sensor.master_bath_motion_occupancy`,
the shower sensor, the fast EPP presence path. Reading the flow, all of them
already converge: the two Hubitat cascades and the EPP direct path all land on
`388a70141d329ccd` (*MasterBathOverride true else*) and fan out at
`8976bc84753a1f6b`. `mbhold_guard_v1` sits between the two, so there is
exactly one place an off can be suppressed and exactly one place to look when
it misbehaves.

**The guard checks a deadline, not a flag.** Node-RED persists globals to the
local filesystem (`settings.js` `contextStorage`), so a `masterLightsHold`
boolean stuck true would survive restarts and mean bathroom lights that never
turn off again — the same failure mode as the EPP static-presence stick. The
authority is `masterLightsHoldUntil`, a timestamp, which can only expire.

**Expiry rechecks; it does not turn anything off.** Someone may still be in
the room — that was the point of the hold. `mbhold_expire_v1` re-enters the
flow's *own* all-clear cascade at `98cefd2e34906dab`, which polls all five
motion sensors plus the HA occupancy sensor and starts the normal off timer
only if every one is quiet. If anybody is still there it simply stops.

**It also turns the lights back on**, which reverses the design note that
previously sat here ("suppress OFF; never force ON"). Brad's framing settled
it: the lights have usually *already* gone out by the time you say this, and
walking back into the closet sensor's view to undo it is the entire
annoyance. `mbhold_arm_v1` output 1 enters the flow's motion-on junction
(`02550db2a14d02e2`), so brightness, colour temp, the `bath` already-on
dedup and the Sleeping In scene all behave exactly as if someone had walked
past a sensor. Nothing about "on" is reimplemented.

**A manual off cancels it.** The Lutron wall switch going off
(`96bd397d9f52ce9d` out 1) and the closet switch going off
(`26126472f537dbe3` out 1, previously unwired) both clear the hold and stop
the timer. Saying it again re-arms the full 30 minutes.

Nodes: `mbhold_mqtt_v1` → `mbhold_arm_v1` → `mbhold_timer_v1` →
`mbhold_expire_v1`, plus `mbhold_cancel_v1` and `mbhold_guard_v1`. MQTT
topic `voice/button/master_lights_hold`, consumed directly on the Upstairs
Bathroom tab — no cross-tab link nodes, so no one-sided `link out` to get
wrong.

Still open: **light #5**, the bath floor RGB — nobody has confirmed it should
follow the group at all hours.

### Timers know their room — SHIPPED 2026-08-07 (half of it)

Timers predated there being a second satellite, and it showed: the `timers`
table had no room column and `SATELLITE_ALARM_URL` was a single global env
var. A timer set in the master bath rang in the kitchen.

Now every timer records the satellite it was set from, and
`zones.host_for(sat)` addresses the ring — so a third satellite (Simon's
room) is a table edit, not a code change. An unlisted satellite falls back to
the old env var rather than going silent.

**A stop only reaches the room it was said in.** Silencing an alarm you
cannot hear, in a room you are not standing in, is worse than missing the
stop — so unlabelled `stop`/`cancel` and `dismiss_any_ringing` are scoped to
the speaker's satellite. The deliberate escapes are naming the timer
("cancel the pasta timer" searches your room first, then the house) and
"cancel all timers", which is house-wide by definition. `/alarm/stop` takes
an optional `sat` and defaults to `DEFAULT_SAT` (kitchen) because its one
caller is the kitchen touchscreen. Covered by `test_timer_rooms.py`.

### Ringing through the bath zone — BUILT 2026-08-07, loft-proven

Measured against MA 2.6 on the loft player before writing a line of it. All
four findings changed the design:

| Probe | Result |
| --- | --- |
| `play_announcement` on a 60.0s clip | **blocks** 60.6s — we get control back per chunk, no polling |
| `players/cmd/stop` 5s into a 60s clip | **does not interrupt**; clip ran to completion |
| second `play_announcement` during one | **queues behind**, does not preempt |
| `players/cmd/volume_set 0` mid-announcement | **takes effect instantly and holds** |
| volume after MA's own announcement ends | **not restored** once we have set it by hand — loft ended at 0 having started at 50 |

So a single 40-second ring track is out: it would be an alarm that ignores
"stop" for up to 40 seconds. The shape that survives contact:

- **Pre-rendered chunks**, 3 cycles ≈ 7.6 s each, 5 chunks per ring. Static
  per theme (not per timer), cached in the announce dir and served by the
  existing `/audio/{name}` route because MA fetches the URL itself.
- **A dismiss mutes the player**, which is the only thing that actually
  silences a room mid-announcement. Chunking is *not* what makes the stop
  fast — it bounds how long a dismissed alarm keeps the announce slot busy,
  so a doorbell ten seconds later is not stuck behind a corpse.
- **Restoring the volume is ours**, and it must wait for MA to finish. The
  first loft run restored immediately on dismiss, which undid our own mute:
  the room came back to full volume and kept beeping for ~8 s — precisely the
  sound the user had just asked to stop. `_settle()` polls
  `announcement_in_progress` before restoring.

Loft run after the fix: dismiss → `vol=0` within 1.2 s, announcement ends at
2.5 s, volume back to 50 at 3.7 s, everything idle. Tested at volume 5, which
MA floors to 15.

`steam_whistle` is deliberately not among the orchestrator's themes — it is a
recording of a person whistling and is unsettling coming out of the walls of a
dark bathroom. A timer carrying that theme rings in the zone as marimba.

The satellite still gets its `/alarm` POST, now with `playback: "zone"`: it
runs the dismiss listener, the biased ASR client, the unattended watchdog and
the confirmation chirp, and only suppresses the ring audio. The loop becomes
the timekeeper for a sound playing somewhere else.

#### First live bath test, 2026-08-08 — three bugs, all fixed

**1. The alarm dismissed itself on its own announcement.** The closet mic
hears the zone announcement and Parakeet mangles it: `"You're tired."`,
`"Your time."`, `"You're turn it off."` — and that last one contains a bare
`off`, which is a dismiss word. The ring died three seconds in. `_dismiss_in`
even documents the assumption that broke: *"the announcement never otherwise
contains these words."* True when the announcement came from the satellite's
own speaker at a known level; false once it comes off big speakers across a
doorway.

Fix: a zone ring starts with the satellite's dismiss listener **disarmed**,
and `zone_alarm` arms it (`POST /alarm/arm`) the moment the announcement's
`play_announcement` returns — the orchestrator is playing it, so it knows
exactly when its own voice stopped. The satellite self-arms after
`DISMISS_ARM_CAP_S` (12s) if that call never comes, so a broken orchestrator
costs a little self-dismiss risk, never an unstoppable alarm.

Two follow-ons were needed before it held:
- **The ASR window is 2.5s and rolling**, so the first decode after arming
  still contained the announcement. Arming now also flushes it.
- **MA reports an announcement finished ~1.5s before the sound leaves the
  room** (snapcast buffering plus the amp path's tail padding), so
  `ARM_SETTLE_S` waits that out first.

Result: a 36-second ring ran to completion untouched, with the garbled
transcripts landing pre-arm and correctly ignored.

**2. `_settle` restored the volume during a gap between chunks.** One clear
poll is not enough — the silence between chunks reads as quiet. It restored
1.0s after a dismiss instead of 8.2s, the next chunk started, and MA then
re-asserted a volume it had captured during our mute. Now requires two
consecutive clear polls.

**3. MA's `volume_level` for these players is unreliable.** `ma_shower`
reported `0` for hours while the snapclient was really at 20 — and MA kept
reporting 0 even as its own `volume_set` correctly moved the client from 20 to
35. Restoring MA's number would have left the bathroom permanently silent.

The whole-home players are snapcast clients on an **external snapserver at
192.168.10.140:1705** (`snapcast_use_external_server` in MA's provider
config), and that is the source of truth. `_resting_volume()` reads it first,
falls back to MA, then to the configured level — and never returns zero,
because a room handed back at zero is a room that never speaks again.

Verified live: ring, dismiss at 7s, volume back to 20 on the snapserver 5.1s
later.

**Still unverified: whether 45 is loud enough over a running shower**, and the
voice `"stop"` path (all dismiss tests so far were via the API).

**Superseded note: the ring was on the wrong speaker.** A master-bath timer now
rings on the closet Pi's little USB speaker, which is almost certainly
inaudible from the shower. Moving it to the bath zone means the orchestrator
driving the loop itself — MA `play_media` of the theme WAV on `ma_shower`
every `ALARM_GAP_S`, behind an `amp_wake`, until dismissed — because the
Amp Speakers subflow takes *text* and plays once, and the satellite's own
loop (announce-once, dismiss listener, unattended watchdog) is local-audio
only. The orchestrator already serves the WAVs at `/sounds/{name}` and
`/audio/{name}`, so MA can fetch them.

### The 15-minute Day/Evening timeout — measured, and it was NOT the answer

Recorded here earlier as "Day/Evening lights linger 15 minutes". Wrong in
practice, and Brad's contrary observation was right. Measured 2026-08-07 from
HA history, mmWave clearing → closet light off:

| mmWave off (UTC) | Light off | Gap | Path |
| --- | --- | --- | --- |
| 12:43:51 | 12:59:08 | **15m 17s** | norm (Hubitat cascade) |
| 13:48:15 | 13:58:31 | **10m 16s** | `MasterBathOverride` +10 min |
| 14:07:26 | 14:07:42 | 16s | EPP direct |
| 14:10:24 | 14:10:40 | 16s | EPP direct |
| 15:17:54 | 15:18:10 | 16s | EPP direct |
| 16:31:11 | 16:31:27 | 16s | EPP direct |
| 17:49:20 | 17:49:36 | 16s | EPP direct |

All three paths are real, but the **EPP direct path wins 5 times out of 7**:
it checks only the two EPP sensors and waits 15 s, so it beats both Hubitat
cascades to the punch. The 15-minute misnaming is a genuine bug but mostly
academic — it only surfaces when the EPP path is blocked.

The same history shows the false-off signature plainly: off at 14:07:42 then
back on at 14:07:59 (17 s), and again at 01:44:14 → 01:44:32 (18 s),
01:45:07 → 01:45:23 (16 s).

### The all-sensor two-minute path — SHIPPED 2026-08-09

The adaptive false-off ladder shipped on 2026-08-07 treated the symptom but
left the real defect in place: the EPP direct shortcut checked only the EPP PIR
and EPP combined mmWave. It bypassed the external bath, closet, and shower PIR
current-state checks, so one of those PIRs could be active while the shortcut
turned every light off.

The direct shortcut and `mbladder_*` nodes are now removed. EPP PIR-off and
mmWave-off events enter the same existing chain as the other motion sensors:

`Master Bath Motion Switch` → HA master-bath occupancy → closet PIR → shower
PIR → EPP PIR → EPP combined mmWave → two-minute timer → shared hold/off guard.

Both Day/Evening and Early-Morning/Night use two minutes; the former timer is
configured in minutes (`msg.delay = 2`) and the latter in seconds
(`msg.delay = 120`). Any active sensor aborts the current-state chain, and any
new activity hits the shared `STOP` fan-out. The prior accidental 15-minute
Day/Evening delay and 15-second Early/Night delay are therefore both gone.

The EPP static trigger range was initially raised from 6.0 to 9.5 m with a
10.3 m maximum, and combined occupancy timeout was raised from 15 to 120 s.
This lets the long-range SEN0609 reacquire someone in the closet and gives
brief radar dropouts two minutes before Node-RED even sees mmWave clear.
Static trigger/sustain sensitivity was subsequently raised from 5/7 to 6/8
for the same closet test; the earlier static false-presence problem has not
recurred since the EPP was moved away from the PWM LED fixture.
That combination covered the toilet but still cleared in one closet spot, so
the static maximum/trigger pair was raised once more to 12/11 m.

---

## 7. What the desk cannot prove

Do not mistake a clean desk run for a finished satellite:

- **The self-hearing loop.** At the desk the mic never hears its own reply. In
  the closet it will, off the ceiling, with no AEC reference. §6 item 5 is
  written against a problem that is invisible until the Pi moves.
- **Mic performance at closet distance** — the entire premise of using the
  Jabra there.
- **The five-light hold under real conditions** — the failure is standing still
  while folding laundry.
- **PoE**, pending the splitter.

## 8. Gotchas

- **Repeated announcement testing wedges MA.** `announcement_in_progress`
  sticks true and presents as silent audio with no error. If the test zone goes
  quiet mid-session that is the wedge, not your code; restart
  `music-assistant-server`. Watchdog shipped 2026-07-29.
- **`data/flows.json` on the Beelink is stale** (`nodered-flow-agent-guide.md:48`).
  The Node-RED Admin API on `127.0.0.1:1880` is the source of truth.
- **`home_config` shares one git index across concurrent sessions.** Commit by
  explicit path; never `git add -A`.
- **Never pattern-kill** (`pkill uvicorn/python/node`) on the main Pi. Kill by
  exact PID.
- Keep `assistant.py` in lockstep across boxes — the same file runs on `.251`,
  on pw_pi, and here.

---

## 9. The amp, measured (2026-08-08 evening)

Brad's first live timers in the bath: the confirmation was never heard at all,
then a second one came out as "timer set fo…" and cut. Both rings drew a
"Kitchen timer unattended" push. Three separate causes.

### 9.1 The amp is asleep and we tell ourselves it isn't

Measured on the shower zone by recording the closet mic (borrowing the TONOR
with `voice-assistant` briefly stopped) and running the capture through
Parakeet:

| Condition | Heard in the room |
| --- | --- |
| Reply into a cold amp, no wake tone | **nothing** — empty transcript |
| Same reply 2s later (the first woke the amp) | "Timer set for five seconds" |
| Wake tone, then a reply at +90s / +150s / +210s | complete, all three |
| Wake tone, then a reply at +330s | **nothing** |
| Wake tone to the **loft**, reply to the **shower** | complete |

Four things fall out of that table:

1. A reply into a cold amp is not clipped, it is **silent end to end**.
2. **One shared amp** — waking any zone wakes it for all of them.
3. The hold is **~5 min**, not the 15 the MA1240a manual claims, and not
   stable: 11.5 min failed on 2026-08-07, 5.5 min fails now. §5's 10-minute
   window was tuned against a number that moves.
4. A quiet TTS reply does not reliably wake it. The wake tone is loud on
   purpose.

Brad's failing test: the last amp audio was a **Loft** announcement 7.4 min
earlier, so the pre-wake said "fresh — no wake" and the Amp Speakers subflow
independently skipped its wake tone *and* collapsed its 3s gate to 1ms.

**And the belief was self-reinforcing.** `Prepare ungroup + wake + TTS` ran
`node.send([null, null, ampState])` unconditionally, so a reply that went into
a sleeping amp — and was never heard — renewed "amp is awake" for another full
window. Once wrong it stayed wrong as long as you kept talking, which is why
the second test failed on the back of the first.

### 9.2 What the room actually heard was never truncated

Worth recording because the obvious theory was wrong. The reply file is intact
(**1.61s of speech in a 5.52s padded file**), and MA **serialises**
announcements per player — it started the ring at reply-start + 5.82s, i.e.
the instant the 5.52s file ended. The ring cannot have stepped on the reply.
Four for four, with the amp awake, the room got the whole sentence. The
"cut off" did not reproduce; best remaining theory is the amp dropping out
mid-phrase as its hold expired, which §9.4 makes moot.

The alarm side genuinely was unpadded, though: `announcement.wav` shipped
**1.46s total with 0.36s of tail** against 3.89s for everything that reaches
these speakers through Node-RED's pad service.

### 9.3 The unattended alert

Timing was correct — 15.5s and 15.3s, measured. It only felt instant because
the first ~5s of ring was inaudible. The bugs were the hardcoded "Kitchen" in
`app.py`, and that a zone ring spends its first ~4.4s on the announcement and
the settle, so a 15s watchdog fired after ~11s of anything a person would call
ringing.

### 9.4 Shipped

**Node-RED** (deployed via a nodes-only `POST /flows`; subflows have no
`/flow/:id` route, so the whole-flows deploy is the only way in — 7196 nodes
verified intact afterwards):

- `Pre-wake: decide + ungroup` (Voice Broadcast) — `CONFIDENCE_MS` 10 min →
  **150s**, half the measured hold, with the table above in the comment.
- `Prepare ungroup + wake + TTS` (Amp Speakers subflow) — the amp-awake belief
  now **expires** on the same shared `ampPreWakeConfidenceMs` global instead of
  being a bare boolean, the timestamp is stamped **only when a wake actually
  fires**, and one `willWake` decision drives both the chime and the gate.
  Those used to disagree: `forceWake` played a tone but still set the gate to
  1ms, landing the forced wake under its own announcement.

**Orchestrator** — `WAKE_GATE_S = 1.0` between the amp wake and the first
announcement (we POST MA directly while the wake goes MQTT → HA → Node-RED →
isolate → MA; measured 11ms, but lose that race and the announcement plays into
a sleeping amp with the wake tone queued behind it); `padded_announcement()`
adds 2.0s of real tail silence for the zone path only; `zones.spoken_for()`
names the room in the phone alert.

**Satellite** — the unattended watchdog starts at the **arm** for a zone ring,
which is exactly when the beeps start.

### 9.5 Two traps found while building it

- **Our TTS writes a streaming WAV header**: `nframes = 2147483647`, the
  "length unknown" sentinel. Copying the source params into the padded file
  overflows the RIFF size field on close (`'L' format requires 0 <= number <=
  4294967295`) and the pad silently never happens. Take only the *format* from
  the source and bound the read by the file size. The first version of this
  shipped because the tests used tidy handwritten WAVs; `_write_wav` in
  `test_zone_alarm.py` now emits the ugly shape on purpose.
- **The alarm volume ratchets.** MA restores its *cached* pre-announcement
  volume when an announcement ends, and that cache holds the 45 the ring set —
  so the next announcement puts the room back to 45 behind us, and
  `_resting_volume()` then reads 45 as the resting level and locks it in. Two
  rings and the bath was left at alarm volume, where every reply after it is
  shouted. A reading at or above `alarm_volume` is now refused in favour of the
  configured level.
- **`spoken` had to be added to the live `/data` table too.** The repo copy only
  seeds a table that does not exist yet; the first run showed "Master timer
  unattended" because the live file predated the key.

### 9.6 Next: the 12V trigger

All of §9.1 is guesswork about a number that moves. Brad owns a relay and a 12V
supply, and the MA1240a takes a trigger — so the plan is to drive it
deterministically and delete the guessing. Design constraints agreed: **one**
keeper service owning on/off and the hold (not each consumer app driving the
relay), a short *measured* trigger→audio gate rather than an assumed one, and
the wake tone kept as the fallback when relay state cannot be confirmed. Only
worth doing if the rear power-mode switch can follow the trigger; switching
mains instead would trade a 3s gate for a 10s one.

---

## 10. Room isolation (2026-08-08, from Brad watching the tests)

Two things leaked out of the master bath into the kitchen while the §9 tests
were running.

**Kitchen music ducked for bath timers.** Every satellite POSTs `/music/duck`
on a stage-1 wake trigger and again at alarm start, so speech beats the music.
But `music.duck()` acts on `config.MA_QUEUE_ID` — there is one queue and it is
the kitchen's. So the closet was ducking music two floors away, both for its
own wake word and for a bath timer nobody in the kitchen could hear. Fixed by
tagging the request with the satellite id (`sat_path()`, which already existed)
and having the orchestrator refuse it unless `zones.owns_music(sat)`. The
kitchen entry carries `"music": true`; an untagged request still ducks, so an
older satellite build does not go silent in the one room where ducking matters.
The unduck is scoped identically **and has to be** — the duck is refcounted, so
an unduck from a room that never ducked would release someone else's hold.

**Bath timers appeared on the kitchen screen.** A timer you can watch count
down and cannot hear reads as an alarm that failed, not one ringing elsewhere.
Two paths fed it and both needed scoping: the live events (`timers=` was
`ENGINE.active()`, house-wide) and the kiosk's restore-on-reload poll, which
proxies `GET /timers`. Now `app._timer_event()` drops events whose timer
belongs to another room and scopes the list to `config.DASHBOARD_SAT`, and
`dashboard_webapp` asks for `?sat=` (`ASSISTANT_SAT`, default kitchen — the two
must agree). House-wide events with no single timer, i.e. cancel-all, still get
through; the board has to drop what it was showing.

`ENGINE.active(sat)` also had to start including rows with **no** sat when
asked for the default room. Those predate the column and were all kitchen
timers; a kitchen-scoped board would otherwise have silently dropped them.

One display, one room, so `events.on_dashboard()` is a comparison rather than
routing. A second screen means giving each subscriber its own sat and filtering
per connection — the shape is there, the plumbing is not.

## 11. The crackle (2026-08-08 night)

"Your timer is done" broke up in the same place on every ring — three rings,
three identical crackles — while the beeps that followed it were clean and the
spoken reply before it was clean. A defect in the same spot every time is a
file, not a network, so that is where the search started, and it was wrong.

What it was **not**, each ruled out before the next was tried:

- **The file.** Served it over HTTP and Brad played it on his laptop: clean.
  Ruled out the synthesiser, the cache and our own padding in one step.
- **A sample-level defect.** A max-sample-delta scan flagged a 1.20 ratio 0.1s
  in; slowed 4x it is a /t/ plosive. Real speech, not a click. The metric was
  the bug.
- **Determinism.** Two renders came back byte-identical, which proved only that
  the TTS router's Kokoro cache works.
- **Sample format.** The snapcast stream is `48000:16:2` and ours was 24 kHz
  mono, so the announcement was the only thing on that path needing both a rate
  conversion and a mono→stereo map. Converted it, played A and B into the bath:
  *"hmm, both bad."* The resampler was deleted rather than left in — it fixed
  nothing and would have read as load-bearing later.
- **The closet mic as a judge.** HF energy ratio came back 0.0002–0.0003 for
  every playback; 9–20 kHz barely clears that mic's noise floor. Inconclusive,
  and reported as inconclusive.

What worked was giving up on rendering it ourselves. Every spoken thing that
already sounds right in that room — replies, broadcasts, bedtime — goes
HA `tts_get_url` → pad service → 48 kHz stereo MP3 with a 3.5s tail. Same
words, same voice through that route: *"those latest 2 you did are perfect."*
So `zone_alarm.house_announcement()` borrows the reply route, and
`padded_announcement()` (our own WAV plus 2s of silence) is now only the
fallback for HA or the pad service being unreachable — a dead pad service
should cost audio quality, never the alarm.

Two consequences worth knowing:

- **The 1.5s arm-settle is skipped on the house route.** It existed because MA
  reports an announcement done before the sound has left the room; the file's
  own 3.5s tail already outlasts the snapcast buffer, so keeping it would be
  five seconds of dead air before the first beep.
- **The URL is cached in memory by (text, voice).** `tts_padded/` is never
  pruned — 710 files, 118 MB, oldest 26 April — so caching keeps the alarm from
  adding one file per ring to a directory nothing cleans. The growth is
  pre-existing (Node-RED replies, ~35 MB/month) and still wants a sweep.

The root cause inside MA or snapcast was never identified, only routed around.
If it resurfaces on a path that cannot borrow the reply route, the untried
experiment is playing the same WAV as a **queue item** rather than an
announcement, which splits MA's announcement pipeline from snapcast itself.
