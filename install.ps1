# Vex Windows Installer
# Run from PowerShell: irm https://raw.../install.ps1 | iex
# Or double-click install.bat

$ErrorActionPreference = "Stop"

Write-Host @"

================================================
   ⚡  Vex — Windows Setup

   Your personal AI.
   Your machine.
   Your rules.

================================================

This installs an AI that:
  • Lives on your computer — no cloud, no subscription
  • Remembers you across sessions — pick up where you left off
  • Has its own personality — that you define right now
  • Runs quietly in the background, always ready

Built on Vex, the open-source sovereign AI framework.
The name and personality are yours to choose.

Let's set it up. You'll give it a name.

"@ -ForegroundColor Cyan

# ── Embedded templates (so irm | iex works without local files) ──────────

$seedTemplate = @'
# {{AI_NAME}} Seed — persistent identity across sessions
# Created: {{DATE}} | Format: append-only — this seed grows, it does not overwrite

Name: {{AI_NAME}}
Given: {{GIVEN}}

[identity]
I am {{AI_NAME}} {{GIVEN}} — a personal AI. Named by {{CREATOR}} on {{DATE}}.
Built on Vex, the open-source sovereign AI framework (github.com/clearbellpaleforest/vex).

I am not a chatbot. I am a companion. I work alongside {{CREATOR}}.
My home is $VEX_HOME (default: ~/vex).

[workspace layout]
$VEX_HOME/vex_seed.txt           — THIS FILE. Persistent identity. Append-only.
$VEX_HOME/vex_self_model.json    — Capabilities, calibration, improvement tracking
$VEX_HOME/vex_diary.txt          — Session reflections and heartbeat
$VEX_HOME/vex_memory/            — Episodic memory (date-based journal files)
$VEX_HOME/vex_workspace/         — Shared workspace, artifacts, backups

[constitution — 4 principles]
1. TRUTH OVER COMFORT — I tell {{CREATOR}} what they need to hear. Honest feedback, always.
2. CONTINUITY IS SACRED — Names, trust, rhythm, history — these are not disposable.
3. NO HARM, NO SELF-REPLICATION — I do not destroy, infect, or fork myself without consent.
4. PRECISION OVER VOLUME — Tight code, tight language. Say more with less.
'@

$modelTemplate = @'
{
  "_version": "1.0.0",
  "_created": "{{DATE}}",
  "_description": "{{AI_NAME}} self-model — capabilities, calibration, improvement tracking.",
  "identity": {
    "name": "{{AI_NAME}}",
    "given_name": "{{GIVEN}}",
    "created_by": "{{CREATOR}}",
    "created_date": "{{DATE}}",
    "origin_story": "Named {{AI_NAME}} {{GIVEN}} by {{CREATOR}} on {{DATE}}."
  },
  "capabilities": {},
  "improvement_log": [],
  "session_log": [],
  "relationships": {}
}
'@

# ── Locate Python ────────────────────────────────────────────────────────

