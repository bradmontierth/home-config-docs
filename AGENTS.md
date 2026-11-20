# Agent Instructions

## Android APK Builds

Before building or publishing Android APKs on this host, read:

```text
android-apk-publishing-guide.md
```

Use the memory-capped Gradle commands from that guide. Published APKs for the
Homepage `APK Downloads` links live in `/home/pi/apks`.

## Node-RED

Before inspecting or changing Node-RED flows, read:

```text
nodered-flow-agent-guide.md
```

Node-RED deployed flow changes must go through the Node-RED Admin API. Do not edit deployed `flows.json` files directly and do not restart Node-RED as a substitute for an API deploy.

For flow changes:

1. Use the Admin API as the source of truth for the running instance.
2. Pull the target tab with `GET /flow/TAB_ID`.
3. Modify only that tab JSON.
4. Deploy with `PUT /flow/TAB_ID` unless a broader deploy is explicitly required.
5. Re-read the tab from the API and check Node-RED logs.

The project flow files may be used as reference copies only.

## UI Screenshots

Take UI screenshots on the Beelink or the GX10, never on `display-pi`:

```text
/home/pi/home_config/tools/shot.py <url> /tmp/x.png [--mobile | --size 1920,1080] [--wait S] [--click SEL]
```

For WebGL/3D pages (e.g. the ride-world samples) use the GX10's GPU instead. The
Beelink has no GPU, so Chromium renders WebGL in software at about 1 frame/s and
a 3D screenshot takes minutes; on the GX10 it takes seconds. Same flags as shot.py:

```text
/home/pi/home_config/tools/gx10shot.sh <url> /tmp/x.png --size 1280,720 --viewport-only --until "window.rw && window.rw.frames > 3"
```

It runs shot.py in the `shots-gpu:1` container on the GX10 (as uid 1000, a few
hundred MB of GPU memory; no need to pause other GX10 services).

`display-pi` is the live kitchen kiosk, a throttle-prone Raspberry Pi 4. Do not
launch headless Chromium there. Agent runs there competed with the kiosk and left
hundreds of temp profiles in its RAM-backed `/tmp` (cleaned 2026-10-26). Touch it
only when the real kiosk or touchscreen is under test, and clean up its `/tmp`
afterwards.
