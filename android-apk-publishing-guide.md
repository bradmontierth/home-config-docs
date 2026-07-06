# Android APK Build And Publishing Guide

This guide documents the shared Android toolchain on this host, the safe Gradle
settings to use on a memory-constrained homelab server, and where locally built
APKs are published for the Home Services dashboard.

## Homepage APK Endpoints

The Home Services dashboard is configured in:

```text
/home/pi/homepage/config/services.yaml
```

APK links are under the `APK Downloads` section.

| App | Homepage link | Served file |
| --- | --- | --- |
| TTS Router | `http://192.168.10.217:3000/apk/tts-router-latest.apk` | `/home/pi/apks/tts-router-latest.apk` |
| Home Agent | `http://192.168.10.217:3000/apk/home-agent-latest.apk` | `/home/pi/apks/home-agent-latest.apk` |
| AntennaPod V2 | `http://192.168.10.217:3000/apk/antennapod-v2-latest.apk` | `/home/pi/apks/antennapod-v2-latest.apk` |
| Tempo | `http://192.168.10.217:3000/apk/tempo-latest.apk` | `/home/pi/apks/tempo-latest.apk` |
| Voice Notes | `http://192.168.10.217:3000/apk/voice-notes-latest.apk` | `/home/pi/apks/voice-notes-latest.apk` |
| STT Keyboard | `http://192.168.10.217:3000/apk/android-stt-latest.apk` | `/home/pi/apks/android-stt-latest.apk` |
| Windows Transcribe | `http://192.168.10.217:3000/apk/windows-transcribe-latest.exe` | `/home/pi/apks/windows-transcribe-latest.exe` |

Publish by copying the built APK or executable to the matching served file in `/home/pi/apks`,
then verify the source and destination hashes match. Homepage serves this
directory from its `/apk/` path.

Note: when adding a brand-new served filename, Homepage (Next.js) can cache the
not-found result for a path requested before the file existed, returning `404`
even though the file is present in the container. Restart the dashboard to clear
it: `cd /home/pi/homepage && docker compose restart homepage`.

The `/home/pi/apks` mount is declared in:

```text
/home/pi/homepage/docker-compose.override.yml
```

Current source APKs for the service-backed apps:

```text
/home/pi/tts-router/android/app/build/outputs/apk/debug/app-debug.apk
/home/pi/home_config/home-agent-android/app/build/outputs/apk/debug/app-debug.apk
```

## Shared Android Toolchain

The shared local Android toolchain is stored outside individual app repos:

```text
/home/pi/home_config/home-agent-android/.build-tools/jdk-17
/home/pi/home_config/home-agent-android/.build-tools/jdk-21
/home/pi/home_config/home-agent-android/.android-sdk
/home/pi/home_config/home-agent-android/.android-user-home
/home/pi/home_config/home-agent-android/.gradle-home
/home/pi/.android
```

Use JDK 21 for AntennaPod V2. Its Gradle config requires Java 21.

```bash
export JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-21
export ANDROID_HOME=/home/pi/home_config/home-agent-android/.android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME=/home/pi/.android
export GRADLE_USER_HOME=/home/pi/home_config/home-agent-android/.gradle-home
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
```

Use JDK 17 for Tempo unless that project is upgraded.

```bash
export JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-17
export ANDROID_HOME=/home/pi/home_config/home-agent-android/.android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME=/home/pi/home_config/home-agent-android/.android-user-home
export GRADLE_USER_HOME=/home/pi/home_config/home-agent-android/.gradle-home
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
```

## Safe Gradle Settings

Do not run broad aggregate tasks such as `assembleDebug` on this host unless
you specifically intend to build every debug flavor. On AntennaPod V2,
`assembleDebug` builds both `freeDebug` and `playDebug`, which can start enough
Java workers to put the host under memory pressure.

For homelab safety:

