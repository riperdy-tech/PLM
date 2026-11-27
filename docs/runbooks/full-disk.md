# Full disk

Everything PLM stores locally is on C:: the Docker Desktop virtual disk (Postgres and
MinIO volumes: songs, video masters, thumbnails), the main checkout's `data\` folder and
the venv. D: is failing and is never used for PLM or Docker. A 1440p render of a
two-hour programme needs several GB of scratch space on top of its 1.7-2.6 GB master.

## Signs

- The Studio launcher warns "Only N GB is free on drive C:" (below 20 GB).
- A render fails with "No space left on device", or Postgres stops accepting writes
  (`could not extend file`, `No space left on device` in `docker compose logs postgres`).
- MinIO answers uploads with `XMinioStorageFull`.

## First: stop writing more

1. Do not start a new project. If a render is running, let it fail or finish; do not
   restart containers in the middle of one.
2. Check where the space went (read-only):

       Get-PSDrive C
       docker system df
       docker system df -v    # per volume: plm_pgdata, plm_miniodata

## Free space, safest first

1. **Archive published videos to the backup drive.** With `E:\PLM-backups` plugged in
   (it holds `.plm-archive`), P6 copies each published video's renders there and frees
   them from MinIO (docs/runbooks/backup-restore.md). Packages waiting for the drive are
   listed in `data\archive-pending.json`; they go with the next upload. Plug the drive in
   and restart Docker Desktop if a package stays pending.
2. **Docker build cache and dangling images** (never touches volumes):

       docker builder prune
       docker image prune

   Do not run `docker system prune --volumes` or `docker volume prune`: the PLM volumes
   hold the database and every song.
3. **Old one-off migration backups** in `data\migration_backups\`: once a newer backup in
   `E:\PLM-backups\db\` exists and was restore-tested, older folders there can be moved to
   the backup drive.
4. **Windows**: empty the recycle bin, Storage Sense, temporary files.

## Docker Desktop's disk image does not shrink by itself

Freed space inside the Docker VM is reused by Docker but not returned to C:. To return
it: stop the PLM stack when no run is active (`docker compose stop` in the main
checkout), then Docker Desktop, Settings, Resources, and use the disk-image clean-up /
"Reclaim space" option for your Docker Desktop version, then start the stack again.

## After

- `plm doctor` passes; the Studio opens.
- A run that failed for lack of space shows as held or failed in the Studio: see
  docs/runbooks/stuck-run.md.
