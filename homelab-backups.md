# Homelab Backups

This host uses a rebuild-focused backup system. The goal is to preserve enough configuration to recreate the Docker/service stack after a machine failure, not to back up every application volume or media/data directory.

## What Runs

Production command:

```bash
/home/pi/scripts/homelab_config_backup.sh --upload-s3
```

This command:

1. Auto-discovers Docker Compose projects under `/home/pi`.
2. Copies Compose files, nearby env files, selected service config, Node-RED flows/credentials, and selected generated backups.
3. Writes a manifest and restore notes.
4. Creates an encrypted archive.
5. Runs a restore rehearsal by decrypting and extracting the archive into a temp directory.
6. Uploads the encrypted archive to S3.
7. Writes status rows to MySQL for Grafana.

## Scheduling

The intended scheduler is Node-RED. Add or update an `exec` node to run:

```bash
/home/pi/scripts/homelab_config_backup.sh --upload-s3
```

Schedule it after the other nightly backup jobs have completed, for example around `03:54` or `04:05`.

As of 2026-05-16, the active host Node-RED `Backup Flows` tab runs this at `04:05`:

```text
http://192.168.10.217:1881/#flow/1bf7acacad8c9169
```

Node-RED should send a Pushover alert if the command exits nonzero. The backup script handles S3 upload, so Node-RED does not need to upload the archive separately.

The old direct S3 upload nodes for Home Assistant, Joplin, Paperless, Grafana SQL, and Docker Compose backups were disabled because those artifacts are now included in the encrypted homelab backup archive. The jobs that generate those source backups still run.

## Media Center DB (added 2026-10-10)

The media-center watch history/progress DB lives on the Plex Pi, outside this
host. The host Node-RED Backup Flows tab runs
`/home/pi/scripts/media_center_backup.sh` at 03:40, writing
`/home/pi/backups/media-center/daily/media-center-YYYYMMDD.db.gz` (14 days kept,
Pushover on failure). The `generated_backups` entry `media-center` (mode
`latest`) puts the newest one in the 04:05 archive. Details:
`media-center-guide.md`.

## Secrets Vault (cecret_lake)

`/home/pi/cecret_lake` is the homelab secrets vault (OpenRouter/Tesla/Rachio/
Pushover keys, Firebase service accounts, HA token, codex `auth.json`, etc.). It
is **not a git repo** and its secrets are mounted into containers by path, so the
compose-adjacent `.env` capture does not reach them. Added to `explicit_includes`
2026-07-07 so the encrypted archive protects it. The include excludes codex
runtime junk (`tmp`, `log`, `sessions`, `cache`, `plugins`, ...) — without those
excludes it balloons to ~1.7 GB of `tmp/arg0/codex-linux-sandbox` binaries;
scoped, it is ~1 MB. If you add a new secret under a codex-style runtime dir,
re-check the exclude list.

**Root-owned files break the run (found 2026-10-15).** Two files in
`cecret_lake/dashboard_webapp/` (`google_maps_browser_key`, `google_maps_map_id`)
were created by root on 2026-07-21 with mode 0600. The nightly run executes as
`pi`, could not read them, and the script aborted on the first `PermissionError`,
so **every nightly backup from 2026-07-22 to 2026-10-15 failed** (86 runs) with no
alert: the Node-RED exec node only wires to debug nodes and `pushover.enabled`
is false in `config.json`. Fix applied 2026-10-15: `copy_file()` now records an
`error` warning for an unreadable file and continues, so the run completes with
status `warning` instead of `failed`. Still to do: `sudo chown pi:pi` those two
files so they land in the archive again, and wire a failure alert. Rule: anything
written into `cecret_lake` must be owned by `pi` (or at least readable by `pi`).

## Failure Alerts (added 2026-10-15)

Two layers, both Pushover to Brad's phone (device name from the Node-RED
convention endpoint `GET :1880/pushover/device/brad`, no hardcoded fallback):

1. **In the script.** `pushover.enabled` is true in `config.json`; credentials
   are read at run time by path from `/home/pi/cecret_lake/pushover/.env`
   (`PUSHOVER_API`, `PUSHOVER_USER`, `PUSHOVER_DEVICE`), never copied anywhere.
   - run `failed` -> "Homelab backup FAILED", priority 1, exit code **3**.
   - run `warning` with any `error`-severity warning (e.g. an unreadable file
     skipped by `copy_file`) -> "Homelab backup completed with errors",
     priority -1 (quiet), exit code 0.
   - `/home/pi/scripts/homelab_config_backup.sh --test-pushover` sends a test.
2. **In Node-RED (host :1881, Backup Flows tab).** The exec node's stderr and
   exit-code outputs now also feed `hcb_err` / `hcb_rc` / `hcb_push`. `hcb_rc`
   alerts (priority 1) on any exit code other than 0 or 3, i.e. when the script
   crashed or never ran and so could not alert itself. Both layers were
   live-tested 2026-10-15 (script `--test-pushover`; NR via a temporary inject
   with `{code: 99}`, removed afterwards).

## Personal Documents (added 2026-10-15)

`/home/pi/backups/personal-documents/` holds one-off personal archives that
should ride along in the encrypted nightly archive and its S3 copies. It is an
`explicit_includes` entry (`dest: personal-documents`, `patterns: **/*`,
`required: true`). First contents: `linux-migration-20261015/` = the tax
documents + OneNote export from the Windows laptop before the ThinkPad P51 was
reinstalled (`tax-documents-onenote.zip`, its `.sha256`, `verification.json`,
~147 MB, plaintext zip, so the GPG archive is what protects it). Each daily and
monthly archive grows by the size of this directory, so keep it to things that
genuinely need off-site copies; verify with `sha256sum -c` after any move.

