# pw_pi rebuild kit — everything needed to recreate `tesla-pw-listener`

Captured **2026-07-28**, off the live box, immediately before its root drive
(a 462GB PNY USB flash drive) was replaced with an SSD after the 2026-07-25
power-outage corruption.

The box's *behaviour* is documented in `../powerwall-pi-guide.md`. This
directory is the *artifacts* — the files that existed only on that drive.
`deploy.sh` reinstalls all of them onto a fresh Raspberry Pi OS image.

## What lives where

| What | Where | In git? |
|---|---|---|
| Powerwall publisher, watchdog, systemd units, `.asoundrc`, satellite `.env`, pip freezes | **this directory** | yes |
| Satellite code `assistant.py` + `sounds/` | `../voice-assistant/satellite/` (source of truth, kitchen + family room share it) | yes |
| `PW_GATEWAY_PASSWORD`, both Tesla AP Wi-Fi PSKs | `/home/pi/cecret_lake/powerwall_pi/` | **no — secrets, by design** |
| Wake models (`okay_computer`/`okay_google`/`stop`), `silero_vad.onnx`, this box's wake-clip corpus, boot/alsa config snapshot | `/home/pi/backups/pw_pi-20260728/` | no (binaries) |

The three wake `.onnx` models are byte-identical to the kitchen box's copies
in `big-speaker-mini-pc:/home/pi/wake-bench/`, so they were never
single-copy — but the Beelink held none until this capture.

`silero_vad.onnx` is also a public download (github.com/snakers4/silero-vad,
`src/silero_vad/data/silero_vad.onnx`) if the local copy is ever lost.

## Rebuild

1. Flash Raspberry Pi OS (trixie, 64-bit) to the SSD. In the imager, set
   hostname `tesla-pw-listener`, user `pi`, locale `America/Denver`, and
   **paste the Beelink's public key** so `ssh pw_pi` works on first boot.
   (Fallback if you forget: `ssh-copy-id -i ~/.ssh/id_ed25519_pw_pi.pub pi@<ip>`.)
2. Boot it, then from the Beelink:
   ```bash
   ssh-keygen -R 192.168.40.244        # host keys regenerate on a fresh image
   ./deploy.sh                         # add a target if the IP differs: ./deploy.sh 192.168.40.99
   ```
3. Verify — see the checklist at the bottom of `deploy.sh` output.

### Things that must NOT be restored from the old drive

- **`/etc/fstab` and `/boot/firmware/cmdline.txt`** — both reference the old
  drive's `PARTUUID=0b8c5d5e-*`. The imager writes correct ones. The saved
  copies in `blobs/../data/system-misc.tar.gz` are reference only.
- **`/etc/hosts`** — cloud-init manages it (`manage_etc_hosts: true`).
- **`/etc/machine-id`, SSH host keys** — regenerate, and should.

### Things that need no action

- **IP `192.168.40.244` is DHCP**, not static: it comes from a router
  reservation keyed to eth0 MAC `e4:5f:01:8f:40:87`. Same board → same MAC →
  same IP. (wlan0 is `e4:5f:01:8f:40:88`; it gets `192.168.91.124` from the
  Powerwall's own AP.)
- **Default-route metrics** (eth0 100 beats wlan0 600) are NetworkManager
  defaults for wired-vs-wireless, not something that was configured.
- `/boot/firmware/config.txt` on the old drive was stock apart from nothing —
  no custom overlays were ever added.

### Known gotchas that will bite during the rebuild

- **The ReSpeaker XVF3800 runs its own DSP firmware.** If capture returns
  `Input/output error`, a USB reset or reboot will *not* clear it. A 44-byte
  `arecord` output file is a header with no samples, i.e. the device opened
  but never streamed. **Characterized 2026-07-30:** this "wedge" is a state
  where capture only delivers frames *while a playback stream is concurrently
  open* — playback itself always works, and a stalled capture stream resumes
  the instant playback starts. `respeaker-clock-keeper.service` holds a
  silent `aplay /dev/zero` open forever, which both cures and prevents it.
  Physical unplug/replug also restores free-running capture.
- **`~/.asoundrc` has been lost twice to unclean shutdowns.** It is the
  `respeaker_ch0` alias; without it the satellite crash-loops with
  `audio open error: No such file or directory` — that message means the
  ALSA *alias* is missing, not the mic.
- The satellite `.env` has `MODE=active`; unlike the kitchen box, mode
  persists here through restarts because it is set in the env file.
- This VLAN (40) cannot reach `192.168.10.251`. Playback relays through the
  orchestrator proxy — that is deliberate, not a misconfiguration.

## Files in this directory

```
pw3_mqtt_publisher.py            Powerwall 3 TEDAPI -> MQTT publisher (was /home/pi/bin/)
pw3_watchdog.sh                  wifi/staleness watchdog, runs every 60s (was /home/pi/bin/)
requirements-pypowerwall.txt     pip freeze of ~/.venvs/pypowerwall
asoundrc                         ~/.asoundrc — respeaker_ch0 mono ch0 route
authorized_keys.txt              the pi user's authorized_keys (Beelink key)
systemd/pw3-mqtt.service
systemd/pw3-watchdog.service
systemd/pw3-watchdog.timer
systemd/voice-assistant.service  family-room variant (differs from the kitchen unit only in Description)
systemd/respeaker-clock-keeper.service  silent aplay loop; XVF3800 capture gates on an open playback stream after unclean power-up
satellite/satellite.env          /home/pi/voice-pipeline/.env — no secrets, safe in git
satellite/requirements-satellite.txt
deploy.sh                        push-button rebuild
```

`pw3_mqtt_publisher.py` was previously only in git as
`.backups/powerwall-reserve-20260726_211002/pw3_mqtt_publisher.py.staged`
(byte-identical to the running copy). It now has a real home.
