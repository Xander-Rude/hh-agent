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

# /F is enough: it creates the task if absent and overwrites it if it already exists.
# No preliminary /Query or /Delete is needed.
schtasks /Create `
  /TN $TaskName `
  /TR $Action `
  /SC HOURLY `
  /MO 2 `
  /ST 00:23 `
  /RU hello `
  /IT `
  /F | Out-Host

if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task: $TaskName"
}

# schtasks /Create does not expose StartWhenAvailable.
# Set it explicitly so a missed run starts as soon as Windows can run it
# after reboot/logon, matching the other HH Agent background tasks.
$Task = Get-ScheduledTask -TaskName $TaskName
$Task.Settings.StartWhenAvailable = $true
Set-ScheduledTask -InputObject $Task | Out-Null

Write-Host ""
Write-Host "Resume Raise schedule:" -ForegroundColor Green
schtasks /Query /TN $TaskName /V /FO LIST | Out-Host
Write-Host ""
Write-Host "StartWhenAvailable:" -ForegroundColor Green
(Get-ScheduledTask -TaskName $TaskName).Settings.StartWhenAvailable | Out-Host
