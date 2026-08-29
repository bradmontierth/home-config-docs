# Frigate Jetson Operations Guide

Last updated: 2026-06-08

## Summary

Frigate now runs on the Jetson host as the primary event, detection, and alert engine. Blue Iris should remain the stable stream/recording hub for client viewing and long-term camera recording.

The old mini PC Frigate instance at `192.168.10.250` is legacy/parallel infrastructure and should not be assumed to be the production target after Jetson cutover work.

## Hosts

Primary Frigate host:

```text
Hostname: pi-desktop
IP: 192.168.10.197
SSH: ssh jetson-tts
Frigate UI: https://192.168.10.197:8971/
Working directory: /home/pi/frigate
Compose file: /home/pi/frigate/docker-compose.yml
Config file: /home/pi/frigate/config/config.yml
Media directory: /home/pi/frigate/media
```

Node-RED doorbell snapshot/API fetches use the direct local automation API port:

```text
http://192.168.10.197:5000
```

Port `8971` is the HTTPS/auth UI port. Port `5000` is published on the LAN for local automation calls.

Legacy Frigate host:

```text
Host alias: old-mini
IP: 192.168.10.250
Purpose: previous Frigate instance / comparison fallback
```

Documentation repo:

```text
Host: beelink
Guide path: ~/home_config/frigate-guide.md
```

## Container

The Jetson instance runs in Docker Compose:

```bash
cd /home/pi/frigate
docker compose ps
docker compose restart frigate
docker logs --since 10m frigate
docker stats --no-stream frigate
```

Image:

```text
ghcr.io/blakeblackshear/frigate:stable-tensorrt-jp6@sha256:46d842ae1716125962a5a6a05eab6703a02a512c63eac7ac182f346f7f62fe17
```

Important Compose settings:

```text
runtime: nvidia
shm_size: 512mb
/tmp/cache: tmpfs, 1 GB
ports: 8971, 5000, 8554, 8555 tcp/udp
```

## Intended Architecture

Use Frigate for:

```text
object detection
events
snapshots
Home Assistant triggers
phone/LLM alert inputs
```

Use Blue Iris for:

```text
serving streams to clients
long-term recording
high-volume camera viewing
```

Do not try to turn the Jetson Frigate instance into the main recorder for every camera unless dedicated media storage is added and the NVR/session limits are revisited.

## Kitchen Display Restreams

The go2rtc section defines three `*_kitchen_display` streams
(`simon_kitchen_display`, `claire_kitchen_display`, `doorbell_kitchen_display`)
added 2026-07-04. They are on-demand ffmpeg transcodes (1440-wide h264) of the
camera record streams, consumed over RTSP :8554 by the kitchen display VLC
helper (see `kitchen-dashboard-display-guide.md`). Do not remove them; they
cost nothing while no viewer is connected. They replaced the Blue Iris HTTP
endpoints because those accumulated permanent lag after network stalls.

**Simon is different (2026-08-29).** Simon's camera is the 4K Reolink and its
main stream is **H.265 3840x2160 @ 15 fps (~6.5 Mbit/s)**; Claire's is H.264
2560x1920 @ 15 fps. The plain `ffmpeg:simon_cam_record#video=h264#width=1440`
source ran a *software* HEVC 4K decode + libx264 encode that measured ~1.3x
realtime on an idle box and 0.7x with the Jetson at its usual load 6-8 on 6
cores. go2rtc drops RTP packets to any consumer that can't keep up — here the
consumer was the transcoder's own decoder — so the HEVC decoder lost
reference frames (`Could not find ref with POC n`) and the smeared/partial
frames were encoded into the h264 the display shows. That was the "artifacts,
glitching, partial picture" on the kitchen display; Claire never showed it
because 2560x1920 H.264 decodes fast enough. It also duplicated the 15 fps
source to 25 fps (judder + 40% wasted encode + Pi decode).

