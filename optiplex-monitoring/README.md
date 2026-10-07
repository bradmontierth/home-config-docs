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

Alerts (Pushover, Brad's phone via `BradPhoneNM`, node `Optiplex push`):

- **Optiplex OFFLINE** after 5 min without a sample; "back online" on the next one.
  Priority 1: Frigate has no video while the Optiplex is down.
- **Lookout service down**: go2rtc down or <3 Lookout containers for 3 samples.
- **Lookout: cameras missing from recordings**: `cams_recording < cams_total` for
  10 samples (a camera stopped, or the index is behind and the timeline is stale).
  Each alert sends one recovery push.

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
