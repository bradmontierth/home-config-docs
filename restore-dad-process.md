# Dad N100 Restore Process

This guide captures the restore done on 2026-06-27 after the N100 SSD failure.

## Hosts

- Beelink/admin host: `100.79.129.106`
- Dad N100 server: `n100`, Tailscale `100.88.96.43`, LAN `192.168.123.47`
- Display panel: `montierth-display` / `smartpanel`, Tailscale `100.121.83.104`, LAN `192.168.123.136`

Useful access:

```bash
tailscale ssh root@100.88.96.43
tailscale ssh admin@100.121.83.104
```

The display also accepts LAN SSH from N100:

```bash
ssh admin@192.168.123.136
```

## S3 Sources

Local Beelink AWS profile used for restore:

```bash
AWS_PROFILE=homelab-backup
```

Known restore objects:

```text
s3://bradmontierth/dad_docker/docker_compose_backup_Sun.tar.gz
s3://bradmontierth/dad_homeassistant/homeassistant_Sun.tar
s3://bradmontierth/dad_sql/mysqldump_Mon.sql.gz
s3://bradmontierth/dad_grafana/grafana.db
s3://bradmontierth/NodeRedFlowsDad.json
```

## Restored Layout

Main stack:

```text
/home/markmontierth/dad_stack
├── grafana
├── homeassistant
├── mysql
├── nodered
└── restore
```

Photo viewer:

```text
/home/markmontierth/grandkid_photo_app/rpi_client
/home/markmontierth/cecret_lake/my_photo_app_client/.env
```

## Services

Running containers:

```bash
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Expected services:

```text
homeassistant       http://192.168.123.47:8123/
node_red            http://192.168.123.47:1880/
grafana             http://192.168.123.47:3000/
mqtt                192.168.123.47:1883
mariadb_container   192.168.123.47:3306
rpi_client          http://192.168.123.47:9010/
frigate             http://192.168.123.47:8971/
```

External Tailscale URLs are also available through `100.88.96.43`.

## Restore Summary

1. Installed Docker and Compose on N100.
2. Restored compose files from `dad_docker`.
3. Restored Home Assistant config from `dad_homeassistant/homeassistant_Sun.tar`.
4. Pinned Home Assistant image to the backup version, `2025.11.3`.
5. Created local Mosquitto config.
6. Restored MariaDB from `dad_sql/mysqldump_Mon.sql.gz`.
7. Restored Grafana SQLite DB from `dad_grafana/grafana.db`.
8. Restored Node-RED flows from `NodeRedFlowsDad.json` through the Admin API.
9. Repaired Node-RED MySQL credentials in `flows_cred.json`.
10. Restored the grandkid photo viewer client from Beelink source.
11. Fixed display kiosk startup to use the restored N100 address.
12. Enabled Tailscale SSH on `montierth-display`.
13. Added local nightly backups.
14. Restored Frigate on N100 with `driveway` and `frontdoor` cameras.
15. Reconnected the Home Assistant Frigate integration to the local Frigate API.
16. Added a Browser Mod automation to show the front door camera on the kitchen display when Frigate detects a person in the entry zone.
17. Updated the kitchen dashboard camera cards to use Frigate entities directly.

## Validation

Check service HTTP endpoints from N100:

```bash
curl -s -o /dev/null -w 'ha:%{http_code}\n' http://127.0.0.1:8123/
curl -s -o /dev/null -w 'grafana:%{http_code}\n' http://127.0.0.1:3000/
curl -s -o /dev/null -w 'nodered:%{http_code}\n' http://127.0.0.1:1880/
curl -s -o /dev/null -w 'photo:%{http_code}\n' http://127.0.0.1:9010/
curl -s -o /dev/null -w 'frigate:%{http_code}\n' http://127.0.0.1:5000/api/version
```

Check Node-RED flows:

```bash
curl -s http://127.0.0.1:1880/flows | jq length
```

Expected restored flow count:

```text
179
```

Check MariaDB:

```bash
cd /home/markmontierth/dad_stack
MYSQL_PWD="$(awk -F: '/MYSQL_ROOT_PASSWORD:/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}' mysql/docker-compose.yml)" \
  mariadb -h127.0.0.1 -uroot -e \
  'SELECT TABLE_SCHEMA, COUNT(*) AS tables_count FROM information_schema.tables WHERE table_schema="hubitat_logging" GROUP BY TABLE_SCHEMA;'
```

Expected table count:

```text
hubitat_logging    10
```

Avoid `COUNT(*)` on the `weather` table during routine checks; it has tens of millions of rows.

Check weather ingestion:

```bash
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mariadb -h127.0.0.1 -uroot -e \
  "SELECT name, value, created FROM hubitat_logging.weather ORDER BY created DESC LIMIT 20;"
