# Music library loudness

Read-only EBU R128 survey of the Navidrome library (49 GB on the Plex Pi,
`/media/externalHDD/media/music`). `measure.py` is Media Center's
`server/app/loudness.py` measurement adapted for music: one ffmpeg `ebur128`
pass per file at nice 19, one thread, SAMPLE peak (true peak is ~6x slower on
the Pi and reads at most ~1 dB higher; a limiter covers it).

```bash
scp measure.py plex:/tmp/music_measure.py
ssh plex 'python3 /tmp/music_measure.py /media/externalHDD/media/music' < sample50.json > sample50-results.jsonl
```

## 50-song sample (2026-10-21), 50 different albums

2.5 s per song on the Pi -> the whole library (2,351 songs) is ~1 h 40 min.

- Median -15.9 LUFS. Quietest -42.8, loudest -7.7: a **35 dB** spread. Middle
  half -20.3 to -11.4 (9 dB).
- By group (avg LUFS): Bluey -10.8, Charlie Hope -11.1, Raffi -12.6,
  Halloween/Spooky remixes -12.6, Super Simple Songs -16.1, everything else
  -18.4 (range -35.4 to -7.7), classical (Chopin/Liszt and friends) -26.1
  (range -42.8 to -17.4).
- The kids' music and the Halloween remixes are mastered hot; several remixes
  have sample peaks ABOVE full scale (+1.8 to +4.7 dBFS = already clipped
  lossy masters). 9 of 50 songs sit at full scale.
- Gains to a target: -18 LUFS (ReplayGain 2.0 reference) = -10.3 to +24.8 dB,
  20 of 50 need a boost, 9 would clip without a limiter. -16: 25 need a boost,
  23 would clip. -14: 31 / 29.
- Median loudness range 6.9 LU.

State of normalization in the stack (2026-10-21): Navidrome's
`rg_track_gain` / `rg_album_gain` are empty for all 2,351 songs (no tags).
Music Assistant normalizes every player by default (EBU R128, -17 LUFS,
limiter) but its opensubsonic provider ignores ReplayGain and it holds 0
measurements for Navidrome songs, so every play falls back to live "dynamic"
loudnorm. Tempo (phone) and the TV Music app apply nothing.
