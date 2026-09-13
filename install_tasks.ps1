$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

$PipelineTask = "HH Agent - Pipeline"
$ApplyTask = "HH Agent - Apply"
$TelegramTask = "HH Agent - Telegram"
$TelegramWatchdogTask = "HH Agent - Telegram Watchdog"

$PipelineScript = Join-Path $Root "run_pipeline.ps1"
$ApplyScript = Join-Path $Root "run_apply.ps1"
$TelegramPython = Join-Path $Root ".venv\Scripts\pythonw.exe"
$TelegramEntry = Join-Path $Root "telegram_bot_entry.py"
$TelegramWatchdog = Join-Path $Root "telegram_watchdog.py"

foreach ($Path in @(
    $PipelineScript,
    $ApplyScript,
    $TelegramPython,
    $TelegramEntry,
    $TelegramWatchdog
)) {
    if (-not (Test-Path $Path)) {
        throw "File not found: $Path"
    }
}

# Remove old versions if present.
foreach ($TaskName in @(
    $PipelineTask,
    $ApplyTask,
    $TelegramTask,
    $TelegramWatchdogTask
)) {
    try {
        Unregister-ScheduledTask `
            -TaskName $TaskName `
            -Confirm:$false `
            -ErrorAction Stop
    }
    catch {
        # First install: task may not exist.
    }
}

$UserId = "$env:USERDOMAIN\$env:USERNAME"

# Shared principal: run only while this interactive user is logged on.
$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

# ---------------- Pipeline ----------------
$PipelineAction = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument (
        "-NoProfile -NonInteractive -WindowStyle Hidden " +
        "-ExecutionPolicy Bypass -File `"$PipelineScript`""
    )

$PipelineTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$PipelineSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $PipelineTask `
    -Action $PipelineAction `
    -Trigger $PipelineTrigger `
    -Settings $PipelineSettings `
    -Principal $Principal `
    -Force | Out-Null

# ---------------- Apply worker ----------------
$ApplyAction = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument (
        "-NoProfile -NonInteractive -WindowStyle Hidden " +
        "-ExecutionPolicy Bypass -File `"$ApplyScript`""
    )

$ApplyTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$ApplySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $ApplyTask `
    -Action $ApplyAction `
    -Trigger $ApplyTrigger `
    -Settings $ApplySettings `
    -Principal $Principal `
    -Force | Out-Null

# ---------------- Telegram bot ----------------
# Launch pythonw.exe directly. Scheduling powershell.exe first can still expose
# a console window at interactive logon even when -WindowStyle Hidden is used.
$TelegramAction = New-ScheduledTaskAction `
    -Execute $TelegramPython `
    -Argument "`"$TelegramEntry`"" `
    -WorkingDirectory $Root

$TelegramTrigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $UserId

# Important:
# - restart bot one minute after failure;
# - retry many times;
# - do not launch duplicate instances;
# - no 72-hour forced stop;
# - allow running on battery.
$TelegramSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TelegramTask `
    -Action $TelegramAction `
    -Trigger $TelegramTrigger `
    -Settings $TelegramSettings `
    -Principal $Principal `
    -Force | Out-Null

# ---------------- Telegram watchdog ----------------
# Checks the event-loop heartbeat every minute. If the heartbeat is stale or
# the recorded Telegram PID is gone, only the Telegram task is restarted.
$TelegramWatchdogAction = New-ScheduledTaskAction `
    -Execute $TelegramPython `
    -Argument "`"$TelegramWatchdog`"" `
    -WorkingDirectory $Root

$TelegramWatchdogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$TelegramWatchdogSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TelegramWatchdogTask `
    -Action $TelegramWatchdogAction `
    -Trigger $TelegramWatchdogTrigger `
    -Settings $TelegramWatchdogSettings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host ""
Write-Host "Created scheduled tasks:"
Write-Host "  $PipelineTask          - every 2 hours"
Write-Host "  $ApplyTask             - every 10 minutes"
Write-Host "  $TelegramTask          - at logon + restart after crash"
Write-Host "  $TelegramWatchdogTask  - every minute, stale heartbeat > 3 min"
Write-Host ""

Write-Host "Starting Telegram and one pipeline run..."
Start-ScheduledTask -TaskName $TelegramTask
Start-ScheduledTask -TaskName $PipelineTask

Write-Host ""
Write-Host "Logs:"
Write-Host "  C:\hh-agent\logs\telegram.log"
Write-Host "  C:\hh-agent\logs\telegram_watchdog.log"
Write-Host "  C:\hh-agent\logs\collector.log"
Write-Host "  C:\hh-agent\logs\processor.log"
Write-Host "  C:\hh-agent\logs\pipeline_supervisor.log"
Write-Host "  C:\hh-agent\logs\apply_worker.log"
Write-Host "  C:\hh-agent\logs\apply_supervisor.log"
Write-Host ""
Write-Host "Checks:"
Write-Host '  schtasks /Query /TN "HH Agent - Telegram" /V /FO LIST'
Write-Host '  schtasks /Query /TN "HH Agent - Telegram Watchdog" /V /FO LIST'
Write-Host "  Telegram: /health"
