# Launcher for PLM Visual Control Deck
$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython"
    exit 1
}

Write-Host "==========================================================" -ForegroundColor Magenta
Write-Host "  PLM Autonomous Music Engine - Visual Control Deck" -ForegroundColor Cyan
Write-Host "  URL: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Magenta

& $VenvPython -m plm_platform.cli dashboard @args
