# Claire Is Up Pipeline Guide

This guide documents the nap-mode baby monitor pipeline that sends the
`Claire is up` phone alert.

## Summary

The pipeline is armed from Home Assistant nap mode. A lightweight orchestrator
runs on the Beelink and calls GX10 services for scoring:

- Audio cry scoring: GX10 PANNs service
- Image awake scoring: GX10 Qwen/VLM endpoint
- Alert transport: MQTT to Node-RED
- Phone alert: Node-RED Pushover node
- Speaker alert: Node-RED Music Assistant / Home Assistant TTS paths

Production `cry` and `awake` alerts are intended to notify all devices on the
configured Pushover user and announce on the loft and kitchen speakers. Test
and degraded/ops alerts are intentionally Brad-only and do not announce on
speakers.

## Hosts And Paths

Beelink:

```text
Host/IP: beelink / 192.168.10.217
Service source: /home/pi/baby-monitor
Node-RED: Docker Node-RED on http://127.0.0.1:1880
MQTT broker: mosquitto container on 192.168.10.217:1883
```

GX10:

```text
Host/IP: gx10-5398 / 192.168.10.187
PANNs cry scoring: http://192.168.10.187:8098/score
Qwen VLM endpoint: http://192.168.10.187:8095/v1/chat/completions
```

Camera sources:

```text
Audio:    http://192.168.10.49:81/audio/clairecam/temp.wav
Snapshot: http://192.168.10.49:81/image/clairecam
```

## Home Assistant Arming

Home Assistant mirrors nap mode to retained MQTT:

```text
Entity: input_boolean.nap_mode
Topic: babymonitor/armed
Payload: ON or OFF
Retain: true
```

The automation is in:

```text
/home/pi/homeassistant/config/automations.yaml
```

Look for:

```text
id: babymonitor_nap_mode_mirror
alias: Baby monitor: mirror nap mode to MQTT
```

## Beelink Orchestrator

Main files:

```text
/home/pi/baby-monitor/app/config.yaml
/home/pi/baby-monitor/app/monitor.py
/home/pi/baby-monitor/app/vlm.py
/home/pi/baby-monitor/docker-compose.yml
```

The orchestrator subscribes to `babymonitor/armed`, processes audio windows,
polls the image model, and publishes alerts to:

```text
babymonitor/alert
```

Alert payload shape:

```json
{
  "reason": "cry",
  "detail": {},
  "ts": 1781376845,
  "snapshot_url": "http://192.168.10.49:81/image/clairecam"
}
```

`reason` can be:

```text
cry
awake
```

The orchestrator publishes state to:

```text
babymonitor/state
```

and cry score telemetry to:

```text
babymonitor/score
```

Successful parsed VLM/LLM snapshot results are retained to:

```text
babymonitor/vlm
```

Home Assistant MQTT discovery entities published by the orchestrator:

```text
sensor.baby_monitor_state
sensor.baby_cry_score
sensor.baby_monitor_last_llm_state
```

`sensor.baby_monitor_last_llm_state` uses a concise state such as
`lying / open / awake=0.8` and exposes the full parsed JSON as attributes,
including `baby_visible`, `position`, `eyes`, `awake`, `source`,
`monitor_state`, and `ts`.

## Node-RED Flow

Use the Node-RED Admin API as the source of truth. Do not edit deployed flow
files directly.

Relevant tab:

```text
Tab label: Baby Monitor
Tab id: bmtab0000000001
```

Read it with:

```bash
curl -s http://127.0.0.1:1880/flow/bmtab0000000001 | jq
```

Important nodes:

```text
bmmqttin000001     mqtt in          babymonitor/alert
bmcompose00001     function         compose pushover
bmpush00000001     pushover api     pushover cry alert
bmspeakerprep01    function         speaker alert
bmloftamp01        Amp Speakers     Loft Amp Speakers
bmkitchentts01     function         Kitchen fast TTS
bmkitchenspeak01   api-call-service kitchen fast speak
bmstate000001      mqtt in          babymonitor/state
bmstatefn0001      function         degraded? (10min ratelimit)
bmstatepush01      pushover api     pushover degraded
```

