# Satellite feedback chimes

Short UI cues played **locally on the satellite** for instant feedback (see
voice-assistant-plan.md). Kept version-controlled here; deployed to the
satellite's local sound dir so playback is zero-latency (no network fetch).

## Chosen mapping
| File | Role | Source clip | Length |
| --- | --- | --- | --- |
| `wake.wav` | **Wake confirmed** (fires on stage-2 verify, not stage-1) | chime_tts `glockenspiel` | 0.97s |
| `vad.wav` | **End-of-speech** ("got it, thinking") | chime_tts `chirp` | 0.39s |
| `dismiss.wav` | **Alarm stopped** (confirmation chirp) | chime_tts `bright` | 0.64s |
| `vad_alt_tap.wav` | VAD alternative | chime_tts `microphone_tap` | 0.76s |
| `vad_alt_bright.wav` | VAD alternative | chime_tts `bright` | 0.64s |

Wake fires on **stage-2** (Parakeet-verified) so the chime is never spurious;
the dashboard badge lights on stage-1 for an instant silent visual ack.

## Source + license
From **chime_tts** (github.com/nimroddolev/chime_tts), **MIT licensed** — the
bundled mp3s are covered by the repo's MIT license, so redistribution here is
fine. Originals kept in `_src/`. Primary CC0 alternatives if we want more:
Pixabay sound-effects, Mixkit.

## Reproduce / re-normalize
All chimes normalized to a consistent loudness + format (EBU R128, 44.1k
stereo 16-bit):
```bash
ffmpeg -y -i _src/<clip>.mp3 -af "loudnorm=I=-16:TP=-1.5:LRA=11" \
       -ar 44100 -ac 2 -c:a pcm_s16le <out>.wav
```
To swap a chime: drop a new mp3 in `_src/`, re-run the command, commit.

**Trim lead silence** (2026-07-12): `wake.wav` carried ~170ms of silence before
the first audible sample — dead time added to every wake ack. Any new/re-made
chime must get the same pass (keeps a 20ms pad):
```bash
ffmpeg -y -i <out>.wav -af \
  "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02" \
  -c:a pcm_s16le <out>-trimmed.wav && mv <out>-trimmed.wav <out>.wav
```

## Still to source
Cooking timer theme foley (`cluck`, `moo`, `sizzle`, `bubbling` — real animal/
kitchen sounds) — chime_tts has no animal foley; pull CC0 from Pixabay.
chime_tts `whistle`/`ding_dong`/`mario_coin`/`tada` cover the neutral/fun themes.
