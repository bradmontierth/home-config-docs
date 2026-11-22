# SpotiFLAC Web Pipeline Guide

Last updated: 2026-11-22

This documents the SpotiFLAC web app integration built for searching Spotify, queueing music downloads on the Plex Pi, importing validated files into the shared music library, and refreshing Navidrome.

## Purpose

The system provides a browser UI on the Ubuntu server for:

- Searching Spotify by track, album, playlist, or artist.
- Expanding albums/playlists before queueing.
- Queueing download jobs against the Plex Pi backend.
- Watching job progress and event logs.
- Importing completed downloads into the shared Plex/Navidrome music path.
- Creating `.m3u8` playlist files for Spotify playlist jobs.
- Triggering Navidrome scans from the UI.

The web UI intentionally stays on the Ubuntu server, while downloads run on the Plex Pi because the Plex Pi owns the mounted download/media paths and can run the downloader behind the VPN network namespace.

## Hosts

- Ubuntu server: `192.168.10.217`
  - Runs the browser frontend container.
  - Runs the Navidrome bridge container.
  - Runs Navidrome at `:4533`.
  - Serves the SpotiFLAC UI at `http://192.168.10.217:8099/`.

- Plex Pi: `192.168.10.150`
  - SSH alias: `plex`.
  - Runs `spotiflac-api`.
  - Runs the downloader through `network_mode: container:vpn`.
  - Owns the `/media/externalHDD` paths used for inbox and released media.

## Main Paths

Ubuntu server:

- App source: `/home/pi/spotiflac/spotiflac-web`
- Frontend source: `/home/pi/spotiflac/spotiflac-web/frontend`
- Backend source copy: `/home/pi/spotiflac/spotiflac-web/backend`
- Navidrome bridge source: `/home/pi/spotiflac/spotiflac-web/navidrome-bridge`
- Frontend compose: `/home/pi/spotiflac/spotiflac-web/docker-compose.frontend.yml`
- Frontend env: `/home/pi/spotiflac/spotiflac-web/.env`
- Homepage config: `/home/pi/homepage/config/services.yaml`

Plex Pi:

- Service root: `/home/pi/spotiflac-service`
- Backend deployed source: `/home/pi/spotiflac-service/src/spotiflac-web/backend/app.py`
- Plex compose: `/home/pi/spotiflac-service/docker-compose.plex.yml`
- Service env: `/home/pi/spotiflac-service/.env`
- SQLite state DB: `/home/pi/spotiflac-service/state/spotiflac-web.sqlite3`
- Download/import root: `/media/externalHDD/downloads/spotiflac`
- Music release root: `/media/externalHDD/media/music`
- Playlist release root: `/media/externalHDD/media/music/playlists`

Do not store credentials in this guide. Check the `.env` files on each host when needed.

## Containers

Ubuntu server:

- `spotiflac-web`
  - Nginx static frontend.
  - Publishes `8099:8088`.
  - Proxies `/api/` to the Plex Pi backend.
  - Proxies `/local-api/` to the local Navidrome bridge.

- `spotiflac-navidrome-bridge`
  - Small FastAPI service.
  - Calls local Navidrome from the Ubuntu host side.
  - Exists because the Plex Pi backend is inside the VPN network namespace and cannot reliably call back to Ubuntu/Navidrome directly.

Plex Pi:

- `spotiflac-api`
  - FastAPI backend.
  - Runs as UID/GID `1000:1000`.
  - Uses `network_mode: container:vpn`.
  - Handles Spotify search/detail, job queueing, progress state, and ready markers.

The VPN container itself is managed outside this app. `spotiflac-api` shares its network namespace, so backend traffic exits through VPN.

## Request Flow

1. User opens `http://192.168.10.217:8099/`.
2. Browser calls frontend nginx.
3. Search/detail/job API calls go through `/api/` to Plex Pi `spotiflac-api`.
4. Navidrome scan calls go through `/local-api/` to `spotiflac-navidrome-bridge` on Ubuntu.
5. Backend resolves Spotify metadata using SpotiFLAC internals.
6. Backend creates a job row and per-track rows in SQLite.
7. Backend downloads into `/media/externalHDD/downloads/spotiflac/inbox/<job_id>`.
8. Backend writes a ready marker to `/media/externalHDD/downloads/spotiflac/ready/<job_id>.json`.
9. Importer validates and releases files into `/media/externalHDD/media/music`.
10. Importer writes playlist files under `/media/externalHDD/media/music/playlists`.
11. User can trigger Navidrome scan from the UI.

## Frontend Features

