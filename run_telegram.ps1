$ErrorActionPreference = "Stop"

$Root = "C:\hh-agent"
$Logs = Join-Path $Root "logs"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

# Use pythonw.exe on Windows so the Telegram bot has no console window at all.
$Python = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Script = Join-Path $Root "telegram_bot_entry.py"
$Log = Join-Path $Logs "telegram.log"

Set-Location $Root

# Force UTF-8 for Python stdout/stderr.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# PowerShell is already started hidden by Scheduled Task. pythonw.exe is a
# windowless interpreter, so the bot cannot create or depend on a console.
& $Python -u $Script 2>&1 | Out-File -FilePath $Log -Append -Encoding utf8
$ExitCode = $LASTEXITCODE

exit $ExitCode
