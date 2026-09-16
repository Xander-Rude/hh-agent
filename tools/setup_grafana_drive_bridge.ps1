param(
    [string]$Repo = "C:\hh-agent",
    [string]$RemoteName = "hh-agent-drive",
    [string]$RemoteFolder = "HH-Agent/observability",
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$StateDir = "C:\ProgramData\HHAgentGrafanaBridge"
$Rclone = Join-Path $StateDir "rclone.exe"
$Bridge = Join-Path $Repo "tools\grafana_drive_bridge.py"
$Runner = Join-Path $Repo "tools\grafana_drive_bridge_runner.py"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Pythonw = Join-Path $Repo ".venv\Scripts\pythonw.exe"
$TaskName = "HH Agent - Grafana Drive Bridge"
$Remote = "${RemoteName}:$RemoteFolder"
$RcloneConfigDir = Join-Path $env:APPDATA "rclone"
$RcloneConfig = Join-Path $RcloneConfigDir "rclone.conf"

function Write-Step([string]$Text) {
    Write-Host "[STEP] $Text" -ForegroundColor Cyan
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-RcloneCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        $output = @(& $Rclone @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }

    if ($exitCode -ne 0) {
        $detail = (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
        throw "rclone $($Arguments -join ' ') failed with code ${exitCode}: $detail"
    }

    return @($output | ForEach-Object { $_.ToString() })
}

function Invoke-RcloneInteractive {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        & $Rclone @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }

    if ($exitCode -ne 0) {
        throw "rclone $($Arguments -join ' ') failed with code ${exitCode}."
    }
}

if (-not (Test-IsAdministrator)) {
    throw "Run this script from PowerShell as Administrator."
}

foreach ($required in @($Bridge, $Runner, $Python, $Pythonw)) {
    if (-not (Test-Path $required)) {
        throw "Required bridge file was not found: $required"
    }
}

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
New-Item -ItemType Directory -Path $RcloneConfigDir -Force | Out-Null
if (-not (Test-Path $RcloneConfig)) {
    New-Item -ItemType File -Path $RcloneConfig -Force | Out-Null
}

if (-not (Test-Path $Rclone)) {
    Write-Step "Downloading rclone"
    $zipPath = Join-Path $env:TEMP "hh-agent-rclone.zip"
    $extractDir = Join-Path $env:TEMP "hh-agent-rclone"
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue

    Invoke-WebRequest -Uri "https://downloads.rclone.org/rclone-current-windows-amd64.zip" -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    $downloadedRclone = Get-ChildItem $extractDir -Filter rclone.exe -Recurse | Select-Object -First 1
    if (-not $downloadedRclone) {
        throw "rclone.exe was not found in downloaded archive"
    }
    Copy-Item $downloadedRclone.FullName $Rclone -Force
}

Write-Step "Checking rclone"
$rcloneVersion = @(Invoke-RcloneCapture -Arguments @("version"))
$rcloneVersion | Select-Object -First 2 | ForEach-Object { Write-Host $_ }

$remoteMarker = "${RemoteName}:"
$configuredRemotes = @(Invoke-RcloneCapture -Arguments @("listremotes"))
if ($configuredRemotes -notcontains $remoteMarker) {
    Write-Step "Authorizing Google Drive remote: $RemoteName"
    Write-Host "A browser window will open once. Approve Google Drive access there." -ForegroundColor Yellow

    Invoke-RcloneInteractive -Arguments @(
        "config",
        "create",
        $RemoteName,
        "drive",
        "scope=drive",
        "config_is_local=true",
        "--no-output"
    )

    $configuredRemotes = @(Invoke-RcloneCapture -Arguments @("listremotes"))
    if ($configuredRemotes -notcontains $remoteMarker) {
        throw "Remote $remoteMarker was not created."
    }
}

Write-Step "Ensuring Drive destination exists: $Remote"
$null = Invoke-RcloneCapture -Arguments @("mkdir", $Remote)

Write-Step "Testing Grafana Cloud query and first Drive upload"
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $Python $Runner --remote $Remote
    $bridgeExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorAction
}
if ($bridgeExitCode -ne 0) {
    throw "Initial Grafana -> Drive export failed. See $StateDir\bridge.log"
}

Write-Step "Creating hidden scheduled task: every $IntervalMinutes minutes"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument ('"{0}" --remote "{1}"' -f $Runner, $Remote) -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes($IntervalMinutes)) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Export rolling 24h HH Agent logs from Grafana Cloud Loki to Google Drive" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "[OK] Grafana -> Google Drive bridge installed." -ForegroundColor Green
Write-Host "Task: $TaskName (hidden, pythonw.exe, every $IntervalMinutes minutes)"
Write-Host "Remote: $Remote"
Write-Host "Local state: $StateDir"
Write-Host "Drive files:"
Write-Host "  hh-agent-summary.json"
Write-Host "  hh-agent-errors-24h.jsonl"
Write-Host "  hh-agent-last-24h.jsonl"
Write-Host "  logs\<source>.log"
Write-Host ""
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