```

Check MQTT discovery:

```bash
timeout 6 docker exec mqtt mosquitto_sub \
  -h 127.0.0.1 \
  -t 'homeassistant/sensor/weatherstation/#' \
  -C 30 -v
```

Expected HA MQTT entities include:

```text
sensor.weather_station_indoor_temperature
sensor.weather_station_outdoor_temperature
sensor.weather_station_outdoor_humidity
sensor.weather_station_wind_speed
sensor.weather_station_daily_rainfall
```

Check photo viewer:

```bash
curl -s http://127.0.0.1:9010/api/random-photo | jq '{count:(.items|length), first:.items[0]}'
```

Expected result after first sync:

```text
count: 1 or 2
```

The first photo sync can take several minutes. During the initial sync, `/api/random-photo` may return an empty list until the whole manifest has been downloaded and committed.

Check Frigate:

```bash
curl -s http://127.0.0.1:5000/api/stats | jq \
  '{version:.service.version, detectors:.detectors, cameras:{driveway:.cameras.driveway.camera_fps, frontdoor:.cameras.frontdoor.camera_fps}, gpu:.gpu_usages}'
```

Expected:

```text
Frigate 0.17.2
OpenVINO detector named ov
driveway and frontdoor around 5 camera fps
Intel QSV/iGPU stats present
```

The Frigate UI is exposed at:

```text
http://192.168.123.47:8971/
```

The generated Frigate admin login from the 2026-06-28 install was:

```text
user: admin
password: <redacted>
```

Change this in Frigate's user settings if retaining authenticated UI access.

## Display Kiosk

Display autostart file:

```text
/home/admin/.config/labwc/autostart
```

Expected Chromium URL:

```text
http://192.168.123.47:8123/kitchen-dasher/0
```

The kitchen dashboard camera cards should use the Frigate entities directly:

```text
Entry Camera: camera.frontdoor
Driveway Cam: camera.driveway
```

Do not leave old generic camera overrides such as `camera_image:
camera.192_168_123_60_2` on these cards. After the 2026-06-28 restore, that
stale reference caused `Camera not found` websocket errors from the kiosk user
at the display IP `192.168.123.136`.

Check from the display:

```bash
curl -sS --max-time 8 -o /dev/null -w 'ha_dash:%{http_code}\n' \
  http://192.168.123.47:8123/kitchen-dasher/0

curl -sS --max-time 8 -o /dev/null -w 'photo_api:%{http_code}\n' \
  http://192.168.123.47:9010/api/random-photo
```

Restart the physical kiosk browser if needed:

```bash
tailscale ssh admin@100.121.83.104

pkill -f '[c]hromium.*kitchen-dasher' || true
```

`lwrespawn` should normally restart Chromium. If it was killed too, rebooting the display is the cleanest recovery:

```bash
sudo reboot
```

## Current Known Warnings

Node-RED still needs external integration credentials:

```text
Hubitat token invalid
Alexa re-auth needed
Pushover keys missing
S3 backup node credentials missing
```

Home Assistant still reports external dependencies:

```text
Harmony hub connectivity may need local network/device follow-up
```

The weather station payload often omits `eventrainin`, which causes a Home Assistant template warning for `sensor.weather_station_event_rain`.

## Backups

Local backup script:

```text
/home/markmontierth/scripts/dad_backup/dad_stack_backup.sh
```

Local backup output:

```text
/home/markmontierth/backups/dad_stack/daily
/home/markmontierth/backups/dad_stack/logs
```

Nightly systemd timer:

```bash
systemctl status dad-stack-backup.timer --no-pager
systemctl list-timers dad-stack-backup.timer --no-pager
```

The local backup includes:

```text
Docker compose files
Home Assistant config, excluding large DB/cache/log files
Frigate config, excluding DB/model cache/media
MariaDB logical dump
Grafana DB
Node-RED flows, credentials, settings
Photo viewer compose/env
```

It does not include:

```text
cached photo media
container images
Frigate recordings/database/media
```

## S3 Backup Credentials

S3 upload is enabled as of 2026-06-27.

Template on N100:

```text
/home/markmontierth/.config/dad-backup/backup.env.example
```

Active N100 env file:

```text
/home/markmontierth/.config/dad-backup/backup.env
```

Dedicated S3 destination:

```text
s3://bradmontierth/dad_n100_backups/
```

The local Beelink AWS profiles available during the 2026-06-27 restore were
writer profiles only. They could not create IAM users or access keys:

```text
arn:aws:iam::967344250279:user/homelab-backup-writer
arn:aws:iam::967344250279:user/immich-backup-writer
```

Creating the dedicated key requires an AWS admin profile or the AWS console.

IAM user:

```text
dad-n100-backup-writer
```

Recommended inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WriteDadN100BackupObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::bradmontierth/dad_n100_backups/*"
    }
  ]
}
```