Production alert behavior in `compose pushover`:

```javascript
const real = (a.reason === 'cry' || a.reason === 'awake');
msg.topic = real ? 'Claire is up' : 'Baby monitor test';
msg.priority = real ? 1 : 0;
if (!real) { msg.device = global.get('BradPhoneNM'); }  // tests only ping Brad
msg.image = 'http://192.168.10.49:81/image/clairecam';
```

This means:

- `cry` and `awake` production alerts do not set `msg.device`.
- Pushover sends to all devices on the configured recipient when `msg.device`
  is absent.
- non-real test alerts are Brad-only.
- degraded/ops alerts from `babymonitor/state` are Brad-only.

Speaker behavior for production `cry` and `awake` alerts:

```text
Message: Claire is up. Awake detected.
Message: Claire is up. Crying detected.
Voice: fast:doorbell
Loft target: media_player.loft through the existing Amp Speakers subflow
Kitchen target: media_player.squeezeplay_e4_5f_01_67_1e_56 through HA tts.speak
Loft volume: 50
```

The loft path uses the reusable `Amp Speakers` subflow
`subflow:e711d48f74f78209`, which calls Music Assistant directly, ungroups the
player, wakes the amp path when needed, pads the TTS tail, and plays the final
announcement.

The kitchen path mirrors the Doorbell tab's `Kitchen fast TTS` pattern and
uses Home Assistant `tts.speak` against the Squeezelite/Music Assistant kitchen
speaker entity.

## Pushover Notes

The shared Pushover credential node is:

```text
a3903de6e20b03dc
```

As of 2026-06-13, validating the credential with Pushover showed:

```text
status: 1
group: 0
devices: pixel8, pixel9pro
licenses: Android
```

So production `Claire is up` alerts should fan out to both `pixel8` and
`pixel9pro`. If one phone has the alert in Pushover history but did not visibly
notify, suspect device-side suppression such as Do Not Disturb, Flip to Shhh,
Android notification channel settings, lock-screen behavior, or battery/app
background restrictions.

Do not print or commit Pushover tokens or user keys.

## Safe Verification

Check current retained arming/state using the Mosquitto container:

```bash
docker exec mosquitto sh -lc \
  'timeout 4 mosquitto_sub -h 127.0.0.1 -p 1883 -v -C 1 -t babymonitor/armed 2>&1 || true'

docker exec mosquitto sh -lc \
  'timeout 4 mosquitto_sub -h 127.0.0.1 -p 1883 -v -C 1 -t babymonitor/state 2>&1 || true'
```

Send a production-shaped test alert only with explicit approval, because it
will notify phones:

```bash
docker exec mosquitto sh -lc 'mosquitto_pub -h 127.0.0.1 -p 1883 \
  -t babymonitor/alert -q 1 \
  -m "{\"reason\":\"awake\",\"detail\":{\"manual_test\":true},\"ts\":$(date +%s),\"snapshot_url\":\"http://192.168.10.49:81/image/clairecam\"}"'
```

Then check Node-RED logs:

```bash
docker logs --since 30s node-red-container-main 2>&1 | \
  rg -i 'baby|claire|pushover|error|warn|babymonitor'
```

Expected success line:

```text
[info] [pushover api:pushover cry alert] pushover POST succeeded
```

If the speaker branch is enabled, this same test also announces on the loft and
kitchen speakers.

## Operational Constraints

- Read `nodered-flow-agent-guide.md` before inspecting or changing Node-RED.
- Use the Node-RED Admin API for deployed flow changes.
- Pull and modify only the `Baby Monitor` tab with `GET /flow/bmtab0000000001`
  and `PUT /flow/bmtab0000000001`.
- Do not restart Node-RED as a substitute for deploying through the API.
- Do not change `/home/pi/baby-monitor` service code, Docker config, MQTT
  topics, or Node-RED flows without explicit approval.
