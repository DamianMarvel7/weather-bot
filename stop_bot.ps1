# Stop the background bot process
$pidFile = "$PSScriptRoot\logs\bot.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "No PID file found — bot may not be running."
    exit
}

$botPid = Get-Content $pidFile
$proc   = Get-Process -Id $botPid -ErrorAction SilentlyContinue

if ($proc) {
    Stop-Process -Id $botPid -Force
    Write-Host "Bot stopped (PID $botPid)"
} else {
    Write-Host "Bot process (PID $botPid) not found — already stopped."
}

Remove-Item $pidFile -ErrorAction SilentlyContinue
