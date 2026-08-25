$ErrorActionPreference = "Stop"

$PipelineTask = "HH Agent - Pipeline"
$ApplyTask    = "HH Agent - Apply"

$Pythonw = "C:\hh-agent\.venv\Scripts\pythonw.exe"
$PipelineScript = "C:\hh-agent\background_pipeline.py"
$ApplyScript    = "C:\hh-agent\background_apply.py"

if (-not (Test-Path $Pythonw)) {
    throw "pythonw.exe not found: $Pythonw"
}
if (-not (Test-Path $PipelineScript)) {
    throw "Pipeline script not found: $PipelineScript"
}
if (-not (Test-Path $ApplyScript)) {
    throw "Apply script not found: $ApplyScript"
}

$PipelineAction = "`"$Pythonw`" `"$PipelineScript`""
$ApplyAction    = "`"$Pythonw`" `"$ApplyScript`""

Write-Host "Recreating HH Agent tasks with pythonw.exe (no console window)..." -ForegroundColor Cyan

schtasks /Delete /TN $PipelineTask /F 2>$null | Out-Null
schtasks /Delete /TN $ApplyTask /F 2>$null | Out-Null

schtasks /Create `
  /TN $PipelineTask `
  /TR $PipelineAction `
  /SC MINUTE `
  /MO 30 `
  /ST 00:03 `
  /RU hello `
  /IT `
  /F | Out-Host

schtasks /Create `
  /TN $ApplyTask `
  /TR $ApplyAction `
  /SC MINUTE `
  /MO 10 `
  /ST 00:08 `
  /RU hello `
  /IT `
  /F | Out-Host

Write-Host ""
Write-Host "Pipeline:" -ForegroundColor Green
schtasks /Query /TN $PipelineTask /V /FO LIST | Out-Host

Write-Host ""
Write-Host "Apply:" -ForegroundColor Green
schtasks /Query /TN $ApplyTask /V /FO LIST | Out-Host
