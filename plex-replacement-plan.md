# Plex Replacement Plan ("our own Plex")

Operational reference (deploy, build, monitoring, backup): `media-center-guide.md`.

Status: SCOPED 2026-10-10, nothing built. Owner: Brad.

## Why

The new Plex UI is slower (metadata loads over the network) and needs more clicks.
The old Plex Android TV UI was best-in-class and is the bar. Secondary goal: no
dependency on Plex the company (account login, cloud guide data, UI decisions).
Jellyfin/Emby were tried and rejected on UI.

## Current state (measured 2026-10-10, read-only)

- Host `plex` = Raspberry Pi 4, 4 GB, 192.168.10.150. Plex 1.42.1 in Docker
  (linuxserver/plex, host network). Same host runs Transmission, aria2 and
  spotiflac (all in the gluetun `vpn` netns), audiobookshelf, promtail.
- Library on the 4 TB USB HDD (80% full): TV 2.2 TB, movies 142 GB, music 49 GB.
  Plex DB: 39 movies, 34 shows, 141 seasons, 2,503 episodes.
- Usage, last 90 days: 189 plays, one account. SHIELD 167, Chromecast with
  Google TV 12, Pixel 7, iPad 3. This is a one-screen problem.
- Codecs: h264 1,630 / hevc 824 / mpeg2 447 (OTA recordings) / mpeg4 243 /
  msmpeg4v3 19. Audio: aac, ac3, eac3, a few dts and truehd. 19 files are 4K.
  The Shield can play almost all of this directly, so no live transcoder is needed.
- Live TV: HDHomeRun FLEX DUO (HDFX-2US), 192.168.40.34 (IoT VLAN), 2 tuners,
  126 ATSC 1.0 MPEG-2 channels. Guide = Plex cloud EPG (Gracenote). Series
  rules: Jeopardy, Milan Olympics, Primetime in Milan. Used a few times a year.

## Decisions so far

1. Build our own server and our own Android TV app. Jellyfin as a backend is
   the fallback, mainly for live TV, if building our own recorder turns into a slog.
2. The server stays on the dedicated media host (today the Plex Pi), NOT on the
   Beelink. Brad wants the media/download world separate from the home server.
   A future option is a small dedicated mini PC with internal SATA as the media
   host, which also ends the USB-bridge wedge class of problems.
3. The TV app UI is the most important deliverable. It is what people see.
4. Live TV/DVR is kept in the plan but comes LAST. Plex keeps doing live TV
   until our version exists.
5. Plex runs in parallel the whole time. Nothing is retired until the
   replacement has carried daily use for weeks.

## Metadata sources

- TMDB: primary for movies and TV down to the episode, plus artwork. Free for
  non-commercial use. Needs a TMDB logo and the line "This product uses the TMDB
  API but is not endorsed or certified by TMDB" on an About/Credits screen.
  Key goes in cecret_lake.
- fanart.tv (free key): show logos / clearart for a Plex-like home screen.
- IMDb non-commercial TSV datasets (free, personal use, daily): optional, IMDb
  rating only. No plots or images. No free IMDb API exists.
- TheTVDB: about $12/yr. Only if TMDB coverage of a specific show is bad.
- Live guide: Schedules Direct (about $25-35/yr, Gracenote data, series IDs +
  original air dates). Not scraping. Only needed for the live TV phase.
- Artwork is downloaded once, resized for TV, and served locally. Browsing never
  waits on the internet.

## Security and permissions (media host)

### Findings on the Plex Pi, 2026-10-10

- Transmission (the least trusted, internet-facing process) has
  `/media/externalHDD/media` mounted READ-WRITE at `/media`. It can write straight
  into the published library and skip the pipeline. Its done-hook only writes
  trigger files under `/downloads`, so the mount looks unneeded. Verify that
  Transmission's `settings.json` never points at `/media` before removing it.
  Checked: download/incomplete dirs are under `/downloads`, and the done-hook's
  only "/media" match is its log name (`media-pipeline-hook.log`).
- Plex also mounts the library read-write. Part of that is real: Plex DVR
  records into `tv shows/.grab`. In the target model, recordings (Plex's for now,
  ours later) get their own writable `recordings/` tree outside the
  pipeline-owned library.
- Every container and the organizer run as uid 1000 (`pi`). That user has sudo
  and the docker group, so all zones share one identity.
