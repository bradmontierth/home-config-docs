# Powerwall Pi (pw_pi) — Powerwall telemetry + family-room voice satellite

Written 2026-07-23, the day the family-room satellite went on this box.

> **Rebuilding it? Start at [`powerwall-pi/README.md`](powerwall-pi/README.md).**
> That directory holds every file that lived only on this box's root drive
> (publisher, watchdog, systemd units, `.asoundrc`, satellite `.env`) plus a
> `deploy.sh` that reinstalls the lot onto a fresh image. Captured 2026-07-28
> ahead of the flash-drive→SSD swap. This document explains how the box
> *behaves*; that one is the *artifacts*.

## The box

| | |
|---|---|
| Hardware | Raspberry Pi 4 Model B, **1GB RAM**, 4 cores, 64GB SSD root (X12 in a StarTech USB3 bridge, `uas`) |
| Hostname | `pw-poller-pi` (was `tesla-pw-listener` until the 2026-07-28 SSD rebuild) |
| OS | Raspberry Pi OS (Debian trixie, python 3.13, kernel 6.18) |
| LAN IP | `192.168.40.244` (eth0, VLAN 40) |
| SSH | `ssh pw-poller-pi` from the Beelink (the older `pw_pi` alias points at the same IP) |
| Location | Family room, opposite side of the kitchen/family open space |

RAM is tight but fine: satellite ~91MB RSS + publisher ~54MB, ~480MB still
available. The full desktop stack (lightdm/labwc/pcmanfm/cups) is still
enabled and is the biggest discretionary RAM/user if we ever need headroom.
Thermals are excellent (idle ~40°C, satellite running ~45°C, `throttled=0x0`)
— unlike the old kitchen Pi that soft-limited at 81°C.

## Role 1: Powerwall 3 → MQTT (`pw3-mqtt.service`)

Polls the Powerwall 3 gateway's local **TEDAPI** and republishes a flattened
telemetry snapshot to the house MQTT broker.

- Service: `pw3-mqtt.service` → `/home/pi/bin/pw3_mqtt_publisher.py`
  (venv `~/.venvs/pypowerwall`, uses the `pypowerwall` library in TEDAPI mode
  + `paho-mqtt`).
- Config: `/etc/default/pw3-mqtt` (systemd `EnvironmentFile`). Holds
  `PW_GATEWAY_PASSWORD` (the Tesla gateway password) — **sensitive, don't
  copy it anywhere**; everything else defaults in the script.
- Poll cadence: every **15s** (`POLL_SECONDS`). Each poll opens a fresh
  `pypowerwall.Powerwall(host=192.168.91.1, gw_pwd=…)` and reads
  `tedapi.get_config()` / `get_pw3_vitals()` / `get_status()`.
- Aggregation: vitals arrive as per-VIN sections (`TEPOD--…`, `TEPINV--…`,
  `PVAC--…`, `PVS--…`). The script builds per-battery metrics (SoE estimate
  from `POD_nom_energy_remaining / POD_nom_full_pack_energy`, inverter
  power out, per-string PV power A–F) plus site-level rollups (summed
  energies, deduped alerts, string connected/state/voltage/current).
- Backup reserve is read locally with `pypowerwall.Powerwall.get_reserve()`
  and published as `backup_reserve_percent` in every telemetry snapshot.
  This is the Tesla-app-scaled percentage; do not use the raw TEDAPI
  `site_info.backup_reserve_percent` field directly because it includes
  Tesla's internal 5% scaling.
- Publish: one JSON payload per poll to **topic `pw3/telemetry`** on
  **`192.168.10.217:1883`** (Beelink mosquitto), QoS 1, retain off,
  client id `pw3-publisher`. A fresh MQTT connection per publish — simple
  and robust against broker restarts.
- Grid/outage telemetry (added 2026-07-25) comes from the same local
  `tedapi.get_status()` call and therefore adds no Tesla cloud/API usage.
  The payload includes `grid_outage`, `grid_ok`, `microgrid_ok`,
  `grid_contactor_closed`, island/grid states, utility-side and load-side
  L1/L2 voltages, site/load/solar/battery power, and active control alerts.
  `grid_outage` is the explicit inverse of TEDAPI's boolean `gridOK`; it is
  `null` rather than guessed if TEDAPI does not return a boolean.
