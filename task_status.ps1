$Tasks = @(
    "HH Agent - Pipeline",
    "HH Agent - Apply",
    "HH Agent - Telegram"
)

foreach ($TaskName in $Tasks) {
    Write-Host ""
    Write-Host "=== $TaskName ==="
    schtasks.exe /Query /TN $TaskName /V /FO LIST
}
