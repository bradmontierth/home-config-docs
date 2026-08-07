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

Announcements are also quieter than music at the same volume number for an honest
reason: OpenAI TTS renders around **-21 LUFS** (mono, 24 kHz) while pop masters run
around **-13.7 LUFS**, and speech is peaky where music is dense. `volume_normalization`
is `false` on this player. If you want the 0-100 numbers to mean the same thing for
speech and music, normalize the TTS — the `tts-pad-service` container (beelink `:8097`)
already post-processes clips for the Amp Speakers subflow and would be the place to add
an ffmpeg `loudnorm` pass. Kitchen announcements currently do not pass through it.

Kitchen announcement levels live in the Node-RED **Kitchen Message** subflow
(`587e7ece8eaefef2`), keyed off global `mode`:

```
Day 95 / Early Morning 80 / Evening 80 / Night 70 / Away 80
```

`msg.volume` overrides the map. Editing that subflow requires a **full** Node-RED
deploy — a scoped `PUT /flow` leaves existing subflow instances holding stale copies.

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
