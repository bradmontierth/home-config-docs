# Grandparents Photo Displays Guide

This guide documents the remote Raspberry Pi photo displays that run the
`MyPhotoApp` slideshow client.

## Architecture

The server runs on the Beelink:

```text
Beelink / this host
├── MyPhotoApp server  http://beelink:8088
└── MinIO              http://beelink:9000
```

Each display runs the Dockerized client from:

```text
/home/pi/my_photo_app/rpi_client
```

The display browser opens its local slideshow:

```text
http://localhost:9010/
```

The slideshow intentionally continues serving cached photos if the display
cannot reach the Beelink.

## Display Inventory

Steinhorst display:

```text
Tailscale device: steinhorst-display
Tailscale IP:     100.66.199.1
Host hostname:    kitchen-pi-display
Client ID:        steinhorsts
Client name:      Steinhorst Display
```

Connect from the Beelink with Tailscale SSH:

```bash
tailscale ssh pi@100.66.199.1
```

Use the raw Tailscale IP. Do not assume the Tailscale DNS name resolves from
the Beelink shell.

Montierth display client (moved to the N100 on `2026-06-27`):

```text
Tailscale device: n100
Tailscale IP:     100.88.96.43
Host hostname:    N100
Client ID:        montierth
Client name:      Montierth Display
Client source:    /home/markmontierth/grandkid_photo_app/rpi_client
Client env:       /home/markmontierth/cecret_lake/my_photo_app_client/.env
```

Connect from the Beelink with Tailscale SSH:

```bash
tailscale ssh root@100.88.96.43
```

The client env uses the raw Beelink Tailscale IP `100.79.129.106` for both
`SERVER_BASE_URL` and `S3_ENDPOINT_URL`. Do not switch these back to the bare
`beelink` hostname; see the `2026-07-02` incident below.

Retired/related nodes:

- `n100mini` (`100.98.121.21`) was the previous Montierth client host. It has
  been Tailscale-offline since `2026-06-24`.
- `montierth-display` (`100.121.83.104`, host `smartpanel`) is not the
  MyPhotoApp client host despite its device name. It shows online in the
  Tailscale console, which is misleading when diagnosing display outages.

## Required Tailscale ACL Paths

Displays need outbound TCP access to the Beelink:

```text
display -> 100.79.129.106:8088  MyPhotoApp server and heartbeat API
display -> 100.79.129.106:9000  MinIO manifest and encrypted media
```

Tailscale SSH access is separate from application-port ACL rules. A display can
accept `tailscale ssh` while sync and heartbeat traffic remain blocked.

## Read-Only Checks

Connect:

```bash
tailscale ssh pi@100.66.199.1
```

Check runtime state:

```bash
uptime
systemctl --failed --no-pager
systemctl status tailscaled --no-pager -l
nmcli general status
nmcli -f GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY device show wlan0
docker ps
```

Check Beelink connectivity:

```bash
curl -fsS --max-time 8 http://beelink:8088/health
curl -sS -o /dev/null -w 'http=%{http_code} connect=%{time_connect} total=%{time_total}\n' \
  --max-time 8 http://beelink:9000/
```

MinIO normally returns HTTP `403` for the unauthenticated root request. That is
enough to confirm the TCP path works.

Read the MinIO feed using the deployed client credentials:

```bash
docker exec -i -w /app rpi_client-rpi_client-1 python - <<'PY'
from src.s3_client import get_object
from src.config import FEED_S3_KEY
import json

items = json.loads(get_object(FEED_S3_KEY).decode("utf-8"))
print("manifest_ok", len(items))
PY
```

Inspect slideshow client logs:

```bash
docker logs --timestamps --tail 240 rpi_client-rpi_client-1
```

Check recent Tailscale ACL rejections:

```bash
journalctl -b 0 -u tailscaled --no-pager | \
  grep -E 'rejected due to acl|Drop:|8088|9000' | tail -n 160
```

## Touchscreen Multi-Touch (Steinhorst And Montierth Smartpanel)

