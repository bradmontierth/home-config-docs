# pixel-monitor

Logs Brad's Pixel 8 Pro performance once a minute over wireless adb into MariaDB
`hubitat_logging` (beelink :3306), charted in Grafana:

    http://192.168.10.217:3001/d/pixel-monitor/pixel-phone-performance

Lag marker: open http://192.168.10.217:8814/lag on the phone when it lags (home-screen shortcut).

- Service: `systemctl --user status pixel-monitor` (unit symlinked from this dir); logs `journalctl --user -u pixel-monitor`
- DB creds: `/home/pi/cecret_lake/pixel_monitor/mysql.env`; state: `~/.local/state/pixel-monitor/state.json`
- Tables: `pixel_metrics` (1/min), `pixel_cpu` (top 5/min), `pixel_proc_mem` (top 30 every 5 min),
  `pixel_events` (lmk_kill, launch, anr, crash, skipped_frames, davey, lag_mark, reboot).
  `pixel_cpu`/`pixel_proc_mem` pruned at 180 days; metrics + events kept.
- Dashboard source: `make_dashboard.py` (regenerates `grafana-pixel-monitor.json` and pushes it).
- Reconnects by mDNS name `adb-38241FDJG0097U` when the wireless-debugging port changes.
  Phone pairing is persistent; if Wireless debugging is switched off (reboot / Wi-Fi change), turn it back on.
- Never add `dumpsys meminfo`/`procstats` to the per-minute loop: on a full-swap phone they page every app back in.
