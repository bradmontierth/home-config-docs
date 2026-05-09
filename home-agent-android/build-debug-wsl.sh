#!/usr/bin/env bash
set -euo pipefail

export JAVA_HOME="${JAVA_HOME:-/home/brad/.local/android-build-tools/jdk-17.0.19+10}"
export ANDROID_HOME="${ANDROID_HOME:-/home/brad/.android-sdk}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"
export ANDROID_USER_HOME="${ANDROID_USER_HOME:-/home/brad/.android}"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-/home/brad/.gradle}"
export GRADLE_OPTS="${GRADLE_OPTS:-"-Duser.home=/home/brad"}"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"

./gradlew assembleDebug
