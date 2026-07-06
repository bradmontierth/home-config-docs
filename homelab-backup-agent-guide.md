# Homelab Backup Agent Guide

This host uses a rebuild-focused backup system for Docker Compose stacks, service config, selected generated backups, and recovery metadata.

## Files

- Backup wrapper: `/home/pi/scripts/homelab_config_backup.sh`
- Backup engine: `/home/pi/scripts/homelab_backup/homelab_config_backup.py`
- Backup config: `/home/pi/scripts/homelab_backup/config.json`
- Persistent implementation plan: `/home/pi/scripts/homelab_backup/IMPLEMENTATION_PLAN.md`
- Optional secrets/env file: `/home/pi/.config/homelab-backup/backup.env`
- Env template: `/home/pi/scripts/homelab_backup/backup.env.example`
- DB schema: `/home/pi/scripts/homelab_backup/sql/schema.sql`
- Grafana dashboard: `/home/pi/scripts/homelab_backup/grafana/homelab_backup_dashboard.json`

## Local Backup Output

- Backup root: `/home/pi/backups/homelab-config`
- Logs: `/home/pi/backups/homelab-config/logs`
- Daily archives: `/home/pi/backups/homelab-config/daily`
- Monthly archives: `/home/pi/backups/homelab-config/monthly`
- Restore rehearsal scratch dir: `/tmp/homelab-restore-test`

Archives are encrypted GPG symmetric archives and may contain secrets.

## Normal Commands

Dry-run discovery:

```bash
/home/pi/scripts/homelab_config_backup.sh --dry-run --no-db
```

Local backup without S3:

```bash
/home/pi/scripts/homelab_config_backup.sh --no-db
```

Nightly production command:

```bash
/home/pi/scripts/homelab_config_backup.sh --upload-s3
```

Skip restore rehearsal only for troubleshooting:

```bash
/home/pi/scripts/homelab_config_backup.sh --upload-s3 --skip-rehearsal
```

## Adding Or Changing A Service

When adding a new Dockerized service under `/home/pi`, no manual change is usually required if the service has one of these files within the discovery depth:

- `docker-compose.yml`
- `docker-compose.yaml`
- `compose.yml`
- `compose.yaml`

If the service needs extra config files outside the Compose directory, update `/home/pi/scripts/homelab_backup/config.json`:

1. Add a narrow `explicit_includes` entry.
2. Include only config/recovery files.
3. Avoid broad data directories, media directories, caches, databases, and `node_modules`.
4. Run dry-run and one local backup.
5. Inspect `manifest.json` in the extracted rehearsal directory or archive.

## Database Monitoring

The backup script can write status into MySQL/MariaDB tables:

- `homelab_backup_runs`
- `homelab_backup_items`
- `homelab_backup_warnings`
- `homelab_backup_uploads`
- `homelab_backup_restore_checks`

Initialize the schema with:

```bash
/home/pi/scripts/homelab_backup/sql/init_schema.sh
```

The env file must provide DB admin credentials for schema setup and backup-user credentials for normal runs.

## Grafana

Install or update the Grafana datasource and dashboard through the API:

```bash
/home/pi/scripts/homelab_backup/grafana/install_backup_monitoring.sh
```

The dashboard expects the MySQL datasource UID `homelab-backup-mysql` unless overridden by `HOMELAB_BACKUP_GRAFANA_DS_UID`.

## Restore Rehearsal

The nightly restore rehearsal intentionally does not start containers. It verifies:

1. The encrypted archive exists.
2. The archive decrypts.
3. The tarball extracts.
4. `manifest.json` is valid JSON.
5. Manifest-listed files exist after extraction.
6. S3 object existence is checked when S3 upload is enabled.

A deeper manual restore test can run `docker compose config` in extracted project directories, but do not run `docker compose up` on the live host.

## Node-RED

The existing Backup Flows tab should call:

```bash
/home/pi/scripts/homelab_config_backup.sh --upload-s3
```

Node-RED should send Pushover alerts when the exec node returns a nonzero exit code. Direct Pushover from the backup script is optional and disabled by default.
