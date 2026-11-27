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

# The live stack (compose project plm) belongs to the main checkout: from a git worktree
# (.git is a file there) a compose target needs its own COMPOSE_PROJECT_NAME.
if ($Target -in @('up', 'down') -and (Test-Path -LiteralPath (Join-Path $RepoRoot '.git') -PathType Leaf) `
        -and (-not $env:COMPOSE_PROJECT_NAME -or $env:COMPOSE_PROJECT_NAME -eq 'plm')) {
    Write-Host "make.ps1 '$Target' refused: this is a git worktree; set COMPOSE_PROJECT_NAME (not plm) so the live stack is untouched" -ForegroundColor Red
    exit 1
}

switch ($Target) {
    'up'        { & docker compose up -d; Stop-IfFailed $LASTEXITCODE }
    'down'      { & docker compose down; Stop-IfFailed $LASTEXITCODE }
    'logs'      { & docker compose logs -f; Stop-IfFailed $LASTEXITCODE }
    'test'      { & $Python -m pytest -q -m 'not integration'; Stop-IfFailed $LASTEXITCODE }
    'lint'      { & $Python -m ruff check .; Stop-IfFailed $LASTEXITCODE }
    'fmt'       { & $Python -m ruff format .; Stop-IfFailed $LASTEXITCODE }
    'typecheck' { & $Python -m mypy; Stop-IfFailed $LASTEXITCODE }
    'doctor'    {
        # The real Wave 0 gate now lives in the plm CLI (W0.12); run it via
        # `python -m` so PATH or an unactivated venv cannot pick a different tool.
        & $Python -m plm_platform.cli doctor
        Stop-IfFailed $LASTEXITCODE
    }
    'migrate'   { & $Python -m plm_platform.cli migrate; Stop-IfFailed $LASTEXITCODE }
}

Pop-Location