- `/media/externalHDD/media/tv shows` is mode 777.
- Good: the music NFS export to the Beelink is read-only, root-squashed and
  limited to the LAN.

### Target model: three trust zones on the media host

- Download zone (`media-dl`): Transmission, aria2, spotiflac. Writes only under
  `downloads/`. No access to the library.
- Pipeline zone (`media-pipeline`): the organizer. Reads `downloads/`, scans and
  validates, and is the ONLY writer to `media/`. Library files are owned by
  `media-pipeline`, dirs 755, files 644.
- Serve zone (`media-serve`): the new media server. Library mounted `:ro`. Its
  only writable paths are its own state dir (SQLite, artwork cache, watch
  progress). Container: non-root user, read-only root fs, `cap_drop: ALL`,
  `no-new-privileges`, memory cap. NOT in the VPN netns.
- Media files came from untrusted sources. Anything that parses them (ffprobe in
  the scanner) runs inside the serve container with no extra privileges. The
  pipeline's ClamAV and ffprobe gate stays the first line of defence.
- Network: the server listens on the LAN only and the Android app talks only to
  it. Its outbound traffic is TMDB, fanart.tv and later Schedules Direct.
  Optional later: an egress allowlist.
- Android app: INTERNET permission only. No storage permission and no accounts.

DONE 2026-10-10: Transmission's library mount removed (compose backup
`docker-compose.yml.bak-libmount-20261010T*`). Only that container was
recreated. VPN egress, web UI and all 38 torrents were verified, and the hook ->
media-pipeline-trigger.path -> organizer chain was tested with a manual hook
fire (0 items, clean). The first real download is the remaining live proof.
Still open: the uid split and the 777 on `tv shows`.

Hardening the existing Pi (removing the Transmission mount, the uid split,
fixing 777) touches the live download pipeline. It needs its own approval and
a pipeline test run afterwards.

## UI principles (the main deliverable)

Bar: the old Plex Android TV UI. Things that were confusing: home pushing
recently-added episodes first, and difficulty finding plain "TV shows A-Z" or
"shows by what I watched last".

- Visual style (Brad, 2026-10-10): like old Plex. Rectangular posters and
  cards with small corner radii (about 2-4 dp). NO pills, no heavily rounded
  bubbles, no "AI app" look. Focus is shown by a crisp border/scale, not a
  blob.
- Fixed LEFT-SIDE menu that never reorders: Home, TV Shows, Movies, Live TV
  (once built), Search. Pressing left from any screen's first column opens it.
  No algorithmic surprises.
- Home is short and predictable:
  1. Continue Watching: in-progress items plus the next episode of shows in
     progress, ordered by when you last watched.
  2. Recently Added, grouped by show ("3 new episodes"). Never a flood of
     single episodes.
  Nothing else until we decide to add it.
- TV Shows library: poster grid, DEFAULT SORT = LAST WATCHED (decided
  2026-10-10), with "A-Z" one press away. The choice is remembered. A-Z gets a letter-jump rail
  (press left from the first column).
- Show page: the default-focused button names the exact action, e.g.
  "Resume S3 E4 · 12 min left" or "Play S3 E5". Then seasons, then episodes.
- Episodes are a horizontal strip with left/right moving one episode. Holding
  repeats with acceleration, so a 50-episode season (Bluey) is crossed in about
  2 s. The strip opens focused on the next unwatched episode. Rows render
  only what is visible plus a buffer, so fast repeat never stutters.
- Back always goes up exactly one level and restores the focused item and
  scroll position.
- Speed targets: cold launch to usable home under 1.5 s; any screen after that
  under 200 ms. The whole catalog (about 2,600 items) is cached in the app and
  delta-synced. Artwork is pre-sized.
- Remote in the player: one press RIGHT = +30 s, one press LEFT = -10 s,
  center = pause/play. HOLD left/right = fast scrub that speeds up the longer it
  is held, with a thumbnail preview of the target frame; release to jump.
  Preview thumbnails are generated server-side per file (sprite sheet), in the
  background and at low priority. HEVC 10-bit software decode on the Pi 4 is
  slow, so the backfill over about 2,600 files may take days. Scrub still
  works without thumbnails (time only).
- Playback: minimal on-screen controls, next-episode countdown with cancel,
  audio and subtitle choices remembered per show. Later: skip intro, detected
  by matching audio across episodes of a season.
