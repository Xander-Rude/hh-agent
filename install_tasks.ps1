$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

$PipelineTask = "HH Agent - Pipeline"
$ApplyTask = "HH Agent - Apply"
$TelegramTask = "HH Agent - Telegram"

$PipelineScript = Join-Path $Root "run_pipeline.ps1"
$ApplyScript = Join-Path $Root "run_apply.ps1"
$TelegramScript = Join-Path $Root "run_telegram.ps1"

foreach ($Path in @(
    $PipelineScript,
    $ApplyScript,
    $TelegramScript
)) {
    if (-not (Test-Path $Path)) {
        throw "Не найден файл: $Path"
    }
}

# Remove old versions if present.
foreach ($TaskName in @(
    $PipelineTask,
    $ApplyTask,
    $TelegramTask
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
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
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
$TelegramAction = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument (
        "-NoProfile -NonInteractive -WindowStyle Hidden " +
        "-ExecutionPolicy Bypass -File `"$TelegramScript`""
    )

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

Write-Host ""
Write-Host "Готово. Созданы задачи:"
Write-Host "  $PipelineTask  — каждые 30 минут"
Write-Host "  $ApplyTask     — каждые 10 минут"
Write-Host "  $TelegramTask  — при входе + restart через 1 мин при падении"
Write-Host ""

Write-Host "Запускаю Telegram и один pipeline..."
Start-ScheduledTask -TaskName $TelegramTask
Start-ScheduledTask -TaskName $PipelineTask

Write-Host ""
Write-Host "Логи:"
Write-Host "  C:\hh-agent\logs\telegram.log"
Write-Host "  C:\hh-agent\logs\collector.log"
Write-Host "  C:\hh-agent\logs\processor.log"
Write-Host "  C:\hh-agent\logs\pipeline_supervisor.log"
Write-Host "  C:\hh-agent\logs\apply_worker.log"
Write-Host "  C:\hh-agent\logs\apply_supervisor.log"
Write-Host ""
Write-Host "Проверка:"
Write-Host '  schtasks /Query /TN "HH Agent - Telegram" /V /FO LIST'
Write-Host "  Telegram: /health"
