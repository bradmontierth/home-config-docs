# Kitchen Dashboard Display Guide

This guide documents the custom kitchen touchscreen dashboard in
`/home/pi/dashboard_webapp` and the display-side VLC helper on `display-pi`.

## Hosts And URLs

Main app host:

```text
beelink / 192.168.10.217
repo: /home/pi/dashboard_webapp
dashboard: http://192.168.10.217:8777/
slideshow source: http://192.168.10.217:9010
```

Display host:

```text
ssh display-pi
hostname: kitchen-display-pi
LAN IP: 192.168.10.92
VLC helper: http://192.168.10.92:8778
helper source: /home/pi/dashboard_webapp/display_helper
```

The kitchen display browser is plain Chromium in kiosk mode. It is not
Playwright. Playwright may be useful for screenshots against `:8777`, but it is
not part of the live display.

## Architecture

The main dashboard runs in Docker on the Beelink:

```text
dashboard_webapp container
  FastAPI backend
  static frontend under app/static
  Home Assistant websocket/REST integration
  narrow HA service proxy for allowlisted actions
```

The display Pi runs a separate user service because VLC must open in the real
graphical touchscreen session:

```text
~/.config/systemd/user/kitchen-vlc-helper.service
  uvicorn app:app --host 0.0.0.0 --port 8778
  launches VLC for named streams
  launches a lightweight Tk overlay for Back / Play audio / Mute
```

The dashboard camera buttons call:

```text
dashboard /api/vlc/open/{simon|claire|doorbell}
  -> display-pi helper /open/{stream}
  -> VLC fullscreen MJPEG stream
  -> Python Tk overlay over VLC
```

Doorbell object detections also use this helper. The active Docker Node-RED
`Doorbell` tab consumes confirmed Jetson Frigate doorbell person events from
`frigate_jetson/events/#`, posts to:

```text
http://192.168.10.92:8778/open/doorbell
```

and then posts to `/close` after 60 seconds. This replaced the old
`browser_mod.popup` path, which depended on the Lovelace/browser_mod kitchen
display.

## Deploy / Update

Important: code is baked into the Docker image via `COPY app ./app`. Editing
source files and reloading Chromium is not enough.

After changing dashboard code:

```bash
cd /home/pi/dashboard_webapp
docker compose up -d --build
```

For a hard rebuild:

```bash
cd /home/pi/dashboard_webapp
docker compose build --no-cache
docker compose up -d
```

After changing display helper code:

```bash
cd /home/pi/dashboard_webapp
rsync -a display_helper/app.py display_helper/back_overlay.py display_helper/.env \
  display-pi:/home/pi/dashboard_webapp/display_helper/
ssh display-pi 'systemctl --user restart kitchen-vlc-helper.service'
```

Do not use `rsync --delete` on `display_helper/` unless you explicitly preserve
`.venv`. It previously deleted the display helper venv and caused systemd
`status=203/EXEC`.

If that happens, repair with:

```bash
ssh display-pi 'cd /home/pi/dashboard_webapp/display_helper && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
ssh display-pi 'systemctl --user restart kitchen-vlc-helper.service'
```

Reload the kiosk browser after deploy:

```bash
ssh display-pi 'pkill chromium || true'
ssh display-pi 'DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 chromium --kiosk --password-store=basic --disable-accelerated-video-decode --noerrdialogs --disable-infobars --check-for-update-interval=31536000 --no-first-run "http://192.168.10.217:8777/?v=$(date +%s)" >/tmp/kitchen-dashboard-chromium.log 2>&1 &'
```

`--password-store=basic` is REQUIRED (learned 2026-07-09): without it Chromium
may pop a GNOME "choose password for new keyring" dialog and hang before
loading anything — symptom is zero HTTP traffic from the kiosk and an empty
chromium log. Debug a mystery blank/stuck display with a screenshot:
`ssh display-pi 'WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 grim /tmp/shot.png'`.

`--disable-accelerated-video-decode` is REQUIRED (learned 2026-07-10): with
hardware decode, slideshow videos starve the Pi's GPU memory (chromium log
fills with `Unable to initialize SkSurface` / `MakeFromBackendTexture()
failed`) and parts of the page silently stop repainting — the header clock
freezes while video/radar keep moving. Software decode of the pre-transcoded
720p clips is fine and page paint stays healthy. (May also be aggravated by
the display-pi low-voltage condition — power supply still needs attention.)

The app route `/` also cache-busts `app.js`, `styles.css`, and `editorial.css`
using their mtimes. Still relaunch/reload Chromium after a deploy.

## Current Dashboard Features

Layout is primarily controlled by `app/static/editorial.css`, not just
`styles.css`.

Current high-level layout:

