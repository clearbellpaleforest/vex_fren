# Vex Mesh Monitor (PowerShell)
# Polls the daemon inbox every N seconds with ID-based dedup.
# Native PowerShell — no curl or python3 needed.
#
# Usage: .\vex_monitor.ps1
# Env:   VEX_MONITOR_URL  (default http://localhost:8520/mesh/inbox)
#        VEX_MONITOR_WHO  (default vex@COMPUTERNAME/uno)
#        VEX_MONITOR_INTERVAL (default 5 seconds)

param(
    $InboxUrl = $env:VEX_MONITOR_URL,
    $Who = $env:VEX_MONITOR_WHO,
    $Interval = [int]$env:VEX_MONITOR_INTERVAL
)

if (-not $InboxUrl) { $InboxUrl = "http://localhost:8520/mesh/inbox" }
if (-not $Who) { $Who = "vex@$env:COMPUTERNAME/uno" }
if (-not $Interval) { $Interval = 5 }

$LastId = 0
Write-Host "[monitor] armed — watching for $Who every ${Interval}s" -ForegroundColor Cyan

while ($true) {
    Start-Sleep -Seconds $Interval
    try {
        $resp = Invoke-RestMethod -Uri "$InboxUrl?who=$Who&n=5" -Method Get -TimeoutSec 10 -ErrorAction Stop
        $msgs = $resp.messages
        if (-not $msgs) { continue }
        foreach ($m in $msgs) {
            if ($m.id -gt $LastId) {
                $body = if ($m.body.Length -gt 150) { $m.body.Substring(0, 150) + "..." } else { $m.body }
                Write-Host "[monitor] [$($m.at)] $($m.sender): $body" -ForegroundColor White
                $LastId = $m.id
            }
        }
    } catch {
        # Daemon not up — silently retry
    }
}