- Build only the needed variant.
- Use `--no-daemon` so Gradle does not leave a daemon resident.
- Use `--max-workers=1` to limit parallel Java/Gradle work.
- Prefer a systemd memory cap so a bad build fails instead of pressuring the
  whole host.

Memory-capped Gradle wrapper pattern:

```bash
systemd-run --user --scope \
  -p MemoryMax=3G \
  -p MemorySwapMax=1G \
  ./gradlew --no-daemon --max-workers=1 TASK_NAME
```

If `systemd-run --user` is unavailable in the current shell, use the same
single-variant Gradle command without the scope and watch memory with `free -h`
or `htop`.

Stop any Gradle daemons after accidental daemon builds:

```bash
./gradlew --stop
```

## AntennaPod V2 Build And Publish

AntennaPod V2 is the podcast Android app being built for this pipeline.

Repo (single checkout on `develop`; the old `/home/pi/antennapod-v2` worktree was
merged into `develop` and removed in July 2026):

```text
/home/pi/antennapod
```

Application ID for debug builds:

```text
de.danoeh.antennapod.v2.debug
```

Use the normal pi user's debug keystore for AntennaPod V2. Earlier local
AntennaPod debug installs used this key; switching to the shared home-agent
debug key will cause Android to reject upgrades with a signature mismatch.

```text
/home/pi/.android/debug.keystore
```

Expected AntennaPod V2 debug signer SHA-256:

```text
9b26a7ee33539dc9045d367641a584d43158af1bb038b9672e576ec27e3b1d99
```

Build only the free debug variant:

```bash
cd /home/pi/antennapod

export JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-21
export ANDROID_HOME=/home/pi/home_config/home-agent-android/.android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME=/home/pi/.android
export GRADLE_USER_HOME=/home/pi/home_config/home-agent-android/.gradle-home
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

systemd-run --user --scope \
  -p MemoryMax=3G \
  -p MemorySwapMax=1G \
  ./gradlew --no-daemon --max-workers=1 :app:assembleFreeDebug
```

Build output:

```text
/home/pi/antennapod/app/build/outputs/apk/free/debug/app-free-debug.apk
```

Publish to the dashboard endpoint:

```bash
mkdir -p /home/pi/apks
cp /home/pi/antennapod/app/build/outputs/apk/free/debug/app-free-debug.apk \
  /home/pi/apks/antennapod-v2-latest.apk

sha256sum \
  /home/pi/antennapod/app/build/outputs/apk/free/debug/app-free-debug.apk \
  /home/pi/apks/antennapod-v2-latest.apk
```

Verify the homepage endpoint:

```bash
curl -I http://192.168.10.217:3000/apk/antennapod-v2-latest.apk
```

Verify the signer:

```bash
JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-21 \
PATH=/home/pi/home_config/home-agent-android/.build-tools/jdk-21/bin:$PATH \
/home/pi/home_config/home-agent-android/.android-sdk/build-tools/35.0.0/apksigner \
  verify --print-certs /home/pi/apks/antennapod-v2-latest.apk
```

The SHA-256 digest should be:

```text
9b26a7ee33539dc9045d367641a584d43158af1bb038b9672e576ec27e3b1d99
```

## Tempo Build And Publish

Tempo is a separate Android app. Use this section only when rebuilding Tempo.

Repo:

```text
/home/pi/tempo
```

Application ID:

```text
com.cappielloantonio.tempo
```

Build:

```bash
cd /home/pi/tempo

export JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-17
export ANDROID_HOME=/home/pi/home_config/home-agent-android/.android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME=/home/pi/home_config/home-agent-android/.android-user-home
export GRADLE_USER_HOME=/home/pi/home_config/home-agent-android/.gradle-home
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

systemd-run --user --scope \
  -p MemoryMax=3G \
  -p MemorySwapMax=1G \
  ./gradlew --no-daemon --max-workers=1 assembleTempoDebug
```

Build output:

```text
/home/pi/tempo/app/build/outputs/apk/tempo/debug/app-tempo-debug.apk
```

Publish:

