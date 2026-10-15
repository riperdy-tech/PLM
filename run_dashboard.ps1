# Serves the PLM dashboard. Live data (database + phase APIs) by default; -Demo serves the
# fixed demo sample instead. -ReviewersFile (or PLM_DASHBOARD_REVIEWERS_FILE) names the
# JSON reviewer directory that authorizes exception actions; without it they are refused.
param(
    [int]$Port = 0,
    [switch]$Demo,
    [string]$ReviewersFile = ""
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

$DashboardArgs = @("-m", "plm_platform.cli", "dashboard", "--port", "$Port")
if ($Demo) {
    $Banner = "  PLM Control Deck - DEMO DATA (not connected to the pipeline)"
    $DashboardArgs += "--demo"
} else {
    $Banner = "  PLM Control Deck - LIVE DATA (database and phase APIs)"
    if ($ReviewersFile -ne "") {
        $DashboardArgs += @("--reviewers-file", $ReviewersFile)
    }
}

Write-Host "==========================================================" -ForegroundColor Magenta
Write-Host $Banner -ForegroundColor Cyan
Write-Host "  URL: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Magenta

& $VenvPython @DashboardArgs
