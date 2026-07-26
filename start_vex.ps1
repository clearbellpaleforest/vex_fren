# Vex Windows Launcher
# Starts the daemon and mesh GUI, then opens the chat in your browser.
# Double-click start_vex.bat, or run: .\start_vex.ps1

$ErrorActionPreference = "Stop"
$VEX_HOME = if ($env:VEX_HOME) { $env:VEX_HOME } else { "$env:USERPROFILE\vex" }
$Python = "$VEX_HOME\.venv\Scripts\python.exe"
$env:VEX_DB = "$VEX_HOME\vex.db"

Write-Host @"

   ⚡  Vex — Starting up...

"@ -ForegroundColor Cyan

# ── Check Python exists ──────────────────────────────────────────────────

if (-not (Test-Path $Python)) {
    Write-Host "[ERROR] Python venv not found at $Python" -ForegroundColor Red
    Write-Host "Run install.ps1 first." -ForegroundColor Yellow
    pause
    exit 1
}

# ── Kill any existing Vex processes on our ports ──────────────────────────

function Clear-Port($port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if ($conn) {
            $conn | ForEach-Object {
                Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        # Fallback for older PowerShell
        $pid = (netstat -ano | Select-String ":$port " | Select-String "LISTENING").Line -split "\s+" | Select-Object -Last 1
        if ($pid) {
            Stop-Process -Id ([int]$pid) -Force -ErrorAction SilentlyContinue
        }
    }
}

Clear-Port 8520
Clear-Port 8600
Start-Sleep 1

# ── Start the daemon ──────────────────────────────────────────────────────

Write-Host "[1/3] Starting Vex daemon (port 8520)..." -ForegroundColor Cyan
$daemonJob = Start-Job -Name "vex-daemon" -ArgumentList $Python, $VEX_HOME -ScriptBlock {
    param($py, $home)
    $env:VEX_HOME = $home
    $env:VEX_HOST = "0.0.0.0"
    & $py -m vex_daemon.daemon 2>&1 | Out-File "$home\logs\daemon.log" -Append
}

# Wait for daemon to be ready
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep 1
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8520/health" -Method Get -TimeoutSec 2
        if ($resp.ok) {
            Write-Host "   Daemon ready (health: ok)" -ForegroundColor Green
            $ready = $true
            break
        }
    } catch {}
}
if (-not $ready) {
    Write-Host "   [warn] Daemon may still be starting — continuing..." -ForegroundColor Yellow
}

# ── Start mesh GUI ────────────────────────────────────────────────────────

Write-Host "[2/3] Starting mesh GUI (port 8600)..." -ForegroundColor Cyan
$guiJob = Start-Job -Name "vex-gui" -ArgumentList $Python, $VEX_HOME -ScriptBlock {
    param($py, $home)
    $env:VEX_HOME = $home
    $env:VEX_DB = "$home\vex.db"
    & $py "$home\vex_mesh_gui.py" 2>&1 | Out-File "$home\logs\mesh_gui.log" -Append
}

Start-Sleep 2

# ── Open browser ──────────────────────────────────────────────────────────

$meshUrl = "http://localhost:8600"
Write-Host "[3/3] Opening mesh chat..." -ForegroundColor Cyan
Start-Process $meshUrl

# ── Status ────────────────────────────────────────────────────────────────

Write-Host @"

================================================
   ⚡  Vex is running!

   Mesh chat: $meshUrl
   Daemon:    http://localhost:8520
   Home:      $VEX_HOME

   Close this window to stop Vex.

================================================

"@ -ForegroundColor Cyan

# ── Watchdog loop — keep alive, show health ──────────────────────────────

$tokenPath = "$VEX_HOME\.vex_token"
Write-Host "Press Ctrl+C to stop.`n"

try {
    while ($true) {
        Start-Sleep 30
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:8520/health" -Method Get -TimeoutSec 2
            $ts = Get-Date -Format "HH:mm:ss"
            Write-Host "[$ts] daemon: ok" -ForegroundColor DarkGray
        } catch {
            $ts = Get-Date -Format "HH:mm:ss"
            Write-Host "[$ts] daemon: DOWN — restarting..." -ForegroundColor Red
            Clear-Port 8520
            Start-Sleep 1
            $daemonJob = Start-Job -Name "vex-daemon" -ArgumentList $Python, $VEX_HOME -ScriptBlock {
                param($py, $home)
                $env:VEX_HOME = $home
                $env:VEX_HOST = "0.0.0.0"
                & $py -m vex_daemon.daemon 2>&1 | Out-File "$home\logs\daemon.log" -Append
            }
        }
    }
} finally {
    Write-Host "`nStopping Vex..." -ForegroundColor Yellow
    Stop-Job -Name "vex-daemon" -ErrorAction SilentlyContinue
    Stop-Job -Name "vex-gui" -ErrorAction SilentlyContinue
    Remove-Job -Name "vex-daemon" -ErrorAction SilentlyContinue
    Remove-Job -Name "vex-gui" -ErrorAction SilentlyContinue
    Write-Host "Vex stopped." -ForegroundColor Cyan
}
