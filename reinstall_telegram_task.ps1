$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$TaskName = "HH Agent - Telegram"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Script = Join-Path $Root "run_telegram.ps1"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
catch {
}

try {
    Unregister-ScheduledTask `
        -TaskName $TaskName `
        -Confirm:$false `
        -ErrorAction SilentlyContinue
}
catch {
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument (
        "-NoProfile -NonInteractive -WindowStyle Hidden " +
        "-ExecutionPolicy Bypass -File `"$Script`""
    )

$Trigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $UserId

$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

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

Start-ScheduledTask -TaskName $TaskName

Write-Host "Telegram task recreated and started."
Write-Host "Restart on failure: every 1 minute, up to 999 attempts."
