param(
    [string]$Root = "C:\hh-agent"
)

$ErrorActionPreference = "Stop"

$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$DashboardPythonw = Join-Path $Root "dashboard\.venv\Scripts\pythonw.exe"

function Set-HiddenTaskAction {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,
        [Parameter(Mandatory = $true)]
        [string]$Execute,
        [Parameter(Mandatory = $true)]
        [string]$Argument,
        [string]$WorkingDirectory = $Root,
        [switch]$RunNow
    )

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "[SKIP] Scheduled task not installed: $TaskName"
        return
    }

    if (-not (Test-Path $Execute)) {
        throw "Executable for '$TaskName' was not found: $Execute"
    }

    $action = New-ScheduledTaskAction `
        -Execute $Execute `
        -Argument $Argument `
        -WorkingDirectory $WorkingDirectory

    Set-ScheduledTask `
        -TaskName $TaskName `
        -Action $action | Out-Null

    $task = Get-ScheduledTask -TaskName $TaskName
    $task.Settings.Hidden = $true
    Set-ScheduledTask -InputObject $task | Out-Null

    Write-Host "[OK] Hidden/windowless: $TaskName"

    if ($RunNow) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "[OK] Started: $TaskName"
    }
}

function Reset-GrafanaBridgeTask {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Runner,
        [int]$IntervalMinutes = 5
    )

    $taskName = "HH Agent - Grafana Drive Bridge"
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "[SKIP] Scheduled task not installed: $taskName"
        return
    }

    if (-not (Test-Path $Runner)) {
        throw "Grafana Drive bridge runner was not found: $Runner"
    }

    $userId = $existing.Principal.UserId
    if (-not $userId) {
        throw "Unable to determine principal for '$taskName'."
    }

    $action = New-ScheduledTaskAction `
        -Execute $Pythonw `
        -Argument ('"{0}" --remote "hh-agent-drive:HH-Agent/observability"' -f $Runner) `
        -WorkingDirectory $Root

    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At ((Get-Date).AddMinutes(1)) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $principal = New-ScheduledTaskPrincipal `
        -UserId $userId `
        -LogonType Interactive `
        -RunLevel Highest

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
        -MultipleInstances IgnoreNew `
        -Hidden

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Export rolling 24h HH Agent logs from Grafana Cloud Loki to Google Drive" `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $taskName
    Write-Host "[OK] Recreated hidden/windowless bridge task: every $IntervalMinutes minutes"
}

if (-not (Test-Path $Pythonw)) {
    throw "pythonw.exe not found: $Pythonw"
}

Set-HiddenTaskAction `
    -TaskName "HH Agent - Pipeline" `
    -Execute $Pythonw `
    -Argument ('"{0}"' -f (Join-Path $Root "background_pipeline.py"))

Set-HiddenTaskAction `
    -TaskName "HH Agent - Apply" `
    -Execute $Pythonw `
    -Argument ('"{0}"' -f (Join-Path $Root "background_apply.py"))

Set-HiddenTaskAction `
    -TaskName "HH Agent - Telegram" `
    -Execute $Pythonw `
    -Argument ('"{0}"' -f (Join-Path $Root "telegram_bot_entry.py"))

Set-HiddenTaskAction `
    -TaskName "HH Agent - Telegram Watchdog" `
    -Execute $Pythonw `
    -Argument ('"{0}"' -f (Join-Path $Root "telegram_watchdog.py"))

Set-HiddenTaskAction `
    -TaskName "HH Agent - Resume Raise" `
    -Execute $Pythonw `
    -Argument ('"{0}"' -f (Join-Path $Root "background_resume_raise.py"))

if (Test-Path $DashboardPythonw) {
    Set-HiddenTaskAction `
        -TaskName "HH Agent - Dashboard" `
        -Execute $DashboardPythonw `
        -Argument ('-m dashboard --source-root "{0}" --port 8765' -f $Root)
}
else {
    Write-Host "[SKIP] Dashboard pythonw.exe not installed."
}

$bridgeRunner = Join-Path $Root "tools\grafana_drive_bridge_runner.py"
if (Test-Path $bridgeRunner) {
    Reset-GrafanaBridgeTask -Runner $bridgeRunner -IntervalMinutes 5
}
else {
    Write-Host "[SKIP] Grafana Drive bridge runner not installed."
}