Implemented in `/home/pi/spotiflac/spotiflac-web/frontend/src/main.jsx`.

- Spotify search.
- Search tabs: all, tracks, albums, playlists, artists.
- Album/playlist expansion via `/api/spotify/detail`.
- Provider chips.
- Quality selector.
- Job queue list.
- Job detail view.
- Progress bar and per-status counts.
- Recent job event log.
- Event timestamps rendered in the browser's local timezone.
- Editable job providers/quality/retries in the job detail panel.
- Navidrome scan button.
- App icon and favicon.

Current default providers:

```text
tidal,qobuz,deezer,amazon,spoti,apple,soundcloud
```

Current default quality:

```text
HIGH
```

`HIGH` is not a strict “high or above” global rule. It is passed to each provider and interpreted per provider. Tidal uses `HIGH` then fallback to `LOW`; Qobuz maps `HIGH` to its quality `6`; Apple/Amazon currently tend toward lossless codecs despite the label; SoundCloud chooses the best available stream.

## Backend API

Implemented in `/home/pi/spotiflac/spotiflac-web/backend/app.py` and deployed to the Plex Pi copy.

Important endpoints:

- `GET /api/health`
- `GET /api/search?q=<query>&type=<all|track|album|playlist|artist>`
- `GET /api/spotify/detail?url=<spotify_url>`
- `POST /api/jobs`
- `PATCH /api/jobs/{job_id}`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/events`
- `POST /api/jobs/{job_id}/cancel`
- `GET /api/stats`
- `POST /api/navidrome/scan`

All protected endpoints require the admin token via `X-Admin-Token` or bearer auth unless the service is running with the default development token.

`PATCH /api/jobs/{job_id}` updates:

- `services`
- `quality`
- `track_max_retries`

Queued jobs use the latest DB settings when they actually start. Active jobs only pick up changed settings after restart/resume because SpotiFLAC runs as a synchronous call that captures settings at invocation time.

## Queue/Resume Behavior

The backend uses a single-process `ThreadPoolExecutor`, currently with one worker by default.

On API startup:

- Jobs in `queued`, `resolving`, or `downloading` are resumed.
- Resume writes a warning event.
- The job is resubmitted by ID, and the worker reloads job settings from SQLite at run start.

For already-downloaded files:

- SpotiFLAC usually prints `Skip (already existing)` while replaying the playlist from the beginning.
- The UI file count may stay flat until the worker passes the previous high-water mark and creates a new file.

This is normal after a backend restart. For example, a job at `360/1008` may restart at track `1/1008`, skip the first 360 existing files, and only show visible progress again when file `361` appears.

## Progress Tracking

The backend monitors downloaded audio file count in the job inbox and writes events like:

```text
Downloaded 360 file(s)
```

The UI displays:

- downloaded, failed, and processed track counts
- total tracks
- status counts
- latest events

Failed tracks have an expandable `Why?` section containing the provider-specific
failure summary captured in the SpotiFLAC manifest. Jobs that import only some
tracks use `released_with_failures` instead of appearing as fully released.

Album and playlist manifests live below the collection subdirectory. The backend
reads them recursively and maps results by Spotify source ID; it does not infer
track identity from the number or order of files in the inbox.

The progress count is file-count based, not a perfect “currently attempting track N” tracker. Logs are still the best place to see the exact current track number while SpotiFLAC is replaying/skipping or stuck inside provider retries.

Useful commands:

```bash
token=$(awk -F= '/SPOTIFLAC_ADMIN_TOKEN/{print $2}' /home/pi/spotiflac/spotiflac-web/.env)
curl -fsS -H "X-Admin-Token: $token" http://127.0.0.1:8099/api/health | python3 -m json.tool
curl -fsS -H "X-Admin-Token: $token" http://127.0.0.1:8099/api/jobs | python3 -m json.tool
ssh plex 'docker logs --tail 160 spotiflac-api'
```

## Importer

The importer watches ready markers and validates files before release.

Important behaviors:

- Validates with ClamAV, ffprobe, and mutagen where available.
- Runs as `spotiflac-importer.service` on the media host and uses host
  `clamdscan --fdpass`. If clamd isn't running, every file is rejected with
  `clamav_error` ("Could not connect to clamd … clamd.ctl"). On 2026-10-26 the
  freshly installed `clamav-daemon` was skipped at boot because freshclam
  hadn't finished downloading the signature DB, and systemd never retried it.
  It was started manually on 2026-11-01. Check
  `ssh plex 'systemctl status clamav-daemon'` first on any `clamav_error`.
- Uses audio tags to match downloaded files back to track rows.
- Releases into artist/album folders under `/media/externalHDD/media/music`.
- Writes `.m3u8` playlist files for Spotify playlist jobs.
- Uses relative playlist paths such as `../Artist/Album/Track.flac`.

Known repaired issue:

- A playlist import initially wrote bad playlist paths and falsely rejected already-moved files on retry.
- Importer was patched to tolerate marker `collection` as either string or object, prefer audio tag matching, and write correct relative playlist paths.

## Navidrome Integration

### Scan & sync button (deployed 2026-09-10)

The updated frontend replaces the Navidrome button with **Scan & sync**. It
uses `POST /local-api/library/refresh` and polls
`GET /local-api/library/refresh` for progress. Both endpoints use the existing
admin-token protection. The original scan-only endpoint remains available.

The bridge starts an incremental Navidrome scan, waits for `getScanStatus` to
report that scanning has finished, then requests Music Assistant `music/sync`
for artists, albums, tracks, and playlists. Only available `opensubsonic` and
`filesystem_local` providers are selected. `MA_URL` defaults to
`http://host.docker.internal:8095` in the frontend compose file.

