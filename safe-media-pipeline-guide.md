# Safe Media Pipeline Guide

This guide documents the media ingestion pipeline shared by Transmission, Plex, Navidrome, and Music Assistant.

The Plex/download Raspberry Pi is available as:

```bash
ssh plex
```

## Device Roles

`plex` is the download and media preparation host.

This main home server runs Navidrome, Music Assistant, MariaDB, and Grafana. It consumes the Plex Pi music library over NFS.

```text
Transmission on plex
        |
        v
completed downloads
        |
        v
safe media pipeline on plex
        |
        v
/media/externalHDD/media/music
        |
        +--> Navidrome over read-only NFS
        +--> Music Assistant over read-only NFS
```

## Active Files

On `plex`:

```text
/home/pi/transmission/organizer/plex_organize.py
/home/pi/transmission/organizer/run_pipeline_locked.sh
/home/pi/transmission/organizer/pipeline.env
/home/pi/transmission/transmission-config/hooks/transmission-done.sh
```

On this main home server:

```text
/home/pi/plex-workspace
```

Use that workspace for future Plex/Pipeline scripts before copying them to `plex`.

## Media Paths

On `plex`:

```text
/media/externalHDD/downloads/complete
/media/externalHDD/downloads/incomplete
/media/externalHDD/downloads/.quarantine
/media/externalHDD/downloads/.pipeline-triggers
/media/externalHDD/downloads/.pipeline-backups
/media/externalHDD/media/music
/media/externalHDD/media/movies
/media/externalHDD/media/tv shows
```

The shared music library is:

```text
/media/externalHDD/media/music
```

Navidrome mounts it read-only as:

```text
/music
```

## Pipeline Policy

The organizer is fail-closed.

Normal flow:

1. Move each completed item into `.quarantine`.
2. Insert run/item/file/action rows into MariaDB.
3. Scan files with ClamAV, preferring `clamdscan`.
4. Reject and delete the whole item for infected files, scanner errors, dangerous extensions, dangerous MIME on unsupported files, mixed audio/video, invalid media, failed `ffprobe`, or missing required audio tags.
5. Delete junk sidecars such as `.nfo`, `.txt`, `.url`, `.sfv`, `.log`, and `.pdf` after scanning and logging.
6. MIME-inspect unknown non-media sidecars. Delete and log harmless unknown sidecars per-file as `unsupported_sidecar_deleted`; reject the whole item as `unsupported_bundle_dangerous_mime` if the MIME looks executable, script-like, archive-like, disk-image-like, or binary.
7. Release approved audio to `Artist/Album/NN - Title.ext`.
8. Release approved video to the Plex movie/TV folders.
9. Remove quarantine leftovers after successful release.

This intentionally favors deletion over keeping suspicious files around.

## Transmission Hook

Transmission is configured to run:

```text
/config/hooks/transmission-done.sh
```

Host path:

```text
/home/pi/transmission/transmission-config/hooks/transmission-done.sh
```

Transmission settings:

```text
script-torrent-done-enabled: true
script-torrent-done-filename: /config/hooks/transmission-done.sh
```

The container hook writes a trigger file under:

```text
/media/externalHDD/downloads/.pipeline-triggers
```

There is also a host-side fallback watcher on:

```text
/media/externalHDD/downloads/complete/*
```

Fallback units:

```bash
systemctl status media-pipeline-complete.path
systemctl status media-pipeline-complete.service
```

The fallback watcher creates a trigger file if completed items exist and Transmission did not create one. This is a safety net because Transmission's post-complete hook can miss existing items or items completed before the setting was enabled.

Host systemd watches that directory:

```bash
systemctl status media-pipeline-trigger.path
systemctl status media-pipeline-trigger.service
```

The triggered service runs:

```text
/home/pi/transmission/organizer/run_pipeline_locked.sh
```

The runner:

