# Roborock miio MQTT Bridge

This host runs a standalone local-token bridge for the Roborock vacuum. It replaces the old enabled Node-RED `node-red-contrib-miio-roborock` command path while keeping the same local miio token control model.

## Service

Project directory:

```bash
/home/pi/roborock-miio-mqtt
```

Runtime config:

```bash
/home/pi/roborock-miio-mqtt/.env
```

The `.env` file contains the vacuum IP, MQTT broker URL, room segment map, fan-speed map, and the miio token. Do not copy the token into this guide.

Common commands:

```bash
cd /home/pi/roborock-miio-mqtt
docker compose ps
docker compose logs -f --tail=100
docker compose up -d
docker compose restart
docker compose build && docker compose up -d
```

The service uses `network_mode: host` so the old miio UDP protocol can reach the vacuum on the LAN.

## Current Endpoints

Vacuum:

```text
VACUUM_NAME=Black Kitty
VACUUM_ID=black_kitty
VACUUM_HOST=192.168.10.72
```

MQTT:

```text
MQTT_URL=mqtt://192.168.10.217:1883
BASE_TOPIC=roborock/black_kitty
DISCOVERY_PREFIX=homeassistant
```

Home Assistant entity created by MQTT discovery:

```text
vacuum.black_kitty_black_kitty
```

## Room Segments

Configured room IDs:

```text
16 = Dining Room
17 = Office
18 = Family Room
19 = Kitchen
20 = Hall
```

To update these, edit `ROOM_SEGMENTS` in:

```bash
/home/pi/roborock-miio-mqtt/.env
```

Then restart:

```bash
cd /home/pi/roborock-miio-mqtt
docker compose restart
```

## Fan Speeds

For this Roborock model (`roborock.vacuum.a15`), the bridge uses Roborock mode values:

```text
Quiet    = 101
Balanced = 102
Turbo    = 103
Max      = 104
```

These live in `.env`:

```text
FAN_SPEEDS=Quiet:101,Balanced:102,Turbo:103,Max:104
```

If Max behaves like Balanced/Turbo again, check this mapping first.

## MQTT Topics

Command topics:

```text
roborock/black_kitty/command
roborock/black_kitty/clean_segments
roborock/black_kitty/set_fan_speed
roborock/black_kitty/send_command
```

State topics:

```text
roborock/black_kitty/state
roborock/black_kitty/attributes
roborock/black_kitty/availability
```

Example: clean Family Room:

```bash
cd /home/pi/roborock-miio-mqtt
docker compose exec -T roborock-miio-mqtt node -e "const mqtt=require('mqtt'); const c=mqtt.connect(process.env.MQTT_URL); c.on('connect',()=>c.publish('roborock/black_kitty/clean_segments','[18]',{qos:1},()=>c.end()));"
```

Example: set Max:

```bash
cd /home/pi/roborock-miio-mqtt
docker compose exec -T roborock-miio-mqtt node -e "const mqtt=require('mqtt'); const c=mqtt.connect(process.env.MQTT_URL); c.on('connect',()=>c.publish('roborock/black_kitty/set_fan_speed','Max',{qos:1},()=>c.end()));"
```

## Home Assistant Actions

Clean everything:

```yaml
action: vacuum.start
target:
  entity_id: vacuum.black_kitty_black_kitty
```

Dock:

```yaml
action: vacuum.return_to_base
target:
  entity_id: vacuum.black_kitty_black_kitty
```

Pause:

```yaml
action: vacuum.pause
target:
  entity_id: vacuum.black_kitty_black_kitty
```

Set Max suction:

```yaml
action: vacuum.set_fan_speed
target:
  entity_id: vacuum.black_kitty_black_kitty
data:
  fan_speed: Max
```

Clean one room, for example Family Room:

```yaml
action: vacuum.send_command
target:
  entity_id: vacuum.black_kitty_black_kitty
data:
  command: app_segment_clean
  params:
    - 18
```

## Node-RED Integration

Active Node-RED Docker project flow file:

```bash
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json
```

Use the Node-RED Admin API for deployed changes; see:

```bash
/home/pi/home_config/nodered-flow-agent-guide.md
```

Tabs updated for the bridge:

```text
Bedtime Routine      ab965bba13d85fcb
Black Kitty Robot Vac db2ed951691ef858
Misc                 24bcec58e89ffeb4
Away Presence        fe98a166119b1dd1
Kitchen Motion Lighting fb8d8707af18844f
```

The Bedtime Routine now:

1. Checks `input_boolean.disable_vaccum`.
2. Stops if the disable boolean is on/true.
3. Calls `vacuum.set_fan_speed` with `fan_speed: Max` if the disable boolean is off/false.
4. Waits 3 seconds.
5. Calls `vacuum.start` for a full clean.

The old bedtime `input_button.roborock_clean_kitchen` action was disabled so bedtime does not run kitchen-only cleaning.

The TV-off reminder in the `Misc` tab now always includes:

```text
Clean the floor for the vacuum tonight.
```

The `Black Kitty Robot Vac` tab still listens to the old Home Assistant helper buttons/selects, but the execution path now calls `vacuum.black_kitty_black_kitty` directly instead of old miio nodes.

Important for Node-RED Home Assistant call-service nodes: populate the `action` field, not only `domain` and `service`. For example, a full-clean node should have:

```text
action: vacuum.start
domain: vacuum
service: start
entityId: ["vacuum.black_kitty_black_kitty"]
```

If `action` is blank in the editor, the node is not properly configured even if `domain` and `service` are present in JSON.

Old helper buttons still listened for:

```text
input_button.roborock_dock
input_button.roborock_clean_kitchen
input_button.roborock_pause
input_button.roborock_resume
input_button.roborock_start
input_select.roborock_fan_speed
input_select.roborock_room_selector
```

Those are compatibility triggers only. The command execution should stay on the MQTT vacuum entity.

## Operational Notes

- Keep `192.168.10.72` reserved for the vacuum.
- If the bridge logs `handshake timeout`, first check basic LAN reachability from this host:

```bash
ping -c 3 -W 2 192.168.10.72
ip neigh show dev enp1s0 | rg '192.168.10.72'
```

- The bridge logs every miio call and result. For example:

```text
MQTT command roborock/black_kitty/clean_segments: [19]
miio call app_segment_clean [19]
miio result app_segment_clean ["ok"]
```

- `miio@0.15.6` has old dependency audit warnings. The isolation boundary is the Docker container; do not re-enable the old Node-RED miio server node unless there is a deliberate reason.

## Last Known Backup Before Node-RED Conversion

Before converting the flows to the MQTT vacuum entity, a backup was created:

```bash
/home/pi/nodered/data/projects/nodered_n100_mini/flows.json.backup_before_roborock_mqtt_20260525_201106
```
