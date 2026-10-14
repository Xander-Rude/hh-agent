$ErrorActionPreference = "Stop"

# Install under the same interactive Windows account that owns Ollama models.
$TaskName = "HH Agent - Ollama"
$OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path $OllamaExe)) {
    throw "Ollama is not installed for $env:USERNAME at $OllamaExe"
}

$UserId = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Action = New-ScheduledTaskAction -Execute $OllamaExe -Argument "serve" -WorkingDirectory $env:USERPROFILE

$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$LogonTrigger.Delay = "PT30S"

# Repeating trigger acts as a watchdog if the server exits after its retries.
# MultipleInstances=IgnoreNew prevents starting a second server while it is healthy.
$WatchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $Action `
    -Trigger @($LogonTrigger, $WatchdogTrigger) `
    -Settings $Settings -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Ollama autostart installed for $UserId"