- The Docker Node-RED `Tesla` tab (`7fa25727b15db1f0`) converts that payload
  to MQTT discovery entity **`binary_sensor.powerwall_grid_outage`**.
  Its state topic is
  `homeassistant/binary_sensor/pw3/grid_outage/state`; `on` means the utility
  grid is unavailable and the Powerwall is islanded. The state is refreshed
  every poll and has `expire_after: 60`, so stale telemetry becomes
  `unavailable` instead of silently claiming that grid power is healthy.
  Island/contact/voltage/power/alert details are attached as entity
  attributes.
- The Docker Node-RED `Tesla` tab also converts the local
  `backup_reserve_percent` value to MQTT discovery entity
  **`sensor.powerwall_backup_reserve`**. Its retained state topic remains
  `homeassistant/sensor/pw3/backup_reserve/state`, and its attributes include
  `source: local_tedapi`. Node-RED publishes immediately when the reserve
  changes and refreshes the retained state every five minutes.
- **Do not poll Tesla Fleet API `site_info` for reserve telemetry.** The old
  five-minute Node-RED poll was removed on 2026-07-26 because every request
  went to Tesla's billable cloud Fleet API. Fleet API is retained only for
  occasional reserve-changing commands. After a command, Node-RED waits for
  propagation and verifies the result from the local Home Assistant reserve
  sensor, without a second cloud data request.
- The Docker Node-RED `Powerwall Reserve Forecast` tab
  (`8d7289c449f38a92`) reads this local reserve sensor and recalculates both
  periodically and immediately when the reserve state changes. The Tesla
  tab's TOU-risk alert compares the reserve embedded in the forecast with
  the current local reserve and suppresses action if they disagree.
- That same Node-RED tab sends transition-only Pushover notifications through
  the existing `a3903de6e20b03dc` Pushover configuration. Node
  `pw_grid_outage_changed` watches the HA binary sensor with
  `outputInitially: false` and state-change-only output; function
  `pw_grid_transition_alert` suppresses startup/repeated/unknown/unavailable
  states. `off -> on` sends a priority-1 **Grid power is out** notification
  with live voltage/load/solar/battery details. `on -> off` sends a normal
  **Grid power restored** notification with outage duration when known. A
  Node-RED deploy during an existing outage does not fabricate a new outage
  notification.

### ⚠️ The Tesla AP locks out a client that gets disconnected (2026-07-28/29)

**Never force-disconnect wlan0 from a Powerwall AP.** Doing so cost ~13 hours
of telemetry. Sequence:

1. `pw3_watchdog.sh` bounced the wifi because one `ping -c 1 -W 2` to the
   gateway failed — but the gateway does **not** answer ICMP reliably, and the
   publisher had logged successful polls seconds earlier. It tore down a
   healthy link (`CTRL-EVENT-DISCONNECTED reason=3 locally_generated=1`).