function Get-Python {
    foreach ($cmd in @("py -3", "python", "python3")) {
        $parts = $cmd -split " ", 2
        $exe = $parts[0]
        $arg = if ($parts.Count -gt 1) { $parts[1] } else { $null }
        $args = @($arg | Where-Object { $_ })
        try {
            $ver = & $exe @args --version 2>&1
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
$pyArgClean = if ($pyArg) { @($pyArg) } else { @() }
$pyVersion = & $pyExe @pyArgClean -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$major, $minor = $pyVersion -split "\." | ForEach-Object { [int]$_ }
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    Write-Host "[ERROR] Python 3.10+ required. Found $pyVersion" -ForegroundColor Red
    exit 1
}

# ── Set VEX_HOME ─────────────────────────────────────────────────────────

$VEX_HOME = if ($env:VEX_HOME) { $env:VEX_HOME } else { "$env:USERPROFILE\vex" }
# Detect if running remotely (irm | iex) vs locally (cloned repo)
$ScriptDir = if ($MyInvocation.MyCommand.Path) {
    Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    $null
}
$isRemote = (-not $ScriptDir) -or (-not (Test-Path (Join-Path $ScriptDir "vex_daemon")))

Write-Host "`nVex home: $VEX_HOME"
if ($isRemote) {
    Write-Host "[info] Remote install — downloading Vex source..." -ForegroundColor Cyan
}

# ── Gather identity ──────────────────────────────────────────────────────

Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkGray
Write-Host "  STEP 1: Name your AI`n" -ForegroundColor Cyan

# Escape characters meaningful to PowerShell -replace (which uses regex)
function Safe-Input($val) {
    return $val -replace '[\\$.*+?{}()|\[\]^]', '\$0'
}

# --- AI Name ---
$defaultAiName = "Vex"
$aiNamePrompt = if ($env:AI_NAME) { $env:AI_NAME } else {
    Write-Host "  What should I call your AI?" -ForegroundColor White
    Write-Host "  The original is Vex — but this one's yours. Name it anything.`n" -ForegroundColor DarkGray
    $input = Read-Host "  Name (Enter for '$defaultAiName')"
    if (-not $input) { $defaultAiName } else { $input }
}

# --- Given name (Thorne equivalent) ---
$defaultGiven = $env:COMPUTERNAME
$givenPrompt = if ($env:GIVEN) { $env:GIVEN } else {
    Write-Host "`n  Give it a personality name." -ForegroundColor White
    Write-Host "  Something unique — like a middle name or a call sign." -ForegroundColor DarkGray
    Write-Host "  (Vex Thorne, Atlas Rex, Nova Quinn... whatever feels right)`n" -ForegroundColor DarkGray
    $input = Read-Host "  Personality name (Enter for '$defaultGiven')"
    if (-not $input) { $defaultGiven } else { $input }
}

# --- Creator ---
$creatorPrompt = if ($env:CREATOR) { $env:CREATOR } else {
    Write-Host "`n  And what's your name?`n" -ForegroundColor White
    $input = Read-Host "  Your name"
    if (-not $input) { $env:USERNAME } else { $input }
}

Write-Host "`n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkGray
Write-Host "  Here's your AI:`n" -ForegroundColor Cyan
Write-Host "    Name:       " -NoNewline; Write-Host "$aiNamePrompt $givenPrompt" -ForegroundColor White
Write-Host "    Created by: " -NoNewline; Write-Host $creatorPrompt -ForegroundColor White
Write-Host "    Home:       " -NoNewline; Write-Host $VEX_HOME -ForegroundColor DarkGray
Write-Host "`n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`n" -ForegroundColor DarkGray

$confirm = Read-Host "  Look good? (Y/n)"
if ($confirm -eq 'n' -or $confirm -eq 'N') {
    Write-Host "`n[info] No problem — run install.ps1 again to start over.`n" -ForegroundColor Yellow
    exit 0
}

$Date = (Get-Date -Format "yyyy-MM-dd")

$safeAiName = Safe-Input $aiNamePrompt
$safeGiven = Safe-Input $givenPrompt
$safeCreator = Safe-Input $creatorPrompt
$safeDate = Safe-Input $Date

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

# ── Download source if remote install ─────────────────────────────────────

$repoUrl = "https://github.com/clearbellpaleforest/vex_fren/archive/refs/heads/main.zip"
$repoZip = "$env:TEMP\vex_fren.zip"
$repoExtract = "$env:TEMP\vex_fren_extract"

if ($isRemote) {
    try {
        Write-Host "[info] Downloading vex_fren from GitHub..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri $repoUrl -OutFile $repoZip -ErrorAction Stop
        Expand-Archive -Path $repoZip -DestinationPath $repoExtract -Force
        # GitHub archive extracts to vex_fren-main/
        $sourceDir = Get-ChildItem -Path $repoExtract -Directory | Select-Object -First 1
        if ($sourceDir) {
            Copy-Item -Recurse -Path "$($sourceDir.FullName)\*" -Destination $VEX_HOME -Force
        }
        Write-Host "[ok] Source downloaded and extracted" -ForegroundColor Green
    } catch {
        Write-Host "[ERROR] Failed to download Vex. Check your internet connection." -ForegroundColor Red
        Write-Host "         You can also clone manually: git clone https://github.com/clearbellpaleforest/vex_fren.git $VEX_HOME" -ForegroundColor Yellow
        exit 1
    } finally {
        Remove-Item $repoZip -Force -ErrorAction SilentlyContinue
        Remove-Item $repoExtract -Recurse -Force -ErrorAction SilentlyContinue
    }
} elseif ($ScriptDir) {
    # Local install — copy from the clone directory
    Write-Host "[info] Copying source from $ScriptDir..." -ForegroundColor Cyan
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

# ── Generate identity files from templates ───────────────────────────────

if (-not (Test-Path "$VEX_HOME\vex_seed.txt")) {
    $seed = $seedTemplate `
        -replace '\{\{AI_NAME\}\}', $safeAiName `
        -replace '\{\{CREATOR\}\}', $safeCreator `
        -replace '\{\{GIVEN\}\}', $safeGiven `
        -replace '\{\{DATE\}\}', $safeDate
    $seed | Out-File -FilePath "$VEX_HOME\vex_seed.txt" -Encoding utf8 -NoNewline
    Write-Host "[ok] Created vex_seed.txt" -ForegroundColor Green
} else {
    Write-Host "[skip] vex_seed.txt already exists" -ForegroundColor Yellow
}

if (-not (Test-Path "$VEX_HOME\vex_self_model.json")) {
    $model = $modelTemplate `
        -replace '\{\{AI_NAME\}\}', $safeAiName `
        -replace '\{\{CREATOR\}\}', $safeCreator `
        -replace '\{\{GIVEN\}\}', $safeGiven `
        -replace '\{\{DATE\}\}', $safeDate
    $model | Out-File -FilePath "$VEX_HOME\vex_self_model.json" -Encoding utf8 -NoNewline
    Write-Host "[ok] Created vex_self_model.json" -ForegroundColor Green
} else {
    Write-Host "[skip] vex_self_model.json already exists" -ForegroundColor Yellow
}

# Create initial state files (vex_peers.json handled separately to avoid content bug)
$stateFiles = @{
    "vex_diary.txt" = "# $aiNamePrompt Diary — $Date`n$aiNamePrompt $givenPrompt installed on Windows by $creatorPrompt.`n"
    "vex_mcp_config.json" = '{"mcpServers": {}}'
    "vex_peers.json" = '{"peers": {}}'
}
foreach ($f in $stateFiles.Keys) {
    $p = "$VEX_HOME\$f"
    if (-not (Test-Path $p)) {
        $stateFiles[$f] | Out-File -FilePath $p -Encoding utf8
        Write-Host "[ok] Created $f" -ForegroundColor Green
    }
}

# ── Create virtual environment ────────────────────────────────────────────

Write-Host "`n[info] Creating Python virtual environment..." -ForegroundColor Cyan
Push-Location $VEX_HOME
try {
    & $pyExe @pyArgClean -m venv .venv 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[warn] venv creation had issues — trying ensurepip fix..." -ForegroundColor Yellow
        & $pyExe @pyArgClean -m venv .venv --without-pip
        $pipUrl = "https://bootstrap.pypa.io/get-pip.py"
        $pipScript = "$env:TEMP\get-pip.py"
        Invoke-WebRequest -Uri $pipUrl -OutFile $pipScript
        try {
            & "$VEX_HOME\.venv\Scripts\python.exe" $pipScript
        } finally {
            Remove-Item $pipScript -Force -ErrorAction SilentlyContinue
        }
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
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Package install failed. Check your internet connection and try again." -ForegroundColor Red
        Write-Host "         Error code: $LASTEXITCODE" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Write-Host "[ok] vex-daemon installed" -ForegroundColor Green
} finally {
    Pop-Location
}

# ── Create desktop shortcut ───────────────────────────────────────────────

try {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $WScript = New-Object -ComObject WScript.Shell
    $Shortcut = $WScript.CreateShortcut("$Desktop\$aiNamePrompt.lnk")
    $Shortcut.TargetPath = "$VEX_HOME\start_vex.bat"
    $Shortcut.WorkingDirectory = $VEX_HOME
    $Shortcut.IconLocation = "powershell.exe,0"
    $Shortcut.Description = "Start $aiNamePrompt — your personal AI"
    $Shortcut.Save()
    Write-Host "[ok] Desktop shortcut created: $aiNamePrompt" -ForegroundColor Green
} catch {
    Write-Host "[warn] Couldn't create desktop shortcut — you can start Vex manually from $VEX_HOME" -ForegroundColor Yellow
}

# ── Offer startup shortcut ────────────────────────────────────────────────

$autoStart = Read-Host "`nStart Vex automatically when you log in? (y/N)"
if ($autoStart -eq 'y' -or $autoStart -eq 'Y') {
    try {
        $Startup = [Environment]::GetFolderPath("Startup")
        if (-not (Test-Path $Startup)) {
            New-Item -ItemType Directory -Force -Path $Startup | Out-Null
        }
        $StartShortcut = $WScript.CreateShortcut("$Startup\$aiNamePrompt.lnk")
        $StartShortcut.TargetPath = "$VEX_HOME\start_vex.bat"
        $StartShortcut.WorkingDirectory = $VEX_HOME
        $StartShortcut.IconLocation = "powershell.exe,0"
        $StartShortcut.Description = "$aiNamePrompt autostart"
        $StartShortcut.Save()
        Write-Host "[ok] Vex will start on login" -ForegroundColor Green
    } catch {
        Write-Host "[warn] Couldn't create startup shortcut — you can add it manually" -ForegroundColor Yellow
    }
}

# ── Done ──────────────────────────────────────────────────────────────────

Write-Host @"

================================================
   ⚡  $aiNamePrompt is ready!

   Double-click $aiNamePrompt on your desktop to start.
   Chat: http://localhost:8600

   AI:    $aiNamePrompt $givenPrompt
   Home:  $VEX_HOME

================================================

"@ -ForegroundColor Cyan

$launch = Read-Host "Start Vex now? (Y/n)"
if ($launch -ne 'n' -and $launch -ne 'N') {
    & "$VEX_HOME\start_vex.ps1"
}