- Process: build screen by screen and review real screenshots from the Shield
  (`adb exec-out screencap -p`) before calling a screen done. Brad (and the
  family) review on the TV.

## Heavy media jobs offloaded to the Optiplex (designed 2026-10-10)

Brad asked for CPU-heavy work (scrub thumbnails, MPEG-2 recording -> HEVC,
normalising old AVI/DivX) to run on the Optiplex at night, with a clear device
boundary and a sandbox.

Optiplex facts (2026-10-10, read-only): i7-6700 (Skylake, 4C/8T), 7 GB RAM.
Quick Sync does HEVC 8-bit decode + encode but NO HEVC 10-bit (vainfo has no
Main10), so 10-bit decode is CPU. The camera stack (lookout-recorder/go2rtc/web)
uses about 0.8 of 8 threads. Root SSD is near end of life: the worker must write
NOTHING to it.

Boundary: the Optiplex worker PULLS. It holds no media and mounts nothing.
- In: one job at a time from the media server (`GET /jobs/next`). A job is a
  file id + a job TYPE from a fixed list (thumbnails, recording_hevc,
  normalize). It is never an ffmpeg command line: the worker owns the recipes.
  It streams that one file read-only over HTTP from the media server.
- Out: artifacts POSTed to a narrow results inbox on the media server.
  Thumbnails = one JPEG sprite sheet + JSON index, re-decoded and size-checked
  by the server. Video outputs land in a pipeline STAGING dir and must pass the
  same ffprobe/ClamAV checks as any download. Only the pipeline user releases
  into the library. The worker can never write the library.
- Auth: one worker token (cecret_lake on the Beelink, a 0400 file on the
  Optiplex). It can only claim jobs, read the claimed file and post results.
- No NFS export to the Optiplex: one access path, logged per job.

Sandbox on the Optiplex (ffmpeg parsing untrusted files is the real exposure):
- Docker container, non-root uid, read-only root fs, `cap_drop: ALL`,
  `no-new-privileges`, default seccomp, pids limit.
- Scratch = tmpfs with a size cap (RAM, never the SSD).
- `--cpus 4`, memory cap about 2 GB, nice/ionice low.
- Own docker network, nothing from Lookout mounted, and an egress rule allowing
  ONLY the media server ip:port (no internet, no other LAN hosts).
- No `/dev/dri` at first (GPU stays with the cameras, smaller attack surface).
  Add it later for recording -> HEVC Quick Sync encodes if CPU is too slow.
- systemd timer runs it 01:00-06:00 and stops it at 06:00; a half-done job is
  re-queued. The media server works fine without the worker (no thumbnails =
  time-only scrub), so Optiplex downtime (e.g. the SSD swap) only delays work.

Rough cost, NOT measured: thumbnail backfill reads every file once, about
2.4 TB, so gigabit transfer (~6-7 h) is the bottleneck, not CPU, if frames are
taken from keyframes only (`-skip_frame nokey`). Expect 2-3 nights, then
minutes per week for new arrivals. Measure on one 10-bit episode on both the
Pi and the Optiplex during the spike. If the Pi alone is fast enough for
thumbnails, the Optiplex worker is still worth building for real encodes
(MPEG-2 -> HEVC, DivX normalisation), which the Pi cannot do well.
Watch: lookout-recorder health/dropped segments on the first night.

## Shield facts (adb, 2026-10-10)

- adb: `.../home-agent-android/.android-sdk/platform-tools/adb connect
  192.168.40.67:5555`. Network debugging is ON and the Beelink key was
  approved on the TV ("always allow").
- SHIELD Android TV, product `sif` (2019 tube, not Pro). Android 11 / API 30.
  Userspace ABI armeabi-v7a (32-bit): any native libs (e.g. the Media3 ffmpeg
  decoder) must ship armeabi-v7a.
- About 2 GB RAM, /data 980 MB free (81% used). Keep the APK and on-device
  catalog/artwork cache small (catalog JSON + a bounded image cache, not every
  poster).
- UI surface 1920x1080 (panel 3840x2160). Design at 1080p.
- Display modes include 2160p and 1080p at 23.976/24/25/29.97/30/50/59.94/60, so
  per-video refresh matching is possible. The app requests the mode itself
  (preferredDisplayModeId); there is no global AFR setting to lean on. Desktop
  mode is 2160p59.94.
