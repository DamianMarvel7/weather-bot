# Start the weather bot as a background process that survives terminal closes
$root    = $PSScriptRoot
$pidFile = "$root\logs\bot.pid"
$logFile = "$root\logs\bot.log"
$errFile = "$root\logs\bot_err.log"

# Check if already running
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        Write-Host "Bot is already running (PID $oldPid). Run stop_bot.ps1 first."
        exit
    }
}

$proc = Start-Process `
    -FilePath "uv" `
    -ArgumentList "run weatherbet.py" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError  $errFile `
    -PassThru

$proc.Id | Out-File $pidFile -Encoding ascii
Write-Host "Bot started (PID $($proc.Id))"
Write-Host "Logs: logs\bot.log"
Write-Host "Run 'uv run weatherbet.py status' to check positions"