Admin CLI creation sequence used:

```bash
cat >/tmp/dad-n100-backup-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WriteDadN100BackupObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::bradmontierth/dad_n100_backups/*"
    },
    {
      "Sid": "ListOnlyDadN100BackupPrefix",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::bradmontierth",
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "dad_n100_backups",
            "dad_n100_backups/*"
          ]
        }
      }
    }
  ]
}
JSON

AWS_PROFILE=<admin-profile> aws iam create-user \
  --user-name dad-n100-backup-writer

AWS_PROFILE=<admin-profile> aws iam put-user-policy \
  --user-name dad-n100-backup-writer \
  --policy-name dad-n100-backup-writer-s3 \
  --policy-document file:///tmp/dad-n100-backup-policy.json

AWS_PROFILE=<admin-profile> aws iam create-access-key \
  --user-name dad-n100-backup-writer
```

The access key was installed on N100 in `backup.env`. Values are intentionally
not stored in this repo.

If the key ever needs to be rotated, create a new access key for
`dad-n100-backup-writer`, update `backup.env`, run a verified backup, then
delete the old access key.

To recreate the N100 env file after rotating credentials:

```bash
cp /home/markmontierth/.config/dad-backup/backup.env.example \
   /home/markmontierth/.config/dad-backup/backup.env

chmod 600 /home/markmontierth/.config/dad-backup/backup.env
```

Fill in:

```text
DAD_BACKUP_PASSPHRASE
DAD_BACKUP_UPLOAD_S3=1
DAD_BACKUP_S3_URI=s3://bradmontierth/dad_n100_backups
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION
```

Store `DAD_BACKUP_PASSPHRASE` somewhere outside N100. If the passphrase only lives on the failed disk, encrypted backups cannot be restored after another disk failure.

After filling the env file, run:

```bash
/home/markmontierth/scripts/dad_backup/dad_stack_backup.sh
```

Then verify the archive and checksum exist in S3. The backup script exports
variables from `backup.env`, uploads the encrypted archive and checksum, then
verifies the archive with `aws s3api head-object`.

First successful S3 upload:

```text
s3://bradmontierth/dad_n100_backups/dad_stack_20260628T000807Z.tar.gz.gpg
s3://bradmontierth/dad_n100_backups/dad_stack_20260628T000807Z.tar.gz.gpg.sha256
```

Verified encrypted archive size:

```text
220152444 bytes
```

## Frigate

Frigate was restored on 2026-06-28.

Layout:

```text
/home/markmontierth/dad_stack/frigate
├── config/config.yml
├── docker-compose.yml
└── media
```

Compose:

```bash
cd /home/markmontierth/dad_stack/frigate
docker compose ps
docker compose logs --tail 120
docker compose up -d
```

Current image:

```text
ghcr.io/blakeblackshear/frigate:0.17.2
```

Cameras:

```text
driveway   rtsp://admin:<password>@192.168.123.60:554/cam/realmonitor?channel=1&subtype=1
frontdoor  rtsp://admin:<password>@192.168.123.60:554/cam/realmonitor?channel=2&subtype=1
```

Detector and hardware acceleration:

```text
OpenVINO detector: ov
Intel QSV ffmpeg hwaccel
/dev/dri passed through to container
```

Home Assistant integration:

```text
http://127.0.0.1:5000
```

The authenticated Frigate UI remains available on port `8971`; Home Assistant uses the local unauthenticated API on port `5000`.

Restored HA entities include:

```text
camera.driveway
camera.frontdoor
binary_sensor.entry_person_occupancy
binary_sensor.frontdoor_person_occupancy
sensor.frontdoor_person_count
```

Doorbell popup automation:

```text
Home Assistant automation: Front Door Person Display Popup
Trigger: binary_sensor.entry_person_occupancy from off to on
Target browser_id: browser_mod_b704bb32_4971aa79
Popup content: camera.frontdoor
Timeout: 60 seconds
```

Zone tuning:

```text
frontdoor.entry      lower/entry portion of the frame
```

The current `entry` polygon was recreated from intent, not from the old lost config. Tune it in Frigate's debug view after looking at the live camera frame. Frigate alerts and detections for `frontdoor` require the `entry` zone, so people/cars outside that zone should not trigger the display popup.
