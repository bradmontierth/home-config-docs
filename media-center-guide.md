# Media Center Guide (our Plex replacement)

Home-built replacement for the Plex client experience: a library server on the
media host plus an Android TV app on the Shield. Plex was REMOVED from the
media host on 2026-10-22 (container, compose block and image gone; the Plex
config dir `/home/pi/transmission/plex-config` is kept because this server
mounts its `Metadata/` and `Media/` read-only, plus the DB snapshot). Both
antenna tuners are now ours. Design history,
measurements and decisions live in `plex-replacement-plan.md`; this guide is the
operational reference.

Repo: `/home/pi/media-center` on the Beelink, pushed to
`github-illuminate:bradmontierth/media-center.git` (`main`).

```text
media-center/
├── server/   FastAPI + SQLite, runs on the Plex Pi (docker compose)
└── tv/       Kotlin/Compose Android TV app (com.local.mediacenter, "Media Center")
```

## Where things run

| Piece | Where | Address |
|---|---|---|
| Server container `media-center` | Plex Pi (`ssh plex`, 192.168.10.150) | `http://192.168.10.150:8790` (LAN address only) |
| Server code + compose on the Pi | `/home/pi/media-center/server` | deploy target (rsync from the Beelink copy) |
| Server data (DB, art, thumbs) | Pi `/home/pi/media-center/server/data` (uid 1500) | `media.db`, `art/`, `thumbs/` |
| TV app | Shield (192.168.40.67, IoT VLAN 40), Chromecast GTV not yet | adb over network |
| Secrets | Beelink `/home/pi/cecret_lake/media_center/` | `client_token`, `tmdb_api_key` |
| Server env on the Pi | `/home/pi/media-center/server/server.env` (0600) | `MC_TOKEN`, `MC_TMDB_KEY`, `MC_SCAN_TOKEN` (scan-only key the download pipeline uses) |

Never copy token/key values into code, commits, logs or memory; reference the paths.

## Security model (media host)

- Container: uid 1500, read-only root fs, `cap_drop: ALL`, `no-new-privileges`,
  pids 128, `cpu_shares: 512` (below Plex), tmpfs `/tmp` 256 MB.
- Library `/media/externalHDD/media` mounted **read-only**. Plex `Metadata/` and
  `Media/` read-only, plus a snapshot copy of the Plex DB
  (`plex-snapshot/library.db`). Plex's `Preferences.xml` (account token) is never mounted.
- Every request except `/healthz` needs header `X-MC-Token` (or `?t=`).
- Transmission no longer mounts the library (removed 2026-10-10); see
  `safe-media-pipeline-guide.md`. Still open: per-zone uids and the 777
  `tv shows` folder (plan, "Security and permissions").

## What the server does

- **Scanner** every 30 min, and immediately when the download pipeline finishes
  (`POST /api/v1/scan`, see `safe-media-pipeline-guide.md`): stable ids from paths,
  ffprobe for new/changed files (fps, codecs, HDR, audio/subtitle tracks,
  sidecar `.srt`). Understands `SxxEyy`, multi-episode, `Season 03 Episode 01`,
  and Plex DVR date names. Missing files are flagged, never deleted.
- **Loudness** (background, nice 19, yields to iPad copies): EBU R128 integrated
  loudness, loudness range and sample peak per file into `file_loudness`, ~9 s per
  Bluey episode, ~40 s per half hour. `GET /api/v1/loudness` = progress + per-show
  averages. Input for the planned "even volume" player mode (target ~-16 LUFS,
  boost capped, limiter); not used by any player yet.
- **Live TV guide** (step 1 of live TV, `app/livetv.py`): HDHomeRun FLEX DUO at
  `192.168.40.34` (2 tuners, 126 channels, MPEG-2/AC3). Every 2 h it syncs
  `/lineup.json` and SiliconDust's free guide (`api.hdhomerun.com/api/guide`, device
  DeviceAuth read fresh from `/discover.json`, never stored), paging `Start` 6 h at a
  time; the free guide reaches ~48-53 h ahead. Airings kept 60 days in `tv_airings`.
  Endpoints: `/api/v1/livetv/status` (health, reach), `/channels`, `PUT /favorites`
  (`{"numbers": [...]}`; seeded with the 18 HD main channels), `/guide?hours=48`
  (favourites, ~16 KB gzipped), `/audit?title=Jeopardy` (raw First flag, original
  air date, episode + the "new only" verdict and why), `POST /sync`. Gracenote's
  tvlistings grid (what Jellyfin guides scrape) answers 403 now; not used.
  Container runs TZ=America/Denver.
