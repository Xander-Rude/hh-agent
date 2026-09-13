$ErrorActionPreference = "Stop"

$Repo = "C:\hh-agent"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Git = "C:\Program Files\Git\cmd\git.exe"
$MaxWaitSeconds = 2 * 60 * 60
$WaitIntervalSeconds = 30

Write-Host "=== HH Agent deployment ==="
Write-Host "Repository: $Repo"

if (-not (Test-Path $Repo)) {
    throw "Repository not found: $Repo"
}

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

if (-not (Test-Path $Git)) {
    throw "git.exe not found: $Git"
}

Write-Host "[OK] Git: $Git"
Write-Host "[OK] Python: $Python"

function Invoke-Git {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GitArgs
    )

    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        @GitArgs

    if ($LASTEXITCODE -ne 0) {
        throw "git command failed: git $($GitArgs -join ' ')"
    }
}

function Test-AgentLockIdle {
    $probe = @'
from background_common import AgentLock
try:
    with AgentLock():
        pass
except RuntimeError as exc:
    if str(exc) == "agent_lock_busy":
        raise SystemExit(75)
    raise
'@

    & $Python -c $probe
    $code = $LASTEXITCODE

    if ($code -eq 0) {
        return $true
    }

    if ($code -eq 75) {
        return $false
    }

    throw "AgentLock probe failed with code=$code"
}

function Wait-AgentIdle {
    $started = Get-Date
    $deadline = $started.AddSeconds($MaxWaitSeconds)
    $lastMessage = [datetime]::MinValue

    while (-not (Test-AgentLockIdle)) {
        $now = Get-Date

        if ($now -ge $deadline) {
            $elapsed = [int](($now - $started).TotalSeconds)
            throw "AgentLock stayed busy for ${elapsed}s; deployment timed out waiting for Pipeline/Apply/Resume Raise."
        }

        if (($now - $lastMessage).TotalSeconds -ge 60) {
            $remaining = [int](($deadline - $now).TotalMinutes)
            Write-Host "[WAIT] AgentLock is busy. Waiting for current agent job to finish; up to ${remaining} min remaining."
            $lastMessage = $now
        }

        Start-Sleep -Seconds $WaitIntervalSeconds
    }

    $elapsedSeconds = [int](((Get-Date) - $started).TotalSeconds)

    if ($elapsedSeconds -gt 0) {
        Write-Host "[OK] AgentLock became idle after ${elapsedSeconds}s."
    }
    else {
        Write-Host "[OK] AgentLock is idle."
    }
}

function Stop-Telegram {
    Write-Host "[STEP] Stopping Telegram components..."

    Stop-ScheduledTask `
        -TaskName "HH Agent - Telegram Watchdog" `
        -ErrorAction SilentlyContinue

    Stop-ScheduledTask `
        -TaskName "HH Agent - Telegram" `
        -ErrorAction SilentlyContinue

    Start-Sleep -Seconds 2
}

function Start-Telegram {
    Write-Host "[STEP] Starting Telegram components..."

    $telegram = Get-ScheduledTask `
        -TaskName "HH Agent - Telegram" `
        -ErrorAction SilentlyContinue

    if ($telegram) {
        Start-ScheduledTask `
            -TaskName "HH Agent - Telegram"

        Start-Sleep -Seconds 2
    }

    $watchdog = Get-ScheduledTask `
        -TaskName "HH Agent - Telegram Watchdog" `
        -ErrorAction SilentlyContinue

    if ($watchdog) {
        Start-ScheduledTask `
            -TaskName "HH Agent - Telegram Watchdog"
    }
}

# Wait for the shared browser lock instead of failing just because a scheduled
# Pipeline/Apply/Resume Raise run is currently active.
Wait-AgentIdle

# Local tracked edits are protected. Untracked files are allowed; git itself
# will still refuse a pull if an untracked path would be overwritten.
$trackedDirty = @(
    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        status --porcelain --untracked-files=no
)

if ($LASTEXITCODE -ne 0) {
    throw "git status failed."
}

if ($trackedDirty.Count -gt 0) {
    Write-Host ""
    Write-Host "Tracked local changes detected:"
    $trackedDirty | ForEach-Object { Write-Host "  $_" }

    throw "Tracked working tree is not clean. Deployment aborted."
}

$untracked = @(
    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        ls-files --others --exclude-standard
)

if ($LASTEXITCODE -ne 0) {
    throw "Unable to list untracked files."
}

if ($untracked.Count -gt 0) {
    Write-Host "[INFO] Untracked files are present and will be left untouched:"
    $untracked | ForEach-Object { Write-Host "  $_" }
}

# A user may have left the working copy on a feature branch. With a clean
# tracked tree it is safe to switch back to main; no commits are discarded.
$branch = (
    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        rev-parse --abbrev-ref HEAD
).Trim()

