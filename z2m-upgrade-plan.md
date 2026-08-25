# Zigbee2MQTT 1.40.2 → 2.x upgrade plan

**Status:** PLANNED 2026-08-25, nothing changed yet. Brad is open to it; the
cost of getting it wrong is 125 devices dark, so this is backup-first.

## Why

- z2m `1.40.2` / zigbee-herdsman `2.1.3` (image built 2024-10-01). The 2.x
  line has the request-queue / concurrency rework and better tolerance of
  misbehaving routers — relevant here because ten Tuya/MiBoxer `TS0502B`
  strips (kept on purpose: good PWM) refuse routing-table/LQI requests.
- Coordinator: Sonoff ZBDongle-P (CC2652P), Z-Stack `20240710` — current
  Koenkk firmware, on a USB extension 2 m above everything. **Placement is
  fine; do not touch the radio.**
- Findings from the 2026-08-25 network map are in memory
  `zigbee-network-health` (Claire Lamp: no routes, direct LQI-20 link to the
  basement coordinator, 13 s to ack).

## Backup strategy (do all of it, in this order)

1. `docker tag` of the running image exists: `koenkk/zigbee2mqtt:1.40.2-rollback`
   (digest `25170ffb5b10`). Rollback = point compose at that tag.
2. Stop z2m, then copy the whole data dir:
   `cp -a /home/pi/z2m/data /home/pi/backups/z2m-data-$(date +%Y%m%d)`.
   It holds `configuration.yaml`, `database.db` (the device table — losing
   this is what forces re-pairing), `state.json`, and `coordinator_backup.json`
   (network key/PAN/channel + the coordinator's NVRAM — restores the *network*
   onto any CC2652 if the stick dies).
3. Also take a fresh `coordinator_backup.json` via the frontend
   ("Backup" in Settings → Tools) right before the upgrade.
4. Verify the backup restores: `docker run` the old image against a *copy* of
   the backup dir with a dummy serial port is not possible — instead diff the
   copied `database.db` line count against `/home/pi/z2m/data/database.db`
   and check the tarball opens. Good enough; the rollback image + data copy
   is the real test.

Rollback: stop the new container, restore the data dir copy, start the
`1.40.2-rollback` tag. Devices never re-pair as long as `database.db` and the
coordinator NVRAM are intact.

## 2.0 breaking changes that apply here

Checked against the current `configuration.yaml`:

- `permit_join: true` at top level — **2.x refuses to start with it**; delete
  the line (it should be off anyway; the frontend button still works).
- `serial.adapter` must be explicit: add `adapter: zstack`.
- `advanced.homeassistant_legacy_entity_attributes`, `legacy_api`,
  `legacy_availability_payload`, `device_options.legacy` — all already
  `false`, and the keys themselves are removed in 2.x (delete them; z2m 2.x
  errors on unknown legacy keys). Because they were already false, the MQTT
  payload shapes Node-RED sees today are the *new* shapes — this is the main
  reason the migration is smaller than it looks.
- `homeassistant: true` → `homeassistant: {enabled: true}`;
  `frontend: {port: 8080}` → `frontend: {enabled: true, port: 8080}`.
- `advanced.last_seen`, `cache_state` stay under `advanced` (fine).
- Removed in 2.x: `zigbee2mqtt/bridge/config/*` legacy topics,
  `transmit_power` default change (now 5 dBm on zstack — set
  `advanced.transmit_power: 20` explicitly to keep the P-dongle's output).
- Home Assistant: 2.x renames some entity ids for devices that had
  `legacy: false`? No — those were already migrated. But the **HA MQTT
  discovery `object_id`** convention changed for *actions*: `action` sensors
  became `event` entities. Any HA automation/Node-RED `events: state` node
  on `sensor.<pico>_action` breaks. None found in Node-RED (button flows use
  the z2m contrib nodes, see below), but grep HA automations before flipping.

## Node-RED exposure (inventory 2026-08-25)

227 nodes reference zigbee2mqtt, almost all via `node-red-contrib-zigbee2mqtt`
**2.7.4** (`zigbee2mqtt-in` 36+, `zigbee2mqtt-out` ~110 across Kitchen Motion
Lighting, Button Controllers 1-3, All Blinds Open/Close, Update CT Values
while On, Motion Lighting, Outside House Lights Colors, and 26 inside
subflows; `zigbee2mqtt-get` 26). Only two raw `zigbee2mqtt/...` MQTT topics
are used directly. So the migration risk is concentrated in that one contrib
package: it discovers devices through `bridge/devices` and (in older
versions) `bridge/config`. **Check the package changelog for "2.0" support
and update it first** (`npm i node-red-contrib-zigbee2mqtt@latest` in
`/home/pi/nodered/data`, restart, confirm the server node still lists
devices) — do this *before* touching z2m, since it is independently
reversible.

## Order of work

1. Update `node-red-contrib-zigbee2mqtt`, verify a blind + a button flow.
2. Backups (above). Announce a maintenance window — every Zigbee light is
   out for the restart (~2 min) and any device that fails will show quickly.
3. Edit `configuration.yaml` per the list; `docker compose pull` the pinned
   new tag (pin a version, never `latest`, so the rollback is one line);
   start; watch `docker logs` for "config validation" errors first, then the
   device count in `bridge/info`.
4. Walk the house: one device per family (Innr bulb, Tuya strip, Sengled
   plug, Third Reality contact, IKEA blind, Inovelli switch, Pico via the
   Button Controllers tabs).
5. Only then: `permit_join` stays off; consider `advanced.transmit_power: 20`.

## Not part of this

The MiBoxer strips' routing behaviour won't change with software. If they
ever become the bottleneck, the clean option Brad floated is a **second
coordinator / second network** for them (Wi-Fi is pinned to channel 1, so
Zigbee channels 15–25 are all clear; keep the two networks ≥ 5 channels
apart). Re-pairing existing devices is off the table by preference; a laggy
router can be nudged to re-discover routes by a 10 s power cycle without any
re-pair (routes/neighbour tables are RAM).
