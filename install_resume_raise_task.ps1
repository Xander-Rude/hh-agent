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
  /SC MINUTE `
  /MO 30 `
  /ST 00:23 `
  /RU hello `
  /IT `
  /F | Out-Host

if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task: $TaskName"
}

Write-Host ""
Write-Host "Resume Raise schedule:" -ForegroundColor Green
schtasks /Query /TN $TaskName /V /FO LIST | Out-Host
