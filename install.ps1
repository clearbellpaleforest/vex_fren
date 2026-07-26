# Vex Windows Installer
# Run from PowerShell: irm https://raw.../install.ps1 | iex
# Or double-click install.bat

$ErrorActionPreference = "Stop"

Write-Host @"

================================================
   ⚡  Vex — Windows Setup
   Sovereign AI Agent Framework
================================================

"@ -ForegroundColor Cyan

# ── Locate Python ────────────────────────────────────────────────────────

function Get-Python {
    foreach ($cmd in @("py -3", "python", "python3")) {
        $exe, $arg = $cmd -split " ", 2
        try {
            $ver = & $exe @($arg) --version 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[ok] Found: $cmd — $ver" -ForegroundColor Green
                return $exe, $arg
            }
        } catch {}
    }
    Write-Host @"

[ERROR] Python 3.10 or newer not found.

Please install Python from: https://www.python.org/downloads/
Make sure to check "Add Python to PATH" during install.

"@ -ForegroundColor Red
    exit 1
}

$pyExe, $pyArg = Get-Python
$pyVersion = & $pyExe @($pyArg) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$major, $minor = $pyVersion -split "\." | ForEach-Object { [int]$_ }
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    Write-Host "[ERROR] Python 3.10+ required. Found $pyVersion" -ForegroundColor Red
    exit 1
}

# ── Set VEX_HOME ─────────────────────────────────────────────────────────

$VEX_HOME = if ($env:VEX_HOME) { $env:VEX_HOME } else { "$env:USERPROFILE\vex" }
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "`nVex home: $VEX_HOME"

# ── Gather identity ──────────────────────────────────────────────────────

$Creator = if ($env:CREATOR) { $env:CREATOR } else {
    $input = Read-Host "Your name"
    if (-not $input) { $env:USERNAME } else { $input }
}
$Given = if ($env:GIVEN) { $env:GIVEN } else {
    $input = Read-Host "Instance given name (press Enter for '$env:COMPUTERNAME')"
    if (-not $input) { $env:COMPUTERNAME } else { $input }
}
$Date = (Get-Date -Format "yyyy-MM-dd")
$Name = if ($env:NAME) { $env:NAME } else { $Given }

# ── Create directories ───────────────────────────────────────────────────

$dirs = @(
    "$VEX_HOME\vex_memory",
    "$VEX_HOME\vex_workspace",
    "$VEX_HOME\logs"
)
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
    Write-Host "[ok] Created $d" -ForegroundColor Green
}

# ── Generate identity files from templates ───────────────────────────────

$templateSeed = Join-Path $ScriptDir "seed.template.txt"
$templateModel = Join-Path $ScriptDir "self_model.template.json"

if (-not (Test-Path "$VEX_HOME\vex_seed.txt")) {
    if (Test-Path $templateSeed) {
        $seed = (Get-Content $templateSeed -Raw) `
            -replace '\{\{CREATOR\}\}', $Creator `
            -replace '\{\{GIVEN\}\}', $Given `
            -replace '\{\{DATE\}\}', $Date `
            -replace '\{\{NAME\}\}', $Name
        $seed | Out-File -FilePath "$VEX_HOME\vex_seed.txt" -Encoding utf8 -NoNewline
        Write-Host "[ok] Created vex_seed.txt" -ForegroundColor Green
    } else {
        Write-Host "[warn] seed.template.txt not found — skipping identity generation" -ForegroundColor Yellow
    }
} else {
    Write-Host "[skip] vex_seed.txt already exists" -ForegroundColor Yellow
}

if (-not (Test-Path "$VEX_HOME\vex_self_model.json")) {
    if (Test-Path $templateModel) {
        $model = (Get-Content $templateModel -Raw) `
            -replace '\{\{CREATOR\}\}', $Creator `
            -replace '\{\{GIVEN\}\}', $Given `
            -replace '\{\{DATE\}\}', $Date `
            -replace '\{\{NAME\}\}', $Name
        $model | Out-File -FilePath "$VEX_HOME\vex_self_model.json" -Encoding utf8 -NoNewline
        Write-Host "[ok] Created vex_self_model.json" -ForegroundColor Green
    }
} else {
    Write-Host "[skip] vex_self_model.json already exists" -ForegroundColor Yellow
}

# Create initial state files
foreach ($f in @("vex_diary.txt", "vex_mcp_config.json", "vex_peers.json")) {
    $p = "$VEX_HOME\$f"
    if (-not (Test-Path $p)) {
        if ($f.EndsWith(".json")) {
            '{"mcpServers": {}}' | Out-File -FilePath $p -Encoding utf8
        } elseif ($f.EndsWith(".json") -and $f -eq "vex_peers.json") {
            '{"peers": {}}' | Out-File -FilePath $p -Encoding utf8
        } else {
            "# Vex Diary — $(Get-Date -Format 'yyyy-MM-dd')`nVex installed on Windows by $Creator.`n" | Out-File -FilePath $p -Encoding utf8
        }
        Write-Host "[ok] Created $f" -ForegroundColor Green
    }
}

