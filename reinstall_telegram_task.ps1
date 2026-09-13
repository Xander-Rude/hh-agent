$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$TaskName = "HH Agent - Telegram"
$WatchdogTaskName = "HH Agent - Telegram Watchdog"
$Python = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Entry = Join-Path $Root "telegram_bot_entry.py"
$Watchdog = Join-Path $Root "telegram_watchdog.py"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

foreach ($Path in @($Python, $Entry, $Watchdog)) {
    if (-not (Test-Path $Path)) {
        throw "Не найден файл: $Path"
    }
}

foreach ($Name in @($TaskName, $WatchdogTaskName)) {
    try {
        Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    }
    catch {
    }

    try {
        Unregister-ScheduledTask `
            -TaskName $Name `
            -Confirm:$false `
            -ErrorAction SilentlyContinue
    }
    catch {
    }
}

$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Entry`"" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $UserId

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Force | Out-Null

$WatchdogAction = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Watchdog`"" `
    -WorkingDirectory $Root

$WatchdogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$WatchdogSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $WatchdogTaskName `
    -Action $WatchdogAction `
    -Trigger $WatchdogTrigger `
    -Settings $WatchdogSettings `
    -Principal $Principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Telegram task recreated and started."
Write-Host "Watchdog installed: check every 1 minute, stale heartbeat after 3 minutes."
Write-Host "Crash restart remains enabled every 1 minute."
