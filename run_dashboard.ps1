param(
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython"
    exit 1
}

function Test-PortInUse([int]$p) {
    $conn = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue
    return ($null -ne $conn)
}

if ($Port -eq 0) {
    if (-not (Test-PortInUse 8000)) {
        $Port = 8000
    } elseif (-not (Test-PortInUse 8050)) {
        $Port = 8050
    } elseif (-not (Test-PortInUse 8080)) {
        $Port = 8080
    } else {
        $Port = 8888
    }
}

Write-Host "==========================================================" -ForegroundColor Magenta
Write-Host "  PLM Control Deck - DEMO DATA (not connected to the pipeline)" -ForegroundColor Cyan
Write-Host "  URL: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Magenta

& $VenvPython -m plm_platform.cli dashboard --port $Port
