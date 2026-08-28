# Master Closet Satellite — Monitoring & Self-Recovery Guide

Companion to `master-closet-satellite-build.md`. Written 2026-08-27 after the
closet Pi (`master-closet-assist`, 192.168.10.24, Wi-Fi on Mirkwood) silently
dropped off the network on the evening of 2026-08-25 and was only noticed two
days later when a wake word went unanswered. The shelf mains never blinked (the
`closetleds` controller beside it had 33 days of uptime) — the Pi itself hung,
or its Wi-Fi died, and nothing on the box could tell us which because the
journal was volatile. A power cycle brought it back.

Files live in `home_config/master-closet-monitoring/`. `install.sh` there is
idempotent and does everything in §1; run it **on the Pi** from that directory.

## 1. What runs on the Pi now

| Piece | Unit / file | Why |
| --- | --- | --- |
| Persistent journal | `/etc/systemd/journald.conf.d/persistent.conf` (200 MB cap) | `journalctl -b -1` works after a crash. Was volatile: `/var/log/journal` existed but was empty (same trap as pw_pi before 2026-07-30). |
| Hardware watchdog | `/etc/systemd/system.conf.d/watchdog.conf` → `RuntimeWatchdogSec=15s` (BCM2835 wdt) | A hung kernel reboots itself in 15 s instead of waiting for a human. Verified: `systemctl show -p RuntimeWatchdogUSec` = 15s, kernel log "Using hardware watchdog 'Broadcom BCM2835 Watchdog timer'". |
| Metrics publisher | `master-closet-metrics-mqtt.service` → `/usr/local/bin/master-closet-metrics-mqtt` | One JSON sample/min to MQTT `pi/metrics/master-closet-pi`, `device: "Master Closet Pi"`. Same script as display-pi with the topic/name swapped. Needs `mosquitto-clients` (installed 2026-08-27). |
| Wi-Fi watchdog | `master-closet-wifi-watchdog.timer` (1 min) → `/usr/local/sbin/master-closet-wifi-watchdog` | display-pi's script verbatim: link down or Beelink unreachable → `nmcli connection up netplan-wlan0-Mirkwood`; 5 consecutive failures → restart NetworkManager. Log `/var/log/master-closet-health/wifi-watchdog.log`. |

`voice-assistant.service` already had `Restart=always`, which covers a crashed
satellite process — the 08-25 failure was below that layer.

## 2. Beelink side

```text
master-closet-assist / master-closet-metrics-mqtt.service
  -> MQTT pi/metrics/master-closet-pi
  -> system Node-RED :1881, tab Device Monitoring (3453be336e524ff8), nodes mcpi_*
     -> hubitat_logging.DeviceMonitoring  (DeviceNM = Master Closet Pi, 17 stats)
     -> "silent 5 min?" trigger -> Pushover "Master Closet Pi OFFLINE"
        (next sample after an outage -> Pushover "Master Closet Pi back online")
  -> Grafana http://192.168.10.217:3001/d/master-closet-pi-monitoring
```

The dedicated topic + allow-listed function mirrors the kitchen-display chain on
purpose. There is also a generic `pi/metrics` subscriber on that tab (used by
`speaker-pi`) that inserts *every* payload key — including the `device` and
`iface` strings — into the numeric column; don't route new devices through it.

Node-RED nodes are in `master-closet-monitoring/nodered-master-closet-metrics-nodes.json`;
they were added with a tab-scoped `PUT /flow/3453be336e524ff8` (backup of the
tab beforehand in `/home/pi/nodered-1881-backups/`). The Grafana dashboard is a
clone of the live kitchen-display one with the name swapped
(`master-closet-monitoring/grafana-master-closet-pi-monitoring.json`, imported
via the anonymous-Editor API — no credentials needed).

**Offline alert semantics.** The trigger node re-arms on every sample and
fires once after 5 min of silence; the heartbeat function sends one recovery
push when samples resume. A Node-RED deploy of that tab resets the trigger, so
if the Pi is *already* down during a deploy no alert fires until it comes back
— check Grafana in that case. Pushes go through the existing `pushover-keys`
config on that tab (no per-device targeting, same as the tab's other node).

**Verified 2026-08-27:** publisher stopped 18:35:51 → "Master Closet Pi
OFFLINE" push sent 18:40:21; publisher restarted 18:49:05 → "back online" push
sent 18:49:17 on the first sample. Both show in the :1881 log as
`[pushover api:Master closet push] pushover POST succeeded`.

## 3. Checks

```bash
ssh master-closet-assist 'systemctl status master-closet-metrics-mqtt master-closet-wifi-watchdog.timer --no-pager; journalctl --list-boots; vcgencmd measure_temp; vcgencmd get_throttled'
docker exec mosquitto mosquitto_sub -t pi/metrics/master-closet-pi -C 1 -W 70 -v
```

Last-seen via the DB (Grafana datasource `n100mini`):
`SELECT max(id) FROM hubitat_logging.DeviceMonitoring WHERE DeviceNM='Master Closet Pi'`.

## 4. The open problem: heat

Measured 2026-08-27 within minutes of boot: **78–81 °C**, `throttled=0x0`, the
satellite pinning ~1.5 cores. **No HAT EEPROM, no fan, no cooling device** —
this is the same Pi 4 that hit the 81–82 °C soft-throttle in the kitchen with
its fan removed (`master-closet-satellite-build.md` §3). The 80 °C soft limit is
where the firmware starts clocking down; a Wi-Fi firmware (brcmfmac) dropout
under heat is a plausible cause of the 08-25 outage and exactly the kind of
thing the metrics will now show. Until a fan or the PoE hat (which ships with
one) is on it, treat `Temp C` on the Grafana dashboard as the number to watch.
If it ever needs relief in software, `HOP_MS` above 320 buys temperature at the
cost of detect lag.
