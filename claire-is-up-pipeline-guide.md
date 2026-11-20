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
Audio:    rtsp://192.168.10.197:8554/claire_cam_master_detect   (Jetson go2rtc, sub-stream audio)
Snapshot: rtsp://192.168.10.197:8554/claire_cam_master_record   (one full-res frame per check,
          scaled to 960x720 by vlm.grab_frame so crop_box still fits)
Pushover image (Node-RED): http://192.168.10.197:5000/api/claire_cam_master/latest.jpg?height=720
```

### Moved off Blue Iris (2026-10-06)

Until 2026-10-06 both sources were Blue Iris (`temp.wav` and `/image/clairecam`
on 192.168.10.49:81). They now come from the Jetson go2rtc restreams, so the
pipeline no longer depends on the Blue Iris box. Pre-change backups:
`/home/pi/baby-monitor/app/config.yaml.bak-bi-20261006` and
`app/vlm.py.bak-20261006` (that directory is not a git repo).

Why a full-res RTSP grab rather than Frigate's `latest.jpg` for the VLM:
`latest.jpg` is upscaled from the 640x480 detect stream. Comparing all three
sources on the empty crib, every one (Blue Iris included) sometimes reports
"lying, awake 0.1-0.2" — an existing model quirk, far below the 0.8/0.85
alert thresholds — so the grab is about keeping Blue Iris-equivalent detail
for the sitting/standing call, not a fix. A grab takes ~2-3 s.

### Full-res crop + rotated double-check (2026-10-11)

On 2026-10-11 the nap produced 4 false `awake` alerts (eyes clearly closed,
often face-up). The cause was that `grab_frame` scaled the frame to 960x720
before cropping, so the face reached the VLM ~30 px wide. Since then `vlm.py`:

- grabs the full 2560x1920 frame and scales `crop_box` (still written in
  960x720 coordinates) to the real frame size, so the VLM gets a ~1147x1619 crib crop;
- when the upright answer is lying-awake (`awake >= vlm_lying`, not
  sitting/standing), re-asks the SAME crop rotated 180 deg and stores it as
  `rot_awake`; `lying_awake` needs both >= `vlm_lying`. At full res the
  remaining false "eyes open" calls are heads at the bottom rail (face
  upside down). The rotated view is a veto only: it wrongly reports
  sitting/standing, so its position is never used. If the rotated call
  fails, the hit is not counted.

Bench (970 frames from Optiplex Lookout recordings: the 10-11 nap, Oct 8-11
morning wake windows, night samples; kit in `/home/pi/claire-eval`, run
`bench.py` then `score.py`): 0 of 335 asleep frames at alert level (old: 16),
all 5 real wakes caught. Prompt and thresholds unchanged. Pre-change backup:
`app/vlm.py.bak-20261011`. If false alerts recur, pull the alert-time
frames from `optiplex:/srv/lookout-archive/claire/` (UTC-named 1-min MP4s)
and add them to the bench before touching the prompt.

To test the Node-RED path without alerting everyone, publish `reason: "test"`
(goes only to Brad's phone, no speakers); `awake`/`cry` are production-shaped.

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
  "snapshot_url": "rtsp://192.168.10.197:8554/claire_cam_master_record"
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
msg.image = 'http://192.168.10.197:5000/api/claire_cam_master/latest.jpg?height=720';
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
  -m "{\"reason\":\"awake\",\"detail\":{\"manual_test\":true},\"ts\":$(date +%s),\"snapshot_url\":\"rtsp://192.168.10.197:8554/claire_cam_master_record\"}"'
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