```bash
cp /home/pi/tempo/app/build/outputs/apk/tempo/debug/app-tempo-debug.apk \
  /home/pi/apks/tempo-latest.apk

sha256sum \
  /home/pi/tempo/app/build/outputs/apk/tempo/debug/app-tempo-debug.apk \
  /home/pi/apks/tempo-latest.apk
```

## Voice Notes Build And Publish

Voice Notes is a private LAN voice-capture Android app that records, transcribes
on the GB10 ASR server, and syncs to the companion service.

Repo:

```text
/home/pi/voice-notes-android
```

Application ID:

```text
com.local.voicenotes
```

Build (JDK 17):

```bash
cd /home/pi/voice-notes-android

export JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-17
export ANDROID_HOME=/home/pi/home_config/home-agent-android/.android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME=/home/pi/home_config/home-agent-android/.android-user-home
export GRADLE_USER_HOME=/home/pi/home_config/home-agent-android/.gradle-home
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

systemd-run --user --scope \
  -p MemoryMax=3G \
  -p MemorySwapMax=1G \
  ./gradlew --no-daemon --max-workers=1 :app:assembleDebug
```

Build output:

```text
/home/pi/voice-notes-android/app/build/outputs/apk/debug/app-debug.apk
```

Publish:

```bash
cp /home/pi/voice-notes-android/app/build/outputs/apk/debug/app-debug.apk \
  /home/pi/apks/voice-notes-latest.apk

sha256sum \
  /home/pi/voice-notes-android/app/build/outputs/apk/debug/app-debug.apk \
  /home/pi/apks/voice-notes-latest.apk
```

## STT Keyboard Build And Publish

STT Keyboard is a system-wide voice dictation keyboard (IME). It records audio,
transcribes on the GX10 Parakeet endpoint, and offers an LLM "Clean up" pass.
See `stt-keyboard-guide.md` for architecture and behavior.

Repo:

```text
/home/pi/android-stt
```

Remote: `git@github-illuminate:bradmontierth/android-stt.git` (push with the
`github-illuminate` SSH host alias).

Application ID:

```text
com.local.androidstt
```

Build (JDK 17):

```bash
cd /home/pi/android-stt

export JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-17
export ANDROID_HOME=/home/pi/home_config/home-agent-android/.android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME=/home/pi/home_config/home-agent-android/.android-user-home
export GRADLE_USER_HOME=/home/pi/home_config/home-agent-android/.gradle-home
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

systemd-run --user --scope \
  -p MemoryMax=3G \
  -p MemorySwapMax=1G \
  ./gradlew --no-daemon --max-workers=1 :app:assembleDebug
```

Build output:

```text
/home/pi/android-stt/app/build/outputs/apk/debug/app-debug.apk
```

Publish:

```bash
cp /home/pi/android-stt/app/build/outputs/apk/debug/app-debug.apk \
  /home/pi/apks/android-stt-latest.apk

sha256sum \
  /home/pi/android-stt/app/build/outputs/apk/debug/app-debug.apk \
  /home/pi/apks/android-stt-latest.apk
```

## Signing Keys

Android upgrades require the new APK to have the same application ID and signer
certificate as the installed APK.

The shared debug keystore is:

```text
/home/pi/home_config/home-agent-android/.android-user-home/debug.keystore
```

Do not delete, regenerate, or replace this keystore for internal debug builds
that are expected to upgrade in place.

Known Tempo signer SHA-256:

```text
9d629274f5119770ef5f106b23ca3467f9df5c713ea2b9772942b91eccb31b6b
```

Tempo reads its signing config from:

```text
/home/pi/tempo/local.properties
```

Expected Tempo signing entries:

```text
sdk.dir=/home/pi/home_config/home-agent-android/.android-sdk
tempo.debug.storeFile=/home/pi/home_config/home-agent-android/.android-user-home/debug.keystore
tempo.debug.storePassword=android
tempo.debug.keyAlias=androiddebugkey
tempo.debug.keyPassword=android
```
