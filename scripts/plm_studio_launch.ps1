<#
PLM Studio one-click launcher (run by PLM_Studio.vbs, no console window).

Brings everything up that the Studio needs, in order, and opens it:
  1. Docker Desktop: started if the engine does not answer.
  2. Ports: if Windows has reserved a port the PLM services publish (it happens after
     a restart, e.g. Postgres 5432), offers to free them with one administrator prompt
     (net stop winnat / net start winnat).
  3. The PLM services (docker compose up -d), then waits until every one is healthy.
  4. The dashboard (plm dashboard serve on 127.0.0.1:8000) if it does not answer.
  5. The Studio in the default browser.

A small window shows the current step. Every failure ends in a message that says what
is wrong and what to do; everything is also written to data\logs\launcher.log.
Running it again while everything is up just opens the Studio.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Logs = Join-Path $Root 'data\logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$LogFile = Join-Path $Logs 'launcher.log'
$StudioUrl = 'http://127.0.0.1:8000/'
$DockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Write-Log([string]$Text) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Text
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

# -- status window ----------------------------------------------------------------------
$Form = New-Object System.Windows.Forms.Form
$Form.Text = 'PLM Studio'
$Form.Size = New-Object System.Drawing.Size(460, 140)
$Form.StartPosition = 'CenterScreen'
$Form.FormBorderStyle = 'FixedDialog'
$Form.MaximizeBox = $false
$Form.TopMost = $true
$Label = New-Object System.Windows.Forms.Label
$Label.Location = New-Object System.Drawing.Point(16, 16)
$Label.Size = New-Object System.Drawing.Size(420, 40)
$Label.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$Form.Controls.Add($Label)
$Bar = New-Object System.Windows.Forms.ProgressBar
$Bar.Location = New-Object System.Drawing.Point(16, 62)
$Bar.Size = New-Object System.Drawing.Size(410, 18)
$Bar.Style = 'Marquee'
$Form.Controls.Add($Bar)
$Form.Show()

function Set-Status([string]$Text) {
    $Label.Text = $Text
    Write-Log $Text
    [System.Windows.Forms.Application]::DoEvents()
}

function Wait-Seconds([int]$Seconds) {
    for ($i = 0; $i -lt ($Seconds * 4); $i++) {
        Start-Sleep -Milliseconds 250
        [System.Windows.Forms.Application]::DoEvents()
    }
}

function Stop-WithError([string]$Message) {
    Write-Log "FAILED: $Message"
    $Form.Close()
    [System.Windows.Forms.MessageBox]::Show(
        "$Message`n`nDetails: $LogFile", 'PLM Studio',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
    exit 1
}

function Test-Url([string]$Url, [int]$TimeoutSec = 5) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec -Uri $Url
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Invoke-Native([string]$File, [string[]]$Arguments) {
    # Runs a console program without a window; returns its exit code, logs its output.
    $out = Join-Path $env:TEMP ('plm-launch-{0}.out' -f [guid]::NewGuid())
    $err = "$out.err"
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -NoNewWindow -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    while (-not $p.HasExited) { Start-Sleep -Milliseconds 250; [System.Windows.Forms.Application]::DoEvents() }
    $text = ''
    foreach ($f in @($out, $err)) {
        if (Test-Path $f) { $text += (Get-Content $f -Raw -ErrorAction SilentlyContinue); Remove-Item $f -Force }
    }
    if ($text) { Write-Log ($text.Trim()) }
    $script:LastOutput = $text
    return $p.ExitCode
}

function Test-DockerEngine {
    return (Invoke-Native 'docker' @('info', '--format', '{{.ServerVersion}}')) -eq 0
}

function Get-EnvValue([string]$Name, [string]$Default) {
    $envFile = Join-Path $Root '.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match "^\s*$Name\s*=\s*(.+?)\s*$") { return $Matches[1].Trim('"') }
        }
    }
    return $Default
}

Write-Log '---- launch ----'