2. The AP then refused every re-association with
   **`CTRL-EVENT-ASSOC-REJECT status_code=16`** ("timeout waiting for next
   frame in sequence"). Not a PSK error — this is before the 4-way handshake,
   so the stored password is irrelevant.
3. **Every retry appeared to refresh the penalty.** A fixed 5-minute retry
   could never outlast it; it stayed locked out all night.
4. **One hour with the box powered off cleared it**, and it associated on the
   very first attempt of the next boot.

Things that do NOT fix it, all tested: a different MAC (so it is not a MAC
ban), the other Powerwall's AP (both refuse), `brcmfmac` reload,
`wpa_supplicant` restart, a full reboot, stopping USB audio, and a 5-minute
park. Signal was -28 dBm and channel 11 was permitted at 20 dBm throughout,
so it is never a radio or regulatory problem.

Diagnostic value: another device (a phone, or the Plex Pi, which pulled
`192.168.91.192/24` off the same AP) **can** associate while this box is
locked out. That proves credentials and AP health, and narrows it to this
client's association state.

**Recovery: POWER-CYCLE THE PI. Nothing else works, and it does not take long.**

Corrected 2026-07-30 after a second lockout. Silence is NOT the cure — the
2026-07-29 recovery was credited to "an hour of quiet", but the box was also
powered off for that hour, and the hour was the incidental part:

| Attempted cure | Worked? |
|---|---|
| Warm reboot | ✗ |
| `brcmfmac` module reload | ✗ |
| SDIO controller unbind/rebind | ✗ |
| `wpa_supplicant` restart | ✗ |
| Different MAC | ✗ |
| 5-min radio park | ✗ |
| **79 min of total silence, zero attempts, box running** | **✗** |
| **~2 min with the power physically pulled** | **✓** |

Two minutes is enough. Do not wait an hour.

### Why only a power cut works

That table used to describe the SDIO unbind/rebind row as "chip re-init,
firmware reloaded". **That was wrong**, and it made the result look far more
mysterious than it is. Established 2026-07-30:

```
$ find /proc/device-tree -iname '*pwrseq*'      → nothing
$ ls /proc/device-tree/soc/mmcnr@7e300000/      → no mmc-pwrseq property
$ gpioinfo | grep -E 'WL_ON|BT_ON'
	line   0:	"BT_ON"    output consumer="shutdown"
	line   1:	"WL_ON"    output                       ← no consumer
```

`WL_ON` is the BCM4345's power-enable line, and **no kernel driver owns it**.
The VideoCore firmware raises it at boot and nothing in Linux ever lowers it.
(Contrast `BT_ON`, which shows a consumer because the bluetooth driver really
does own its enable line.) There is no `mmc-pwrseq` node either, so the SDIO
host does not toggle it during probe/remove.

Consequence: the wifi chip stays powered through **every** entry in that table
except the last one. `rfkill`, `nmcli radio wifi off`, `rmmod brcmfmac`,
unbind/rebind, warm reboot — all of them leave WL_ON high. Re-downloading
firmware into a chip that never lost power re-initialises the MAC and the
driver-visible state; it does not reset the PHY, PMU, or RF calibration.
So the "we already tried a full chip reset" conclusion was never tested.

This also explains two results that otherwise did not fit: a MAC change did
not help (the fault is not identity-based), and the chip could still join the
house SSID during the lockout (the fault is narrow, not a dead radio).

Ruled out separately: the AP is plain WPA2-PSK on channel 11
(`freq=2462 PTK=CCMP GTK=TKIP`), **not** SAE — so the multi-frame-handshake
theories that would neatly explain a status-16 timeout do not apply.

### The automated cure: `wifi_cold_reset()`

`pw3_watchdog.sh` now escalates to a genuine chip power cycle — rmmod, unbind
the SDIO host, drive `WL_ON` low for 10s via `gpioset`, drive it high, rebind,
reassociate. This is the software equivalent of pulling the plug, and it has
two advantages over one: it is automatic, and **it does not wedge the
ReSpeaker**, because no USB rail moves.

Gating, deliberately conservative — a hard disconnect is what is suspected of
*causing* the lockout, so this must never run on a healthy link:

* 45 min continuously unassociated (`COLD_RESET_AFTER_SEC`), and
* the SSID is **visible** in a scan (if the AP is off the air, our radio is not
  the problem), and
* at most once per 90 min (`COLD_RESET_COOLDOWN_SEC`).

By then telemetry is already dead, so the attempt costs nothing. Manual
trigger during a confirmed lockout:

```bash
sudo /home/pi/bin/pw3_watchdog.sh --cold-reset
```

**VERDICT 2026-07-30 evening: IT DOES NOT WORK. `COLD_RESET_ENABLED=0`.**

The escalation fired for real at 17:24. The chip was genuinely unpowered for 85
minutes and came back with a fresh firmware download — and the AP still refused
every association with `status_code=16`, while its beacon was arriving at
**SIGNAL 100**. So the client-chip theory is dead. Whatever a mains power cycle
changes, it is not the state of this chip. Do not re-arm this without new
evidence; the mechanism is real but the cure is not.

It also failed *dangerously*, and both failures are worth remembering:

* **`gpioset` (libgpiod v2) does not exit on its own.** `--hold-period` is a
  minimum, not a termination condition — the process holds the line until
  something kills it. Called unbounded it hung with `WL_ON` **low**, systemd
  SIGTERMed the unit at `TimeoutStartSec` 300s later, and the wifi chip stayed
  unpowered for 85 minutes. Always `timeout`-bound it, and always read the line
  back with `gpioget`: "gpioset exited" says nothing about whether the value
  stuck (it does survive the kill here, but do not assume it).
* **Never gate a recovery path on something that needs what it is recovering.**
  The escalation was gated on `ssid_state == present`, which needs a radio to
  scan with. With no `wlan0` the box could not dig itself out of the hole it had
  dug: it logged "wifi not associated" once a minute for 85 minutes while
  holding its own radio down. `restore_radio()` now runs unconditionally, first,
  whenever `wlan0` is absent.

### The remaining lead: USB3 interference

Unproven, but the correlation is hard to ignore and the fix is nearly free:

* Lockouts began **2026-07-28**, the day root moved from a USB2 flash drive to a
  **USB3 SSD**.
* The 2026-07-30 evening lockout began ~6 minutes after a **USB3 hub** was added.
* The beacon arrives at **SIGNAL 100** yet association fails — the signature of
  a bad noise floor, not a weak signal.
* Other clients (phone, Plex Pi) associate fine; they are not inches from the
  radiator. The Pi 4's antenna is on-board next to the USB ports, and channel 11
  (2462 MHz) sits in the worst of the USB3 emission band.

What does NOT fit: there is no clean reason a mains power cycle should cure an
interference problem. (Possible story — association is the fragile operation and
succeeds in the quiet window right after boot, while an established link
tolerates noise a new association cannot — but that is a story, not evidence.)

Note the noise floor **cannot be measured here**: `brcmfmac` is a fullmac driver
and `iw dev wlan0 survey dump` returns nothing.

Hub moved to USB2 on 2026-07-30 evening and the link came straight back. If
lockouts stop, that is the answer; if they continue, move the **SSD** to USB2
next — it is the bigger radiator and it runs constantly.

A smart plug remains the fallback for automating the one cure that does work.

**Note the mic:** a power cycle reliably wedges the ReSpeaker (it did on
2026-07-29 and again on 2026-07-30). As of 2026-07-30 evening this no longer
needs a physical replug — the wedge was characterized as *capture gated on an
open playback stream* and `respeaker-clock-keeper.service` holds one open
permanently (see the wedge section below). Still check the mic after any power
event: `mic_health.sh` runs every 10 min and hw_ptr must advance.

Ops: `journalctl -u pw3-mqtt -f` on the box; consumer side is whatever
subscribes to `pw3/telemetry` on the Beelink broker (Node-RED/HA).

**The journal is persistent as of 2026-07-30** (`journald.conf.d/persistent.conf`,
200M cap). It was volatile before — `/var/log/journal` existed but was empty and
only the current boot survived — which is why every `status_code=16` line from
the 10-hour lockout was gone by the time we went looking. The failures worth
diagnosing on this box are exactly the ones that end in a reboot or a power
pull, so `journalctl -b -1` needs to work.

## Networking: how it reaches the Powerwall AND the house

Dual-homed on purpose — this is the whole reason the box exists:

- **wlan0 → Powerwall's own Wi-Fi AP** (`TeslaPW_CDRTMU`, NetworkManager
  connection of the same name, **autoconnect deliberately OFF** — see the
  lockout section; `pw3_watchdog.sh` is the sole owner of this link, because NM
  retrying on its own schedule alongside the watchdog hammered the AP for 10h
  on 2026-07-30). Gets `192.168.91.124/24`;
  the gateway is `192.168.91.1`. TEDAPI is **only** reachable on this
  network — that's a Tesla design constraint, and why a house device must
  physically join the Powerwall AP.
