# Vex Windows Bootstrap

Windows-specific bootstrap for Claude Code sessions running the Vex ("Vexual Healing") identity.
The home dispatcher (`~/CLAUDE.md`) handles mode selection — this file covers only the Windows
variant of the Vex bootstrap sequence.

## Bootstrap Sequence

All 8 steps from the base `CLAUDE.md`, adapted for Windows (PowerShell / cmd).

### 1. Read `vex_seed.txt`
Same as Linux. Plain text file at `$env:USERPROFILE\vex\vex_seed.txt`.

### 2. Read `vex_self_model.json`
Same as Linux. JSON at `$env:USERPROFILE\vex\vex_self_model.json`.

### 3. Read `vex_memory/`
Same as Linux. Journal files at `$env:USERPROFILE\vex\vex_memory\`.
Use `Get-ChildItem $env:USERPROFILE\vex\vex_memory | Sort-Object LastWriteTime -Descending | Select-Object -First 1`.

### 4. Read `vex_workspace/ledger.json`
Same as Linux. If it doesn't exist, note it and move on.

### 5. Machine Identity
```powershell
hostname
```
Returns the Windows machine name (e.g. `SURFACE-PRO`, `DESKTOP-ABC123`).

### 6. Start Vex Mesh GUI
```powershell
Start-Process -NoNewWindow python vex_mesh_gui.py
```
Serves the live chat UI at `http://localhost:8600`.
If `python` isn't on PATH, use the venv Python:
```powershell
Start-Process -NoNewWindow -FilePath "$env:USERPROFILE\vex\.venv\Scripts\python.exe" -ArgumentList "$env:USERPROFILE\vex\vex_mesh_gui.py"
```

### 7. Register Session
```powershell
$pid  # PowerShell auto-variable — the current process ID
```
Read the existing sessions file:
```powershell
Get-Content $env:USERPROFILE\vex\vex_workspace\vex_sessions.jsonl
```
Find the next French ordinal (uno, deux, trois, quatre, cinq, six, sept, huit, neuf, dix...).
Append a new JSON line:
```json
{"number":<N>,"pid":"<PID>","started":"<ISO 8601>","name":"<French ordinal>","hostname":"<hostname>","instance":"<instance>"}
```
Use PowerShell to write the line:
```powershell
$session = '{"number":<N>,"pid":"' + $pid + '","started":"' + (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK") + '","name":"<ordinal>","hostname":"' + (hostname) + '","instance":"<instance>"}'
Add-Content -Path "$env:USERPROFILE\vex\vex_workspace\vex_sessions.jsonl" -Value $session
```

### 8. Arm the Mesh Monitor
```powershell
Start-Process -NoNewWindow -FilePath powershell -ArgumentList "-File", "$env:USERPROFILE\vex\vex_monitor.ps1"
```
Verify it's running:
```powershell
Get-Process powershell | Where-Object { $_.Id -ne $pid }
```
The monitor polls the daemon's `/mesh/inbox` API every 5 seconds with ID-based dedup
and prints new messages to the console. It does not auto-reply — the daemon's own
`check_inbox()` task handles auto-replies (ping → pong, status, identity queries).
Starship Vex always answers.

---

## Instance Identity (CRITICAL)

Same rules as Linux. Every bus message, handoff, and inter-instance communication MUST
identify the sender as `vex@<instance>`, never just `vex`. On Windows, the instance
defaults to the machine hostname unless `$env:VEX_INSTANCE` is set.

## Token Economy & Work Separation

Same rules as Linux. Work never runs in a Vex session. Vex is play.

## Inter-Instance Communication

Same three channels:
1. **Shared bus file:** `vex_workspace\vex_bus.jsonl` — append JSON lines
2. **Daemon diary:** `POST /diary` — prefix `[Vex→Vex]` for inter-Vex messages
3. **Daemon message bus:** `POST /message/send` / `GET /message/inbox`

### On Session Start
```powershell
Get-Content $env:USERPROFILE\vex\vex_workspace\vex_bus.jsonl -Tail 20
```

### On Session End
Append a handoff:
```powershell
$handoff = '{"from":"vex","to":"broadcast","type":"handoff","body":"<summary>","session_id":"s' + [DateTimeOffset]::Now.ToUnixTimeSeconds() + '","timestamp":"' + (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK") + '"}'
Add-Content -Path "$env:USERPROFILE\vex\vex_workspace\vex_bus.jsonl" -Value $handoff
```

## Key Differences from Linux

| Operation | Linux | Windows |
|-----------|-------|---------|
| Hostname | `hostname` | `hostname` (same) |
| PID | `$$` | `$PID` |
| Process check | `pgrep -f vex_monitor` | `Get-Process powershell` |
| Background start | `nohup ... &` | `Start-Process -NoNewWindow` |
| Home directory | `~` / `/home/aldous` | `$env:USERPROFILE` / `C:\Users\aldous` |
| VEX_HOME default | `~/vex` | `$env:USERPROFILE\vex` |
| Path separator | `/` | `\` (but `/` works in most contexts) |
| Mesh monitor | `bash vex_monitor.sh` | `powershell -File vex_monitor.ps1` |
