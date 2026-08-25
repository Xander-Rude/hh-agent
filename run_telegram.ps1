$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$Logs = Join-Path $Root "logs"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Script = Join-Path $Root "telegram_bot.py"
$Log = Join-Path $Logs "telegram.log"

Set-Location $Root

# Force UTF-8 for Python stdout/stderr.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Let cmd.exe do byte-oriented redirection so Cyrillic stays UTF-8.
$Command = "`"$Python`" -u `"$Script`" >> `"$Log`" 2>&1"

cmd.exe /d /s /c $Command

exit $LASTEXITCODE
