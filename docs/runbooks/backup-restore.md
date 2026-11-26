# Backup and restore

PLM's state lives in three places, none of which can be rebuilt from git:

| What | Where | Holds |
| --- | --- | --- |
| Postgres cluster | compose volume `pgdata` | every phase's schema, the budget ledger, the exception queue, Temporal's databases |
| MinIO | compose volume `miniodata` | songs, video masters, thumbnails, P6 packages |
| `data\` state files | the main checkout | write grants, Studio notes, pipeline config, reviewer list |

Before 2026-11-26 there were only one-off dumps made before a migration, all inside
`data\` on the same disk as the data they protect. `scripts\backup_plm.ps1` makes a full,
repeatable backup.

## After every upload (automatic)

Owner decision 2026-11-27: no schedule. Each time a video is confirmed live on YouTube
(the pipeline's `await_processing` step), P6 archives it in the background:

1. Copies the video master and audio master to `E:\PLM-backups\videos\<package>\` and
   checks each copy against the SHA-256 its storage key pins.
2. Writes `manifest.json` there (original storage keys, hashes, sizes).
3. Only then deletes those renders from local MinIO (1.7-2.6 GB per video). Songs and
   thumbnails stay local: later playlists may reuse them.
4. Writes a database dump to `E:\PLM-backups\db\plm-<stamp>.dump` (the newest 10 are kept).

The publication never waits for or fails because of this. If the drive is unplugged
(no `E:\PLM-backups\.plm-archive` marker visible), nothing is copied or deleted; the
package is listed in `data\archive-pending.json` and goes with the next video's run. If a
package stays pending after the drive is back, restart Docker Desktop (it may still show
the container an empty folder) and it goes with the next upload. State:
`GET /media/archive` on api-media (127.0.0.1:8006, service token).

Configuration (main checkout `.env`): `PLM_ARCHIVE_HOST_DIR=E:/PLM-backups` (mounted into
api-media at `/archive`) and `PLM_ARCHIVE_DIR=/archive`. Unset, nothing is archived.

The Studio shows "Video file archived to the backup drive" for an archived video. To get
one back into MinIO, put the file back under its original key from `manifest.json`
(e.g. `mc cp` into `plm-media/<key>`).

## Make a backup

The stack must be running (Studio launcher or `docker compose up -d`). Nothing is
stopped; the run takes a few minutes, mostly the MinIO volume.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup_plm.ps1 -Destination E:\PLM-backups
```

- `-Destination` is required. It is refused on D: (failing drive) and inside the PLM
  checkout. A folder on C: protects against a bad delete, a wrong migration or a test
  that wrote to the real database, but **not** against losing the disk: for that, use an
  external drive or a cloud-synced folder.
- `-Keep 14` (default) keeps the newest 14 backups and deletes older `plm-*` folders
  under the destination.
- `-IncludeData` zips the whole `data\` folder (inboxes, logs, ~4.5 GB) instead of only
  its top-level state files.

Each run writes `plm-<UTC stamp>\` with `postgres.sql.gz`, `minio.tar.gz`,
`data-state.zip` and `manifest.json` (size and SHA-256 of each file).

### Schedule it

Daily at 04:00 with Windows Task Scheduler (run once in an elevated PowerShell; adjust
the paths):

```powershell
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File <PLM checkout>\scripts\backup_plm.ps1 -Destination E:\PLM-backups'
Register-ScheduledTask -TaskName 'PLM backup' -Trigger (New-ScheduledTaskTrigger -Daily -At 4am) -Action $a
```

## Check a backup

```powershell
$b = 'E:\PLM-backups\plm-20261126T040000Z'
(Get-Content "$b\manifest.json" | ConvertFrom-Json).files | ForEach-Object {
  $h = (Get-FileHash -Algorithm SHA256 "$b\$($_.file)").Hash.ToLower()
  '{0,-16} {1}' -f $_.file, $(if ($h -eq $_.sha256) { 'ok' } else { 'MISMATCH' }) }
```

A backup is only proven by a restore. Restore into the disposable Postgres
(`127.0.0.1:55432`, see `tests/support/migration_safety.py`) now and then, never into the
real one, to check the dump loads.

## Restore

Restoring replaces the current state. Make a fresh backup of the current state first,
even if it is broken, and check that no pipeline run is in progress.

1. Stop everything except the stores:

   ```powershell
   docker compose stop
   docker compose up -d postgres minio
   ```

2. Postgres (the dump has `--clean --if-exists`, so it drops and recreates each
   database):

   ```powershell
   $pg = docker ps -q --filter label=com.docker.compose.project=plm --filter label=com.docker.compose.service=postgres
   docker cp "$b\postgres.sql.gz" "${pg}:/tmp/restore.sql.gz"
   docker exec $pg sh -c 'gunzip -c /tmp/restore.sql.gz | psql -U plm -d postgres -v ON_ERROR_STOP=0 -q'
   docker exec $pg rm -f /tmp/restore.sql.gz
   ```

   Errors about the `plm` role already existing are expected.

3. MinIO (replace the volume's contents):

   ```powershell
   docker compose stop minio
   $v = docker volume ls -q --filter label=com.docker.compose.project=plm --filter label=com.docker.compose.volume=miniodata
   docker run --rm -v "${v}:/data" -v "${b}:/backup:ro" postgres:16 sh -c 'rm -rf /data/* /data/.[!.]* ; tar xzf /backup/minio.tar.gz -C /data'
   ```

4. `data\` state files: open `data-state.zip`, compare with the current files and copy
   back what is needed. Grants in `write-authorizations.json` may have expired; do not
   restore a grant you did not mean to give again.

5. Start the stack (`docker compose up -d` or the Studio launcher) and check the Studio
   shows the expected projects and videos.