Fixed 2026-07-04 on both remote Waveshare displays, same root cause as the
kitchen display (see `kitchen-dashboard-display-guide.md`, "Touchscreen And
Multi-Touch"): labwc was converting touch to mouse pointer events. All three
panels use the same ILITEK ILITEK-TP 10-point controller. Change made in
`~/.config/labwc/rc.xml` (backup: `rc.xml.bak-20260704` on each host):

```xml
<touch deviceName="ILITEK ILITEK-TP" mapToOutput="HDMI-A-1" mouseEmulation="no"/>
```

Applied live with `killall -SIGHUP labwc`; verified on both with injected
evemu gestures that the page receives real touchstart/touchmove and two
simultaneous touch points.

Montierth smartpanel specifics (`montierth-display` / `100.121.83.104`):

- SSH as `root` (`tailscale ssh root@100.121.83.104`); the session user is
  `admin`, so browser relaunches need
  `runuser -u admin -- env WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 ...`.
- It displays an HA dashboard from the N100 LAN endpoint
  (`http://192.168.123.47:8123/kitchen-dasher/0`), not the slideshow client.
- Chromium is wrapped in `/usr/bin/lwrespawn` from
  `/home/admin/.config/labwc/autostart`, so it auto-respawns if killed. To
  run a temporary debug instance, kill the wrapper first with
  `pkill -f "[l]wrespawn"` (the bracket trick avoids pkill matching your own
  ssh command and dropping the session).
- Touch device is `/dev/input/event4`; no rotation (no udev matrix,
  transform normal). `evemu-tools` and `python3-websockets` are installed.

Notes specific to Steinhorst:

- Chromium there is launched from `~/.config/labwc/autostart` without
  `--ozone-platform=wayland`; touch still arrives correctly (via Xwayland
  XInput2), so the launch flags were left unchanged.
- No display rotation exists on this unit (no udev calibration matrix,
  `transform normal`), unlike the kitchen display.
- Pinch-zoom is suppressed by the slideshow page itself, not the input
  stack; single-finger swipe/drag and taps deliver as real touch.
- There is no browser watchdog timer on this host: if you `pkill chromium`,
  relaunch it manually with the autostart flags plus
  `WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000`.
- Test scripts: `home_config/diagnostics/touchscreen/` (touch device is
  `/dev/input/event4` on this host; requires `evemu-tools`, installed).

The Montierth N100 itself has no touchscreen input device (mouse/keyboard
only); it is the server. The touch display on that side is the smartpanel
above.

## Steinhorst Incident: 2026-05-31

Observed evidence:

- The slideshow container stayed alive from `2026-02-03` until the manual power
  cycle around `2026-05-30 21:29 MDT`.
- Local slideshow requests continued every five minutes while remote sync was
  dark. The display kept serving cached photos.
- Primary photo downloads slowed around `2026-05-19`, stopped after
  `2026-05-21`, and caught up immediately after reboot with 33 downloads.
- The old host journal was not retained, so the original Wi-Fi/Tailscale trigger
  cannot be proven retrospectively.
- The client implementation ran heartbeat reporting only after sync completed,
  so a blocked sync request also suppressed heartbeat reporting.

Likely failure shape:

```text
network or MinIO request stalls
        |
        v
single client sync thread stalls
        |
        +--> no new downloads
        +--> no heartbeat
        +--> local cached slideshow continues normally
```

## Montierth Incident: 2026-07-02

Heartbeats stopped at `2026-07-02 04:45 UTC` after the N100 rebooted. The
container came back up and kept serving cached photos locally, but every sync
and heartbeat failed with:

```text
Failed to resolve 'beelink' ([Errno -3] Temporary failure in name resolution)
```

Root cause: the container auto-started at boot before `tailscaled` registered
MagicDNS, so Docker generated the container's `resolv.conf` with only
`search localdomain` and no `tail65546d.ts.net` search domain. The bare name
`beelink` then never resolved, while the raw IP `100.79.129.106` remained
reachable the whole time.

Fix applied `2026-07-03`: replaced `beelink` with `100.79.129.106` in
`SERVER_BASE_URL` and `S3_ENDPOINT_URL` in the client env (backup saved as
`.env.backup_beelink_dns_20260703`) and recreated the container. This removes
the boot-order DNS race entirely.

Diagnostic note: the Tailscale console showed `montierth-display` online
during the outage, but that device is the `smartpanel`, not the client host.
Confirm which host actually runs the `rpi_client` container before trusting
console online status.

## Local Source

Source-of-truth repo on the Beelink:

```text
/home/pi/my_photo_app
```

The hardened local source adds:

- independent heartbeat reporting
- bounded MinIO timeouts and retries
- sync attempt, failure, success, and recovery logs
- preservation of cached photos when feed retrieval fails
- server-side heartbeat history
- stale-heartbeat, stale-sync, and recovery Pushover alerts

Deployment status as of `2026-05-31`:

- Steinhorst client hardening is deployed and active.
- Montierth client hardening is deployed and active; the client moved from
  the `n100mini` to the N100 (`100.88.96.43`) on `2026-06-27`.
- Beelink server monitoring is deployed and active.
- The server stores heartbeat history and checks for stale heartbeat and stale
  successful sync independently.
- Default stale-alert thresholds are 3 hours, with repeat alerts every 24
  hours while a problem persists.

Deploying a client rebuilds and restarts its slideshow container.

## Editorial Pro Viewer And Age Captions (2026-07-10)

The client slideshow page was rebuilt as
`rpi_client/src/static/viewer.html` (the Editorial Pro design shared with
the my_photo_app console Viewer and the kitchen immich-slideshow viewer),
replacing the old ~860-line inline page in `slideshow_server.py`:

- clock + date upper-left, tick progress marks lower-right
- captions lower-left: `Name (age)` main line, `Location · Month Year` sub
- portrait pairs and lone portraits render uncropped over a blurred fill;
  landscape singles are full-bleed with a top-biased crop
- swipe left/right navigation with history (plus arrow keys / mouse drag)
- videos rotate as posters with a play badge; tap plays WITH sound and
  native controls (Steinhorst has a speaker and uses it); clip end resumes
  rotation
- the "Videos only" toggle survives as a pill top-right
  (`SHOW_VIDEOS_ONLY_TOGGLE`); `?forceVideoId=` / `?audioMode=` still work
- dwell defaults to 300s; override with `DWELL_SECONDS` env or `?dwell=`
- pinch-zoom/pan (restored 2026-07-10, commit `57dc9d7`): pinch zooms the
  touched pane, one finger pans while zoomed, double-tap resets; swipe
  and tap-to-play are suspended while zoomed, and auto-advance holds at
  most one extra dwell cycle before resetting the zoom, so a stray pinch
  cannot freeze the rotation
- clock fonts are 64px/22px (bumped 2026-07-10 for cross-room legibility;
  same change in the kitchen immich-slideshow viewer)
- the page keeps the kitchen postMessage protocol
  (`slideshow-nav`/`slideshow-tap`/`slideshow-fullscreen`) and a widget
  mode below 700x450px, so it can be embedded dashboard-style later
  (Montierth plan)

Age captions ("Claire (11 mo)", "Simon (3)") are computed server-side at
feed build: `Person.birth_date` is synced from Immich people
(`seed-people`), shared across the per-Immich-account duplicate person
rows by display name, and baked into each feed item as `ageLabel` (age at
photo time, so it never goes stale). Clients store it in a new
`age_label` column; the sync backfills existing rows. To refresh after a
birthday edit in Immich: `POST /api/admin/seed-people` then
`POST /api/frame/republish` on the Beelink, then let clients sync.

Deployed 2026-07-10: Beelink server + Steinhorst client (commit
`73e789c`). The Montierth N100 client still runs the old page until its
display rework lands (its HA dashboard iframes the viewer; the new API is
backward-compatible with the old page).

## Deploying A Client From Git

The display Pis clone the GitHub repo over https and have no credentials,
so `git pull` fails there. Ship a bundle over Tailscale SSH instead
(Steinhorst example):

```bash
cd /home/pi/my_photo_app && git bundle create /tmp/mpa.bundle master
tailscale ssh pi@100.66.199.1 'cat > /tmp/mpa.bundle' < /tmp/mpa.bundle
tailscale ssh pi@100.66.199.1 'cd /home/pi/my_photo_app && git fetch /tmp/mpa.bundle master && git reset --hard FETCH_HEAD'
tailscale ssh pi@100.66.199.1 'cd /home/pi/my_photo_app && docker compose -f rpi_client/docker-compose.yml up -d --build'
```

The `reset --hard` is safe: the 2026-05-31 hardening was rsynced onto the
Pi as uncommitted modifications, but those files are byte-identical to
what is now committed on master (verified by md5 on 2026-07-10).

Reload the kiosk browser after a deploy. Steinhorst has NO chromium
watchdog, and `pkill -f chromium` over ssh kills your own session (the
pattern matches the remote command line). The bracket trick does NOT
save you if the same compound command also contains a plain `chromium`
relaunch — run the kill and the relaunch as two separate ssh commands:

```bash
tailscale ssh pi@100.66.199.1 'pkill -f "[c]hromium"'
tailscale ssh pi@100.66.199.1 'setsid env WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 chromium http://localhost:9010/ --password-store=basic --alsa-output-device=default --autoplay-policy=no-user-gesture-required --disable-features=AudioServiceOutOfProcess,AudioServiceSandbox --kiosk --noerrdialogs --disable-infobars --no-first-run --enable-features=OverlayScrollbar --touch-events=enabled --enable-pinch --start-maximized >/tmp/chromium-relaunch.log 2>&1 < /dev/null &'
```

Verify with a screenshot:
`tailscale ssh pi@100.66.199.1 'WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 grim /tmp/shot.png'`.

## Beelink Local Test Client

The Beelink also runs an `rpi_client` container on `:9010` (profile mode;
useful as a deploy test bed). On 2026-07-10 its recreated container could
no longer resolve `beelink` — AdGuard serves the host's `/etc/hosts` entry
`127.0.1.1`, which is the container itself. Same fix as the Montierth
2026-07-02 incident: `S3_ENDPOINT_URL=http://192.168.10.217:9000` in
`/home/pi/cecret_lake/my_photo_app_client/.env` (backup:
`.env.backup_beelink_dns_20260710`). Never use the bare `beelink` name
from inside containers.
