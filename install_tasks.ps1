$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"

$PipelineTask = "HH Agent - Pipeline"
$ApplyTask = "HH Agent - Apply"
$TelegramTask = "HH Agent - Telegram"
$TelegramWatchdogTask = "HH Agent - Telegram Watchdog"
$ResponseSyncTask = "HH Agent - Response Sync"

$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$TelegramPython = Join-Path $Root ".venv\Scripts\pythonw.exe"
$PipelineScript = Join-Path $Root "background_pipeline.py"
$ApplyScript = Join-Path $Root "background_apply.py"
$TelegramEntry = Join-Path $Root "telegram_bot_entry.py"
$TelegramWatchdog = Join-Path $Root "telegram_watchdog.py"
$ResponseSyncScript = Join-Path $Root "background_response_sync.py"

foreach ($Path in @(
    $Pythonw,
    $TelegramPython,
    $PipelineScript,
    $ApplyScript,
    $TelegramEntry,
    $TelegramWatchdog,
    $ResponseSyncScript
)) {
    if (-not (Test-Path $Path)) {
        throw "File not found: $Path"
    }
}

foreach ($TaskName in @(
    $PipelineTask,
    $ApplyTask,
    $TelegramTask,
    $TelegramWatchdogTask,
    $ResponseSyncTask
)) {
    try {
        Unregister-ScheduledTask `
            -TaskName $TaskName `
            -Confirm:$false `
            -ErrorAction Stop
    }
    catch {
    }
}

$UserId = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

# ---------------- Pipeline ----------------
$PipelineAction = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$PipelineScript`"" `
    -WorkingDirectory $Root

$PipelineTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$PipelineSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -Hidden

Register-ScheduledTask `
    -TaskName $PipelineTask `
    -Action $PipelineAction `
    -Trigger $PipelineTrigger `
    -Settings $PipelineSettings `
    -Principal $Principal `
    -Force | Out-Null

# ---------------- Apply worker ----------------
$ApplyAction = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$ApplyScript`"" `
    -WorkingDirectory $Root

$ApplyTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$ApplySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -Hidden

Register-ScheduledTask `
    -TaskName $ApplyTask `
    -Action $ApplyAction `
    -Trigger $ApplyTrigger `
    -Settings $ApplySettings `
    -Principal $Principal `
    -Force | Out-Null

# ---------------- HH response sync ----------------
$ResponseSyncAction = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$ResponseSyncScript`"" `
    -WorkingDirectory $Root

$ResponseSyncTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$ResponseSyncSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -Hidden

Register-ScheduledTask `
    -TaskName $ResponseSyncTask `
    -Action $ResponseSyncAction `
    -Trigger $ResponseSyncTrigger `
    -Settings $ResponseSyncSettings `
    -Principal $Principal `
    -Force | Out-Null

# ---------------- Telegram bot ----------------
$TelegramAction = New-ScheduledTaskAction `
    -Execute $TelegramPython `
    -Argument "`"$TelegramEntry`"" `
    -WorkingDirectory $Root

$TelegramTrigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $UserId

$TelegramSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -Hidden

Register-ScheduledTask `
    -TaskName $TelegramTask `
    -Action $TelegramAction `
    -Trigger $TelegramTrigger `
    -Settings $TelegramSettings `
    -Principal $Principal `
    -Force | Out-Null

# ---------------- Telegram watchdog ----------------
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
    -DontStopIfGoingOnBatteries `
    -Hidden

Register-ScheduledTask `
    -TaskName $TelegramWatchdogTask `
    -Action $TelegramWatchdogAction `
    -Trigger $TelegramWatchdogTrigger `
    -Settings $TelegramWatchdogSettings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host ""
Write-Host "Created hidden/windowless scheduled tasks:"
Write-Host "  $PipelineTask          - every 2 hours"
Write-Host "  $ApplyTask             - every 10 minutes"
Write-Host "  $ResponseSyncTask      - every 30 minutes"
Write-Host "  $TelegramTask          - at logon + restart after crash"
Write-Host "  $TelegramWatchdogTask  - every minute, stale heartbeat > 3 min"
Write-Host ""

Write-Host "Starting Telegram and one pipeline run..."
Start-ScheduledTask -TaskName $TelegramTask
Start-ScheduledTask -TaskName $PipelineTask
Start-ScheduledTask -TaskName $ResponseSyncTask

Write-Host ""
Write-Host "Logs:"
Write-Host "  C:\hh-agent\logs\telegram.log"
Write-Host "  C:\hh-agent\logs\telegram_watchdog.log"
Write-Host "  C:\hh-agent\logs\collector.log"
Write-Host "  C:\hh-agent\logs\processor.log"
Write-Host "  C:\hh-agent\logs\pipeline_supervisor.log"
Write-Host "  C:\hh-agent\logs\apply_worker.log"
Write-Host "  C:\hh-agent\logs\apply_supervisor.log"
Write-Host "  C:\hh-agent\logs\response_sync_worker.log"
Write-Host "  C:\hh-agent\logs\response_sync_supervisor.log"