The final message says **sync requested**: Music Assistant queues the sync,
and the button does not certify that every track has been indexed. Scan failure,
unreadable scan status, and a ten-minute scan timeout prevent the MA request.
Repeated taps while the bridge is working join the existing refresh. Closing
the browser does not interrupt it; bridge restart does. Progress is held in
memory and resets on bridge restart. This is a manual button, with no automatic
refresh after each release.

Offline regression tests: `spotiflac-web/tests/test_library_refresh.py`.

Deployment validation: all nine tests passed in the newly built bridge image,
the frontend production build passed, and `pip check` found no broken
requirements. Both containers were recreated after phone approval. The live
button API started Navidrome at `19:52:15Z`, its scan finished at `19:52:17Z`,
and MA sync requests followed; all local provider sync tasks subsequently
finished. Navidrome and MA search both returned the Spirit
Halloween track; MA marked `library://track/3281` playable.

Pre-deployment images are retained for rollback:

- `spotiflac-web:before-scan-sync-20260910`
- `spotiflac-navidrome-bridge:before-scan-sync-20260910`

### Missing Spirit Halloween track diagnosis (2026-09-10)

Job `acd8a2b3-0f65-4c2d-8fb4-26e8cce14bdd` released at `19:38:27Z` to
`Spooked4u/YouTube/01 - Spooky Scary Skeletons (Spirit Halloween Music Video).m4a`.
The NFC jukebox refresh at `19:38:57Z` rebuilt its filesystem search index and
requested only MA playlists. It did not scan Navidrome, whose previous scan
was at `19:00Z`. The file existed and was visible over NFS, but was absent from
Navidrome and MA's indexed library.

The approved Navidrome scan imported the track at `19:45:50Z`; an MA local
provider sync followed. MA search then returned `library://track/3281` with
artist **Spooked4u** and album **YouTube**. To discover newly released music,
scan Navidrome first, then sync MA tracks as well as playlists.

### Original scan-only integration

The original Navidrome button called this endpoint, which remains available:

```text
POST /local-api/navidrome/scan
```

That hits the local Ubuntu bridge, not the Plex Pi backend. This is intentional.

Reason:

- The Plex Pi backend is inside the VPN network namespace.
- The Ubuntu frontend/bridge can reliably reach local Navidrome.
- Keeping Navidrome credentials local to the bridge limits exposure to the downloader/VPN side.

Navidrome credentials live in the frontend `.env`; do not put them in this guide.

## Homepage Shortcut

The app was added to Homepage under the vibe-coded apps section.

Config path:

```text
/home/pi/homepage/config/services.yaml
```

The app icon was added to the frontend and used for favicon/page branding.

## Deployment

Frontend deploy on Ubuntu:

```bash
cd /home/pi/spotiflac/spotiflac-web
docker compose -f docker-compose.frontend.yml --env-file .env up -d --build spotiflac-web
```

Backend deploy to Plex Pi:

```bash
scp /home/pi/spotiflac/spotiflac-web/backend/app.py plex:/home/pi/spotiflac-service/src/spotiflac-web/backend/app.py
scp /home/pi/spotiflac/spotiflac-web/docker-compose.plex.yml plex:/home/pi/spotiflac-service/docker-compose.plex.yml
ssh plex 'cd /home/pi/spotiflac-service && docker compose -f docker-compose.plex.yml --env-file .env up -d --build spotiflac-api'
```

Restarting `spotiflac-api` interrupts the active synchronous downloader call. The job should resume from SQLite and skip existing files, but visible progress may appear to stall until it reaches new files.

