param(
    [string]$Root = "C:\hh-agent",
    [int]$IntervalMinutes = 10
)

$ErrorActionPreference = "Stop"
$TaskName = "HH Agent - Cover Letters"
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Runner = Join-Path $Root "background_cover_letters.py"
if (-not (Test-Path $Pythonw) -or -not (Test-Path $Runner)) {
    throw "Cover-letter task files are missing"
}

$Pipeline = Get-ScheduledTask -TaskName "HH Agent - Pipeline" -ErrorAction Stop
if (-not $Pipeline.Principal.UserId) {
    throw "Pipeline principal not found"
}

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument ('"{0}"' -f $Runner) `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 40) `
    -Hidden

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Pipeline.Principal `
    -Description "Prepare CLEAN and OLD letters independently of pipeline outcomes" `
    -Force | Out-Null

Enable-ScheduledTask -TaskName $TaskName | Out-Null
Write-Host "[OK] Independent cover-letter worker: every $IntervalMinutes minutes"
