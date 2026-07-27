# Node-RED Flow Agent Guide

This note documents how agents can inspect, modify, and monitor the active Node-RED flows on this host.

## Runtime Layout

This host currently has both a Docker Node-RED container and a system Node-RED service. Check the active runtime before editing backup flows.

As of 2026-05-13, `systemctl list-units` showed `nodered.service` active, and the active backup flow inspected for homelab backups was under:

```text
/home/pi/.node-red/projects/nodered/flows.json
```

The Docker Node-RED instance also exists and may still be used for other flows:

Node-RED runs in Docker from:

```bash
cd /home/pi/nodered
docker compose ps
```

The main container is expected to be:

```text
node-red-container-main
```

The Docker compose file mounts the local data directory into the container:

```text
/home/pi/nodered/data -> /data
```

This instance is running Node-RED Projects. The current on-disk project flow file is:

```text
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json
```

The top-level file also exists:

```text
/home/pi/nodered/data/flows.json
```

Do not treat the top-level `data/flows.json` as current on this host. As of 2026-05-08 it was stale while the Admin API and project file had the live deployed flows.

Treat the Node-RED Admin API as the source of truth for the running instance. Use the project file as the current on-disk reference copy.

## Finding A Flow Tab

List tabs from the active flow file:

```bash
cd /home/pi/nodered
jq -r '.[] | select(.type=="tab") | [.id,.label,.disabled] | @tsv' data/projects/nodered_n100_mini/flows.json
```

Find one tab, for example Flume:

```bash
jq -r '.[] | select(.type=="tab") | [.id,.label,.disabled] | @tsv' data/projects/nodered_n100_mini/flows.json | rg -i flume
```

List nodes on a tab:

```bash
jq -r '.[] | select(.z=="TAB_ID") | [.id,.type,(.name // .label // ""),(.disabled // false)] | @tsv' data/projects/nodered_n100_mini/flows.json
```

For the current Flume work, the original tab was:

```text
33292b7b.b59f84    Flume
```

The replacement Home Assistant sourced tab was created separately:

```text
b6adae965b636183   Flume HA Water Monitor
```

## Use The Admin API For Running Flows

The Node-RED Admin API is reachable locally:

```bash
curl -s http://127.0.0.1:1880/flows
curl -s http://127.0.0.1:1880/flow/TAB_ID
```

Get a compact summary of one tab:

```bash
curl -s http://127.0.0.1:1880/flow/TAB_ID | jq '{id,label,disabled,nodeCount:(.nodes|length)}'
```

When changing a single tab, prefer `PUT /flow/TAB_ID` or `POST /flow` instead of posting the entire `/flows` array. That keeps the deploy scoped to that one flow tab.

Examples:

```bash
curl -s http://127.0.0.1:1880/flow/TAB_ID
```

```bash
curl -s -X PUT http://127.0.0.1:1880/flow/TAB_ID \
  -H 'Content-Type: application/json' \
  --data-binary @updated-flow.json
```

```bash
curl -s -X POST http://127.0.0.1:1880/flow \
  -H 'Content-Type: application/json' \
  --data-binary @new-flow.json
```

## Safe Change Workflow

Before editing or deploying, create a timestamped backup:

```bash
cd /home/pi/nodered
cp data/projects/nodered_n100_mini/flows.json data/projects/nodered_n100_mini/flows.json.backup_before_change_$(date +%Y%m%d_%H%M%S)
```

Recommended workflow:

1. Identify the tab id from the Admin API or `data/projects/nodered_n100_mini/flows.json`.
2. Pull the tab from the Admin API with `GET /flow/TAB_ID`.
3. Modify only that tab JSON.
4. Deploy with `PUT /flow/TAB_ID`.
5. Confirm the API returns the same tab id.
6. Check logs for errors.
7. Re-read the tab from the API and verify node/entity ids.

Avoid broad full-flow deploys unless the task explicitly requires cross-tab config changes.

## Gotchas (learned 2026-07-22, voice-buttons brighten rework)