## Provider/Rate-Limit Notes

SpotiFLAC uses multiple resolver/downloader providers. Many are public services rather than official first-party APIs.

The backend image includes Deno and `yt-dlp-ejs`, which current yt-dlp releases
require for full YouTube extraction. `GET /api/health` reports the installed
yt-dlp/EJS versions, JavaScript runtime path, and a `youtube.ready` result.
YouTube search ranks multiple candidates by title, artist, and duration and
penalizes likely karaoke, cover, dialogue, instrumental, reaction, and tutorial
matches before attempting downloads.

Direct YouTube/YouTube Music links automatically use only the YouTube provider
and keep the supplied video ID; they do not use Spotify matching or the YouTube
search candidate ranking. The job quality selector does not impose a minimum
bitrate on these downloads. Audio is selected with `bestaudio/best` and converted
to M4A; the former `M4A 256kbps` banner described conversion settings, not source
quality or a download threshold.

On 2026-09-10, the direct-link download of video `V-NXhrA2U2M` resolved metadata
but received HTTP 403 twice while fetching media; Cobalt and YT1D also failed.
Subsequent read-only audio stream probes succeeded. The provider now checks
format availability and makes up to three fresh yt-dlp attempts, trying best
audio, M4A-preferred audio, then lower-bandwidth audio before external fallbacks.
Each attempt uses yt-dlp's default clients and an isolated temporary directory.
Only completed audio is moved into the watched inbox. Existing duration checks
and importer validation still apply.

Direct errors, with signed URLs redacted, are retained in the track manifest;
jobs with no files now include track/provider errors in the job error and event.
Regression tests are in `spotiflac-web/tests/test_youtube_retry.py` and run in the
backend image with an isolated state/inbox and no network. The pre-change image
is retained on Plex as
`spotiflac-service-spotiflac-api:before-youtube-retry-20260910`; corresponding
source backups have the suffix `.before-retry-20260910`.

Deployment validation on 2026-09-10: all six regression tests and `pip check`
passed in the rebuilt image (yt-dlp `2026.8.19`, EJS `0.8.0`). Retry job
`b46e6107-2da9-4da7-99c4-6610bd281143` downloaded the exact video and reached
`released` with zero failures/rejections at `14:40:33Z`. The M4A was released to
`/media/externalHDD/media/music/Bass Boosted City/YouTube/01 - SPOOKY SCARY SKELETONS (Dma Illan TRAP REMIX).m4a`.
An incremental Navidrome scan was requested through the local bridge.

### YouTube picker: official uploads over live/fan uploads (2026-11-01)

Lossless providers were all failing on 2026-11-01, so most of that day's
Christmas downloads (91 tracks) came from the YouTube fallback. The song.link
(Odesli) API now returns `401`, so Spotify-track YouTube picks come only from
`_search_youtube_candidates`. Its old scoring (title similarity, artist name,
duration) ranked the fan upload `hlhIYg991vI` of "Queen of the Winter Night"
(TSOVids channel, mean −24.6 dB) above the official upload `AKpb9xOXpi4`. The
official title's "(Official …)" suffix lowered its similarity score.

The patch to `src/SpotiFLAC-Module-Version/SpotiFLAC/providers/youtube.py` on
the media host (`ssh plex`, `/home/pi/spotiflac-service`, not a git repo) adds:

- −4 when the title contains live/concert/performs/performance/performing/
  bootleg/fancam/tour, unless the requested title has one of those words.
- Bonuses only when the title matches (similarity ≥ 0.6 or the requested title
  appears in it). Without that rule, testing promoted the official upload of a
  *different* song ("Remember" → "Christmas Canon") and another artist's Topic
  track ("I Saw Three Ships"):
  - +4 for a YouTube Music `… - Topic` channel that contains the artist name.
  - +2 for the artist's own channel, or VEVO with the artist in the title.
  - +1.5 when the title says "official" and the requested title does not.
- WARNING-level log lines, so the pick is visible in `docker logs spotiflac-api`:
  `[youtube] Ranked search candidates: <id>=<score>, …` and
  `[youtube] Selected video <id> for <artist> - <title>`.

Backups: `youtube.py.before-picker-20261101` and image tag
`spotiflac-service-spotiflac-api:before-picker-20261101`. In a re-test against 8
of the quietest YouTube tracks, using their real durations, 6 picked the
official or artist upload. "Remember" and "The Silent Nutcracker" still pick
third-party uploads with matching durations.

