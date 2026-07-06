# STT Keyboard Guide

A system-wide speech-to-text Android keyboard (IME). The whole surface is a large
microphone button — there are no character keys. Tap to record; audio is sent to the
GX10 Parakeet ASR endpoint and the transcript is committed into whatever text field is
focused. A **Clean up** button sends the last dictation to the GX10 LLM for strict
correction (misheard words, punctuation, capitalization) without rephrasing.

- Repo: `/home/pi/android-stt`
- Remote: `git@github-illuminate:bradmontierth/android-stt.git` (push via the
  `github-illuminate` SSH host alias)
- Application ID: `com.local.androidstt`
- IME service: `com.local.androidstt/.SttImeService`
- Setup/settings Activity: `.SetupActivity`
- Build toolchain + publish steps: see `android-apk-publishing-guide.md`
- Install link (Homepage → APK Downloads): `http://192.168.10.217:3000/apk/android-stt-latest.apk`

## Endpoints (defaults, overridable in the app's setup screen)

| Purpose | Default | Call |
| --- | --- | --- |
| Parakeet ASR | `http://192.168.10.187:8090` | `POST /parakeet/transcribe` (raw WAV body) |
| Reachability | `http://192.168.10.187:8090` | `GET /healthz` (drives the status dot) |
| LLM cleanup | `http://192.168.10.187:8095/v1` | `POST /chat/completions`, model `qwen3-next` |

Cleartext to the LAN hosts is allowed via `res/xml/network_security_config.xml`
(`192.168.10.187`, `192.168.10.217`). The same IPs resolve over WireGuard, so it
works off-LAN with the tunnel up and no config change.

## Architecture

- `SttImeService` — the `InputMethodService`. Owns the keyboard view, recording state
  machine (`idle / recording / transcribing / cleaning / error`), animations, the
  utility actions, and all `InputConnection` text edits.
- `VoiceRecorder` + `WavWriter` — 16 kHz mono PCM16 capture to a WAV file (adapted from
  `voice-notes-android`). `VoiceRecorder.lastLevel()` feeds the waveform amplitude.
- `AsrClient` — posts the WAV to Parakeet, returns `transcript_text`.
- `LlmClient` — OpenAI-compatible chat call with a strict cleanup system prompt; strips
  any `<think>` block (harmless no-op for the current `qwen3-next` build).
- `AppConfig` — endpoint overrides in SharedPreferences.
- `WaveformView` — custom view, ~30 staggered bars, amplitude-driven.
- `SetupActivity` — launcher screen that grants `RECORD_AUDIO` (an IME cannot request
  runtime permissions itself), links to keyboard settings/picker, and edits endpoints.

## UI (design handoff, Layout A — dark/light follow system)

- Status bar: reachability dot (green when `/healthz` answers, pulsing red while
  recording), monospace host label, settings glyph (opens setup; long-press opens the
  IME picker as a safety since there is no ABC key).
- Utility row: Clean up · Undo · Copy · Delete (press-and-hold repeats) · Enter (editor
  action or newline).
- Mic zone: 128dp button (accent idle → red recording), amplitude waveform, and a
  caption that shows `Tap to dictate` / `M:SS Listening…` / working states.
- Color tokens live in `res/values/colors.xml` + `res/values-night/colors.xml`.

## First-run on device

1. Open **STT Keyboard** app → **Grant microphone**.
2. **Enable keyboard (settings)** → turn on "STT Dictation".
3. In any text field, **Choose keyboard now** (or the system picker) → STT Dictation.
4. Tap mic, speak, tap again. Tap **Clean up** to AI-correct the last dictation.

## Debugging

The service logs under logcat tag `SttIme` (mic, transcribe, cleanup with char counts,
errors). Watch live:

```bash
adb -s <device> logcat -s SttIme:*
```

## Known behavior notes

- The status label shows the real ASR host (e.g. `192.168.10.187`) rather than the
  design's placeholder `whisper.local`.
- Spoken "new line"/"new paragraph" are currently handled by the LLM during Clean up;
  the model tends to treat them as sentence breaks. Tighten the `LlmClient` system
  prompt if literal line breaks are wanted.
