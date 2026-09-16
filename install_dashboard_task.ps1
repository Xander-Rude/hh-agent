$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$TaskName = "HH Agent - Dashboard"
$DashboardPython = Join-Path $Root "dashboard\.venv\Scripts\pythonw.exe"
$DashboardEntry = Join-Path $Root "dashboard\__main__.py"

foreach ($Path in @(
    $DashboardPython,
    $DashboardEntry
)) {
    if (-not (Test-Path $Path)) {
        throw "Required file not found: $Path"
    }
}

try {
    Unregister-ScheduledTask `
        -TaskName $TaskName `
        -Confirm:$false `
        -ErrorAction Stop
}
catch {
}

$UserId = "$env:USERDOMAIN\$env:USERNAME"

$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

$DashboardAction = New-ScheduledTaskAction `
    -Execute $DashboardPython `
    -Argument "-m dashboard --source-root $Root --port 8765" `
    -WorkingDirectory $Root

$DashboardTrigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $UserId

$DashboardSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -Hidden

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $DashboardAction `
    -Trigger $DashboardTrigger `
    -Settings $DashboardSettings `
    -Principal $Principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

$Task = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "Installed hidden/windowless: $TaskName"
Write-Host "State: $($Task.State)"
Write-Host "URL: http://127.0.0.1:8765"
