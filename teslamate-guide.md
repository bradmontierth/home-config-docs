# TeslaMate

TeslaMate now runs on the Beelink host instead of the legacy Frigate mini PC.

## Runtime

Directory:

```bash
/home/pi/teslamate
```

Compose services:

```bash
cd /home/pi/teslamate
docker compose ps
docker compose logs --tail 200 teslamate
```

Endpoints:

```text
TeslaMate: http://192.168.10.217:4000
Grafana:   http://192.168.10.217:3002
MQTT:      192.168.10.217:1883
```

TeslaMate publishes to the existing Beelink Mosquitto broker. Topic paths were
kept unchanged:

```text
teslamate/cars/1/#
teslamate/cars/2/#
```

## Migration Notes

Migration date: 2026-06-15

The legacy TeslaMate instance on `192.168.10.250` was on `v1.28.2` and was
getting Tesla Owner API `403` responses. The Beelink deployment runs
`teslamate/teslamate:4.0.1`, which includes the June 2026 Owner API and refresh
token fixes.

The legacy backup used for restore is:

```text
/home/pi/teslamate/backups/teslamate_legacy_20260615_143026.bck
```

The local restore kept the original `ENCRYPTION_KEY` so restored Tesla API
tokens can still be decrypted.

The legacy `.250` TeslaMate app service was stopped after restore to avoid
duplicate Tesla polling and duplicate MQTT publishing. Its database, Grafana,
and Mosquitto containers were left running.

## Node-RED

Active Tesla tab:

```text
7fa25727b15db1f0    Tesla
```

The two TeslaMate MQTT input nodes were moved from the old `.250` broker config
to the Beelink MQTT broker config `82f540b7378c2e35`:

```text
4123d5c1.d02dcc     Yesla      teslamate/cars/1/#
fd1d370d84969272    Threeme    teslamate/cars/2/#
```

The Node-RED backup created before the tab update is:

```text
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json.backup_before_teslamate_migration_20260615_143534
```

Use the Node-RED Admin API workflow from `nodered-flow-agent-guide.md` for any
future flow changes.

## Validation

Useful checks:

```bash
cd /home/pi/teslamate
docker compose ps
curl -fsS -o /dev/null -w 'teslamate_http=%{http_code}\n' http://127.0.0.1:4000/
docker exec mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 -v -C 20 -t 'teslamate/cars/+/+'
docker compose exec -T database psql -U teslamate -d teslamate -c \
  "select c.id,c.name,p.latest_position,now() - p.latest_position as age from cars c left join lateral (select max(date) latest_position from positions where car_id=c.id) p on true order by c.id;"
```
