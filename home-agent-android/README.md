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

## FCM Notifications

Push notifications use Firebase Cloud Messaging for delivery, but Home Agent data and control still go through the LAN gateway.

Setup:

1. Create a Firebase project and add Android package `com.homeagent.phone`.
2. Download `google-services.json` and place it at `/home/pi/cecret_lake/home-agent-android/google-services.json`.
3. Create a Firebase service account JSON for the gateway host.
4. Set gateway env vars:

```text
HOME_AGENT_FCM_PROJECT_ID=your-firebase-project-id
HOME_AGENT_FCM_SERVICE_ACCOUNT_JSON=/home/pi/cecret_lake/home-agent/firebase-service-account.json
HOME_AGENT_PUSH_REGISTRY=/home/pi/home_config/home-agent/sessions/push_tokens.json
```

The Android build copies `google-services.json` from `/home/pi/cecret_lake/home-agent-android/google-services.json` into the ignored app-local location when present. You can override that source with `HOME_AGENT_GOOGLE_SERVICES_JSON` or the Gradle property `homeAgentGoogleServicesJson`.

`app/google-services.json` and service-account JSON files are ignored by git. Without `google-services.json`, the Android app still builds, but FCM token registration is skipped at runtime.
