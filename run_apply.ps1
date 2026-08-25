$ErrorActionPreference = "Stop"
$Root = "C:\hh-agent"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Script = Join-Path $Root "background_apply.py"

Set-Location $Root
& $Python $Script
exit $LASTEXITCODE