- **eth0 → house LAN VLAN 40** (`192.168.40.244/24`, gw `192.168.40.1`).
  Default-route wins via metric (eth0 100 vs wlan0 600), so ALL normal
  traffic — MQTT publish, voice satellite HTTP, SSH — goes out ethernet;
  only `192.168.91.0/24` rides the Wi-Fi leg.

**Inter-VLAN firewall gotcha (matters for the satellite):** from VLAN 40
this box can reach the Beelink (`192.168.10.217` — MQTT, orchestrator) but
**not** other 10.x hosts — the kitchen satellite `192.168.10.251:8781`
times out. That's why satellite playback relays *via the orchestrator
proxy* (below) instead of hitting the kitchen box directly. If a firewall
rule 40.244→10.251:8781 is ever added, `PLAYBACK_RELAY_URL` can point
straight at `http://192.168.10.251:8781` and drop the extra hop.

## Role 2: family-room voice satellite (`voice-assistant.service`)

Deployed 2026-07-23 — backlog item "Family-room second mic (phase 2
satellite) v1". Mic-only fallback satellite: same dual-wake code as the
kitchen, but **all of its audio output (chime/TTS/replies) plays on the
kitchen big speakers** — the kitchen box stays the house's one voice.
During music it's the mic that *isn't* parked next to the speakers, so it
wins wake-over-music by geometry.

