param(
    [string]$Root = "C:\hh-agent"
)

$ErrorActionPreference = "Stop"

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$EnvFile = Join-Path $Root ".env"
$LoginScript = Join-Path $Root "tools\sber_telegram_login.py"
$Worker = Join-Path $Root "sber_screening_worker.py"
$TaskName = "HH Agent - Sber Screening"

if (-not (Test-Path $Python)) { throw "Python venv not found: $Python" }
if (-not (Test-Path $Pythonw)) { throw "pythonw.exe not found: $Pythonw" }
if (-not (Test-Path $LoginScript)) { throw "Login script not found: $LoginScript" }
if (-not (Test-Path $Worker)) { throw "Worker not found: $Worker" }
if (-not (Test-Path $EnvFile)) { throw ".env not found: $EnvFile" }

Write-Host "[STEP] Installing Telethon..."
& $Python -m pip install --disable-pip-version-check "telethon>=1.40,<2"
if ($LASTEXITCODE -ne 0) { throw "Telethon installation failed." }

$envText = Get-Content -Raw -LiteralPath $EnvFile
if ($envText -notmatch "(?m)^TELEGRAM_API_ID=") {
    Write-Host "Add TELEGRAM_API_ID to $EnvFile"
    Write-Host "Get api_id/api_hash from https://my.telegram.org/apps"
    throw "TELEGRAM_API_ID is not configured."
}
if ($envText -notmatch "(?m)^TELEGRAM_API_HASH=") {
    Write-Host "Add TELEGRAM_API_HASH to $EnvFile"
    Write-Host "Get api_id/api_hash from https://my.telegram.org/apps"
    throw "TELEGRAM_API_HASH is not configured."
}

Write-Host "[STEP] Authorizing Telegram user session..."
& $Python $LoginScript
if ($LASTEXITCODE -ne 0) { throw "Telegram user authorization failed." }

function Set-EnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    $content = Get-Content -Raw -LiteralPath $Path
    $pattern = "(?m)^" + [regex]::Escape($Name) + "=.*$"
    $line = "$Name=$Value"
    if ($content -match $pattern) {
        $content = [regex]::Replace($content, $pattern, $line)
    } else {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) { $content += "`r`n" }
        $content += $line + "`r`n"
    }
    Set-Content -LiteralPath $Path -Value $content -Encoding UTF8
}

Set-EnvValue -Path $EnvFile -Name "HH_SBER_SCREENING_ENABLED" -Value "true"

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument ('"{0}"' -f $Worker) -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Local Telegram user-client copilot for Sber GigaRecruiter screening" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] Sber screening copilot installed and started."
Write-Host "Use /sber_arm in HH Agent before opening a GigaRecruiter deep link."
Write-Host "Auto-send is disabled: every answer requires confirmation in HH Agent."
