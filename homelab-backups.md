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