- **One-sided `link out` does NOT deliver.** Adding a `link out` on a new tab
  whose `links` array points at an existing `link in` is silently dead unless
  the TARGET `link in` node's own `links` array also lists the new `link out`
  id. Always update both sides (requires deploying the target's tab too).
- **`PUT /flow/TAB_ID` restarts that tab's nodes** — inject-on-deploy fires,
  walks/init flows re-run. Never deploy a tab and immediately live-test
  another flow that shares state with it; wait ~30s for the noise to settle.
- **Subflow definitions are not addressable via `/flow/:id`** — patch the def
  node inside a full `GET /flows` → `POST /flows` round trip. And a
  `Node-RED-Deployment-Type: nodes` deploy does NOT rebuild existing subflow
  *instances* (they keep stale copies of def internals) nor flush
  rate-limiter (`delay` node, pauseType `rate`) queues — use a `full` deploy
  for subflow-def changes.
- **Rate-limiter queues leak stale state:** a message built before a global
  changed can emerge seconds later and act on old values. Guards that read
  globals belong *inside* the subflow/function that acts, not (only) upstream
  of the queue.
- **Messages sent into the Global CT repaint walk must carry `payload: {}`** —
  several old call-service nodes in the walk have input overrides enabled and
  throw `ValidationError: "data" must be one of [string, object]` on plain
  string/number payloads.
- **`ha-api` nodes silently drop messages right after a `full` deploy**
  (learned 2026-07-24, kitchen-volume rework): the HA websocket config node
  takes ~30-60s to reconnect after a full restart, and messages sent through
  an `ha-api` node before then vanish with no log line. A live test fired
  ~30s post-deploy did nothing; the identical inject a minute later worked.
  Wait a minute after a full deploy before live-testing anything that goes
  through HA nodes.

## Monitoring And Validation

Check container logs:

```bash
docker logs --tail 120 node-red-container-main
```

For recent logs after a deploy:

```bash
docker logs --since 30s node-red-container-main
```

Useful success lines:

```text
Updated flow: FLOW_LABEL [TAB_ID]
Adding flow: FLOW_LABEL [TAB_ID]
```

Watch for Home Assistant node errors like:

```text
InputError: Entity could not be found in cache for entityId: ...
```

That usually means the entity id is wrong or Home Assistant has not exposed it yet. In the Flume rework, `group.sprinkler_zones` was invalid and the helper was later changed to a binary sensor.

## Home Assistant Node Notes

The existing Home Assistant config node id was:

```text
23fd91e9137b71c5    Home Assistant
```

The common MQTT broker config used by the Flume MQTT nodes was:

```text
82f540b7378c2e35
```

The Pushover credential/config node used by existing water alerts was:

```text
a3903de6e20b03dc
```

Reuse existing config nodes when adding a new flow tab. Do not create duplicate server, MQTT, or credential nodes unless necessary.

## Homelab Backup Flow Notes

The homelab config backup system lives at:

```text
/home/pi/scripts/homelab_backup
```

The production command should be:

```bash
/home/pi/scripts/homelab_config_backup.sh --upload-s3
```

As of 2026-05-16, the active host Node-RED `Backup Flows` tab at port `1881` has been updated so:

- The old Docker Compose backup exec node now runs `/home/pi/scripts/homelab_config_backup.sh --upload-s3`.
- It is scheduled at `04:05`.
- The old direct S3 upload nodes for Home Assistant, Joplin, Paperless, Grafana SQL, and Docker Compose are disabled.
- The source backup generation jobs remain enabled.

The older Compose-only backup command was:

```bash
/home/pi/scripts/docker_compose_backup.sh
```

Replace the old command only after confirming the new backup script can run locally and the DB/Grafana/S3 pieces are configured.

For Home Assistant current-state nodes, verify entity ids after deployment by checking logs. `off` is a normal state for binary sensors and switches. Logic that treats only `on`, `open`, `true`, `home`, or `running` as active will classify `off` as inactive.

## Flume HA Water Monitor Reference

The newer Flume flow uses Home Assistant as the source of truth instead of direct Flume and Rachio API calls.

Primary entities:

```text
sensor.flume_sensor_home2_current
binary_sensor.rachio_group_webhook
input_select.mode
```

Published MQTT discovery entities:

```text
sensor.water_runtime_minutes
sensor.water_current_gpm
sensor.water_run_gallons
sensor.water_avg_gpm
sensor.water_max_gpm
sensor.water_classification
sensor.water_last_run_summary
binary_sensor.water_running
```

Shared MQTT state topic:

```text
nodered/water_monitor/state
```

The function node stores runtime state in persistent flow context. Persistent context is enabled in:

```text
/home/pi/nodered/data/settings.js
```

Look for:

```js
contextStorage: {
    default: {
        module:"localfilesystem"
    },
},
```

This means flow context can survive restarts after Node-RED flushes it to disk.
