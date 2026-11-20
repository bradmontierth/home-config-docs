# Kitchen ↔ media host swap plan

Status: **PREP DONE 2026-10-25 night (approved); swap itself pending (2026-10-26 daytime).**

### Prep done 2026-10-25 night (nothing switched over; kitchen + media kept running throughout)
- **Backups:** on the Beelink in `/home/pi/backups/kitchen-media-swap-20261025/{minipc,plexpi}/` (mode 700/600): home + etc tarballs, plus the nfc-jukebox repo bundle.
- **Mini PC:**
  - Installed nfs-kernel-server, clamav (+daemon), smartmontools, rsync and mosquitto-clients. nfs-server, clamav-daemon and freshclam are **disabled** until swap day. Docker CE was already present.
  - Pi configs and source staged at `/motherload/media-host/home/<dir>`, with `/home/pi/<dir>` symlinks for aria, audiobook, media-center, pi-stats, promtail-agent, spotiflac-service and transmission. The bulk data (podcasts 122 G, media-center/server/data 9.8 G) is still to copy from the T5.
  - Images ready: media-center:latest and spotiflac-api built for x86; gluetun, transmission, aria2-ui, audiobookshelf and promtail pulled. Several pulls needed retries on the bad Wi-Fi.
  - Staged in `/motherload/media-host/staged/`: `media-host-metrics-mqtt.{sh,service}` (topic pi/metrics/media-host, DeviceNM "Plex Pi", IFACE eno1), `60-media-host-hardening.conf` (sshd) and `ssh-beelink-only.nft` (syntax-checked).
  - **On swap day:** add a `pi/metrics/media-host` mqtt-in on the Beelink Node-RED Device Monitoring tab (tab 3453be336e524ff8, via the Admin API).
- **Pi:**
  - squeezelite + snapclient installed **masked**; alsa-utils, iw, dfu-util and python3-venv installed.
  - Kitchen files copied: ~/voice-pipeline, wake-bench, respeaker-led, ring-labeler, clip-browser.py, aec-*.sh and nfc-jukebox. `.asoundrc*` is held in `~/kitchen-staging/`.
  - Venvs built on arm64/py3.11: satellite (onnxruntime 1.27, numpy 2.4.6), LED (pyusb) and the NFC agent. All 8 wake/stop/VAD models load.
  - Unit files + udev rule staged in `~/kitchen-staging/systemd/`, with wifi-powersave-off already edited to `wlan0`.
  - `docker image prune` reclaimed only 386 MB: the old builds shared layers.
- **Found:** the NFC scanner's API token sits in plain text in its unit's `ExecStart`. Move it to an `EnvironmentFile` (mode 600) on swap day.

Swap the two boxes:

- **Kitchen** (voice satellite, big speakers, NFC jukebox) moves from the mini PC (`minipcmax`, .251, Wi-Fi) to the **Plex Pi 4** (4 GB, fan).
- **Media host** (`plex`, .150, wired, 4 TB library HDD) moves from the Pi to the **mini PC**.

## Why

- **The kitchen's late dings are the mini PC's Realtek RTL8821CE card.** A 300 KB copy to .251 takes 1.4–7.6 s and once hung 130 s.
- **The Pi's Wi-Fi is clean.** From the basement, on the same AP and the same channel 40, with a weaker signal (−50 dBm), it does the same copy in 0.34–0.43 s, 10 of 10 times.
- **Settings and a reboot didn't fix the mini PC.** Two reboots and the rtw88 settings `disable_aspm` / `disable_lps_deep` changed nothing.
- **The media host gains headroom** for decoding, thumbnails and commercial detection, and on Ethernet the bad Wi-Fi card no longer matters.

Evidence: memory `kitchen-aec-build` (2026-10-24/25 entries) and `unifi-udm-wifi-stats`.

## Principles

1. **Keep the IP addresses.** .150 follows the media role and .251 follows the kitchen role. Then nothing that points at them needs to change: Navidrome and Music Assistant NFS mounts, the Shield app (:8790), the orchestrator `config.py` and compose file, Node-RED, the slideshow `.env`, Homepage and Grafana.
   - **.150 reservation** → mini PC Ethernet MAC `00:e0:4c:f9:0e:45`. It currently holds the stale .250 reservation.
   - **.251 reservation** → Pi Wi-Fi MAC `e4:5f:01:5a:ee:d5`. It currently points at the mini PC's Wi-Fi MAC `3c:7a:aa:79:21:e1`.
   - Brad makes both changes in the UniFi app. I have no API login for the network app.
