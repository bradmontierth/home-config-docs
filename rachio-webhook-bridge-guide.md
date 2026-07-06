# Rachio Webhook Bridge Guide

This host intentionally does not expose Home Assistant to the public internet. The built-in Home Assistant Rachio integration is still useful for manual control, but it does not receive webhook events unless Home Assistant has a public callback URL. This bridge provides event updates without exposing Home Assistant.

## Architecture

Rachio sends HTTPS webhook events to a Cloudflare Worker:

```text
https://inbox.illuminatehealthanalytics.com/r/<long-random-path-token>
```

The Worker stores each event in a Cloudflare D1 database. A local Docker container on this host polls the Worker drain endpoint over outbound HTTPS:

```text
https://inbox.illuminatehealthanalytics.com/drain
```

The local bridge verifies Rachio's `x-signature` HMAC-SHA256 header using the Rachio API token, deduplicates by `eventId`, then publishes verified events to Mosquitto. Node-RED consumes those MQTT events and publishes Home Assistant MQTT discovery/state topics.

No inbound ports to Home Assistant or the bridge are required.

## Local Files

Project:

```text
/home/pi/rachio-webhook-bridge
```

Important files:

```text
/home/pi/rachio-webhook-bridge/docker-compose.yml
/home/pi/rachio-webhook-bridge/src/bridge.js
/home/pi/rachio-webhook-bridge/worker/worker-d1.js
/home/pi/rachio-webhook-bridge/worker/schema.sql
/home/pi/rachio-webhook-bridge/nodered/rachio-flow.json
/home/pi/rachio-webhook-bridge/scripts/person-info.js
/home/pi/rachio-webhook-bridge/scripts/list-event-types.js
/home/pi/rachio-webhook-bridge/scripts/register-webhook.js
```

Secrets and runtime config:

```text
/home/pi/cecret_lake/rachio/.env
```

That file contains the Cloudflare drain URL/token, Rachio API token, MQTT settings, polling interval, and registered Rachio resource/webhook settings.

## Cloudflare

Worker custom domain:

```text
inbox.illuminatehealthanalytics.com
```

Worker binding:

```text
RACHIO_DB
```

The binding points to a Cloudflare D1 database with this table:

```sql
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  received_at TEXT NOT NULL,
  body TEXT NOT NULL,
  headers TEXT NOT NULL,
  cf_connecting_ip TEXT
);
```

The Worker source of truth in this repo is:

```text
/home/pi/rachio-webhook-bridge/worker/worker-d1.js
```

If Cloudflare Worker behavior looks wrong, compare the deployed Worker code with that local file.

## Rachio Registration

Registered controller:

```text
Name: Home
Resource key: irrigationControllerId
Resource value: 4bf8fea1-7b1a-4a35-8b45-e87f3f3edf9b
```

Webhook external ID:

```text
home-rachio-bridge
```

Registered event types include:

```text
SCHEDULE_STARTED_EVENT
SCHEDULE_STOPPED_EVENT
SCHEDULE_COMPLETED_EVENT
DEVICE_ZONE_RUN_STARTED_EVENT
DEVICE_ZONE_RUN_PAUSED_EVENT
DEVICE_ZONE_RUN_STOPPED_EVENT
DEVICE_ZONE_RUN_COMPLETED_EVENT
CLIMATE_SKIP_NOTIFICATION_EVENT
FREEZE_SKIP_NOTIFICATION_EVENT
RAIN_SKIP_NOTIFICATION_EVENT
WIND_SKIP_NOTIFICATION_EVENT
NO_SKIP_NOTIFICATION_EVENT
```

Helper commands:

```bash
cd /home/pi/rachio-webhook-bridge
docker compose run --rm rachio-webhook-bridge node scripts/person-info.js
docker compose run --rm rachio-webhook-bridge node scripts/list-event-types.js
docker compose run --rm rachio-webhook-bridge node scripts/register-webhook.js
```

## Docker Service

Container name:

```text
rachio-webhook-bridge
```

Check status:

```bash
docker ps --filter name=rachio-webhook-bridge
docker logs --tail 100 rachio-webhook-bridge
```

Restart:

```bash
cd /home/pi/rachio-webhook-bridge
docker compose up -d --force-recreate
```

Current intended polling interval:

```text
POLL_SECONDS=10
```

