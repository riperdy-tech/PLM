#requires -Version 5.1
# make.ps1 - Windows runner for the PLM monorepo tasks (mirrors ./Makefile).
# Usage: ./make.ps1 [target]
# Targets: up down logs test lint fmt typecheck doctor migrate

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'logs', 'test', 'lint', 'fmt', 'typecheck', 'doctor', 'migrate')]
    [string]$Target = 'up'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = $PSScriptRoot
Push-Location -LiteralPath $RepoRoot

$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Python = 'python'
if (Test-Path -LiteralPath $VenvPython) { $Python = $VenvPython }

function Stop-IfFailed([int]$Code) {
    if ($Code -ne 0) {
        Write-Host "make.ps1 '$Target' failed with exit code $Code" -ForegroundColor Red
        exit $Code
    }
}

function Show-Doctor {
    Write-Host '== plm doctor =='
    & $Python --version
    & docker --version
    & docker compose version

    if (Test-Path -LiteralPath (Join-Path $RepoRoot '.env')) {
        Write-Host '.env: present'
    } else {
        Write-Host '.env: missing - copy .env.example to .env'
    }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $server = (& docker info --format '{{.ServerVersion}}' 2>&1 | Out-String).Trim()
    } finally {
        $ErrorActionPreference = $previous
    }
    Write-Host "docker server: $server"

    foreach ($tool in @('ffmpeg', 'psql')) {
        if (Get-Command $tool -ErrorAction SilentlyContinue) {
            Write-Host "${tool}: on host"
        } else {
            Write-Host "${tool}: not on host (expected - provided inside the containers)"
        }
    }

    if (Test-Path -LiteralPath $VenvPython) {
        & $Python -c "import plm_contracts, plm_platform; print('plm packages importable')"
    } else {
        Write-Host '.venv: missing - run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"'
    }
}

switch ($Target) {
    'up'        { & docker compose up -d; Stop-IfFailed $LASTEXITCODE }
    'down'      { & docker compose down; Stop-IfFailed $LASTEXITCODE }
    'logs'      { & docker compose logs -f; Stop-IfFailed $LASTEXITCODE }
    'test'      { & $Python -m pytest -q; Stop-IfFailed $LASTEXITCODE }
    'lint'      { & $Python -m ruff check .; Stop-IfFailed $LASTEXITCODE }
    'fmt'       { & $Python -m ruff format .; Stop-IfFailed $LASTEXITCODE }
    'typecheck' { & $Python -m mypy; Stop-IfFailed $LASTEXITCODE }
    'doctor'    { Show-Doctor }
    'migrate'   { Write-Host "No alembic chain yet - a later Wave 0 task wires 'alembic upgrade head' here." }
}

Pop-Location