- HDR: HDR10 only (type 2). No Dolby Vision or HLG reported by this TV/chain.
- Passthrough formats enabled (manual surround mode): AC3, E-AC3, E-AC3-JOC
  (Atmos in DD+). DTS and TrueHD are NOT enabled: those ~10 files get decoded
  to PCM in the app (ffmpeg audio extension).
- Installed: Plex 2026.19.1, Jellyfin, VLC. F-Droid is not installed.
- Screenshot: `adb exec-out screencap -p > shot.png` works. Use it for every UI
  review.

## Playback fundamentals to prove first (spike)

Kotlin + Compose for TV + Media3 ExoPlayer. On the real Shield + receiver:
refresh-rate matching, AC3/EAC3/DTS/TrueHD passthrough, HDR10, PGS and ASS
subtitles, MPEG-2 1080i deinterlacing (recordings and live), resume position,
and a live HDHomeRun stream. The Shield is 192.168.40.67 (fixed, UDM name "Shield",
MAC 48:b0:2d:52:16:7f), the same IoT VLAN 40 as the HDHomeRun, so live
streams can go tuner -> Shield directly. It is reachable from the Beelink.

## Spike results (2026-10-10)

Test app: /home/pi/media-center/spike-tv (com.local.mediaspike, Media3 1.6.1 +
org.jellyfin.media3:media3-ffmpeg-decoder:1.6.1+2). Files were served by a
temporary nginx (`spike-files` container on the Plex Pi, :8091, read-only,
cap_drop ALL). Driven over adb; logcat tag `Spike`.

| Test | Result |
|---|---|
| Curb S10E01, HEVC 10-bit 1080p, E-AC3 | HW decode (OMX.Nvidia.h265), E-AC3 PASSTHROUGH, switched 59.94->23.976 Hz, first frame 2.6 s, seeks 186/238 ms |
| Jeopardy OTA, MPEG-2 1080i, AC3 | HW decode (OMX.Nvidia.mpeg2v), AC3 passthrough, stays 59.94, first frame 0.6 s, seeks 107/681 ms, no visible combing in stills |
| Planet Earth II, 4K HEVC HDR10, DTS 25 fps | HW decode, DTS decoded by ffmpeg -> PCM 5.1, switched to 2160p25, first frame 3.3 s, seeks ~450 ms |
| Return of the King, 4K HEVC HDR10, TrueHD 7.1 | HW decode, TrueHD decoded by ffmpeg -> PCM 7.1, 23.976 Hz, first frame 2.1 s, seeks 1.2 s / 0.4 s. Video layer carries HDR10 static metadata; TV HDR badge not yet eyeballed |
| Oppenheimer, H.264, AC3, PGS | AC3 passthrough, PGS bitmap subtitles RENDER correctly |
| Truman Show, ASS subs | Track selects; no line was due in the captured frame (inconclusive) |
| Groundhog Day, H.264 baseline | 23.976 switch, first frame 0.8 s, seeks 89/171 ms |
| Live KSL 5.1 / KUED 7.1 (HDHomeRun direct) | HW MPEG-2, AC3 passthrough, first frame ~1.3 s |
| Refresh restore on exit | Returns to 2160p59.94 every time |

Findings that change the design:
- Containers never report frame rate to the player (Format.frameRate = -1 for
  every MKV/TS). The SERVER must supply fps (ffprobe at scan time). Confirmed
  design, not optional.
- Closed captions on OTA recordings: the CEA-608 track appears after
  `DefaultExtractorsFactory.setTsSubtitleFormats(CEA-608)`, but nothing renders.
  The captions ARE in the file (ffmpeg `movie=...[out0+subcc]` extracts clean
  SRT). Plan: the server extracts captions to a sidecar subtitle at ingest for
  recordings. Live-TV captions need a real fix in the live-TV phase.
- Mode switches back to back (25 Hz -> 59.94 -> 23.976) can take a few seconds
  of HDMI resync. The app must tolerate it.
- Still needs a human: hold-to-scrub feel (adb cannot inject a held key),
  1080i motion deinterlacing, TV HDR badge, ASS styling, lip sync on PCM vs
  passthrough.

### Follow-up after Brad's hands-on test (2026-10-10)