- **Live TV playback** (step 2, `app/livesessions.py`, TV app 0.18 `LivePlayerActivity`
  + Live TV menu): players never read the tuner. `POST /api/v1/livetv/tune/{channel}`
  starts one ffmpeg (`-c copy`, HLS EVENT, 4 s segments) into `data/live/<id>/` and
  returns once the first segment exists (~5-6 s; KSL's keyframes are 2.4 s apart).
  The app heartbeats every 10 s (also while paused, which keeps the buffer live);
  Back sends `/leave`. Under 3 min watched = channel surfing, deleted at once.
  Otherwise the buffer keeps recording until the programme being watched ends + 15
  min (max 6 h unwatched, 8 h total), stays resumable 24 h, and shows first in
  Continue Watching and under Live TV > Paused (long-press removes). 100 GB cap.
  Both tuners busy (incl. Plex) -> 503 "Both tuners are in use" after first
  stopping an idle buffer. A server restart closes all buffers (they stay resumable).
  `GET /api/v1/livetv/now` = favourites now/next + paused buffers.
- **DVR** (step 3, `app/recorder.py`, TV app 0.19): rules in `tv_rules` (series by
  title/series id on any or one channel, new-only or every airing; or one airing),
  planned against the guide after every sync and every 5 min into `tv_recordings`
  with a state + plain-English reason for every airing considered, skips included.
  The HDHomeRun does NOT share a tuner between two streams of one channel (tested
  2026-10-18), so a recording holds that channel's live buffer like a viewer does,
  and at the end of its padded window (1 min before, 2 after, +60 min when both
  teams are listed) the buffer's segments are concatenated into one `.ts` under
  `/media/externalHDD/media/dvr` (mounted rw at `/dvr`, owner 1500; the only
  writable media path), named like "tv shows" and reusing an existing show folder
  name, then a library scan runs. Watching a channel that is recording shares the
  tuner. More than two channels at once -> later rules marked `conflict`. A dead
  buffer mid-recording is re-tuned and the pieces joined. `tv_alerts` collects
  failures/conflicts/missed/guide-short (`GET /api/v1/livetv/alerts`). Delivered
  by `media-center/tools/alert_relay.py` on the BEELINK (user timer
  `media-center-alerts.timer`, every minute): reads Pushover from
  `cecret_lake/pushover/.env` (not copied to the Pi), talks to the server with the
  alerts-only key `cecret_lake/media_center/alerts_token` = `MC_ALERTS_TOKEN` in
  server.env (GET alerts + POST /alerts/ack only), acks only after Pushover accepts,
  batches several alerts into one push. Guide alerts at most every 12 h. Endpoints: `GET/POST /api/v1/livetv/rules`,
  `DELETE /rules/{id}`, `GET /upcoming`, `POST /recordings/{id}/skip`.
  TV: hold OK on a Live TV channel -> record now/next once or as a series;
  Recordings > Upcoming / Rules show each decision and why.
- **Guide grid + search** (TV app 0.20): Live TV has Now / Guide / Search tabs.
  `GET /api/v1/livetv/grid?hours=&all=` (favourites 24 h ≈ 180 KB, all channels
  12 h; with record flags + trimmed synopsis). The grid (`ui/LiveGuide.kt`) is ONE
  focus target that moves its own selection (variable-width cells defeat Compose
  focus search); 2.5 h across, OK = watch if on now else record dialog, long-press
  = record, Left at the start of the guide reaches the menu.
  `GET /api/v1/livetv/search?q=` searches every channel's title + team names by
  WORD START ("nfl" must not match "Conflict"). Rule kind `keyword` uses the same
  match, so "Record anything matching 'olympics'" waits for things beyond the
  guide's ~2-day reach. Compose text fields swallow D-pad Up/Down on a remote; use
  `Modifier.dpadLeavesField()`.
  Imported from Plex 2026-10-18: Jeopardy! (Plex had it on every airing; ours is
  new-only). Live start fix in the same release: 2 s segments, the player waits for
  4 s of video and sits 8 s behind live.
- **Plex import** (done once, 2026-10-10): metadata, art, watch history.
- **TMDB** after each scan: fills only empty fields for new arrivals. Exact title
  match wins, and episodes need a title match (numbering drifts).
- **Art prewarm**: every width the app requests is generated ahead of time.
- **Recorded TV**: shows whose files are all MPEG-TS tuner captures are flagged
  `recorded` and live under the app's Recordings menu.
- **Rewatch favourites + IMDb ratings** (Surprise me): weekly stream of IMDb's
  non-commercial datasets for flagged shows. Credit is on the app's Settings screen.
- **Thumbnail worker** (background, nice 19, one file at a time): keyframe-only
  decode -> 240x136 tiles in 10x10 sprite sheets + keyframe times (the app's
  exact seek index). The first pass over ~2,500 files takes ~2 days from 2026-10-10.

Status: `curl -H "X-MC-Token: $(cat /home/pi/cecret_lake/media_center/client_token)" http://192.168.10.150:8790/api/v1/status`
(scan counts, `thumbs: {done, errors, total, cpu_hours}`, TMDB/IMDb results).

## Deploying server changes

From the Beelink:

```bash
rsync -a --exclude data --exclude plex-snapshot --exclude server.env \
  /home/pi/media-center/server/ plex:/home/pi/media-center/server/
ssh plex 'cd /home/pi/media-center/server && docker compose build -q && docker compose up -d'
```

- A restart kills the thumbnail file in progress; it is simply redone.
- One-off DB fixes: stop the container and use
  `docker run --rm -v .../data:/data --user 1500:1500 media-center:latest python3 ...`,
  or `docker exec media-center python3 ...` for small updates. The DB uses one
  SQLite connection per thread (sharing one caused "API misuse" under load).
- Retry failed thumbnails: delete their `file_thumbs` rows where `error is not null`.

## Building and installing the TV app

Follow `android-apk-publishing-guide.md` (memory-capped Gradle, JDK 17):

```bash
cd /home/pi/media-center/tv
export JAVA_HOME=/home/pi/home_config/home-agent-android/.build-tools/jdk-17 \
  ANDROID_HOME=/home/pi/home_config/home-agent-android/.android-sdk
export ANDROID_SDK_ROOT=$ANDROID_HOME ANDROID_USER_HOME=/home/pi/.android \
  GRADLE_USER_HOME=/home/pi/home_config/home-agent-android/.gradle-home PATH="$JAVA_HOME/bin:$PATH"
systemd-run --user --scope -q -p MemoryMax=3G -p MemorySwapMax=1G \
  ./gradlew --no-daemon --max-workers=1 -q :app:assembleRelease
ADB=$ANDROID_HOME/platform-tools/adb
$ADB connect 192.168.40.67:5555
$ADB -s 192.168.40.67:5555 install -r app/build/outputs/apk/release/app-release.apk
```

- Release builds (R8) are what's installed: cold start ~0.9 s to library on screen.
  They are signed with `/home/pi/.android/debug.keystore` so upgrades install
  in place. Bump `versionCode` each build.
- Wait a few seconds after `install` before `am start`, or the launch is dropped.
- First-time provisioning of a device:
  `am start -n com.local.mediacenter/.MainActivity --es mc_token "$(cat /home/pi/cecret_lake/media_center/client_token)"`.
- Deep link (testing / future voice): `--es play_item <episode-or-movie id>`.
- Not yet: automatic updates via the F-Droid repo; only adb installs.

## Testing on the real Shield (lessons)

- **Screenshot before every OK press** (`adb exec-out screencap -p`). Blind key
  sequences started real playback twice on 2026-10-10.
- Check the TV is idle first (`dumpsys window | grep mCurrentFocus`), and send
  `KEYCODE_WAKEUP` before launching (the screensaver eats key presses).
- Any test playback over ~5 s writes progress: record the item's `progress` row
  first and restore it afterwards.
- `run-as` doesn't work on release builds (not debuggable).
- **The Shield is also a Snapcast speaker** (`de.badaix.snapcast`, music-tv
  project). Its client keeps a PCM stream on HDMI even when idle, and with our
  Dolby passthrough the two kill each other's AudioTrack (`dead IAudioTrack,
  Offloaded or Direct`, `could not create track -38`); each rebuild left a
  DIFFERENT lip-sync offset (Bluey, 2026-10-20). Since 0.21 both players send
  `de.badaix.snapcast.STOP_SERVICE` on open and `START_SERVICE` 2 s after the
  last one closes (`HouseSpeaker.kt`; explicit broadcast to
  `de.badaix.snapcast/.BroadcastReceiver`). Check with
  `ps -A | grep libsnapclient` (gone while playing).
- Focus links (`focusProperties { down = … }`) to a card in a Lazy row crash the
  whole app on the key press when that card is scrolled off ("FocusRequester is
  not initialized"); scroll the row first, then `requestFocus()` (show page,
  0.21).
- Our player: the first Back only hides the overlay; the second leaves.

## Cameras row (Lookout)

Since 0.23 (2026-10-25) Home has a **Cameras** row, Kids first, fed by Lookout on the
Optiplex. It replaced the VLC shortcuts that pointed at Blue Iris (VLC's saved links
can't be edited over adb; the Shield isn't rooted).

- Server: `GET /api/v1/cameras` (`app/cameras.py`) reads Lookout's `/api/cameras` with
  the `shield` token (`MC_LOOKOUT_TOKEN` in `server.env`; `MC_LOOKOUT_URL`, default
  `http://192.168.10.49:8805`) and returns each camera's still and stream URLs with the
  token embedded, cached 60 s. The TV talks to the Optiplex directly for video; nothing
  Lookout-related is stored on the Shield. Revoke by removing `shield` from
  `cecret_lake/lookout/auth.json` (both copies) and restarting `lookout-web`.
- Streams: Lookout `/api/stream/{cam}.mp4` = go2rtc fragmented MP4 of the recording
  stream (Claire 2560x1920 H.264 + AAC, Simon 4K HEVC + AAC); `?q=low` = sub-stream.
  Played by `CameraPlayerActivity` (Media3 progressive source, ~1 s buffer).
- Remote: OK/Up = name overlay, Left/Right = other cameras, Down = mute, Back = leave.
  No seeking. Errors/stream end reconnect after 3 s.
- Network: the Shield reaches the Optiplex because the UDM rule "Allow LAN to
  Chromecast" is written Chromecast group -> Default LAN (misnamed). No change needed.
- Not yet: a voice deep link ("show Claire on the TV"); Lookout's history/timeline.

## iPad / browser web app

- URL: `https://media.illuminatehealthanalytics.com/app/` (LAN-only DNS ->
  Beelink Caddy `fdroid-https` -> 192.168.10.150:8790). Add to the Home Screen
  in Safari (needed for offline storage), then pair with a code from
  `POST /api/v1/pair/new`.
- The server makes Safari copies on demand (`web.py`): MP4, video copied
  (HEVC tagged hvc1), audio -> AAC stereo, text subs -> WebVTT, cached in
  `data/web` (150 GB LRU). About 2 min for a 22-min episode on the Pi.
  H.264/HEVC only: MPEG-2 recordings and DivX/Xvid show "not on iPad yet".
- Downloads go into the browser's private storage (OPFS) from a worker and
  only run while the app is open on screen.
- Caddy gotcha: `tts_admin_https` also runs Caddy with host networking and
  the same admin port, so `caddy reload` can hit the wrong instance. Use
  `docker restart fdroid-https`.

## Monitoring and backup

- **Health**: host Node-RED (:1881) "Device Monitoring" tab, nodes `mch_*`.
  GETs `/healthz` every 5 min; 15 min without a healthy reply -> Pushover
  "Media center OFFLINE" (to `BradPhoneNM`); the first healthy reply after
  that -> "back online".
- **Backup**: host Node-RED "Backup Flows" tab, nodes `mcb_*`, 03:40 daily runs
  `/home/pi/scripts/media_center_backup.sh`. It takes a SQLite-backup-API
  snapshot inside the container, copies it to the Beelink, checks
  `integrity_check` + item count, and keeps 14 days of
  `/home/pi/backups/media-center/daily/media-center-YYYYMMDD.db.gz` (~1 MB).
  Pushover on non-zero exit. The 04:05 homelab archive includes the newest one
  (`generated_backups` entry `media-center` in
  `/home/pi/scripts/homelab_backup/config.json`) -> encrypted -> S3.
- Restore: stop the container, gunzip the snapshot to `data/media.db` (owned
  1500:1500), start it. Art/thumbs regenerate on their own (prewarm + worker).

## Troubleshooting

- App says it can't reach the server: `curl http://192.168.10.150:8790/healthz`;
  `ssh plex 'docker ps; docker logs --tail 50 media-center'`. A Plex Pi storage
  wedge (see the `plex-pi-uas-bridge-wedge` memory) takes this down too.
- New episode not showing: the pipeline triggers a scan (look for
  `media_center_scan_requested` in `transmission-hook-runner.log`); otherwise
  wait for the 30-min scan or `POST /api/v1/scan`. TMDB looks up episodes added
  since a show's last check on the very next pass; gaps in episodes under two
  weeks old retry daily, and older gaps weekly.
  Check the server log for `unparsed <path>` (naming pattern not recognised).
- Wrong TMDB match: clear that item's fields + `tmdb_id` (see the plan's TMDB
  notes) and let the next scan retry.
- Seek overshoot or audio before picture on a file: its thumbnail/keyframe
  index isn't generated yet; the picture-first fallback is in use.
