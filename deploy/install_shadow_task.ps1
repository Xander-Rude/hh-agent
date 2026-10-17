param(
    [string]$Root = "C:\hh-agent",
    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"
$TaskName = "HH Agent - Shadow Scoring"
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Runner = Join-Path $Root "background_shadow.py"

if (-not (Test-Path $Pythonw) -or -not (Test-Path $Runner)) {
    throw "Shadow scoring task files are missing"
}

$Pipeline = Get-ScheduledTask -TaskName "HH Agent - Pipeline" -ErrorAction Stop
if (-not $Pipeline.Principal.UserId) {
    throw "Pipeline principal not found"
}

# Do not interrupt an existing run or silently reset an operator's task
# settings on every CD; a first install is the only automatic action.
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "[OK] Shadow scoring task already exists: $($Existing.State)"
    return
}

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument ('"{0}"' -f $Runner) `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(2)) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -Hidden

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Pipeline.Principal `
    -Description "Independent fair OLD/CLEAN semantic evaluation without browser access" `
    -Force | Out-Null

Write-Host "[OK] Shadow scoring task: every $IntervalMinutes minutes"
