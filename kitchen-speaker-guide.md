# Kitchen Speaker Host Guide

The host driving the kitchen big speakers. Rewritten 2026-08-06 — the previous
version of this file described the retired `.24` Raspberry Pi (GMediaRenderer/DLNA
+ LibreSpot Spotify Connect). None of that is how the kitchen works now.

## SSH access

```bash
ssh big-speaker-mini-pc
```

```sshconfig
Host big-speaker-mini-pc
  HostName 192.168.10.251
  User pi
  IdentityFile ~/.ssh/id_ed25519_big_speaker_mini_pc
  IdentitiesOnly yes
```

Hostname on the box is `minipcmax`. Wi-Fi attached (`wlp3s0`), pinned to `.251`.

**The `kitchen-speaker` alias is dead.** It still resolves to `192.168.10.24`, the
old kitchen Pi, which is powered off — you get `No route to host`. If that Pi is ever
returned to service, disable its `voice-assistant` and `squeezelite` units *first*,
or it will fight this host.

## Device role

This one mini PC **is** the whole kitchen. Four units matter:

| Service | Role |
| --- | --- |
| `squeezelite` | Music Assistant player for the big speakers |
| `voice-assistant` | Kitchen voice satellite (dual wake word, two-phase pipeline, alarms) |
| `nfc-jukebox-scanner` | NFC card reader → jukebox API on the beelink `:8769` |
| `audio-keepalive` | Holds the output stream open; kills start-of-stream pops |

Squeezelite deliberately impersonates the retired kitchen Pi's player MAC so the
Music Assistant player identity survived the migration:

```
/usr/bin/squeezelite -n Kitchen-Big-Speakers -o kitchenmix -s 192.168.10.217 -C 5 \
  -m e4:5f:01:67:1e:56
```

So in Music Assistant the player id is literally `e4:5f:01:67:1e:56`, named
`Kitchen-Big-Speakers`, provider `squeezelite`. In Home Assistant it is
`media_player.squeezeplay_e4_5f_01_67_1e_56`.

`nfc-jukebox-scanner`'s unit file carries an API token on its `ExecStart` line —
don't copy that line into docs, logs, or commits. Read it off the box when needed.

## Audio stack

Two sound cards:

- **card 0 `Array`** — reSpeaker XVF3800 4-Mic Array (USB), microphone only.
- **card 1 `sofessx8336`** — onboard ES8336, the jack feeding the big speakers.

Both playback consumers share a dmix device defined in `/home/pi/.asoundrc` (there is
no `/etc/asound.conf`):

```
pcm.kitchenmix -> plug -> dmix:CARD=sofessx8336,DEV=0 @ 48000/2ch
```

`kitchenmix` is shared by squeezelite (music) and the satellite (chime/TTS/alarm) so
neither takes an exclusive open and locks the other out. The mic side uses
`respeaker_ch0`, which routes **channel 0 only** — ch1 is the AEC reference and is
near-silent, so a plain stereo downmix would halve speech level.

### ALSA gain is already at unity — do not go hunting here

Verified 2026-08-06:

```
DAC              192/192   [0.00 dB]
Headphone          3/3     [0.00 dB]
Headphone Mixer   11/11    [0.00 dB]
Speaker            off
```

Nothing attenuates in the mixer, and there is no headroom left to recover. If
something sounds quiet, the cause is upstream (see below), not ALSA.

## Announcement volume

This is the part that has burned two calibration attempts, so read it before
touching volume numbers.

Music Assistant clamps announcement volume in
`music_assistant/controllers/players.py::get_announcement_volume`. An explicitly
passed `volume_level` **bypasses** `announce_volume_strategy` but is **still clamped**:

```python
volume_level = max(announce_volume_min, volume_level)   # 15
volume_level = min(announce_volume_max, volume_level)   # was 75
```

`announce_volume_max` was **75** while the player's music volume sat at **85**, so
announcements were hard-capped *below* music and every test level above 75 played
identically. Raised to **100** on this player 2026-08-06. The other five amp players
still ship 15/75.

### The clamp is load-bearing for callers that pass no volume