Queen of the Winter Night was re-downloaded from `AKpb9xOXpi4` (mean −11.3 dB).
The old file is in `/media/externalHDD/downloads/spotiflac/replaced-20261101/`.
Tracks downloaded before this patch have no record of which video was used.
Low loudness alone doesn't prove a live recording; many Mannheim Steamroller
tracks are just quiet.

### Per-track download provenance (2026-11-22)

Every track now records where it came from and why, in the job database, so
it survives container restarts (the docker logs only cover the last
restart, ≤30 MB).

- `job_tracks.download_details` (JSON) per track:
  - `provider_errors`: why each provider before the winner failed (e.g. tidal
    TRACK_NOT_FOUND, qobuz UNAVAILABLE, amazon DNS failure, spoti WAF block).
  - `youtube.searches[]`: query, expected seconds, and **every** search result
    with title, channel, duration, views and score (≥ 6 is kept as a candidate).
  - `youtube.attempted`, `youtube.rejected` (validation errors),
    `youtube.selected_video`, `youtube.via` (yt-dlp / Cobalt / YT1D).
- One `info` event per track, shown in the job log, e.g.
  `Mannheim Steamroller - Fum Fum Fum: downloaded via youtube; YouTube video
  8X8zedKsiPo (yt-dlp); 7 provider(s) failed first`. Its `details_json` holds
  the same JSON.

How it flows: `youtube.py` `download_track` is a wrapper around
`_download_track_impl` that attaches the trace to `DownloadResult.details`
(new optional field in `core/models.py`). `downloader.download_one` adds
`provider_errors`, and `_record_result` writes `details` into
`.spotiflac-downloads.jsonl`. `app.py` `record_details()` stores it while it
applies the manifest. Backups: `*.before-trace-20261122` next to each of the 4
files, image tag `spotiflac-service-spotiflac-api:before-trace-20261122`.

Query a track:

```bash
ssh plex "python3 -c \"import sqlite3,json;c=sqlite3.connect('/home/pi/spotiflac-service/state/spotiflac-web.sqlite3');print(json.dumps(json.loads(c.execute(\\\"select download_details from job_tracks where title like '%Fum%' order by id desc\\\").fetchone()[0]),indent=1))\""
```

Tracks downloaded before 2026-11-22 have no details (and pre-2026-11-01 picks
have no log at all). To identify which upload an old file came from, download
the candidates and compare exact duration + mean volume with `ffmpeg
volumedetect`. That matched Fum Fum Fum to fan upload `SYyetiB4Jh8` (286.1 s,
−11.6 dB, "Miss Magnolia", 687 views); the old pre-patch picker had chosen it.
It was replaced on 2026-11-22 with the official `8X8zedKsiPo`
(mannheimsteamroller channel, 293.5 s, −16.0 dB). The old file is in
`/media/externalHDD/downloads/spotiflac/replaced-20261122/`. All 7 lossless
providers still failed for it on 2026-11-22.

Common failure types seen:

- `401` from song.link/Odesli (YouTube candidate lookup, 2026-11-01)
- `429 Too Many Requests`
- `403`
- invalid JSON
- resolver timeout
- Cloudflare/WAF block
- upstream track not found

VPN rotation may help when the resolver is rate-limiting the Plex Pi's VPN exit IP. It may not help when the resolver service itself is rate-limited by its upstream backend.

Example path:

```text
spotiflac-api -> VPN exit IP -> public resolver API -> upstream music service
```

If the public resolver API has exhausted its own quota/session/IP, changing the Plex Pi VPN exit does not fix the backend quota. A future VPN-rotation feature should be a circuit breaker:

- Rotate only after sustained no-progress windows.
- Require recent IP-shaped failures such as repeated 429/403/timeouts.
- Limit rotation frequency.
- Record rotation events in the job log.
- Stop rotating if the same provider fails across multiple VPN exits.

## Current Operational Caveats

- `cancel` is cooperative only. The underlying downloader is synchronous and may continue until the current provider call completes.
- Active job settings changes require restart/resume to affect the running SpotiFLAC call.
- The UI progress count is file-based and can lag behind the current track attempt.
- Public resolver availability changes frequently.
- Provider additions can improve coverage but can also add latency when every provider fails for a track.
- Avoid restarting the backend casually while a large playlist is running unless resume/replay behavior is acceptable.

## Future Improvements

- Add a “Stop and Import What We Have” control.
- Add exact current track number parsing from logs or structured callbacks.
- Add provider health/cooldown tracking.
- Add cautious VPN rotation circuit breaker.
- Add per-job throughput/no-progress detection.
- Add richer playlist import status in UI.
- Add optional “refresh Navidrome after import complete” automation.