- Top/weather band includes Indoor Climate, Outside, Radar, and Family
  slideshow.
- Hourly forecast is its own row beneath the climate/weather area.
- Family slideshow no longer overlaps radar.
- Energy panel spans the right column with larger Powerwall / solar / usage /
  vehicle readouts.
- Cameras are portrait buttons; Doorbell uses `doorbell.png`.
- The old in-page `Close video` button was removed because VLC is external and
  the display-side overlay is the reliable control surface.

Energy / vehicle details:

- Solar live: `sensor.tesla_powerwall_3_pw3_total_solar`
- Power out live: `sensor.tesla_powerwall_3_pw3_total_power_out`
- Solar today: `sensor.solar_powerwall_energy_daily`
- Usage today: `sensor.daily_home_and_heat_pump_energy`
- Usage/solar today are quiet sub-details, not Lovelace-style cards.
- The Powerwall popup treats `sensor.tesla_powerwall_3_pw3_total_power_out`
  as signed flow: positive means discharging/out; negative means charging/in.
  Negative flow is shown as a charging state instead of a reserve forecast.
- Vehicle charge rate is shown in amps because HA exposes current cleanly; kW
  would need an estimate from amps and voltage.

Tesla entities:

```text
Model Y battery:       sensor.modely_modely_battery_level
Model Y plugged:       sensor.modely_modely_plugged_in
Model Y amps:          sensor.modely_actual_charger_current
Model Y time-to-full:  sensor.modely_time_to_full_charge
Model Y distance:      sensor.modely_modely_distance_away
Model Y geofence:      sensor.modely_modely_geofence

Model 3 battery:       sensor.model3_model3_battery_level
Model 3 plugged:       sensor.model3_model3_plugged_in
Model 3 amps:          sensor.model3_actual_charger_current
Model 3 time-to-full:  sensor.model3_time_to_full_charge
Model 3 distance:      sensor.model3_model3_distance_away
Model 3 geofence:      sensor.model3_model3_geofence
```

Tesla charge popup uses global HA booleans:

```text
input_boolean.tesla_charge_asap
input_boolean.tesla_charge_tonight
```

These are global charging-mode selectors, not per-car commands.

## Camera And Audio Streams

As of 2026-07-04 the display helper consumes RTSP from go2rtc on the Frigate
Jetson (`192.168.10.197:8554`), NOT Blue Iris:

```text
Simon:    rtsp://192.168.10.197:8554/simon_kitchen_display
Claire:   rtsp://192.168.10.197:8554/claire_kitchen_display
Doorbell: rtsp://192.168.10.197:8554/doorbell_kitchen_display
```

Why: the Blue Iris HTTP endpoints (both MJPEG and `temp.ts`) are file/backlog
backed. Any network stall (WiFi hiccup on the display Pi) converted 1:1 into
permanent lag — a 30s outage left the stream permanently 30-50s behind while
the burned-in timestamp kept ticking. Proven with nftables block tests; the
lag never recovered. Unacceptable for baby monitoring. go2rtc restreams live
and drops slow-client data instead of queueing it, and VLC now runs with
`--loop --no-interact` so a dropped RTSP session auto-reconnects to the live
edge (verified: 30s block, reconnect, <5s latency confirmed via a lamp-toggle
probe in the camera view).

The `*_kitchen_display` streams are defined in the Jetson Frigate config
(`/home/pi/frigate/config/config.yml`, go2rtc section) as on-demand ffmpeg
transcodes of the camera main streams down to 1440-wide h264, because the
Pi 4 cannot hardware-decode the native 2560x1920 camera streams (software
decode pegged >100% CPU and triggered low-voltage warnings). Transcode runs
on the Jetson only while a viewer is connected. Editing them requires a
frigate container restart (~40s detection gap).

Old Blue Iris endpoints (working fallback, but lag-prone after stalls):

```text
Simon:    http://192.168.10.49:81/h264/BabyCAMR/temp.ts
Claire:   http://192.168.10.49:81/mjpg/clairecam/video.mjpg
Doorbell: http://192.168.10.49:81/h264/DoorbellReolink/temp.ts
```

Current audio URLs use matching Blue Iris audio:

```text
Simon:    http://192.168.10.49:81/audio/BabyCAMR/temp.wav
Claire:   http://192.168.10.49:81/audio/clairecam/temp.wav
Doorbell: http://192.168.10.49:81/audio/DoorbellReolink/temp.wav
```

Audio target:

```text
media_player.squeezeplay_e4_5f_01_67_1e_56
```

Do not add legacy DLNA/gmediarender targets back into the code. The old DLNA
paths are being deprecated and should not clutter the helper or allowlists.

## VLC Overlay Gotchas

The stable overlay is `display_helper/back_overlay.py`, a lightweight Tk canvas
window launched by the display helper. It provides:

