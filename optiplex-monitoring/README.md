# Optiplex monitoring

The Optiplex (192.168.10.49, Ubuntu 26.04) runs Lookout and is the camera hub
Frigate reads from. Same pipeline as the Pis:

```text
optiplex-metrics-mqtt.service (optiplex-metrics.py | mosquitto_pub -l)
  -> MQTT pi/metrics/optiplex, one JSON sample per minute
  -> Beelink system Node-RED :1881, tab Device Monitoring (3453be336e524ff8), nodes optx_*
  -> hubitat_logging.DeviceMonitoring, DeviceNM = 'Optiplex' (shared 90-day retention)
  -> Grafana http://192.168.10.217:3001/d/optiplex-monitoring/optiplex-monitoring
```

Fields: the Pi set (CPU, memory, swap, load, uptime, CPU package temp, root disk,
interface bytes) plus `cpu_mhz`, `rx_mbps`/`tx_mbps`, the 6 TB archive disk
(`disk_*_archive*`), `ssd_temp_c`/`hdd_temp_c` (smartctl every 5 min), and Lookout
health: `cams_total`, `cams_recording` (cameras with a minute indexed in the last
3 min, so it also drops when indexing lags), `retention_h`, `hot_gb`/`archive_gb`,
`lookout_containers` (of 3), `go2rtc_up`.

Alerts (Pushover, Brad's phone via `BradPhoneNM`, node `Optiplex push`). Quiet by
design: Wi-Fi cameras drop now and then and the recorder's stall watchdog restarts
them, so only sustained problems alert, once each, with one recovery message.

- **Optiplex OFFLINE** after 5 min without a sample (priority 1: Frigate has no video
  while it's down); "back online" on the next sample.
- **Camera down: <name>**: no footage written for 30 min (`cams_down`, from the files
  on disk); "Camera back" when it records again.
- **Lookout timeline behind**: index more than 30 min behind (`index_lag_s`); clears
  under 10 min.
- **Lookout service down**: go2rtc down or <3 Lookout containers for 5 samples.

Fields added 2026-10-06 for this: `cams_recording` now counts cameras whose newest
file grew in the last 3 min (was: indexed minutes), `cams_down_count` / `cams_down`
(names, text, not stored) and `index_lag_s`.

Install / update on the Optiplex:

```bash
scp optiplex-metrics.py optiplex-metrics-mqtt.service optiplex:/tmp/
ssh optiplex 'sudo install -m 755 /tmp/optiplex-metrics.py /usr/local/bin/optiplex-metrics &&
  sudo install -m 644 /tmp/optiplex-metrics-mqtt.service /etc/systemd/system/ &&
  sudo systemctl daemon-reload && sudo systemctl restart optiplex-metrics-mqtt.service'
docker exec mosquitto mosquitto_sub -t pi/metrics/optiplex -C 1 -W 70
```

The Grafana dashboard source is `grafana-optiplex-monitoring.json` (re-import with
`POST /api/dashboards/db`, anonymous editor). Node-RED tab backup from before the
change: `/home/pi/nodered-1881-backups/device-monitoring-before-optiplex-*.json`.

First finding (2026-10-06): the dashboard showed 3/13 cameras recording. The
recorder was capped at 1 CPU (an N100 setting) and indexed minutes one at a time,
so the index fell up to ~1 h behind. Fixed with RECORDER_CPUS=4 and INDEX_WORKERS=3.