- waits 30 seconds for files to settle
- uses `flock` to prevent overlapping organizer runs
- runs `plex_organize.py`
- deletes trigger files only after a successful organizer run

## Backup Policy

The organizer can create a pre-quarantine backup copy for selected high-value downloads before it moves the item into `.quarantine`.

Current default match pattern:

```text
MEDIA_PIPELINE_BACKUP_NAME_REGEX=(?i)(ohlsson|garrick)
```

Backup path:

```text
/media/externalHDD/downloads/.pipeline-backups
```

This is intentionally targeted. Do not back up every download by default or the external disk will fill again.

To change the policy, set these environment variables for the organizer:

```text
MEDIA_PIPELINE_BACKUP_DIR
MEDIA_PIPELINE_BACKUP_NAME_REGEX
```

## Classical Metadata Corrections

Classical metadata cleanup proposals are generated on the main home server, but
audio tag writes must run on `plex`. This preserves the read-only separation for
Navidrome and Music Assistant while keeping all media mutations on the
source-of-truth media host.

Main-server export bundle:

```text
/home/pi/tempo/tools/classical/output/metadata_corrections_chopin_run1.json
```

Plex-side files:

```text
/home/pi/transmission/organizer/apply_metadata_corrections.py
/home/pi/transmission/organizer/run_metadata_corrections_locked.sh
/home/pi/transmission/organizer/metadata-corrections/inbox
```

The deployed writer uses:

- `flock` to prevent concurrent correction runs.
- `/home/pi/transmission/organizer/pipeline.env` for DB credentials.
- `media_pipeline.metadata_correction_runs` for run-level audit records.
- `media_pipeline.metadata_correction_files` for per-file audit records.
- `/media/externalHDD/downloads/.pipeline-backups/tag-writeback` for pre-write
  file backups.

Normal correction flow:

```bash
ssh plex
cd /home/pi/transmission/organizer
./run_metadata_corrections_locked.sh stage --bundle metadata-corrections/inbox/metadata_corrections_chopin_run1.json
./run_metadata_corrections_locked.sh summary
./run_metadata_corrections_locked.sh preflight --run-id 1
./run_metadata_corrections_locked.sh apply --run-id 1 --min-confidence 0.9
```

Only run `apply` after `preflight` reports `canApply: true`. Each applied file
gets a backup copy and an audit row with original DB metadata, original file
tags, proposed tags, applied tags, file hashes, file sizes, and timestamps.

Rollback for an applied run:

```bash
ssh plex
cd /home/pi/transmission/organizer
./run_metadata_corrections_locked.sh revert --run-id 1
```

The first Chopin metadata correction run is:

```text
run id: 1
run uuid: 8b95ad79-4a72-4095-8928-7f13af0661db
status: applied
applied files: 231
errors: 0
backup files: 231
backup path: /media/externalHDD/downloads/.pipeline-backups/tag-writeback/8b95ad79-4a72-4095-8928-7f13af0661db
```

## Manual Commands

Run a manual sweep:

```bash
ssh plex
cd /home/pi/transmission/organizer
./plex_organize.py
```

Trigger a manual backfill through the same hook path:

```bash
ssh plex
mkdir -p /media/externalHDD/downloads/.pipeline-triggers
cat > "/media/externalHDD/downloads/.pipeline-triggers/$(date -u +%Y%m%dT%H%M%SZ)-manual-backfill.trigger" <<EOF
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
torrent_id=manual-backfill
torrent_hash=manual
torrent_name=manual_backfill_existing_complete
torrent_dir=/downloads/complete
EOF
```

Check running pipeline work:

```bash
ssh plex
pgrep -af 'plex_organize|run_pipeline_locked|ffprobe|clamdscan|clamscan' || true
```

Check pending folders:

```bash
ssh plex
find /media/externalHDD/downloads/complete -mindepth 1 -maxdepth 1 -printf '%y %p\n' | sort
find /media/externalHDD/downloads/.quarantine -mindepth 1 -maxdepth 1 -printf '%y %p\n' | sort
find /media/externalHDD/downloads/.pipeline-triggers -type f -maxdepth 1 -printf '%p\n' | sort
find /media/externalHDD/downloads/.pipeline-backups -mindepth 1 -maxdepth 1 -printf '%y %p\n' | sort
```

## Logs And Observability

On `plex`:

```text
/home/pi/transmission/organizer/plex_organize.log
/home/pi/transmission/organizer/transmission-hook-runner.log
/home/pi/transmission/transmission-config/media-pipeline-hook.log
```

Pipeline DB on the main home server:

```text
database: media_pipeline
tables: pipeline_runs, pipeline_items, pipeline_files, pipeline_actions
```

Pipeline timestamps are stored as UTC `DATETIME` values. Grafana should treat them as UTC and render them in the browser timezone. Do not add fixed timezone offsets in dashboard SQL.

Grafana dashboard:

```text
http://192.168.10.217:3001/d/media-pipeline/media-pipeline
```

Useful DB check from the main home server:

```bash
docker exec mariadb-mariadb-1 mariadb -umedia_pipeline -p media_pipeline \
  -e "SELECT id, run_uuid, status, summary, started_at, finished_at FROM pipeline_runs ORDER BY id DESC LIMIT 10;"
```

Do not put DB passwords in this guide. The deployed credentials are in:

```text
/home/pi/transmission/organizer/pipeline.env
```

## Navidrome Refresh

Trigger an incremental scan:

```bash
docker exec navidrome /app/navidrome scan --datafolder /data --musicfolder /music --nobanner
```

Trigger a full scan:

```bash
docker exec navidrome /app/navidrome scan --datafolder /data --musicfolder /music --nobanner --full
```

Navidrome also scans hourly and on startup.

## Single-FLAC Plus CUE Albums

Some legitimate albums arrive as one large FLAC file per disc plus a `.cue` sheet.

Example pattern:

```text
Garrick Ohlsson - Chopin The Complete Works [Disc 01].flac
Garrick Ohlsson - Chopin The Complete Works [Disc 01].cue
Garrick Ohlsson - Chopin The Complete Works [Disc 01].log
```

The pipeline now supports this strict pattern inside quarantine. It does not broadly allow arbitrary cue sheets.

Implemented behavior:

1. Detect strict `.cue` plus sibling `.flac` disc-image pairs.
3. Require the cue sheet to reference only sibling files.
4. Reject absolute paths, parent traversal, external references, archives, executables, mixed video, and unexpected extensions.
5. Scan and MIME-check the cue/flac files before parsing.
6. Parse strict CUE metadata for album, performer, track titles, and track start times.
7. Split each disc FLAC into per-track FLAC files in a quarantine work directory using `ffmpeg`.
8. Apply CUE metadata to the generated tracks.
9. Run the existing ClamAV, MIME, `ffprobe`, tag, and release checks against the generated track files.
10. Log every split action and source-to-output mapping to `pipeline_actions`.
11. Delete the original cue/flac/log bundle only after successful release.

Installed tools on `plex`:

```bash
sudo apt-get install cuetools shntool flac
```

The current implementation uses `ffmpeg` for splitting, but `cuetools`, `shntool`, and `flac` are installed for inspection and fallback work.

Manual inspection command shape:

```bash
cueprint "disc.cue"
ffprobe "disc.flac"
```

Do not change this into a broad `.cue` allow-list. The cue/flac pair should be transformed into normal per-track audio files inside quarantine, then handed back to the existing audio validation/release path.

## Disk Health

If `/media/externalHDD` is nearly full, the Pi can appear hung because Transmission, ClamAV, `ffprobe`, tag readers, and file moves all compete for the same external disk.

Check:

```bash
ssh plex
df -h /media/externalHDD
systemctl is-active clamav-daemon clamav-freshclam
```
