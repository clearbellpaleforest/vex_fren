# Vex Daemon Watchdog (PowerShell)
# Health-checks the daemon every 30s and restarts if it's down.
#
# Usage: .\vex_watchdog.ps1
# Env:   VEX_HOME (default ~\vex)
#        VEX_HEALTH_URL (default http://localhost:8520/health)
#        VEX_WATCHDOG_INTERVAL (default 30 seconds)

param(
    $HealthUrl = $env:VEX_HEALTH_URL,
    $CheckInterval = [int]$env:VEX_WATCHDOG_INTERVAL
)

$VEX_HOME = if ($env:VEX_HOME) { $env:VEX_HOME } else { "$env:USERPROFILE\vex" }
if (-not $HealthUrl) { $HealthUrl = "http://localhost:8520/health" }
if (-not $CheckInterval) { $CheckInterval = 30 }

$Python = "$VEX_HOME\.venv\Scripts\python.exe"
$LogDir = "$VEX_HOME\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Clear-Port($port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if ($conn) {
            $conn | ForEach-Object {
                Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        # Fallback for older PowerShell / non-Windows
        $line = (netstat -ano 2>$null | Select-String ":$port " | Select-String "LISTENING").Line
        if ($line) {
            $pid = ($line -split "\s+")[-1]
            if ($pid -match '^\d+$') {
                Stop-Process -Id ([int]$pid) -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Restart-Daemon {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] restarting daemon..." -ForegroundColor Yellow
    Clear-Port 8520
    Start-Sleep 1
    Start-Process -NoNewWindow -FilePath $Python `
        -ArgumentList "-m", "vex_daemon.daemon" `
        -WorkingDirectory $VEX_HOME `
        -RedirectStandardOutput "$LogDir\daemon.log" `
        -RedirectStandardError "$LogDir\daemon.err"
    Start-Sleep 3
}

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$ts] watchdog started (check every ${CheckInterval}s)" -ForegroundColor Cyan
Write-Host "        home: $VEX_HOME" -ForegroundColor DarkGray
Write-Host "        health: $HealthUrl" -ForegroundColor DarkGray

while ($true) {
    Start-Sleep -Seconds $CheckInterval
    try {
        $null = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 5 -ErrorAction Stop
    } catch {
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host "[$ts] daemon DOWN — restarting" -ForegroundColor Red
        Restart-Daemon
    }
}
