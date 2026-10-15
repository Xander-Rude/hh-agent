$ErrorActionPreference = "Stop"

# Install under the same interactive Windows account that owns Ollama models.
$TaskName = "HH Agent - Ollama"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Launcher = Join-Path $Root "ollama_headless.py"
foreach ($RequiredFile in @($OllamaExe, $Pythonw, $Launcher)) {
    if (-not (Test-Path $RequiredFile)) {
        throw "Required file not found: $RequiredFile"
    }
}

$UserId = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
# Task Scheduler's -Hidden hides the task in its UI, not a console window.
# pythonw.exe plus CREATE_NO_WINDOW in the launcher suppress both consoles.
$Action = New-ScheduledTaskAction -Execute $Pythonw -Argument ('"' + $Launcher + '"') -WorkingDirectory $Root

$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$LogonTrigger.Delay = "PT30S"

# The repeating trigger restarts a crashed server and IgnoreNew prevents duplicates.
$WatchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($LogonTrigger, $WatchdogTrigger) -Settings $Settings -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Windowless Ollama autostart installed for $UserId"
