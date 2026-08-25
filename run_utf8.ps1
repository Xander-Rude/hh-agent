param(
    [Parameter(Position = 0)]
    [string]$Script = "process_vacancies.py",

    [Parameter(Position = 1)]
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ScriptPath = Join-Path $Root $Script
$Utf8 = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

if (-not (Test-Path $ScriptPath)) {
    throw "Script not found: $ScriptPath"
}

# Windows PowerShell may decode native stdout using the active console code
# page. Align both console and Python streams on UTF-8 before starting Python.
chcp 65001 > $null
[Console]::InputEncoding = $Utf8
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Set-Location $Root

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    & $Python -u $ScriptPath
    exit $LASTEXITCODE
}

if (-not [System.IO.Path]::IsPathRooted($LogPath)) {
    $LogPath = Join-Path $Root $LogPath
}

$LogDir = Split-Path -Parent $LogPath
if ($LogDir -and -not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

if (Test-Path $LogPath) {
    Remove-Item $LogPath -Force
}

# Do the tee in PowerShell after forcing the native-process decoding to UTF-8.
# Out-File owns the file encoding explicitly, avoiding OEM/cp1251 log files.
& $Python -u $ScriptPath 2>&1 |
    ForEach-Object {
        $line = [string]$_
        Write-Output $line
        [System.IO.File]::AppendAllText(
            $LogPath,
            $line + [Environment]::NewLine,
            $Utf8
        )
    }

exit $LASTEXITCODE