Brad: Jeopardy and Oppenheimer smooth. Return of the King "a little choppy".
After seeks/scrubs, audio resumes 3-5 s before video.

Measured with frame-release instrumentation (VideoFrameMetadataListener):
- Steady 4K ROTK playback is PERFECT: 120-121 frames per 5 s at 23.976, zero
  gaps, 0 dropped, app CPU 30-50% (TrueHD decode included). The Shield is not
  struggling. Likely causes of the choppiness Brad perceived: (a) true 24p
  cadence on fast pans (the TV is in 23.976 Hz and may disable its motion
  smoothing there), and (b) a startup hold of 0.5-0.8 s on the first frames
  (ROTK ~0.75-0.8 s even with audio OFF, Oppenheimer ~0.5 s, Planet ~0.16 s).
  The player has an A/B toggle (menu button: refresh matched vs TV default) so
  Brad can judge (a) on the same scene.
- Seek gap ROOT CAUSE: this ROTK encode has 10-second GOPs (keyframes at
  4724.72 / 4734.73 / 4744.74 s), AND Media3's MKV seek index (cues) does not
  line up with them. Long jumps landed 5-7.5 s AFTER the target with EXACT,
  CLOSEST_SYNC and PREVIOUS_SYNC alike; audio played from the target while video
  waited for the next keyframe. 30-s taps were usually fine (~1 s).
- FIX in the spike ("smart" seek, now the default): pause, wait for the first
  decoded video frame, and if the position differs by >0.5 s re-seek EXACT to
  that frame's pts, then resume. Picture and sound now start together 0.2-1.8 s
  after key-up. Cost: on bad files the landing can be up to ~7.5 s past target.
- Real fix for the product: the server records each file's keyframe times (the
  thumbnail job reads keyframes anyway with `-skip_frame nokey`), and the app
  seeks EXACT to the keyframe at/before the target. Picture is instant, lands
  on or just before the target, and doesn't depend on container cues.
- Playback start now waits for the TV's refresh switch (+1 s settle, 4 s cap)
  before un-pausing, so the first seconds aren't lost to HDMI resync.

## Phase 2: library server v1 (built 2026-10-10)

Code: /home/pi/media-center/server (repo /home/pi/media-center, git-initialised, no
remote yet). Deployed on the Plex Pi at /home/pi/media-center/server via
docker compose, container `media-center`, http://192.168.10.150:8790 (LAN
address only).

Security: non-root uid 1500, read-only root fs, cap_drop ALL,
no-new-privileges, pids 128, cpu_shares 512 (below Plex). Library mounted
`:ro`. Plex Metadata/ and Media/ mounted `:ro`, plus a CONSISTENT SNAPSHOT copy of
the Plex DB (sqlite backup API, `plex-snapshot/library.db`); Plex's
Preferences.xml / account token is never mounted. Only `./data` (uid 1500) is
writable. Every request except /healthz needs header `X-MC-Token` (or `?t=`).
The token lives at cecret_lake/media_center/client_token on the Beelink and in
`server.env` (0600) on the Pi.

What it does:
- Scanner: walks movies/ and tv shows/ (skips dot-dirs such as .grab), derives
  STABLE ids from paths (sha1), ffprobes new/changed files (duration, codecs,
  fps, w/h, HDR10/HLG/DV, audio + subtitle tracks incl. sidecar .srt). Rescans
  every 30 min or on POST /api/v1/scan. Missing files are flagged, not deleted,
  so watch history survives a drive hiccup.