2. **Both old setups stay intact until the new ones prove out (two weeks).** That makes rollback a cable swap, not a rebuild.
   - **The Pi keeps its Samsung T5 SSD as its boot drive** (Brad, 10-25: the T5 is more reliable long-term than an SD card). The kitchen gets installed **on top of the existing Debian 12 OS**. The media services are **disabled, not removed**, and the media data stays on the T5 until the two weeks are up.
   - **The mini PC's kitchen services** are **disabled, not deleted**.
3. **Nothing secret gets printed or copied into git.** That covers the Wi-Fi password, the gluetun VPN credentials in the transmission `.env`, the Node-RED credentials, and the HA token. Each file is copied as-is from box to box.

## Content: what moves, what doesn't

### Media role (Pi → mini PC)

The Pi boots from the **T5 USB SSD** (931 G, 147 G used). It's an arm64 Debian 12 install, so it **cannot boot the x86 mini PC**. The media role is rebuilt on the mini PC's existing Ubuntu 24.04, and data is copied off the T5. The fastest way is the T5 plugged straight into the mini PC over USB 3, mounted **read-only**.

**The library HDD** (WD 4 TB, ext4, `/media/externalHDD`, 2.8 T used) moves as-is, still in its enclosure. Same fstab line by UUID, with `nofail`.

The mini PC's root SSD is only 116 G, so the bulk data goes on the 1 TB NVMe (`/motherload`, about 150 G used). It gets bind-mounted back at the same `/home/pi/...` paths so the compose files don't change.

| Item on the T5 | Size | In git? | Plan |
|---|---|---|---|
| `~/audiobook/podcasts` | 122 G | no | Copy to `/motherload/audiobook/podcasts`, bind-mount to the old path |
| `~/audiobook/{config,metadata,books}` | 38 M | no | Copy (audiobookshelf database) |
| `~/media-center/server` | 9.8 G | no on the Pi (source lives in its repo) | Copy the data and `.env`. Rebuild the image on x86 (`docker compose build`) |
| `~/transmission/{transmission-config,gluetun-data,organizer}` + compose/.env | ~40 M | no | Copy as-is (VPN credentials inside) |
| `~/transmission/plex-config` | 2.5 G | no | Plex is gone (10-22). Archive it to the NVMe and don't run it |
| `~/spotiflac-service` | 8 M | no | Copy, rebuild the image |
| `~/promtail-agent` | 20 K | no | Copy, set the host label |
| `~/.node-red` | 6.7 M | no | **Retire Node-RED** (Brad 10-25). Archive the folder only. See "Media host hardening" |
| `~/pi-stats`, `pystats3.py` | 27 M | no | Replaced by the MQTT metrics publisher (see below). Archive |
| `~/aria` | 16 K | no | Copy |
| `/etc/exports.d/music.exports` | — | no | Copy as-is (read-only export to 192.168.10.0/24) |
| systemd: `media-pipeline-{trigger,complete}.{path,service}`, `spotiflac-importer`, `transmission-compose-reconcile`, `transmission-lan-firewall` | — | no | Copy the unit files, check paths, enable |
| apt-level: docker, nfs-kernel-server, clamav (+freshclam), smartmontools, node-red | — | — | Install on Ubuntu |
| `/boot/firmware/cmdline.txt` UAS quirks | — | — | **Don't carry over.** The UAS lockup was the Pi's USB controller. Watch the logs on x86 |

### Media host hardening (Brad 10-25)

**Node-RED is retired on the media host.** Today its one flow does four things:

| Job | Status | Replacement |
|---|---|---|
| Every minute: `pystats3.py` → INSERT straight into `hubitat_logging.DeviceMonitoring` (as "Plex Pi") | Live | The **lightweight MQTT publisher** already used on the display Pi (`home_config/display-pi-monitoring/kitchen-display-metrics-mqtt.sh` + `.service`), publishing to `pi/metrics/media-host`. The Beelink Node-RED Device Monitoring tab writes the rows. Keep `DeviceNM = Plex Pi` so the Grafana history continues (or rename + update the dashboard). This also drops the MySQL credential from the media host. |
| Every hour: `plex_organize.py` run **directly, without the `flock` wrapper** | Live, redundant, and risky: it can overlap a real pipeline run | Drop it. The media-pipeline path units plus the host fallback watcher already trigger `run_pipeline_locked.sh` (safe-media-pipeline guide). If a periodic sweep is still wanted, use a systemd timer that calls the **locked** script |
| Every minute: `python3 /home/pi/pyprocess.py` | Dead: the file doesn't exist | Drop |
| MQTT `plex_refresh` in | Dead: Plex was removed on 10-22 | Drop |

