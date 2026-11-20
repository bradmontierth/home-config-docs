# Display Pi Monitoring Guide

This guide documents monitoring added to `display-pi`, the kitchen touchscreen
dashboard host.

## Historical metrics through MQTT

The lightweight publisher runs continuously and sends one JSON sample per minute:

```text
display-pi / kitchen-display-metrics-mqtt.service
  -> MQTT pi/metrics/kitchen-display-pi
  -> Beelink Node-RED Device Monitoring tab
  -> hubitat_logging.DeviceMonitoring
  -> Grafana Kitchen Display Pi Monitoring
```

It publishes CPU utilization, memory use/utilization, swap, load, uptime, CPU
temperature, root filesystem use/utilization, and interface byte counters. The
database uses `DeviceNM = Kitchen Display Pi`; samples are covered by the shared
90-day `DeviceMonitoring` retention routine.

Grafana dashboard:

```text
http://192.168.10.217:3001/d/kitchen-display-pi-monitoring/kitchen-display-pi-monitoring
```

The dashboard defaults to seven days and refreshes once per minute. Its first
row shows current/average/maximum temperature, CPU, memory, and root storage;
the historical panels chart temperature, utilization, system load, storage,
and memory. Dashboard source is kept in
`display-pi-monitoring/grafana-kitchen-display-pi-monitoring.json`.

The Beelink subscriber is on the system Node-RED instance at port `1881`, tab
`Device Monitoring` (`3453be336e524ff8`). Its deployed nodes are documented in
`display-pi-monitoring/nodered-kitchen-display-metrics-nodes.json`.

Checks:

```bash
ssh display-pi 'systemctl status kitchen-display-metrics-mqtt.service --no-pager'
docker exec mosquitto mosquitto_sub -t pi/metrics/kitchen-display-pi -C 1 -W 70 -v
```

## Evidence logs

Health samples run once per minute:

```bash
ssh display-pi
systemctl status kitchen-display-health.timer
sudo systemctl start kitchen-display-health.service
sudo ls -lh /var/log/kitchen-display-health
sudo sed -n '1,160p' /var/log/kitchen-display-health/latest-health.log
```

Health logs are retained under:

```text
/var/log/kitchen-display-health/
```

Each sample captures uptime/load, memory, swap, disk, temperature, Raspberry Pi
throttle flags, failed systemd units, Wi-Fi state, Chromium-related processes,
recent kernel warnings, and recent relevant journal lines.

## Browser check

A bounded browser check runs every five minutes:

```bash
ssh display-pi
systemctl status kitchen-display-browser-check.timer
sudo systemctl start kitchen-display-browser-check.service
sudo sed -n '1,160p' /var/log/kitchen-display-health/latest-browser-check.log
```

The browser check uses `timeout` around headless Chromium so a screenshot probe
cannot become a long-running orphan. To verify no headless probe remains:

```bash
ps -C chromium -o pid,ppid,stat,pcpu,pmem,etime,args --no-headers | grep -E -- '--headless=new|--screenshot=' || true
```

## Wi-Fi Reconnect Watchdog

The display is a Wi-Fi appliance, so a watchdog checks once per minute that
`wlan0` is connected and can reach the Beelink dashboard host:

```bash
ssh display-pi
systemctl status kitchen-display-wifi-watchdog.timer --no-pager
sudo systemctl start kitchen-display-wifi-watchdog.service
sudo tail -80 /var/log/kitchen-display-health/wifi-watchdog.log
```

The watchdog runs:

```text
/usr/local/sbin/kitchen-display-wifi-watchdog
```

Normal recovery path:

```text
nmcli radio wifi on
nmcli device set wlan0 managed yes
nmcli connection up netplan-wlan0-Mirkwood ifname wlan0 --wait 20
```

If Wi-Fi remains unhealthy for several checks, it restarts
`NetworkManager.service`. This is intentionally scoped to the display Pi; it
does not touch the dashboard backend, Home Assistant, or the kiosk browser.

## Chromium launch logging

Future graphical sessions launch Chromium through:

```text
/usr/local/bin/kitchen-display-chromium
```

That wrapper logs future Chromium starts and exits to:

```text
/home/pi/.local/state/kitchen-display/chromium.log
```

The current session is not restarted when the wrapper is installed. It takes
effect on the next graphical login/session start.

## Snapclient removal

`snapclient` was removed from `display-pi` because the kitchen display no longer
has a speaker attached and is no longer used for music playback.
