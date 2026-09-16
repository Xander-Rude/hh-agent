$ErrorActionPreference = "Stop"

$PipelineTask = "HH Agent - Pipeline"
$ApplyTask    = "HH Agent - Apply"

$Pythonw = "C:\hh-agent\.venv\Scripts\pythonw.exe"
$PipelineScript = "C:\hh-agent\background_pipeline.py"
$ApplyScript = "C:\hh-agent\background_apply.py"

foreach ($Path in @($Pythonw, $PipelineScript, $ApplyScript)) {
    if (-not (Test-Path $Path)) {
        throw "File not found: $Path"
    }
}

$PipelineAction = "`"$Pythonw`" `"$PipelineScript`""
$ApplyAction    = "`"$Pythonw`" `"$ApplyScript`""

Write-Host "Recreating HH Agent scheduler tasks hidden/windowless..." -ForegroundColor Cyan

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
  /F | Out-Null

schtasks /Create `
  /TN $ApplyTask `
  /TR $ApplyAction `
  /SC MINUTE `
  /MO 10 `
  /ST 00:08 `
  /RU hello `
  /IT `
  /F | Out-Null

foreach ($TaskName in @($PipelineTask, $ApplyTask)) {
    $Task = Get-ScheduledTask -TaskName $TaskName
    $Task.Settings.Hidden = $true
    $Task.Settings.StartWhenAvailable = $true
    $Task.Settings.MultipleInstances = "IgnoreNew"
    Set-ScheduledTask -InputObject $Task | Out-Null
}

Write-Host "Pipeline and Apply tasks are hidden/windowless." -ForegroundColor Green