Fix: `simon_kitchen_display` is now an `exec:` source that decodes on NVDEC
(`-c:v hevc_nvmpi -resize 1440x810`, the same decoder Frigate's own
`preset-jetson-h264` uses) and encodes with the same libx264 settings, with
`-fps_mode passthrough` so it outputs the camera's real 15 fps. Measured:
35% of one core (was 92-106%), 301 frames/20 s, ~2.3 Mbit/s, 0 decode errors,
Jetson load dropped ~2. Two Frigate-specific gotchas when editing it:

- Frigate runs Python `str.format()` over the go2rtc section (for
  `{FRIGATE_CAMERA_PASSWORD}`), so go2rtc's `{output}` placeholder must be
  written `{{output}}` in `config.yml` or go2rtc dies at start with
  `Invalid substitution found` (every Frigate camera then loses its
  restream — detection stops).
- Frigate 0.17 refuses `exec:`/`echo:`/`expr:` sources unless the container
  has `GO2RTC_ALLOW_ARBITRARY_EXEC=true`; it silently *removes the stream*
  (VLC gets 404). That env var is set in `docker-compose.yml` `environment:`
  (not `.env`). The go2rtc HTTP API also refuses `exec:` (400), so live
  testing was done by publishing a manual ffmpeg into a placeholder stream
  (`-f rtsp rtsp://127.0.0.1:8554/<name>`).

The Orin Nano has NVDEC but **no NVENC**, so the encode stays libx264
(`h264_nvmpi` is listed but has no hardware behind it). Claire's transcode
still software-decodes (~100% of a core) and could get the same treatment with
`-c:v h264_nvmpi -resize 1440x1080` if Jetson CPU ever becomes the problem;
left alone because it displays cleanly. Backups of the pre-change files:
`config.yml.bak-simon-hw-20260829`, `docker-compose.yml.bak-exec-env-20260829`.

## MQTT

MQTT is enabled against the Home Assistant broker:

```yaml
mqtt:
  enabled: true
  host: 192.168.10.217
  client_id: frigate-jetson
  topic_prefix: frigate_jetson
```

The unique `client_id` and `topic_prefix` are intentional. The legacy mini PC uses the default `frigate` identity, and using the same client ID caused broker disconnects while both instances were running.

For Home Assistant migration, update integrations/automations to consume `frigate_jetson/...` topics before retiring the old mini PC topics.

## Node-RED Alert Automation

Node-RED consumes Jetson object events from:

```text
frigate_jetson/events/#
```

The Blue Iris alert webhooks and legacy Blue Iris MQTT alert topics are disabled in the Node-RED `Blue Iris` tab. Blue Iris should no longer be treated as the object-detection or phone-alert source.

**History (2026-08-26):** until this date those nodes were only *renamed* "legacy BI disabled" — none had the disabled flag set, so the `/frontyard`, `/backyard`, `/garage`, `/doorbell`, `/bigkitty` `http in` webhooks and the `kitchen_alert`, `garage_alert`, `backyard_alert`, `sideyard_alert`, `driveway_small_alert`, `driveway_alert/trigger` `mqtt in` nodes were all still live. While Deepstack filtered BI alerts this was invisible; once Deepstack was turned off in Blue Iris (2026-08-25), BI's raw-motion "Web request" alert actions started hitting the webhooks. The man-door spotlight then ran a self-sustaining loop every evening: BI backyard alert → `/backyard` → Hubitat spotlight on → 5-min off timer → the light switching off registers as motion on the backyard cam → new alert → on again (started at the `sunset` gate, ~20:29, on 08-25 and 08-26). All 11 nodes were genuinely disabled (`d: true`) via `PUT /flow/9ae0a3d4d3f8a27a` on 2026-08-26; the webhooks now return 404. The equivalent lighting/alert behavior is already handled by the `Frigate alert/light filter` function below (output 3 = backyard person → spotlight chain).