- One-shot Plex import (MC_PLEX_IMPORT=1, runs once after the first scan):
  titles, summaries, ratings, genres, air dates, posters/backgrounds/episode
  thumbs (resolved from Plex bundles: metadata:// in the item/season/show
  bundle, upload://, media:// in Media/localhost, https fetched once), and
  account-1 watch history (watched, resume offsets, last-viewed).
- API: GET /api/v1/catalog (whole library in one gzip JSON, ETag = catalog
  version), GET /api/v1/home (Continue Watching by last watched + Recently
  Added grouped by show), GET/POST /api/v1/progress (resume positions; >=92% =
  watched), POST /api/v1/watched/{id}, GET /stream/{file_id} (Range/206
  verified), GET /art/{id}/{poster|art|thumb}?w= (resized and cached),
  GET /api/v1/files/{id}, GET /api/v1/status.
- First run (2026-10-10): 2,538 files probed with 0 errors (34 shows, 141
  seasons, 2,409 episodes, 39 movies). Plex import matched 2,553 parts, 2,623
  items, 2,690 images (0 failures, 159 MB), 490 watch-history rows. All movies
  and shows have posters; all episodes have thumbs; 38 lack summaries. Catalog =
  360 KB gzip, 0.2 s cached. Home matches Plex (Veep S2E8, Bluey, Office S6E23,
  TNG Unification, Two Towers @94 min). Parser handles SxxEyy, multi-episode,
  "Season 03 Episode 01" (DS9) and Plex DVR date names (season = year, idx =
  MMDDHHMM, shown as the air date). Extras dirs are skipped in v1. Streaming
  measured 51 MB/s (~400 Mbps) through the server vs 115 MB/s raw nginx: far
  above any file's bitrate. An nginx X-Accel sidecar is an option if it ever
  matters.
- TMDB LIVE 2026-10-10 (key in cecret_lake/media_center/tmdb_api_key, 0600;
  MC_TMDB_KEY in the Pi server.env). It runs after every scan and only fills
  EMPTY fields (Plex "" summaries count as empty). Matching prefers an exact
  normalized title, then year, then popularity (Plex labels both Deathly Hallows
  films 2024, which first matched Part 1 to Part 2; fixed). Episode fills
  require TMDB's episode title to agree with ours, because numbering drifts
  (The Office double episodes). Audit: 821 TMDB-identical summaries, 0
  mismatches. 38 episodes remain without summaries where numbering or titles
  disagree. Misses are retried weekly.
- Not yet: keyframe index (Optiplex job),
  caption sidecars for OTA recordings, Plex re-sync of watch state during the
  parallel-run period.

## Phase 3: TV app v0.1 (built 2026-10-10)

Code: /home/pi/media-center/tv (com.local.mediacenter, "Media Center"),
installed on the Shield over adb (debug build). Token provisioned once with
`am start -n com.local.mediacenter/.MainActivity --es mc_token <token>` and
kept in app prefs.

Built and screenshot-verified on the Shield:
- Fixed left menu (Home, TV Shows, Movies, Settings). Collapsed to icons, it
  expands with labels on focus, entry always lands on the current section, and
  up/down stay inside the menu.
- Home: fixed hero (title, episode, summary, time left) over a dimmed
  backdrop of the focused item; Continue Watching (16:9 cards + progress) and
  Recently Added (posters, grouped "N new episodes").
- TV Shows grid sorted by last watched (default) or A-Z with unwatched badges.
  Movies grid sorted by recently added or A-Z, with watched checks and
  progress bars.
- Show page: meta line, focused-episode info panel, a Play/Resume button that
  names the episode, season tabs, and the episode strip opening on next-up.
  Down from Play goes to next-up and up from the strip goes to the selected tab.
  40 rapid presses moved across Bluey S3 from E50 to E10 cleanly.
- Movie page: poster, meta (4K/HDR flags), summary, Resume + Play from
  beginning.
- Player: picture-first seek, 30/10 skip + hold-scrub, refresh matching
  (Settings toggle), a bottom bar with title/progress/"ends at", progress
  reports every 10 s, a 10 s up-next countdown, and OK long-press for stats.
- Back returns to the exact item you left (focus memory per screen).

Recordings section (2026-10-10, Brad-approved): shows whose files are ALL
MPEG-TS tuner captures are flagged `recorded` by the server (today: Jeopardy
333, Milan Olympics 17, Primetime in Milan 15, FIFA 2, IndyCar 1). They live
under a "Recordings" menu item and are hidden from TV Shows (34 -> 29).
Rows are ordered by latest airing, newest recording first, labelled by air
date, and use show art because Plex frame-grabs of broadcasts are often ads.
This becomes "Live TV" (guide / recordings / schedule tabs) in the live-TV
phase. Possible issue for Brad: the IndyCar 2026-07-19 file's thumbnail shows
soccer, so it may contain the FIFA final from the same FOX slot.

Speed (2026-10-10): the release build (R8 + resource shrinking, 8 MB vs 25 MB
debug, homelab debug key so it upgrades in place) is what's installed now.
Shield cold start: first frame ~0.5 s and library on screen (reportFullyDrawn)
~0.9 s, range 0.56-1.19 s, vs ~4.5 s for the debug build. Cached catalog
parse ~0.3 s is the largest remaining piece. The server prewarms all requested
art widths after each scan (3,047 sizes, 218 MB art dir, fetch ~15 ms).
Build: `./gradlew :app:assembleRelease` with the guide's memory cap. The
release build is not debuggable, so `run-as` pref edits no longer work.

Known gaps / next:
- Not built: A-Z letter rail, search, mark watched/unwatched, an on-screen
  audio/subtitle picker (up/down cycle for now), skip intro, self-update via
  the F-Droid repo index.
- Testing lesson: blind adb key sequences start playback by accident (twice
  on 10-10; Veep S2E8 progress reset both times). Screenshot before every OK.

## "Surprise me" (Brad's idea, BUILT 2026-10-10, tv 0.4)

Pick a great episode of a show you know but haven't seen in a while.

- Scope (Brad, 2026-10-10): ONLY for "unwind" rewatch shows we've seen in
  full before (The Office, Veep, Always Sunny, the Star Trek series...), never
  for shows we're working through, so spoilers aren't a concern. Watch history
  can't tell which shows these are (Plex history is incomplete: Office shows
  118 "unwatched", TNG 163), so it's an explicit per-show flag: a "Rewatch
  favourite" toggle on the show page, seeded from Brad's list.
