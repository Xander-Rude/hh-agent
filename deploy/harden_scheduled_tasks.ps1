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
    Set-HiddenTaskAction `
        -TaskName "HH Agent - Grafana Drive Bridge" `
        -Execute $Pythonw `
        -Argument ('"{0}" --remote "hh-agent-drive:HH-Agent/observability"' -f $bridgeRunner) `
        -RunNow
}
else {
    Write-Host "[SKIP] Grafana Drive bridge runner not installed."
}