Blue Iris cameras (driveway, driveway2, frontdoor, garagedoors, garagehousedoors, backyard) still have "Web request" alert actions configured on the BI side; they now just get a 404 and can be removed at leisure.

Current Jetson alert behavior:

```text
Mode gate: input_select.mode is Night or Away
Pushover debounce: one alert per camera every 5 minutes
Alert cameras: driveway_small, side_yard, garage_2, backyard, garage, front_door, kitchen_cam
Object labels: all labels, except car is ignored on garage and garage_2
Snapshot source: http://192.168.10.197:5000
```

Light automation behavior remains narrower:

```text
Outside/porch/garage motion helpers are triggered from person detections only.
side_yard and backyard must be inside the property zone before alerts or light triggers are forwarded.
```

## Detection Model

Detector:

```yaml
detectors:
  onnx:
    type: onnx
```

Model:

```yaml
model:
  model_type: yolo-generic
  width: 320
  height: 320
  input_tensor: nchw
  input_dtype: float
  path: /config/model_cache/yolov9-s-320.onnx
  labelmap_path: /labelmap/coco-80.txt
```

Frigate reports ONNX/CUDA-backed inference. Typical observed detector latency has been roughly `25-35 ms` with the expanded camera set.

## Hardware Acceleration

Global config:

```yaml
ffmpeg:
  hwaccel_args: preset-jetson-h264
```

Several NVR low-res detect streams override this with:

```yaml
ffmpeg:
  hwaccel_args: []
```

This is intentional. During testing, too many Jetson hardware decode sessions caused FFmpeg/NVDEC instability with errors like `NVVIDEO Video Dec Unsupported Stream`. CPU decoding the low-res detect streams kept the system stable while preserving GPU-backed object detection.

## Retention

The Jetson is not sized as the main video archive. Retention is intentionally tight:

```yaml
record:
  continuous:
    days: 0
  motion:
    days: 1
  alerts:
    retain:
      days: 1
      mode: motion
  detections:
    retain:
      days: 1
      mode: motion

snapshots:
  enabled: true
  retain:
    default: 1
```

If retention is increased, add/mount dedicated media storage first. Do not assume the Jetson root disk is enough for long-term camera recording.

## Camera Map

NVR at `192.168.40.90`:

```text
channel 1 -> driveway_small
channel 2 -> side_yard
channel 3 -> nvr_ch3, not yet named
channel 4 -> garage_2
channel 5 -> backyard
channel 6 -> garage
channel 7 -> nvr_ch7, disabled
channel 8 -> front_door
channels 9-16 -> tested unavailable/forbidden
```

Other cameras:

```text
simon_cam
claire_cam_master
kitchen_cam
doorbell
```

Current enabled cameras:

```text
driveway_small
side_yard
nvr_ch3
garage_2
backyard
garage
front_door
simon_cam
claire_cam_master
kitchen_cam
doorbell
```

Disabled:

```text
nvr_ch7
```

## Doorbell Special Case

The doorbell matters for LLM visitor descriptions, so it uses the main/high-res stream for detection snapshots:

```yaml
doorbell:
  detect:
    fps: 3
  ffmpeg:
    inputs:
      - path: rtsp://127.0.0.1:8554/doorbell_record
        input_args: preset-rtsp-restream
        roles:
          - audio
          - detect
          - record
```

The 3 FPS cap is intentional. High-res doorbell detect at the default FPS caused skipped frames. At 3 FPS it stabilized while preserving high-resolution alert images.

Note: global audio is disabled:

```yaml
audio:
  enabled: false
```

The doorbell input still lists the `audio` role because it was inherited from the prior config. If audio events are needed later, enable/configure audio explicitly and retest load.

## Known Limits

1. NVR session limits

Adding channels 1-7 with both detect and record created too many RTSP sessions against the NVR. The NVR returned `503 Service Unavailable`.

Current mitigation:

```text
extra NVR cameras are detect-only
front_door remains detect+record
nvr_ch7 remains disabled
```