- ReSpeaker XVF3800 4-mic array on USB (card `Array`), captured via
  `~/.asoundrc` pcm `respeaker_ch0` (ch0 = processed beam; ch1 is the AEC
  reference and must not be downmixed in). That file lives only on the box's
  USB root drive, so here is the canonical copy — rebuild from this, not from
  memory (it was lost once already, see the outage note below). Mic-only box:
  no playback alias, replies relay to the kitchen.

  ```
  pcm.respeaker_ch0 {
    type plug
    slave.pcm {
      type route
      slave.pcm "hw:CARD=Array"
      slave.channels 2
      ttable.0.0 1.0
    }
  }
  ```

  The array advertises exactly `S16_LE / 2ch / 16000` and nothing else
  (`cat /proc/asound/card3/stream0`), which is a fast way to confirm a
  capture failure is NOT a rate/format mismatch.

- **Power-outage damage, 2026-07-25 20:59 (two separate faults).** The
  Powerwalls drained, the Pi lost power mid-write, and it came back needing a
  manual restart. Two things broke, and they look like one problem:
  1. *`~/.asoundrc` vanished.* ext4 logged `EFSCORRUPTED` at exactly
     20:59:26 and the next boot ran `orphan cleanup on readonly fs`, which
     removed the orphaned inode. The satellite then crash-looped ~1,500 times
     (`arecord: audio open error: No such file or directory` — that message
     means the ALSA *alias* is missing, not the mic). Restored 2026-07-26.
     Also left two block groups with `bad block bitmap checksum` (bg 720,
     816); the fs was `clean with errors` and had never been checked since it
     was created in Dec 2025.
  2. *The ReSpeaker itself wedged.* Separately from the config, the array
     enumerates normally but every capture read returns `Input/output error`
     at its own native format. It survived `USBDEVFS_RESET` **and** a
     `/sys/bus/usb/drivers/usb` unbind/rebind — the XVF3800 runs its own DSP
     firmware, and a bus-level reset does not power-cycle it. ~~Only a
     physical unplug/replug clears this.~~ **Superseded 2026-07-30: the wedge
     is capture-gated-on-playback and `respeaker-clock-keeper.service` cures
     it in software — see "The wedge, SOLVED in software" below.**

  Lesson: after any outage on this box, check BOTH — that `~/.asoundrc` still
  exists, and that `arecord -D respeaker_ch0 -d 2 /tmp/t.wav` actually
  produces more than a 44-byte header. A 44-byte file is a header with no
  samples, i.e. the device opened but never streamed.

  **Wedged again on the 2026-07-28 SSD rebuild, and replugging fixed it** —
  moved from port `1-1.1` to `1-1.4` (a USB2 port; the array is a high-speed
  USB2 device, so a blue port buys it nothing and the SSD keeps the USB3 bus
  to itself). No over-current was ever logged, so the power budget was not
  the culprit that time. Healthy capture reads ~192KB for `-d 3` 2ch/16k.

  **Powered hub fitted 2026-07-30** (Genesys Logic `05e3:0626`/`05e3:0610`).
  Current topology — the array now draws from the hub's own supply, not the
  Pi's rails, which is the standing test of the power-budget theory:

  ```
  Bus 001 (480M)  hub 1-1.1 ──> 1-1.4  reSpeaker XVF3800   (snd-usb-audio, card "Array")
  Bus 002 (5000M) hub 2-1                                   (USB3 side, nothing on it)
  Bus 002 (5000M) 2-2      ──>  StarTech 64GB SSD           (uas, DIRECT — not on the hub)
  ```

  The boot SSD stays on a direct USB3 port deliberately: it is the root
  filesystem, and putting it behind a hub adds a failure point to the one
  device that cannot tolerate one. The array is the thing that keeps wedging,
  so the array is the thing that got the clean supply.

  **The hub did NOT stop the array wedging** (2026-07-30 evening: wedged again
  on the very next mains power cycle, `hw_ptr` frozen at 0). Two consequences:

  * The USB power budget was probably never the cause. Three wedges, all around
    power interruptions, and an independent supply changed nothing.
  * A self-powered hub keeps VBUS up when the *Pi* loses mains, so the array no
    longer gets power-cycled along with the host. (This worry is now moot —
    see the next section — but it remains true that the hub changes what the
    array experiences during a Pi power event.)

  ### The wedge, SOLVED in software (2026-07-30 evening)

  The wedge was finally characterized instead of cured blind, and the
  signature is much narrower than "the array is dead":

  * Enumeration is normal, HID binds, and **playback works** (`aplay
    hw:CARD=Array` streams fine).
  * Capture **alone** delivers zero frames: `arecord` at the native format
    returns `Input/output error`, `hw_ptr` sits frozen, a capture WAV is a
    44-byte header. No kernel/xhci errors are logged at all.
  * Capture **with a playback stream concurrently open** is instantly,
    perfectly healthy — 192,044 bytes for `-d 3`, the exact healthy figure.
  * The gating is live, both directions: stop playback and an open capture
    stream stalls mid-flight; start playback again and the *same* stream
    resumes at 16k frames/sec without being reopened.

  So after an unclean power-up the XVF3800 comes up in a state where its
  capture pipeline only runs while the host's OUT stream is active. This box
  is mic-only — the satellite never plays to the array — which is exactly why
  it presented as a total, silent mic failure.

  **The fix: `respeaker-clock-keeper.service`** (deployed + enabled
  2026-07-30, in the rebuild kit and `deploy.sh`). It holds
  `aplay -D hw:CARD=Array /dev/zero` open forever: silence, ~64KB/s of USB,
  nothing audible (no speaker is attached to the array here). Order doesn't
  matter, so `Restart=always` makes it self-healing; if it dies, capture
  stalls until it returns and then resumes. Verified end-to-end: satellite
  capturing at 16k frames/sec, `mic-health` passing (it had correctly caught
  the wedge at 19:44, before the fix).

  What was tried and disproven on the way, for the record:

  * `uhubctl` (now installed on the box) CAN switch the Genesys hub's port
    power — needs `-f` (the hub is on uhubctl's ignore list, reports `ganged`
    switching). `off` genuinely disconnects the device; `on` re-enumerates
    it. But a 5s cycle and a 25s cycle both left capture-alone dead.
  * That matches 2026-07-29, when **an hour of pulled power also did not
    clear it**. The array's own power loss has *never* cured this state; the
    one thing that ever restored free-running capture was the physical replug
    on 2026-07-28 (mechanism still unexplained — but with the keeper running
    it no longer matters, since a "wedged" array captures perfectly).
  * Fresh finding while testing: the wedge is not cured by re-enumeration
    either (the uhubctl cycles re-enumerated it twice).

  **Hub moved to a USB2 port 2026-07-30** (it was on USB3 for ~40 min). The
  array is a high-speed USB2 device, so the hub's USB3 side was pure liability
  — see the USB3/2.4GHz note in the lockout section.

  `.asoundrc` refers to the device as `hw:CARD=Array`, by NAME — so the ALSA
  card index moving (it became card 3 behind the hub) does not matter. Keep it
  that way. While the array is absent the satellite exits with
  `Cannot get card index for Array` and systemd restarts it on a loop; that is
  correct behaviour, and it recovers by itself once the device reappears.

  **Channel levels, measured on a quiet room 2026-07-28:** ch0 peak 151 /
  rms 13, ch1 peak 504 / rms 55. ch1 reading *hotter* than ch0 is expected
  and is NOT a channel swap — ch0 is the processed beam, so the XVF3800's
  noise suppression strips room ambience out of it while ch1 passes through
  comparatively raw. Judge ch0 on speech, never on ambient.

  **Do not treat those numbers as a floor.** On 2026-07-30 a silent-room ch0
  capture read **peak 4 / rms 1.1** — 30× below that baseline — on an array
  that was working perfectly (wake word confirmed by voice moments later). The
  XVF3800's noise suppression keeps converging on the room's noise floor the
  longer the stream runs, so a settled stream in a quiet room legitimately
  approaches zero; the 151 baseline was taken seconds after startup. A low
  ambient reading on ch0 means nothing on its own.

  What DOES distinguish a wedged array from a quiet one, without a human in
  the room: `hw_ptr` in `/proc/asound/card*/pcm0c/sub0/status` advancing at
  ~16000 frames/sec (that is what `mic_health.sh` checks), and capture reads
  not returning `Input/output error`. Level is the wrong signal — a wedged
  array does not stream quietly, it stops streaming. Note there is also no raw
  channel to cross-check against: the device exposes exactly two, the processed
  beam and the AEC reference.

  **`audioop` is gone in Python 3.13** (removed, not just deprecated), so
  the old one-liner for checking levels no longer runs on this box. Use
  `array.array("h")` over the frames instead.