```text
Back
Play audio
Mute
```

Back calls the display helper `/close` endpoint. That endpoint intentionally
stops the Squeezelite/Music Assistant camera audio before closing VLC. There is
no automatic Music Assistant resume step: resuming can restart an old queue or
playlist if MA has prior context, which is not desirable for camera audio.

### The flip bug: root cause and fix (2026-07-04)

The overlay flipping to the bottom-right upside down was never a Tk redraw
problem. Root cause: Raspberry Pi OS ships a patched wlroots
(`0.19.1-1+rpt*`, patch `0009-scene-Allow-scanout-of-a-single-overlay`) that
opportunistically places a small surface like the 640x86 overlay window on a
vc4 hardware overlay plane. The display's 180-degree rotation comes from the
kernel cmdline (`video=HDMI-A-1:1920x1080@60,rotate=180`), which wlroots
applies as plane `rotation=4` (rotate-180) on the primary plane only — the
overlay plane got `rotation=1` (none), so its content appeared unrotated:
bottom-right, upside down, on the physically flipped panel. Plane offload
toggled per-commit with damage, which is why each tap swapped the position
back and forth and why the bug was intermittent. grim screenshots never
showed the problem because screencopy composites in software.

Fix: `WLR_SCENE_DISABLE_DIRECT_SCANOUT=1` in `/etc/environment` on
display-pi. Upstream wlroots gates the entire scanout path (including the
RPi overlay-plane patch) on this variable. Note `~/.config/labwc/environment`
does NOT work for this — vars set there did not propagate on this build; use
`/etc/environment`, which requires a session restart
(`sudo systemctl restart lightdm`) to take effect. Perf cost is nil for the
camera use case: with the overlay on screen the primary plane was already
composited.

Verify the fix state (should show exactly one plane, `rotation=4`):

```bash
ssh display-pi 'sudo bash /tmp/plane-mon.sh'   # from diagnostics/touchscreen/
```

Because the flip is fixed at the compositor level, the old rule "keep the
overlay visually static" no longer applies — button states, status text, and
the pulse acknowledgement popup are safe now.

Older history (pre-2026-07-04, kept for context):

- Back-only Tk overlay worked; visual mutations appeared to trigger the flip
  (in reality they just forced commits that re-rolled the plane-offload dice).
- Chromium overlay avoided the flip but was heavier, caused low-voltage
  pressure, could be buried behind VLC after tapping the video, and sometimes
  invoked the on-screen keyboard.

The helper still has an `/overlay` HTML route from the Chromium experiment, but
the active launch path is `back_overlay.py`. Treat the HTML overlay as legacy
unless it is intentionally revived.

## Touchscreen And Multi-Touch

The Waveshare 13.3" panel's ILITEK ILITEK-TP USB controller is a true
10-point multi-touch device (`/dev/input/event0`, ABS_MT slots 0-9, raw
coordinate space 16384x9600).

Fixed 2026-07-04: drag-to-scroll and pinch-to-zoom now work in the kiosk
Chromium. The dashboard previously behaved like a mouse pointer (tap = click,
no drag scrolling) because labwc was converting touch to pointer events via
`mouseEmulation="yes"` in `~/.config/labwc/rc.xml` on display-pi. Current
config (snapshot in `display-pi-monitoring/labwc-rc.xml`):

```xml
<touch deviceName="ILITEK ILITEK-TP" mapToOutput="HDMI-A-1" mouseEmulation="no"/>
```

How the layers divide responsibility:

- 180-degree rotation is handled at the libinput level by
  `/etc/udev/rules.d/99-touchscreen-rotation.rules`
  (`LIBINPUT_CALIBRATION_MATRIX="-1 0 1 0 -1 1"`), so it applies equally to
  native touch and does not depend on mouse emulation.
- labwc forwards native Wayland touch to Chromium (launched with
  `--ozone-platform=wayland`), which does its own gesture recognition
  (scroll, pinch, `navigator.maxTouchPoints` = 10).
- VLC and the Tk `back_overlay.py` are Xwayland clients; Xwayland emulates
  pointer clicks from touch for them, so the overlay Back button still works
  (verified with an injected tap).

Config changes in rc.xml apply live with `killall -SIGHUP labwc`; no session
restart needed.

Test without a physical finger using the scripts in
`home_config/diagnostics/touchscreen/` (scp to display-pi, run with sudo):

```bash
sudo bash touch-drag.sh        # one-finger vertical drag
sudo bash touch-pinch.sh       # two-finger pinch-out (zooms page)
sudo bash touch-tap.sh X Y     # tap at raw device coords
```

