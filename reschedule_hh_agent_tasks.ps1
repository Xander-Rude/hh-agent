$ErrorActionPreference = "Stop"

$PipelineTask = "HH Agent - Pipeline"
$ApplyTask    = "HH Agent - Apply"

$PipelineAction = 'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\hh-agent\run_pipeline.ps1'
$ApplyAction    = 'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\hh-agent\run_apply.ps1'

Write-Host "Recreating HH Agent scheduler tasks..." -ForegroundColor Cyan

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
Write-Host "Pipeline schedule:" -ForegroundColor Green
schtasks /Query /TN $PipelineTask /V /FO LIST | Out-Host

Write-Host ""
Write-Host "Apply schedule:" -ForegroundColor Green
schtasks /Query /TN $ApplyTask /V /FO LIST | Out-Host
