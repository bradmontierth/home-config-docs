# Home Agent Android

Basic native Android client for the LAN Home Agent gateway.

## What It Does

- Records push-to-talk audio with Android microphone permission.
- Uploads the recording to the gateway at `/api/transcribe`.
- Shows the returned transcript for editing. Before a session exists, voice fills the main task field; after a session exists, voice fills the Reply field for continuing that session.
- Starts a Codex session through `/api/sessions`.
- Lists saved sessions through `/api/sessions`.
- Loads formatted terminal history through `/api/sessions/{session_id}/log` when reopening or selecting a session.
- Resumes an archived session through `/api/sessions/{session_id}/resume` when you send clarification after the prior run has exited.
- Streams terminal output through `/ws/sessions/{session_id}`.
- Renders terminal output with subtle styling so agent messages, commands, hidden output summaries, approvals, and errors are easier to scan.
- Sends Approve, Reject, Summary, Stop, and free-text Reply messages.

The app intentionally does not talk to Parakeet or Codex directly. Those stay behind the gateway/runner services in `/home/pi/home_config/home-agent`.

## Build On This PC / WSL

This WSL checkout has a local Android build toolchain installed at:

```text
/home/brad/.local/android-build-tools/jdk-17.0.19+10
/home/brad/.android-sdk
```

Build with:

```bash
cd home-agent-android
./build-debug-wsl.sh
```

Debug APK path:

```text
app/build/outputs/apk/debug/app-debug.apk
```

Install with adb:

```bash
export PATH=/home/brad/.android-sdk/platform-tools:$PATH
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## Defaults

The app defaults to:

```text
http://192.168.10.217:8767
```

You can edit the gateway URL and token in the app. Values are stored in Android shared preferences.

The manifest allows cleartext HTTP for LAN use. Keep the gateway LAN-only and set `HOME_AGENT_TOKEN` before routine use.