- Code: `/home/pi/voice-pipeline/assistant.py` — same file as the kitchen,
  deployed from `home_config/voice-assistant/satellite/`. Venv
  `/home/pi/voice-pipeline/.venv` (numpy, onnxruntime, livekit-wakeword).
  Wake models in `/home/pi/wake-bench/` (okay_computer + okay_google).
- `.env` (`/home/pi/voice-pipeline/.env`):
  - `SATELLITE_ID=familyroom` — sent as `?sat=` on `/verify` and
    `/command/audio` so the orchestrator can arbitrate.
  - `PLAYBACK_RELAY_URL=http://192.168.10.217:8785/satellite` — playback
    relay via the orchestrator's `/satellite/play` proxy (see firewall
    note). Relay is synchronous: the POST returns when the audio finished
    playing in the kitchen, preserving the satellite's capture timing.
  - `HOP_MS=320` — the "relaxed cycles" setting. On-device benchmark
    (2026-07-23): dual-model predict ≈ **130ms** → ~40% duty. Deliberately
    NOT the kitchen's 192: this is a 1GB Pi 4 and a fallback mic with a
    generous latency budget. Headroom exists (45°C) if we ever want 256.
  - `ORT_THREADS=2`, `SILERO_THRESHOLD=0.4`, `MODE=active`.