2. Jetson hardware decode limits

Too many hardware-decoded NVR streams caused FFmpeg/NVDEC errors. Low-res NVR detect streams use CPU decode via `hwaccel_args: []`.

3. Storage

Storage growth is the main risk if Frigate is used like a recorder. Keep retention tight or add dedicated storage.

4. Stream source reliability

Historical short outages were seen for `kitchen_cam` and `doorbell`. Similar issues also appeared on the legacy host, so these are likely camera/network/source issues rather than Jetson overload.

## Health Checks

Container health:

```bash
cd /home/pi/frigate
docker compose ps
docker inspect -f 'restart_count={{.RestartCount}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}}' frigate
```

Resource usage:

```bash
docker stats --no-stream frigate
free -h
df -h / /home/pi/frigate
du -sh /home/pi/frigate/media
```

Camera/detector stats:

```bash
docker exec frigate sh -lc 'python3 -c "import json, urllib.request; s=json.load(urllib.request.urlopen(\"http://127.0.0.1:5000/api/stats\")); print(\"camera_fps=%s process_fps=%s skipped_fps=%s detection_fps=%s inference_ms=%s gpu=%s\" % (s.get(\"camera_fps\"),s.get(\"process_fps\"),s.get(\"skipped_fps\"),s.get(\"detection_fps\"),s[\"detectors\"][\"onnx\"][\"inference_speed\"],s.get(\"gpu_usages\",{}).get(\"jetson-gpu\",{}).get(\"gpu\"))); [print(\"%s camera=%s process=%s skipped=%s detect=%s\" % (n,c.get(\"camera_fps\"),c.get(\"process_fps\"),c.get(\"skipped_fps\"),c.get(\"detection_fps\"))) for n,c in s[\"cameras\"].items()]"'
```

Recent stream/MQTT errors:

```bash
docker logs --since 30m frigate 2>&1 | grep -Ei 'mqtt|Ffmpeg process crashed|Unable to read frames|No frames received|Service Unavailable|wrong response|Server returned 404|Unsupported Stream|timeout'
```

Expected healthy signs:

```text
container: running healthy
restart_count: 0, unless intentionally restarted
skipped_fps: near 0 after startup settles
inference_speed: usually about 25-35 ms
host swap: low/stable
MQTT: no repeated disconnect loop
```

## Validation Baseline

After the expanded camera set and MQTT work, a healthy sample looked like:

```text
camera_fps=41.8
process_fps=41.9
skipped_fps=0.0
detection_fps=15.1
inference_ms=28.48
gpu=99.8%
Docker CPU around 2.0-2.3 cores
Frigate RAM around 2.5-2.8 GiB
```

Single samples vary with motion. Judge health by sustained skipped FPS, restarts, logs, detector latency, and memory/swap trends.

## Editing Workflow

Before edits:

```bash
cd /home/pi/frigate
cp config/config.yml config/config.yml.bak-$(date +%Y%m%d-%H%M%S)
```

Validate YAML:

```bash
docker compose config --quiet
docker exec frigate sh -lc 'python3 -c "from ruamel.yaml import YAML; YAML().load(open(\"/config/config.yml\")); print(\"yaml ok\")"'
```

Apply:

```bash
docker compose restart frigate
```

Wait for health:

```bash
docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' frigate
```

Then check stats/logs using the commands above. Expect startup churn for about 1 minute after restart.

## Secrets

Camera credentials are stored through `/home/pi/frigate/.env` and referenced as:

```text
{FRIGATE_CAMERA_PASSWORD}
```

Do not paste real RTSP passwords into this guide or into shared logs. When sharing logs, redact RTSP URLs.

## Future Work

Recommended next steps:

```text
move Home Assistant automations to frigate_jetson topics
rename nvr_ch3 once identified
decide whether nvr_ch7 should stay disabled or replace another NVR channel
keep Blue Iris as stream/recording authority
add dedicated Frigate media storage before increasing retention
```
