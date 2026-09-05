$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$Logs = Join-Path $Root "logs"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Script = Join-Path $Root "telegram_bot_entry.py"
$Log = Join-Path $Logs "telegram.log"

Set-Location $Root

# Force UTF-8 for Python stdout/stderr.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Run Python directly. The Scheduled Task starts this PowerShell process with
# -WindowStyle Hidden, so no child cmd.exe window is created or kept alive.
& $Python -u $Script 2>&1 | Out-File -FilePath $Log -Append -Encoding utf8
$ExitCode = $LASTEXITCODE

exit $ExitCode