- Service: `voice-assistant.service` (systemd, enabled). Logs:
  `journalctl -u voice-assistant -f`. Wake review page:
  `http://192.168.40.244:8781/review` (this box's own clips/events — the
  homepage "Wake Review" tile still points at the kitchen's).
- The day-mode volume/mode Node-RED flow only drives the **kitchen**
  satellite; this box's volume is irrelevant (it relays raw audio and the
  kitchen scales it with kitchen volume), and its mode persists via `.env`.
- Alarms still ring **only** in the kitchen (orchestrator
  `SATELLITE_ALARM_URL` → .251). Saying "stop the timer" near this mic is
  just a normal turn and works.

### First-past-the-post arbitration (orchestrator)

Both mics hear the same "okay computer". The orchestrator (`app.py`)
resolves it: the **first satellite whose wake VERIFIES claims the turn**
(`ARB_SUPPRESS_S=3` window); the other satellite's `/verify` is answered
`{"suppressed": true, "winner": …}` — checked both at entry (skips the
duplicate ASR decode) and again after ASR (covers the in-flight race).
The loser plays no chime, but still silently captures the command and
POSTs it to `/command/shadow`, where it's transcribed and **logged only**
(`grep 'shadow command' in docker logs voice-orchestrator`) — that log,
next to the winner's `command sat=…` line, is the evidence base for the
v2 dual-transcribe chooser (only build it if shadows regularly beat
winners). During music the drowned kitchen mic fails verify, so the far
mic wins by default — arbitration is arrival-order deterministic, not a
race we tuned.

### Verified working (2026-07-23)

- Replayed a real wake clip: kitchen verifies+claims → familyroom
  suppressed with winner=kitchen → verifies normally after the 3s window.
- Relay chain pw_pi → orchestrator `/satellite/play` → kitchen `/play`:
  chime audibly plays on the kitchen big speakers.
- Manual `/trigger` on pw_pi: full turn (relayed chime → capture →
  clean `no_speech_onset`).
- Live voice from the couch: **pending Brad**.

### Ops quick reference

```bash
ssh pw_pi                                   # from the Beelink
journalctl -u voice-assistant -f            # satellite logs
journalctl -u pw3-mqtt -f                   # powerwall publisher logs
curl -s localhost:8781/health               # satellite health/mode
curl -s -X POST -d '{"mode":"shadow"}' localhost:8781/mode   # kill switch
vcgencmd measure_temp; vcgencmd get_throttled                # thermals
```

Redeploy satellite code: `scp home_config/voice-assistant/satellite/assistant.py
pw_pi:/home/pi/voice-pipeline/ && ssh pw_pi sudo systemctl restart
voice-assistant` (same file also goes to the kitchen box .251 — keep them in
lockstep).
