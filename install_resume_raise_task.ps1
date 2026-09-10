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

Write-Host "Creating/updating Resume Raise task..." -ForegroundColor Cyan

# Wake a tiny supervisor every five minutes. It reads data/runtime/resume_raise.json
# and exits immediately unless next_due_at has arrived. Playwright/Chromium is
# therefore NOT launched every five minutes; HH is opened only near the time it
# advertised for the next free raise (or when a retry is due).
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

# schtasks /Create does not expose these reliability settings.
$Task = Get-ScheduledTask -TaskName $TaskName
$Task.Settings.StartWhenAvailable = $true
$Task.Settings.MultipleInstances = "IgnoreNew"
Set-ScheduledTask -InputObject $Task | Out-Null

Write-Host ""
Write-Host "Resume Raise schedule:" -ForegroundColor Green
schtasks /Query /TN $TaskName /V /FO LIST | Out-Host
Write-Host ""
Write-Host "StartWhenAvailable:" -ForegroundColor Green
(Get-ScheduledTask -TaskName $TaskName).Settings.StartWhenAvailable | Out-Host
Write-Host ""
Write-Host "Smart scheduling state:" -ForegroundColor Green
Write-Host "  C:\hh-agent\data\runtime\resume_raise.json"