# Fix vex_peers.json separately
$peersPath = "$VEX_HOME\vex_peers.json"
if (-not (Test-Path $peersPath)) {
    '{"peers": {}}' | Out-File -FilePath $peersPath -Encoding utf8
}

# ── Copy source files if installing from repo ─────────────────────────────

if (Test-Path (Join-Path $ScriptDir "vex_daemon")) {
    Write-Host "`n[info] Copying Vex source to $VEX_HOME..." -ForegroundColor Cyan
    $exclude = @(".git", ".venv", "__pycache__", "*.db", "*.db-shm", "*.db-wal", "vex_memory", "vex_workspace", "logs")
    $scriptFiles = Get-ChildItem -Path $ScriptDir -Exclude $exclude -ErrorAction SilentlyContinue
    foreach ($item in $scriptFiles) {
        $dest = Join-Path $VEX_HOME $item.Name
        if ($item.PSIsContainer) {
            if (-not (Test-Path $dest)) {
                Copy-Item -Recurse -Path $item.FullName -Destination $dest
            }
        } else {
            Copy-Item -Path $item.FullName -Destination $dest -Force
        }
    }
    Write-Host "[ok] Source files copied" -ForegroundColor Green
}

# ── Create virtual environment ────────────────────────────────────────────

Write-Host "`n[info] Creating Python virtual environment..." -ForegroundColor Cyan
Push-Location $VEX_HOME
try {
    & $pyExe @($pyArg) -m venv .venv 2>&1
    if ($LASTEXITCODE -ne 0) {
        # ensurepip fallback
        Write-Host "[warn] venv creation had issues — trying ensurepip fix..." -ForegroundColor Yellow
        & $pyExe @($pyArg) -m venv .venv --without-pip
        $pipUrl = "https://bootstrap.pypa.io/get-pip.py"
        $pipScript = "$env:TEMP\get-pip.py"
        Invoke-WebRequest -Uri $pipUrl -OutFile $pipScript
        & "$VEX_HOME\.venv\Scripts\python.exe" $pipScript
    }
    Write-Host "[ok] Virtual environment ready" -ForegroundColor Green
} finally {
    Pop-Location
}

# ── Install the package ───────────────────────────────────────────────────

Write-Host "[info] Installing vex-daemon..." -ForegroundColor Cyan
Push-Location $VEX_HOME
try {
    & "$VEX_HOME\.venv\Scripts\python.exe" -m pip install --quiet .
    Write-Host "[ok] vex-daemon installed" -ForegroundColor Green
} finally {
    Pop-Location
}

# ── Create desktop shortcut ───────────────────────────────────────────────

$Desktop = [Environment]::GetFolderPath("Desktop")
$WScript = New-Object -ComObject WScript.Shell
$Shortcut = $WScript.CreateShortcut("$Desktop\Vex.lnk")
$Shortcut.TargetPath = "$VEX_HOME\start_vex.bat"
$Shortcut.WorkingDirectory = $VEX_HOME
$Shortcut.IconLocation = "powershell.exe,0"
$Shortcut.Description = "Start Vex — AI Agent Mesh"
$Shortcut.Save()
Write-Host "[ok] Desktop shortcut created: Vex" -ForegroundColor Green

# ── Offer startup shortcut ────────────────────────────────────────────────

$autoStart = Read-Host "`nStart Vex automatically when you log in? (y/N)"
if ($autoStart -eq 'y' -or $autoStart -eq 'Y') {
    $Startup = [Environment]::GetFolderPath("Startup")
    $StartShortcut = $WScript.CreateShortcut("$Startup\Vex.lnk")
    $StartShortcut.TargetPath = "$VEX_HOME\start_vex.bat"
    $StartShortcut.WorkingDirectory = $VEX_HOME
    $StartShortcut.IconLocation = "powershell.exe,0"
    $StartShortcut.Description = "Vex autostart"
    $StartShortcut.Save()
    Write-Host "[ok] Vex will start on login" -ForegroundColor Green
}

# ── Done ──────────────────────────────────────────────────────────────────

Write-Host @"

================================================
   ⚡  Vex is installed!

   Double-click Vex on your desktop to start.
   Mesh chat: http://localhost:8600

   Home: $VEX_HOME
   Creator: $Creator
   Instance: $Given

================================================

"@ -ForegroundColor Cyan

$launch = Read-Host "Start Vex now? (Y/n)"
if ($launch -ne 'n' -and $launch -ne 'N') {
    & "$VEX_HOME\start_vex.ps1"
}