**SSH:**
- **No outbound SSH.** The Pi holds no private keys (only `authorized_keys` and `known_hosts`) and none of its scripts call ssh/scp/rsync. Keep it that way on the mini PC: no keys on the media host.
- **Inbound SSH** is the Beelink's admin and deploy path (the guides use `ssh plex` and rsync/scp deploys; no automated job uses it). Keep it, but lock it down:
  - key-only (`PasswordAuthentication no`), `PermitRootLogin no`, `AllowUsers pi`;
  - a firewall rule allowing port 22 **only from the Beelink (192.168.10.217)** plus one admin laptop if wanted.
- **An API instead of SSH** is only worth building if an automated job ever needs to reach the box. None does today.

**Also:** the transmission web UI stays LAN-only via the existing DOCKER-USER rule (copy `transmission-lan-firewall.service` as-is; the IP stays .150).

Containers to bring up afterwards: `media-center`, `transmission`, `vpn` (gluetun), `aria2-with-webui`, `audiobookshelf`, `spotiflac-api`, `promtail-agent`.

Open ports to check after: 2049/111 (NFS), 8790, 9091, 13378, 8787 (1880 should be GONE), 6880/6888, 7878, 8989.

### Kitchen role (mini PC → Pi)

Before anything else, the whole mini PC `/home/pi` (about 1.7 G, minus the venv) and the relevant `/etc` files get archived to the Beelink at `/home/pi/backups/kitchen-swap-2026xxxx/`.

| Item | Plan |
|---|---|
| `~/voice-pipeline` (936 M: `.env`, `assistant.py`, sounds, clips, venv) | Copy everything except `.venv`. **Recreate the venv on arm64** (onnxruntime and livekit-wakeword have aarch64 wheels; this ran on Pi 4s before). Code source = `home_config/voice-assistant/satellite` |
| `~/wake-bench` (175 M: okay_computer_v2, stop_v2, models) | Copy |
| `~/.asoundrc` (Array dmix `kitchenmix`, `respeaker_ch0`/`ch1`) | Copy; the card name `Array` is the same on any host |
| `~/respeaker-led` (xvf_host + venv) | Copy, recreate the venv |
| `~/nfc-jukebox` | `git clone` (clean, pushed) |
| `~/clip-browser.py`, `~/ring-labeler`, `aec-*.sh` | Copy |
| systemd: `voice-assistant`, `squeezelite` (**keep `-m e4:5f:01:67:1e:56`**), `nfc-jukebox-scanner`, `respeaker-led`, `audio-keepalive`, `clip-browser`, `wifi-powersave-off` (→ `wlan0`) | Copy and adapt |
| udev `99-respeaker-led.rules`, sudoers `90-pi-nopasswd` | Copy |
| ALSA: Array USB mixer `PCM` at 0 dB | Set it, then `alsactl store`. **This is the silent-jack trap if forgotten** |
| apt: squeezelite, snapclient, alsa-utils, iw, dfu-util, python3-venv | Install |
| XVF3800 settings (firmware 2.1.0_48k2ch, DTSENSITIVE 13) | Saved on the array's own flash, so they travel with it. Verify after |
| **Do NOT carry over** | `sof-es8336-jd.conf`, the `speaker-gpio-pulse` drop-in, `snd-aloop` / `alsaloop-*` (retired), `rtw88.conf`, `iscsi`, `vmtoolsd`, cpufrequtils governor |
| `~/echo-dot-flash` (591 M), `99-echo-dot-amonet.rules` | Not kitchen. Archive to the Beelink |