KV was originally used, but it was migrated to D1 because KV has a low free-tier `list` operation limit. Do not switch the Worker back to KV unless the polling model is also changed.

## MQTT Topics

Bridge publishes:

```text
rachio/events/verified
rachio/events/rejected
rachio/bridge/status
rachio/bridge/last_drain
rachio/bridge/error
```

Node-RED publishes Home Assistant discovery/state topics, including:

```text
homeassistant/sensor/rachio/...
homeassistant/binary_sensor/rachio/...
rachio/controller/<controllerId>/zone/<zoneNumber>/event
rachio/controller/<controllerId>/zone/<zoneNumber>/state
```

Quick MQTT checks:

```bash
docker exec mosquitto mosquitto_sub -v -t 'rachio/#'
docker exec mosquitto mosquitto_sub -t rachio/bridge/last_drain -C 1 -W 15
```

## Node-RED

Importable flow:

```text
/home/pi/rachio-webhook-bridge/nodered/rachio-flow.json
```

The flow has a bootstrap inject node named:

```text
Publish discovery for all zones
```

It runs once on deploy and publishes retained Home Assistant MQTT discovery plus initial inactive state for all zones. This allows entities to exist even when sprinklers are off for the season.

The webhook-derived `Active` entity is a read-only Home Assistant MQTT `binary_sensor`, not a `switch`. It turns on/off from Rachio webhook events:

```text
DEVICE_ZONE_RUN_STARTED_EVENT -> active true
DEVICE_ZONE_RUN_STOPPED_EVENT / DEVICE_ZONE_RUN_COMPLETED_EVENT -> active false
```

Manual control should remain with the built-in Rachio integration unless a separate command bridge is deliberately added.

## Troubleshooting Sensor Updates

Start with these checks in order.

1. Confirm the bridge container is running:

```bash
docker ps --filter name=rachio-webhook-bridge
docker logs --tail 100 rachio-webhook-bridge
```

2. Confirm Cloudflare drain auth and D1 connectivity:

```bash
set -a
. /home/pi/cecret_lake/rachio/.env
set +a
curl -i "$CF_DRAIN_URL"
curl -sS -H "Authorization: Bearer $CF_DRAIN_TOKEN" "$CF_DRAIN_URL"
```

Expected unauthenticated response is `401`. Expected authenticated shape is:

```json
{"ok":true,"events":[],"remaining":0}
```

3. Confirm bridge drain status in MQTT:

```bash
docker exec mosquitto mosquitto_sub -t rachio/bridge/last_drain -C 1 -W 15
```

Look for recent `drainedAt` timestamps and non-error counts.

4. Watch verified and rejected events:

```bash
docker exec mosquitto mosquitto_sub -v -t 'rachio/events/#'
```

If events appear under `rejected`, inspect signature handling and confirm `RACHIO_API_TOKEN` is current.

5. Confirm Node-RED has the Rachio flow imported and deployed. Trigger the manual inject node `Publish discovery for all zones` if Home Assistant entities are missing.

6. Confirm Home Assistant MQTT discovery is enabled and that retained discovery messages exist:

```bash
docker exec mosquitto mosquitto_sub -v -t 'homeassistant/+/rachio/+/config' -C 5 -W 10
```

7. If real Rachio activity is happening but no events arrive, verify the webhook registration:

```bash
cd /home/pi/rachio-webhook-bridge
docker compose run --rm rachio-webhook-bridge node scripts/register-webhook.js
```

Registration is idempotent enough for practical recovery, but check the Rachio response for errors or changed resource requirements.

## Safe Manual End-To-End Test

This posts a fake signed event through Cloudflare using the real local API token. It should be drained by the local bridge and published to `rachio/events/verified`.

```bash
set -a
. /home/pi/cecret_lake/rachio/.env
set +a

body='{"eventId":"manual-test-1","eventType":"MANUAL_TEST","resourceId":"test-resource","resourceType":"TEST","payload":{"zoneNumber":1}}'
sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$RACHIO_API_TOKEN" -hex | awk '{print $2}')

curl -sS -X POST \
  -H 'content-type: application/json' \
  -H "x-signature: $sig" \
  --data "$body" \
  "$RACHIO_WEBHOOK_URL"
```

Then watch:

```bash
docker exec mosquitto mosquitto_sub -v -t 'rachio/events/verified' -C 1 -W 20
```