These inject events into the real `/dev/input/event0` via `evemu-event`
(package `evemu-tools`), exercising the full libinput -> labwc -> Chromium
path. To observe what the page receives, relaunch Chromium with
`--remote-debugging-port=9222 --user-data-dir=/tmp/chromium-cdp-test`
(Chromium 136+ refuses the debug port on the default profile) and use
`diagnostics/touchscreen/cdp.py '<js>'` on display-pi to evaluate JS.
Raw-to-screen coordinate mapping is inverted by the rotation matrix:
`screen_x = (1 - raw_x/16384) * 1920`, `screen_y = (1 - raw_y/9600) * 1080`.

Possible follow-up: with native touch, tapping a text input may summon
squeekboard more readily; `pkill squeekboard` if it sticks.

## Shade Calibration

Open the dashboard with:

```text
http://192.168.10.217:8777/?calibrate
```

Calibration behavior:

- Drag shade boxes to move them.
- Use corner handles to resize.
- Drag names independently.
- Save persists to `/api/covers/calibrate`.
- Overrides are stored in `data/cover_overrides.json`.
- The API refreshes the running app cover list in place; the next bootstrap/page
  load reflects saved positions without editing `config.py`.

Persistence depends on this Docker volume:

```yaml
./data:/app/data
```

The live `data/cover_overrides.json` may be root-owned because the container
runs as root. That is okay for the kiosk. A dev process running as `pi` may not
be able to overwrite it without fixing ownership.

## Verification Commands

Main dashboard:

```bash
curl -fsS http://127.0.0.1:8777/api/health
curl -fsS http://127.0.0.1:8777/api/bootstrap | python3 -m json.tool | head
docker logs --tail 100 dashboard_webapp
docker ps --filter name=dashboard_webapp --format '{{.Names}} {{.Status}}'
```

Display helper:

```bash
ssh display-pi 'systemctl --user status kitchen-vlc-helper.service --no-pager -l'
ssh display-pi 'curl -fsS http://127.0.0.1:8778/health'
ssh display-pi 'curl -fsS http://127.0.0.1:8778/status'
```

Open a stream directly on the helper:

```bash
curl -fsS -X POST http://192.168.10.92:8778/open/simon
curl -fsS -X POST http://192.168.10.92:8778/open/claire
curl -fsS -X POST http://192.168.10.92:8778/open/doorbell
```

Check display processes:

```bash
ssh display-pi 'pgrep -a chromium || true; pgrep -a vlc || true; pgrep -af back_overlay.py || true; pgrep -a squeekboard || true'
```

If the on-screen keyboard appears and stays up:

```bash
ssh display-pi 'pkill squeekboard || true'
```

## Files To Know

Dashboard:

```text
/home/pi/dashboard_webapp/app/main.py
/home/pi/dashboard_webapp/app/config.py
/home/pi/dashboard_webapp/app/static/index.html
/home/pi/dashboard_webapp/app/static/app.js
/home/pi/dashboard_webapp/app/static/styles.css
/home/pi/dashboard_webapp/app/static/editorial.css
/home/pi/dashboard_webapp/docker-compose.yml
/home/pi/dashboard_webapp/data/cover_overrides.json
```

Display helper:

```text
/home/pi/dashboard_webapp/display_helper/app.py
/home/pi/dashboard_webapp/display_helper/back_overlay.py
/home/pi/dashboard_webapp/display_helper/.env
/home/pi/dashboard_webapp/deploy/systemd/kitchen-vlc-helper.user.service
```

Display Pi session/config:

```text
~/.config/systemd/user/kitchen-vlc-helper.service
~/.config/labwc/rc.xml
~/.config/labwc/autostart
/usr/local/bin/kitchen-display-chromium
```

The display compositor is `labwc`. The touchscreen is rotated/mapped in labwc
config; be careful with overlay/window-manager changes.

## Assumptions

- Beelink remains `192.168.10.217`.
- Display Pi remains reachable as `display-pi` / `192.168.10.92`.
- Home Assistant is reachable from the dashboard container via
  `HA_BASE_URL=http://127.0.0.1:8123`.
- HA token is read from `/home/pi/cecret_lake/dashboard_webapp/ha_token`.
- Blue Iris remains `192.168.10.49:81`.
- The slideshow remains external at `http://192.168.10.217:9010`.

## Avoid These Mistakes

- Do not assume a browser refresh picks up code changes; rebuild the Docker
  image.
- Do not `rsync --delete display_helper/` without preserving `.venv`.
- Do not add broad HA service proxies. Keep `/api/ha/service` allowlists narrow.
- Do not reintroduce legacy DLNA/gmediarender audio entities.
- Do not make the VLC overlay visually mutate after taps unless testing the
  known flip bug.
- Do not restart or kill the kiosk browser unless the user expects the visible
  display to change.
