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
