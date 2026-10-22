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

## Whole library (2026-10-21 06:48-08:45, 2,350 songs, 0 errors, 117 min)

- Median -18.1 LUFS (right at the ReplayGain 2.0 reference). 5th-95th
  percentile -29.7 to -8.9; middle half -22.6 to -13.6 (9 dB).
- Loudest artists (avg LUFS, 8+ songs): Bluey -10.3 (75 songs, -15.5..-4.9),
  Charlie Hope -11.4, Elton John -11.4, Roxy Music -11.4, Raffi -14.0
  (71 songs, -24.5..-9.4), John Denver -14.4.
- Quietest: Chopin -24.4, Franz Liszt compilations -25.0, Brahms -23.4,
  Garrick Ohlsson (Chopin complete works, 256 songs) -21.5, Lang Lang -20.5,
  Various Artists (191 songs) -20.8.
- Widest swing inside one album: "Study Focus 2022" (Various Artists, 86
  songs) 28 dB; Brahms "A Concert of Pianists" 27 dB; Ohlsson Chopin disc 2
  24 dB. Compilations need TRACK gain; album gain would keep their swings.
- At a -18 target: 50 % of songs need a boost, 99 need more than +12 dB
  (mostly soft classical; cap boosts), 402 get cut by more than 6 dB, 628
  would go over 0 dBFS without a limiter. 207 songs already have sample
  peaks above full scale (clipped masters).
- Two files decode as pure digital silence (-70 LUFS, no peak), almost
  certainly failed downloads: `Rewired88/Save Your Tears/01 - Save Your
  Tears.flac` (193 s) and `Prefab Sprout/I Trawl the Megahertz/01 - I Trawl
  the Megahertz.flac` (22 min). Exclude from tagging; re-download.

Raw results: `library-results.jsonl` (one JSON per song: Navidrome id, path,
lufs, lra, peak).

## ReplayGain tags written (2026-10-21 09:15)

- Backup first: `/media/externalHDD/backups/music-pre-replaygain-20261021`
  on the Plex Pi (`cp -a`, 20 min, 2,379 files, sizes match). Restore =
  copy it back over `/media/externalHDD/media/music`.
- `rsgain` is not packaged for Debian 12 (Pi), so `tag_replaygain.py` writes
  the tags with mutagen from `library-results.jsonl` (no re-measuring):
  REPLAYGAIN_TRACK_GAIN/PEAK, ALBUM_GAIN/PEAK, REFERENCE_LOUDNESS, reference
  -18 LUFS. FLAC = Vorbis comments, MP3 = TXXX frames, M4A = iTunes freeform
  atoms. Album loudness = duration-weighted energy mean of its tracks.
  Gains unclamped in the tags (players cap boosts). Silent files skipped.
- 2,348 tagged, 0 failed, 2 skipped (the silent pair) in 91 s at idle I/O.
- Navidrome: a QUICK scan does not see in-place tag edits (folder mtimes
  unchanged); a FULL scan (`startScan?fullScan=true`, ~110 s) does. After
  it, 2,348 songs have rg_track_gain + rg_album_gain and OpenSubsonic
  `replayGain` is served (checked one FLAC, one MP3, one M4A). Track gains
  -13.3 to +25.6 dB, mean +0.4; Bluey avg -7.7, Chopin avg +4.1.
- The only other song without gains is a stale Navidrome row
  (`Charlie Hope/.../09 - Sun and Moon.flac`, missing=1, file gone).

Next: Music Assistant measurements (its opensubsonic provider ignores the
tags), TV app gain, Tempo's ReplayGain setting, tag new downloads.

## Players (2026-10-21)

- **TV Music app** (music-tv): applies `replayGain.trackGain` as ExoPlayer
  volume with 6 dB headroom (volume = 10^((gain-6)/20), max 1.0). Verified on
  the Shield.
- **Music Assistant**: `ma_import.py` wrote 4,696 rows into
  `loudness_measurements` (each song under `opensubsonic--bHkR6yvQ` by
  Navidrome id AND `filesystem_local--4Z5yRhf7` by path: MA streams library
  tracks from filesystem_local, checked). Loudness floored at -29 LUFS =
  boosts capped at +12 dB vs MA's -17 target. Done with MA stopped, as root
  via a throwaway container (the DB is root-owned); backup after a WAL
  checkpoint: `/home/pi/music-assistant/backups/library.db.pre-loudness-20261021`.
  MA now reports `loudness` for Navidrome tracks (Bluey theme -10.2).
  **BUT normalization is switched OFF on every original MA player** (Loft,
  Simon, Claire, Master, Shower, Kitchen-Big-Speakers: `volume_normalization`
  False, default True; only the new Kitchen Party + Shield players have it on),
  so streamdetails say `volume_normalization_mode: disabled` and nothing is
  applied yet. Turning it on is a per-player MA config change that also
  affects announcements/TTS and the jukebox's tuned volumes: Brad's call.
  Suggest pairing it with the core setting "Volume normalization method for
  tracks" = measurement only, so unmeasured items (TTS, Tempo URL plays) are
  never run through live loudnorm.
- **Tempo (phone)**: Settings > ReplayGain > track (Brad's + Adrienne's phone).

## 2026-10-21 evening: party groups were quieter in the kitchen

Kitchen + TV via Tempo: the kitchen was barely audible next to the TV. The
volumes were NOT the cause: kitchen-party snapclient 35 % = -17.2 dB on
snapclient's default curve ((10^v-1)/9) vs the kitchen squeezelite at the
jukebox's 65 = -17.3 dB (aioslimproto/LMS curve). The cause: TV groups are
led by the Shield player, which (new player) had MA normalization ON, and
Tempo plays are anonymous URLs (`builtin` provider, no measurement), so MA ran
live `dynamic` loudnorm to -17 LUFS: loud songs -5 to -7 dB. The kitchen's
own squeezelite player has normalization OFF. Fixed by setting
`volume_normalization` False on the Shield and Kitchen Party players (all
players now OFF); next Kitchen + TV play showed mode `disabled`.
The precomputed measurements only apply to MA library plays (jukebox,
voice); Tempo plays need the bridge to send library URIs instead of URLs.
