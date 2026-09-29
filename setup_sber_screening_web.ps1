param([string]$Root = "C:\\hh-agent")

$ErrorActionPreference = "Stop"
$Pythonw = Join-Path $Root ".venv\\Scripts\\pythonw.exe"
$EnvFile = Join-Path $Root ".env"
$Worker = Join-Path $Root "sber_screening_web_worker.py"
$TaskName = "HH Agent - Sber Screening"

if (-not (Test-Path $Pythonw)) { throw "pythonw.exe not found: $Pythonw" }
if (-not (Test-Path $Worker)) { throw "Worker not found: $Worker" }
if (-not (Test-Path $EnvFile)) { throw ".env not found: $EnvFile" }

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
    [System.IO.File]::WriteAllText($Path, $content, (New-Object System.Text.UTF8Encoding($false)))
}

Set-EnvValue -Path $EnvFile -Name "HH_SBER_SCREENING_ENABLED" -Value "true"
Set-EnvValue -Path $EnvFile -Name "HH_SBER_TELEGRAM_WEB_CDP_URL" -Value "http://127.0.0.1:9223"
Set-EnvValue -Path $EnvFile -Name "HH_SBER_GIGA_CHAT_ID" -Value "8491767152"
Set-EnvValue -Path $EnvFile -Name "HH_SBER_TELEGRAM_WEB_PROFILE" -Value "C:\\hh-agent\\data\\secrets\\telegram_web_profile"

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument ('"{0}"' -f $Worker) -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Telegram Web copilot for Sber GigaRecruiter screening" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] Sber screening Web copilot installed and started."
Write-Host "It uses the dedicated Telegram Web profile and local CDP only."
Write-Host "Use /sber_arm APPLICATION_ID before starting a screening."
Write-Host "Auto-send is disabled: every answer requires confirmation."
