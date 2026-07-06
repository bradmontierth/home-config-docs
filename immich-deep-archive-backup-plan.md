# Immich Encrypted Deep Archive Backup Plan

## Summary

Immich photo backups use `restic` for client-side encryption, deduplicated snapshots, and S3 storage. The dedicated S3 bucket is `bradmontierth-immich-backup` in `us-west-2`.

The backup is disaster-recovery oriented: local deletes are retained in backup history, routine prune/delete is not performed by the server credential, and recovery is validated with small canary restore tests.

## AWS Security

- The server uses named AWS profiles instead of the root/default credential.
- `immich-backup` maps to IAM user `immich-backup-writer`.
- `homelab-backup` maps to IAM user `homelab-backup-writer`.
- Routine backup credentials can upload and read required backup objects, but cannot delete objects or change bucket policy, lifecycle, versioning, or Object Lock settings.
- The existing `bradmontierth` bucket has versioning enabled for the current homelab/Joplin/Paperless backup history.

## Immich Bucket

- Bucket: `bradmontierth-immich-backup`
- Region: `us-west-2`
- Public access block: enabled
- Default server-side encryption: SSE-S3
- Versioning: enabled
- Object Lock: enabled
- Default retention: not enabled
- Lifecycle: transition only `restic/data/` objects to Glacier Deep Archive after 14 days
- No expiration rules

Object Lock remains available on the bucket, but bucket-wide default retention is not enabled because restic creates temporary lock objects and needs to delete them. The routine IAM user can delete only `restic/locks/*`; it cannot delete backup data, index, snapshot, config, or key objects. Object Lock itself has no separate feature charge. The cost risk is retained versions if objects are overwritten repeatedly. Restic mostly writes content-addressed objects, so expected extra cost is small.

## Backup Scope

Included:

```text
/home/pi/immich/assets/upload
/home/pi/immich/assets/library
/home/pi/immich/assets/external
/home/pi/immich/assets/backups
/home/pi/immich/assets/profile
/home/pi/immich/docker-compose.yml
/home/pi/immich/docker-compose.override.yml
/home/pi/immich/.env
/home/pi/backups/immich-canary
```

Excluded:

```text
/home/pi/immich/assets/thumbs
/home/pi/immich/assets/encoded-video
/home/pi/immich/data/db
```

## Local Files

```text
/home/pi/scripts/immich_backup.sh
/home/pi/scripts/immich_backup/immich_restic_backup.sh
/home/pi/scripts/immich_backup/schema.sql
/home/pi/.config/immich-backup/backup.env
/home/pi/.config/immich-backup/restic-password
/home/pi/backups/immich-restic/logs
/home/pi/backups/immich-canary
/home/pi/backups/immich-restore-tests
```

## Commands

Run backup:

```bash
/home/pi/scripts/immich_backup.sh backup
```

Run backup plus hot canary restore:

```bash
/home/pi/scripts/immich_backup.sh backup-and-test
```

List snapshots:

```bash
/home/pi/scripts/immich_backup.sh snapshots
```

The script should be scheduled from the host Node-RED backup flow after Immich has produced its normal database dump.

Current schedule:

```text
Backup Flows tab: 04:30 daily
Command: /home/pi/scripts/immich_backup.sh backup
```

## Monitoring

Immich backup runs write to the existing `homelab_backup` MySQL database used by Grafana:

```text
immich_backup_runs
immich_backup_restore_checks
```

The run table records command, status, snapshot id, file counts, processed bytes, added bytes, log path, and errors. Restore checks record hot canary and metadata check results.

The `Homelab Backups` Grafana dashboard JSON includes an Immich section with status, hours since success, latest processed/added bytes, trends, recent runs, and restore checks. Re-import it with:

```bash
/home/pi/scripts/homelab_backup/grafana/import_dashboard.sh
```

That import requires `GRAFANA_PASSWORD` in `/home/pi/.config/homelab-backup/backup.env`.
If the env file does not define it, the current Grafana container also has `GF_SECURITY_ADMIN_PASSWORD` configured and can be used as the source for a one-off import without printing the password.

## Restore Testing

Monthly hot restore:

- Restore the canary directory from the latest snapshot.
- Verify `SHA256SUMS`.
- This confirms restic metadata, credentials, encryption, upload, and restore work while objects are still hot.

Cold restore every 3-6 months:

- Choose an older canary snapshot whose `restic/data/` packs have transitioned to Deep Archive.
- Request S3 restore for the needed archived pack objects.
- Wait for restore completion.
- Run `restic restore` for the canary directory.
- Verify `SHA256SUMS`.

The cold canary uses the same bucket, lifecycle, restic repository, encryption, and restore tooling as the real photo backup, avoiding a forked-path test.

## Cost Estimate

Current planned backup set is about 112.6 GB. In `us-west-2`, expected steady-state storage is roughly $0.11/month in Deep Archive plus pennies for hot restic metadata. A full disaster restore can cost several dollars plus internet egress. The 50 MB canary costs effectively pennies per year even with quarterly tests.
