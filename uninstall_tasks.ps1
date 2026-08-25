$ErrorActionPreference = "Continue"

foreach ($TaskName in @(
    "HH Agent - Pipeline",
    "HH Agent - Apply",
    "HH Agent - Telegram"
)) {
    schtasks.exe /End /TN $TaskName 2>$null | Out-Null
    schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
}

Write-Host "HH Agent scheduled tasks removed."
