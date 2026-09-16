$ErrorActionPreference = "Stop"

$TaskName = "HH Agent - Resume Raise"
$Pythonw  = "C:\hh-agent\.venv\Scripts\pythonw.exe"
$Script   = "C:\hh-agent\background_resume_raise.py"

if (-not (Test-Path $Pythonw)) {
    throw "pythonw.exe not found: $Pythonw"
}

if (-not (Test-Path $Script)) {
    throw "Script not found: $Script"
}

$Action = "`"$Pythonw`" `"$Script`""

Write-Host "Creating/updating hidden Resume Raise task..." -ForegroundColor Cyan

schtasks /Create `
  /TN $TaskName `
  /TR $Action `
  /SC MINUTE `
  /MO 5 `
  /ST 00:00 `
  /RU hello `
  /IT `
  /F | Out-Host

if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task: $TaskName"
}

$Task = Get-ScheduledTask -TaskName $TaskName
$Task.Settings.StartWhenAvailable = $true
$Task.Settings.MultipleInstances = "IgnoreNew"
$Task.Settings.Hidden = $true
Set-ScheduledTask -InputObject $Task | Out-Null

Write-Host ""
Write-Host "Resume Raise task is hidden/windowless." -ForegroundColor Green
Write-Host "Smart scheduling state: C:\hh-agent\data\runtime\resume_raise.json"
