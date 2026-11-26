<#
Back up everything PLM cannot rebuild: the Postgres cluster (every database: plm and
Temporal's, plus roles), the MinIO object store (songs, videos, thumbnails), and the
repo's data\ folder state files (grants, Studio notes, pipeline config). Media inboxes
and logs are skipped (-IncludeData copies the whole data\ folder instead).

Each run writes one folder <Destination>\plm-<UTC stamp>\ with:
  postgres.sql.gz   pg_dumpall of the running postgres service
  minio.tar.gz      the MinIO data volume
  data-state.zip    data\*.json (and the env/grant snapshots next to them)
  manifest.json     sizes and SHA-256 of the three files
and then keeps only the newest -Keep backup folders.

Read-only on PLM: nothing is stopped, written or deleted except old backup folders under
-Destination. Restore: docs/runbooks/backup-restore.md.

  powershell -ExecutionPolicy Bypass -File scripts\backup_plm.ps1 -Destination E:\PLM-backups
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [ValidateRange(1, 365)][int]$Keep = 14,
    [switch]$IncludeData
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

$Destination = [System.IO.Path]::GetFullPath($Destination)
if ($Destination -match '^[Dd]:') {
    throw "D: is failing (read errors since 2026-11-25); choose another drive for backups."
}
$rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
if (($Destination.TrimEnd('\') + '\').StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination is inside the PLM checkout; a backup must live outside it."
}

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$target = Join-Path $Destination "plm-$stamp"
New-Item -ItemType Directory -Force -Path $target | Out-Null
Write-Host "Backing up to $target"

# 1. Postgres: dump inside the container (no PowerShell pipe: it would re-encode the
#    bytes), then copy the file out.
$pg = (& docker ps -q --filter 'label=com.docker.compose.project=plm' --filter 'label=com.docker.compose.service=postgres' | Out-String).Trim()
if (-not $pg) { throw "the plm postgres service is not running (start the stack first)" }
Invoke-Docker @('exec', $pg, 'sh', '-c',
    'pg_dumpall -U plm --clean --if-exists | gzip > /tmp/plm-backup.sql.gz')
Invoke-Docker @('cp', "${pg}:/tmp/plm-backup.sql.gz", (Join-Path $target 'postgres.sql.gz'))
Invoke-Docker @('exec', $pg, 'rm', '-f', '/tmp/plm-backup.sql.gz')

# 2. MinIO: tar the data volume read-only from a throwaway container of the postgres
#    image (already present; it has tar and gzip). Objects are written once and never
#    changed in place, so a copy taken while the service runs is consistent per object.
$volume = (& docker volume ls -q --filter 'label=com.docker.compose.project=plm' --filter 'label=com.docker.compose.volume=miniodata' | Out-String).Trim()
if (-not $volume) { throw "the plm MinIO volume was not found" }
Invoke-Docker @('run', '--rm', '-v', "${volume}:/data:ro", '-v', "${target}:/backup",
    'postgres:16', 'tar', 'czf', '/backup/minio.tar.gz', '-C', '/data', '.')

# 3. The repo's own state files.
$data = Join-Path $Root 'data'
$zip = Join-Path $target 'data-state.zip'
if ($IncludeData) {
    Compress-Archive -Path (Join-Path $data '*') -DestinationPath $zip
} else {
    $files = Get-ChildItem -Path $data -File | Where-Object { $_.Extension -ne '.py' }
    if ($files) { Compress-Archive -Path $files.FullName -DestinationPath $zip }
}

# 4. Manifest: what was written, so a restore can check it is whole.
$entries = foreach ($f in Get-ChildItem -Path $target -File) {
    [ordered]@{ file = $f.Name; bytes = $f.Length
                sha256 = (Get-FileHash -Algorithm SHA256 -Path $f.FullName).Hash.ToLower() }
}
$manifest = [ordered]@{ created_utc = $stamp; host = $env:COMPUTERNAME; files = @($entries) }
$manifest | ConvertTo-Json -Depth 4 | Out-File -FilePath (Join-Path $target 'manifest.json') -Encoding utf8
foreach ($e in $entries) { if ($e.bytes -le 0) { throw "$($e.file) is empty: backup incomplete" } }

# 5. Keep the newest -Keep backups.
Get-ChildItem -Path $Destination -Directory -Filter 'plm-*' |
    Sort-Object Name -Descending | Select-Object -Skip $Keep |
    ForEach-Object { Remove-Item -Recurse -Force -Confirm:$false $_.FullName }

Write-Host "Done:"
$entries | ForEach-Object { Write-Host ("  {0,-16} {1,14:N0} bytes" -f $_.file, $_.bytes) }
