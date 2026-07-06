# SpotiFLAC Web Pipeline Guide

Last updated: 2026-05-20

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

- completed tracks
- total tracks
- status counts
- latest events

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
- Uses audio tags to match downloaded files back to track rows.
- Releases into artist/album folders under `/media/externalHDD/media/music`.
- Writes `.m3u8` playlist files for Spotify playlist jobs.
- Uses relative playlist paths such as `../Artist/Album/Track.flac`.

Known repaired issue:

- A playlist import initially wrote bad playlist paths and falsely rejected already-moved files on retry.
- Importer was patched to tolerate marker `collection` as either string or object, prefer audio tag matching, and write correct relative playlist paths.

## Navidrome Integration

The UI has a Navidrome button that calls:

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

Common failure types seen:

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