Raising that ceiling is **not** the no-op it appears to be. The clamp is only
irrelevant to callers that pass an explicit `volume_level`. A caller that passes
**none** falls through to `announce_volume_strategy`, and the old `percentual` setting
computed:

```
player_volume + (player_volume / 100 * announce_volume)  =  85 + 0.85*85  =  157
```

157 was survivable only because the 75 clamp swallowed it. Moving the clamp to 100
pushed every unvolumed caller from 75 straight to 100 — audible within a day.

Two unvolumed kitchen callers exist, both Node-RED functions issuing HA `tts.speak`
to `media_player_entity_id` with no volume:

- Doorbell **Kitchen fast TTS** — `6c4fd7877eb38795`, Doorbell tab
- Baby monitor **Kitchen fast TTS** — `bmkitchentts01`, Baby Monitor tab

`tts.speak` aimed at an MA-backed player still goes through MA's announcement path
(the MA log says `Playback announcement to player Kitchen-Big-Speakers`), so the
strategy and clamp apply to them exactly as to a direct `play_announcement`.

Settled 2026-08-07: `announce_volume_strategy` **absolute**, `announce_volume` **75**,
ceiling left at **100**. Explicit callers are untouched (`volume_override` wins over the
strategy branch); unvolumed callers get a flat, predictable 75.

**Before changing `announce_volume_max`/`_min` on any player, list the callers that pass
no volume — the clamp may be the only thing bounding them.**

### Voice levels are not equal, and not what you'd guess

Measured 2026-08-07 through the `tts-router` stack behind `tts.openai`:

| Voice | Short utterance | Longer utterance |
| --- | --- | --- |
| `picard:calm` | -18.5 LUFS | -21.2 LUFS |
| `fast:doorbell` (fast Kokoro path) | -24.9 LUFS | -25.7 LUFS |

The fast voice is **4-6 dB quieter** than picard, not louder. If a fast-path
announcement seems loud, look at volume routing, not the voice — and note that
loudness-normalizing would make that path *louder*.

Announcements are also quieter than music at the same volume number for an honest
reason: TTS renders around -21 LUFS (mono, 24 kHz) while pop masters run around
**-13.7 LUFS**, and speech is peaky where music is dense. `volume_normalization`
is `false` on this player. If you want the 0-100 numbers to mean the same thing for
speech and music, normalize the TTS — the `tts-pad-service` container (beelink `:8097`)
already post-processes clips for the Amp Speakers subflow and would be the place to add
an ffmpeg `loudnorm` pass. Kitchen announcements currently do not pass through it.
Doing so would invalidate every level below, so re-sweep afterwards.

### Current levels

| Path | Volume | Set by |
| --- | --- | --- |
| Kitchen Message subflow (dishwasher, garage, water leak) | Day 95 / Early Morning 80 / Evening 80 / Night 70 / Away 80 | explicit `volume_level` from the mode map |
| Doorbell fast TTS | 75 | MA `absolute` strategy (no volume passed) |
| Baby monitor cry alert | 75 | MA `absolute` strategy (no volume passed) |

Kitchen announcement levels live in the Node-RED **Kitchen Message** subflow
(`587e7ece8eaefef2`), keyed off global `mode`. `msg.volume` overrides the map. Editing
that subflow requires a **full** Node-RED deploy — a scoped `PUT /flow` leaves existing
subflow instances holding stale copies.

MA does **not** surface a transient announcement volume in the player's reported
`volume_level`; polling `players/get` during an announcement just returns the resting
volume. Don't use that to verify an announcement level — check the config and use your
ears.

## Troubleshooting

- **No music** — `systemctl status squeezelite`; confirm the player appears in MA at
  `http://192.168.10.217:8095`.
- **Announcement inaudible** — check `announce_volume_max` on the player before
  touching anything else; that ceiling is silent when it bites.
- **Pops at the start of playback** — `audio-keepalive` is the fix; check it's running.
- **Capture wedged / satellite deaf** — capture can gate on an open playback stream;
  same class of problem the pw-pi ReSpeaker hit. Check `audio-keepalive` and the
  satellite unit together.
- **Both music and voice silent** — suspect the shared `kitchenmix` dmix device or an
  exclusive open by a stray process, not the individual services.