if ($LASTEXITCODE -ne 0) {
    throw "Unable to determine current Git branch."
}

if ($branch -ne "main") {
    Write-Host "[STEP] Switching local repository from '$branch' to 'main'..."
    Invoke-Git switch main
}

Write-Host "[STEP] Fetching origin/main..."
Invoke-Git fetch origin main --prune

$oldSha = (
    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        rev-parse HEAD
).Trim()

$newSha = (
    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        rev-parse origin/main
).Trim()

if (-not $oldSha -or -not $newSha) {
    throw "Unable to determine Git revisions."
}

Write-Host "Current: $oldSha"
Write-Host "Remote : $newSha"

if ($oldSha -eq $newSha) {
    Write-Host ""
    Write-Host "========================================"
    Write-Host "DEPLOY SUCCESS"
    Write-Host "Already up to date."
    Write-Host "========================================"
    exit 0
}

& $Git `
    -c "safe.directory=$Repo" `
    -C $Repo `
    merge-base --is-ancestor $oldSha origin/main

if ($LASTEXITCODE -ne 0) {
    throw "Local main cannot be fast-forwarded to origin/main."
}

$changedFiles = @(
    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        diff --name-only "$oldSha..$newSha"
)

if ($LASTEXITCODE -ne 0) {
    throw "Unable to determine changed files."
}

Write-Host ""
Write-Host "Changed files:"

if ($changedFiles.Count -eq 0) {
    Write-Host "  none"
}
else {
    $changedFiles | ForEach-Object { Write-Host "  $_" }
}

$restartTelegram = $false

foreach ($file in $changedFiles) {
    if (
        $file -like "telegram_*" -or
        $file -eq "application_notifications.py" -or
        $file -like "app/*" -or
        $file -like "requirements*.txt" -or
        $file -eq "pyproject.toml" -or
        $file -eq "poetry.lock"
    ) {
        $restartTelegram = $true
        break
    }
}

$telegramStopped = $false

try {
    if ($restartTelegram) {
        Stop-Telegram
        $telegramStopped = $true
    }
    else {
        Write-Host "[OK] Telegram restart is not required."
    }

    Write-Host "[STEP] Updating local main..."
    Invoke-Git pull --ff-only origin main

    Write-Host "[STEP] Running Python sanity checks..."

    $pythonFiles = @(
        $changedFiles |
        Where-Object { $_ -match "\.py$" }
    )

    $pythonFiles += @(
        "apply_worker.py",
        "apply_dispatcher.py",
        "telegram_bot_entry.py"
    )

    $pythonFiles = @(
        $pythonFiles |
        Sort-Object -Unique
    )

    foreach ($relativePath in $pythonFiles) {
        $file = Join-Path $Repo $relativePath

        if (-not (Test-Path $file)) {
            continue
        }

        Write-Host "  py_compile $relativePath"
        & $Python -m py_compile $file

        if ($LASTEXITCODE -ne 0) {
            throw "py_compile failed: $relativePath"
        }
    }

    $dependenciesChanged = @(
        $changedFiles |
        Where-Object {
            $_ -like "requirements*.txt" -or
            $_ -eq "pyproject.toml" -or
            $_ -eq "poetry.lock"
        }
    )

    if ($dependenciesChanged.Count -gt 0) {
        $requirements = Join-Path $Repo "requirements.txt"

        if (Test-Path $requirements) {
            Write-Host "[STEP] Updating Python dependencies..."

            & $Python -m pip install `
                --disable-pip-version-check `
                -r $requirements

            if ($LASTEXITCODE -ne 0) {
                throw "pip install failed."
            }
        }
    }

    if ($telegramStopped) {
        Start-Telegram
        $telegramStopped = $false
    }

    $deployedSha = (
        & $Git `
            -c "safe.directory=$Repo" `
            -C $Repo `
            rev-parse HEAD
    ).Trim()

    Write-Host ""
    Write-Host "========================================"
    Write-Host "DEPLOY SUCCESS"
    Write-Host "$oldSha -> $deployedSha"
    Write-Host "========================================"
}
catch {
    Write-Host ""
    Write-Host "========================================"
    Write-Host "DEPLOY FAILED"
    Write-Host $_.Exception.Message
    Write-Host "========================================"

    Write-Host "[ROLLBACK] Returning repository to $oldSha..."

    & $Git `
        -c "safe.directory=$Repo" `
        -C $Repo `
        reset --hard $oldSha

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Automatic rollback completed."
    }
    else {
        Write-Host "[CRITICAL] Automatic rollback FAILED."
    }

    if ($telegramStopped) {
        Start-Telegram
    }

    throw
}