## Git Bundles (added 2026-10-24)

The archive excludes `.git` directories, and this host's GitHub SSH key is an
account key that can force-push over every repo. So the nightly run now also
writes a full-history `git bundle create --all` for each repo under `/home/pi`
(depth 5) into `git-bundles/` in the archive. Config: the `git_bundles` block in
`config.json`; code: `collect_git_bundles()` in the engine.

- Included: repos whose origin owner is `bradmontierth` or `illuminatehealth`,
  plus repos with no remote (these exist nowhere else). Third-party clones
  (LibreChat, Joplin, Stirling-PDF, Tempo upstream, ESPHome external components)
  are skipped and logged.
- Each bundle is checked with `git bundle verify`. Uncommitted changes to tracked
  files are saved as `<repo>.uncommitted.patch` (cap 50 MB). Untracked files are
  NOT saved, only counted in `INDEX.json`.
- A repo with zero commits cannot be bundled and logs a warning.
  `montierth_panel` was the one case; it got its first commits 2026-10-24
  (the N100 deploy copy had newer October edits, synced back into beelink).
- Linked worktrees and submodules (`.git` is a file) are skipped; their history
  is in the main repo's bundle.
- **Tank runs the same engine** (identical file, deployed by scp) with its own
  `git_bundles` block, which also excludes `dbt_packages` and `.builds`. First
  run 2026-10-24: 5 repos (ballad, lumens-per-watt, slack bot, T++, work-docs),
  archive about 30 MB. The two PSW client repos on Azure DevOps are skipped on
  purpose (owner filter), so client code stays out of the personal bucket.
- First run 2026-10-24: 42 repos, 277 MB of bundles (antennapod 116 MB, sms-gate
  60 MB), about 46 s. Archive grew from about 1.77 GB to 2.06 GB.
- Restore: `git-bundles/RESTORE.md` in the archive. In short,
  `git clone <repo>.bundle /home/pi/<repo>`, set the origin URL from
  `INDEX.json`, `git apply` the patch if present. To repair a force-pushed
  GitHub repo, push the good branches back from the clone.
- Turn off with `"enabled": false` in the `git_bundles` block.

## Host Vs Container Node-RED

The backup command currently runs from the host Node-RED service, not the containerized Node-RED instance. That keeps the execution environment simple because the script uses host paths and host tools:

```text
/home/pi
/home/pi/.config/homelab-backup/backup.env
gpg
aws
mysql
docker
```

The containerized Node-RED instance can run the backup script only if the container is given equivalent access: host path mounts, the backup env file, AWS/GPG/MySQL/Docker CLIs, and Docker socket access for inventory commands. That is possible, but it increases container privileges and is more fragile than running this particular job from the host.

## Local Files

Backup system files:

```text
/home/pi/scripts/homelab_backup/
/home/pi/scripts/homelab_config_backup.sh
```

Runtime secret/config file:

```text
/home/pi/.config/homelab-backup/backup.env
```

Paperless backup encryption secrets:

```text
/home/pi/.config/paperless-backup/backup.env
```

That file contains both the old Paperless GPG passphrase for existing backups and the current passphrase for newly generated backups. Store both off-machine.

Local backup output:

```text
/home/pi/backups/homelab-config/
```

Important subdirectories:

```text
/home/pi/backups/homelab-config/daily
/home/pi/backups/homelab-config/monthly
/home/pi/backups/homelab-config/logs
```

## S3 Layout

Backups upload to:

```text
s3://bradmontierth/homelab-config/daily/
s3://bradmontierth/homelab-config/monthly/
```

The archive is encrypted before upload and may contain secrets.

## Grafana

Grafana dashboard:

```text
Homelab Backups
```

It shows:

- latest backup status
- time since last success
- archive size trend
- included file count
- warnings
- restore rehearsal checks
- S3 uploads
- files included in a selected backup run

The backing MySQL database is:

```text
homelab_backup
```

## Manual Commands

Full production run:

```bash
/home/pi/scripts/homelab_config_backup.sh --upload-s3
```

Local-only run without S3:

```bash
/home/pi/scripts/homelab_config_backup.sh
```

Discovery dry run:

```bash
/home/pi/scripts/homelab_config_backup.sh --dry-run --no-db
```

## Restore Rehearsal

The nightly restore rehearsal does not start containers. It only verifies that:

1. The encrypted archive exists.
2. The archive decrypts.
3. The tarball extracts.
4. `manifest.json` is valid.
5. Manifest-listed files exist after extraction.
6. S3 object existence is confirmed after upload.

Do not run `docker compose up` from an automated restore rehearsal on the live host.

## Recovery Notes

On a replacement host:

1. Install Docker, Docker Compose, GPG, AWS CLI, and Git.
2. Download the latest encrypted archive from S3.
3. Decrypt it using the backup passphrase.
4. Extract it.
5. Read `manifest.json`, `manifest.txt`, and `restore.md`.
6. Restore Compose projects and env/config files under `/home/pi`.
7. Restore app-specific generated backups separately, such as Home Assistant, Joplin, and Paperless.
8. Validate each stack with `docker compose config` before starting it.

## Maintenance

When adding a new service, normally no backup change is needed if it lives under `/home/pi` and has a standard Compose filename. If it needs extra config outside its Compose directory, update:

```text
/home/pi/scripts/homelab_backup/config.json
```

Then run:

```bash
/home/pi/scripts/homelab_config_backup.sh --dry-run --no-db
```

For implementation details, see:

```text
/home/pi/home_config/homelab-backup-agent-guide.md
```