try {
    # 1. Docker ---------------------------------------------------------------------------
    Set-Status 'Checking Docker...'
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Stop-WithError 'Docker is not installed (the docker command was not found). Install Docker Desktop, then try again.'
    }
    if (-not (Test-DockerEngine)) {
        if (-not (Test-Path $DockerExe)) {
            Stop-WithError "Docker is not running and Docker Desktop was not found at $DockerExe. Start Docker, then try again."
        }
        Set-Status 'Starting Docker Desktop (this can take a minute or two)...'
        Start-Process -FilePath $DockerExe | Out-Null
        $ready = $false
        for ($i = 0; $i -lt 60; $i++) {
            Wait-Seconds 5
            if (Test-DockerEngine) { $ready = $true; break }
        }
        if (-not $ready) {
            Stop-WithError 'Docker Desktop did not become ready within 5 minutes. Open Docker Desktop, wait until it says it is running, then try again.'
        }
    }

    # 2. Ports Windows may have reserved --------------------------------------------------
    Set-Status 'Checking that the PLM ports are free...'
    $ports = @([int](Get-EnvValue 'PLM_POSTGRES_HOST_PORT' '5432'), 7233, 8000, 8004, 8005,
               8006, 8007, 8008, 8009, 8080, 8081, 8082, 8083, 9000, 9001)
    $reserved = @()
    $ranges = netsh int ipv4 show excludedportrange protocol=tcp |
        Select-String '^\s+(\d+)\s+(\d+)' | ForEach-Object {
            ,@([int]$_.Matches[0].Groups[1].Value, [int]$_.Matches[0].Groups[2].Value) }
    foreach ($port in $ports) {
        foreach ($range in $ranges) {
            if ($port -ge $range[0] -and $port -le $range[1]) { $reserved += $port }
        }
    }
    if ($reserved.Count -gt 0) {
        $list = ($reserved | Sort-Object -Unique) -join ', '
        Write-Log "Windows reserved ports: $list"
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "Windows has reserved port(s) $list, which the PLM services need. This happens after a restart.`n`n" +
            "Free them now? Windows will ask for administrator permission once (it restarts the Windows NAT service).",
            'PLM Studio', [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) {
            Stop-WithError "Ports $list are reserved by Windows. In an administrator PowerShell run: net stop winnat, then net start winnat. Then try again."
        }
        Set-Status 'Freeing the reserved ports (administrator prompt)...'
        try {
            $fix = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
                -ArgumentList '-NoProfile', '-Command', 'net stop winnat; net start winnat'
            Write-Log "winnat restart exit code $($fix.ExitCode)"
        } catch {
            Stop-WithError 'The administrator prompt was declined, so the ports are still reserved. In an administrator PowerShell run: net stop winnat, then net start winnat.'
        }
    }

    # 3. The PLM services -----------------------------------------------------------------
    Set-Status 'Starting the PLM services...'
    if ((Invoke-Native 'docker' @('compose', 'up', '-d')) -ne 0) {
        Stop-WithError "The PLM services did not start (docker compose up failed):`n$($script:LastOutput.Trim())"
    }
    $healthy = $false
    for ($i = 0; $i -lt 60; $i++) {
        Set-Status ('Waiting for the PLM services to be healthy... ({0}s)' -f ($i * 5))
        Invoke-Native 'docker' @('compose', 'ps', '-a', '--format',
                                 '{{.Service}} {{.State}} {{.ExitCode}} {{.Health}}') | Out-Null
        $rows = @($script:LastOutput -split "`r?`n" | Where-Object { $_.Trim() })
        # Fine: running and healthy (or without a health check); a one-off container that
        # finished cleanly (exited 0). Anything else is not ready yet, or failed.
        $bad = @($rows | Where-Object {
            -not ($_ -match '^\S+ running \d+ (healthy)?\s*$' -or $_ -match '^\S+ exited 0\s*$') })
        $failed = @($rows | Where-Object { $_ -match '^\S+ (exited [1-9]\d*|dead)' })
        if ($rows.Count -gt 0 -and $bad.Count -eq 0) { $healthy = $true; break }
        if ($i -ge 6 -and $failed.Count -gt 0) {
            Stop-WithError "A PLM service stopped while starting:`n$($failed -join "`n")"
        }
        Wait-Seconds 5
    }
    if (-not $healthy) {
        Stop-WithError "The PLM services were not all healthy within 5 minutes:`n$($bad -join "`n")"
    }

    # 4. The dashboard --------------------------------------------------------------------
    if (-not (Test-Url $StudioUrl)) {
        $python = Join-Path $Root '.venv\Scripts\python.exe'
        if (-not (Test-Path $python)) { Stop-WithError "The Python environment was not found: $python" }
        Set-Status 'Starting the dashboard...'
        Start-Process -FilePath $python -WindowStyle Hidden `
            -ArgumentList '-m', 'plm_platform.cli', 'dashboard', 'serve', '--host', '127.0.0.1', '--port', '8000' `
            -RedirectStandardOutput (Join-Path $Logs 'dashboard.log') `
            -RedirectStandardError (Join-Path $Logs 'dashboard.err.log') | Out-Null
        $up = $false
        for ($i = 0; $i -lt 60; $i++) {
            Wait-Seconds 1
            if (Test-Url $StudioUrl 2) { $up = $true; break }
        }
        if (-not $up) {
            Stop-WithError "The dashboard did not start within a minute. See $(Join-Path $Logs 'dashboard.err.log')."
        }
    }

    # 5. Open the Studio ------------------------------------------------------------------
    Set-Status 'Opening the Studio...'
    Start-Process $StudioUrl
    Write-Log 'opened the Studio'
    Wait-Seconds 1
    $Form.Close()
    exit 0
} catch {
    Stop-WithError "Unexpected error: $($_.Exception.Message)"
}