**Media services to disable on the Pi** (all of them; the HDD won't be attached, but anything that half-starts would collide with the mini PC):
- all 7 containers, via `restart: no` + stop, and the `docker` service itself;
- `nfs-server` / the music export;
- `nodered` (it posts DeviceMonitoring as "Plex Pi" and would duplicate the mini PC's rows);
- `promtail`, `media-pipeline-*.path`, `spotiflac-importer`, `transmission-*`, `clamav-*`;
- the `/media/externalHDD` fstab line, commented out.

**Keep** `usb-storage.quirks=04e8:61f5:u` in `cmdline.txt`: it's the T5's own UAS fix, and the T5 stays on this Pi.

The Pi's Wi-Fi needs power-save off: NetworkManager `802-11-wireless.powersave 2` plus `iw ... power_save off`. The Pi already has a working Wi-Fi profile (`preconfigured`, currently .151), so no password handling is needed.

**Boot disk for the kitchen Pi: DECIDED 10-25.** The Pi keeps the T5. No SD card, no reinstall.

**After two weeks of stable running: wipe and reinstall the T5 fresh (RECOMMENDED 10-25, Brad to confirm).** The case is plastic, so no antenna concern. The kitchen Pi is a high-trust box (always-on mic, house control, passwordless sudo), and the T5 spent years running internet-facing download tooling. A clean OS is cheap insurance once the media rollback is no longer needed. Steps:
- Back up the kitchen config.
- Flash Pi OS Lite 64-bit (bookworm or newer) onto the T5, using a USB-SATA dock or the Beelink.
- Restore only the kitchen items from the table above.
- Re-apply the T5 UAS quirk in `cmdline.txt` and Wi-Fi power-save off.

Downtime about 1 h.

## Order of work

**Prep, with no downtime (any evening):**
1. Back up both boxes to the Beelink: the mini PC `/home/pi` + `/etc` bits, and the Pi's `/etc` + crontab + unit files + the small configs (everything except podcasts/media-center data, which get copied on swap night).
2. On the mini PC: install docker, nfs-kernel-server, clamav, smartmontools and node-red, but start nothing. Create the `/motherload` target folders.
3. On the Pi, install the kitchen packages ahead of time: squeezelite, snapclient, alsa-utils, iw, dfu-util, python3-venv. Stage the kitchen files but leave the services **disabled**; squeezelite and snapclient must not start yet. The media keeps running meanwhile.
4. Brad: plan a monitor + keyboard visit for the **mini PC BIOS → "Restore on AC Power Loss = Power On"**. It currently stays off after a power cut, which isn't acceptable for the media host.

**Swap night (about 2 h; the kitchen and media are each down for about 1 h):**
5. Shut down both boxes.
6. **Pi:** shut down, unplug the HDD. **Copy route for the media data:** plug the T5 into the mini PC over USB, mount it read-only and copy locally (fast). Then the T5 goes back on the Pi.

   **Mini PC → media closet.** Connect Ethernet and the library HDD.
   - Disable its kitchen services.
   - Turn its Wi-Fi off.
   - Copy the data off the T5, read-only. About 135 G over USB, roughly 20–30 min.
   - Restore the configs, bring up the stacks and the NFS export.
7. **Pi + T5 → kitchen corner.** Connect the ReSpeaker USB, the NFC scanner and power.
   - Boot it, disable the media services (list above), build the venvs, set ALSA, enable the kitchen services.
8. **UniFi app (Brad):** move the .150 and .251 reservations to the new MACs. Renew the leases (reboot both).
9. **Do not delete the media data from the T5 until two weeks have passed.** It's the media rollback.

**Verify (that night):**
- **Kitchen:**
  - Wake + ding from the kitchen and from the family room (relayed ding).
  - A command, an alarm with "stop", the music duck, an NFC card plays.
  - Snapcast party mode.
  - The 300 KB copy test to .251: target 0.4 s, 10 of 10.
  - Pi temperature under load.
- **Media:**
  - The Shield Music/TV app plays.
  - Navidrome and Music Assistant see the NFS music.
  - Transmission works through the VPN; check the leak test from the transmission guide.
  - Audiobookshelf, spotiflac, the media-pipeline path units.
  - Promtail logs reach Grafana.
  - The Node-RED DeviceMonitoring rows show up as "Plex Pi" (rename later if wanted).
- **Next day:** compare `rtt_ms - server_ms` for kitchen wakes in the turns table against 10-24/25.

## Rollback

- **Kitchen:** put the mini PC back in the corner and re-enable its kitchen services. Its config is untouched, just disabled. Point .251 back at `3c:7a:aa:79:21:e1`.
- **Media:** move the Pi (T5 attached) back to the closet with the HDD. Disable the kitchen services and re-enable the media services and the fstab line. Point .150 back at the Pi's Ethernet MAC `e4:5f:01:5a:ee:d4`. The T5 has the media state as of swap night, so anything new since then (downloads, podcast fetches) has to be copied back from the mini PC.

## Open questions for Brad

1. When can you do the BIOS visit (monitor + keyboard on the mini PC)? It can be the same night.
2. The mini PC fan bearing is failing. In a closet the noise doesn't matter, but plan a replacement fan.
