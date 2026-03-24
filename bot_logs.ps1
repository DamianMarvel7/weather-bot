# Tail the bot log — shows last 50 lines and follows new output
$logFile = "$PSScriptRoot\logs\bot.log"

if (-not (Test-Path $logFile)) {
    Write-Host "No log file yet. Start the bot first with start_bot.ps1"
    exit
}

Get-Content $logFile -Tail 50 -Wait