- Eligible: every episode of a flagged show that's in the top 25% of that show
  by IMDb rating and NOT watched in the last 12 months (setting: 6 or 12; no
  record = eligible). The unwatched flag is ignored for these shows.
- Ratings: IMDb non-commercial datasets (personal use; credit "Information
  courtesy of IMDb"). title.episode.tsv.gz maps the show's IMDb id plus
  season/episode to an episode id, and title.ratings.tsv.gz gives
  averageRating + numVotes. The show's IMDb id comes from TMDB
  /tv/{id}/external_ids. A weekly server job downloads, filters to our shows
  and stores per-episode ratings. Percentile is computed per show, and
  episodes with fewer than ~50 votes are ignored.
- Numbering drift (The Office double episodes etc.): verify with the episode
  title from title.basics, or fall back to TMDB episode vote_average when
  titles disagree.
- UI: a "Surprise me" button on each show page, plus one on the TV Shows
  screen that picks across all rewatch favourites. It shows a reveal card ("S4 E13 · Dinner Party · IMDb 9.4 · last
  seen Mar 2025") with Play and Another; it doesn't jump straight into
  playback.
- Watch history: only the LAST viewed date per episode was imported from
  Plex. That's enough for the "not in 12 months" rule.
- Size: about half a day (server ratings job + endpoint, app button + card).

## Continue Watching rule (2026-10-10, Brad-approved)

Brad watches some shows in order (Veep, The Office, Sunny) and others as random
nightly rewatches (Bluey, TNG). No per-show setting; the rule infers it:
- A half-watched episode always appears (resume).
- A "next episode" card appears only if the last two FINISHED episodes of the
  show were back-to-back. Next = the following episode in library order,
  watched or not (rewatch-friendly).
Checked against real history: Veep/Office/SNW keep next cards; Bluey, TNG,
Magic School Bus drop off; Sunny/Silo/Dark Matter have no next file yet.
Found: Strange New Worlds S4E4 is missing from the library (E3 -> E5).

## Pause menu, search, item menu (built 2026-10-10, tv 0.3)

- OK while playing pauses and opens a menu: Resume (focused, so OK-OK =
  pause/resume), Subtitles, Audio, Start over. Subtitles/Audio open a right
  panel ("English · SDH · SRT", "English · Dolby Digital+ · 5.1"). Choices are
  remembered per show (language, or off). Back closes the panel, then the menu.
  Up/Down while playing only show the timeline; long-press OK = stats.
- External subtitle files (88 files, ".srt" next to the video) are served at
  /subs/{file}/{n} and load as extra tracks; verified on Sunny S9E1.
- Long-press OK on episode/movie cards: Play from beginning / Mark
  (un)watched.
- Search (top of menu): shows, movies, episode titles, accent-insensitive.
  The Shield keyboard opens on focus; its Search key jumps to the results.
- play_item deep link: `am start -n com.local.mediacenter/.MainActivity --es
  play_item <id>` opens the player once the library is loaded (test hook,
  and a future voice-assistant hook).
- A-Z letter rail DEFERRED: 29 shows / 39 movies don't need it yet.

Surprise me tiers (2026-10-10, after the Bluey night): per show, (1) top 25%
unseen 12 months, (2) top 50% unseen 12 months, (3) top 50% picking from the
quarter seen longest ago. Bluey (all 154 seen within a year, 91 in 90 days)
lands in tier 3. All-favourites mode prefers shows with tier 1/2 picks. The
card states the reason. Also fixed: Bluey S03E49 = "The Sign" (29-min special,
TMDB special #27), S03E50 = "Surprise!". Plex had mislabelled E49 and never
matched E50. A runtime-vs-TMDB audit (1,251 episodes) found no other swaps,
only 6 double-length/extended files.

Surprise me build notes: the IMDb sync took 171 s on the Pi; 8 shows, 1,016
IMDb episodes, 957/1,014 of ours matched (930 by title, 27 by number, misses
= season-0 extras). The Office top 5 checks out (Finale 9.8, Stress Relief
9.7, Dinner Party 9.4...). Picks choose a show first, then an episode (12 test
picks spread over 7 shows).

## Scrub previews + keyframe index (built 2026-10-10, tv 0.5; Brad-approved)

Decided to run on the Pi, not the Optiplex: measured 80 s for a 37.5-min 10-bit
HEVC episode (561 keyframes) at nice 19, so the ~2,500-file backlog is ~2 days
of background CPU. The Optiplex would be slower end-to-end (2.4 TB network
pull, night-only window). The Optiplex worker stays reserved for real
encodes (MPEG-2 recordings, DivX).
- Worker thread in media-center: one file at a time, nice 19, -threads 2,
  -skip_frame nokey, 240x136 tiles (MUST be even: 135 broke pad), 10x10
  sheets, keyframe times from showinfo. Priority: in progress, then recently
  watched, then recently added. Failures are recorded and not retried
  (delete file_thumbs rows with error to retry). /tmp tmpfs raised to 256 MB.
- First 4K file (Two Towers) took 507 s for 1,412 keyframes (10 s GOP),
  3.8 MB of sheets.
- App: loads the index + sheets per file; holding left/right shows the target
  frame above the timeline; every seek goes EXACT to the nearest keyframe
  (the real fix for the ROTK overshoot). Falls back to picture-first seek
  until a file is processed.
- Known: HDR (4K) tiles look washed out (no tone-mapping). Add a tonemap
  filter for hdr files later.
- Also fixed: a shared SQLite connection across threads gave "bad parameter
  or other API misuse"; now one connection per thread (40-way concurrent
  test clean).

## Distribution

The same APK runs on the Shield (Android 11) and the Chromecast with Google TV.
Builds are published to the existing F-Droid repo. The app has its own update
check: it reads the repo index and offers "Update available - install?"
(REQUEST_INSTALL_PACKAGES), because the F-Droid client is clumsy with a remote.
First install is over adb with Shield network debugging. Follow
android-apk-publishing-guide.md for memory-capped Gradle.

## Phases

0. Media host permission hardening (separate approval; see above).
1. Spike: playback fundamentals on the Shield. Needs Shield network debugging
   switched on by Brad.
2. Server v1 on the media host: scanner + TMDB + local artwork + progress API.
   Import watched/progress history from the Plex DB.
3. TV app UI v1: home, libraries, show/movie pages, player, iterated by
   screenshots.
4. Daily use alongside Plex for a few weeks. Fix what real use finds.
5. Polish: skip intro, phone/iPad access (probably a small web UI), Chromecast
   check.
6. Live TV + DVR: Schedules Direct guide, recording via HDHomeRun HTTP, series
   rules, 2-tuner conflict handling, sports padding, Pushover alert when a
   scheduled recording fails to start. Plex DVR stays enabled until ours
   proves itself on real events.
7. Retire Plex.

## Open questions

- Long-term media host: stay on the Pi 4, or a dedicated mini PC with internal SATA?

Answered 2026-10-10: default sort = last watched. One shared profile, no
profiles in the MVP (the kids watch Bluey, no overlap with parents' shows).
Skip intro = nice to have, NOT MVP (phase 5). Old-Plex likes = left menu,
square-ish cards, fast episode strip, 30/10 skip + hold-to-scrub.
