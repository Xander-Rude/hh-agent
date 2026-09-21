$ErrorActionPreference = "Stop"

$Repo = "C:\hh-agent"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Git = "C:\Program Files\Git\cmd\git.exe"
$LockHolder = Join-Path $Repo "deploy\agent_lock_holder.py"
$MaxWaitSeconds = 2 * 60 * 60
$WaitIntervalSeconds = 5

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

if (-not (Test-Path $LockHolder)) {
    throw "AgentLock holder not found: $LockHolder"
}

Write-Host "[OK] Git: $Git"
Write-Host "[OK] Python: $Python"

function Invoke-Git {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GitArgs
    )

    # Git writes routine progress/status messages for successful commands such
    # as fetch and pull to stderr. In Windows PowerShell, $ErrorActionPreference
    # = "Stop" can turn redirected native stderr into NativeCommandError before
    # we get a chance to inspect $LASTEXITCODE. Temporarily relax it only around
    # the native Git call, merge both streams, then decide success strictly by
    # Git's exit code.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(
            & $Git `
                -c "safe.directory=$Repo" `
                -C $Repo `
                @GitArgs 2>&1
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    foreach ($line in $output) {
        if ($null -eq $line) {
            continue
        }

        if ($line -is [System.Management.Automation.ErrorRecord]) {
            Write-Host $line.Exception.Message
        }
        else {
            Write-Host $line.ToString()
        }
    }

    if ($exitCode -ne 0) {
        throw "git command failed with code=${exitCode}: git $($GitArgs -join ' ')"
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

$lockWorkDir = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("hh-agent-deploy-" + [guid]::NewGuid().ToString("N"))
$lockReady = Join-Path $lockWorkDir "ready"
$lockRelease = Join-Path $lockWorkDir "release"
$lockProcess = $null

New-Item `
    -ItemType Directory `
    -Path $lockWorkDir `
    -Force | Out-Null

try {
    Write-Host "[STEP] Waiting for AgentLock and reserving the agent for deployment..."

    $lockProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList @(
            $LockHolder,
            "--ready", $lockReady,
            "--release", $lockRelease,
            "--timeout-seconds", "$MaxWaitSeconds",
            "--retry-seconds", "$WaitIntervalSeconds"
        ) `
        -WindowStyle Hidden `
        -PassThru

    $waitStarted = Get-Date
    $waitDeadline = $waitStarted.AddSeconds($MaxWaitSeconds + 30)
    $lastWaitMessage = [datetime]::MinValue

    while (-not (Test-Path $lockReady)) {
        $lockProcess.Refresh()

        if ($lockProcess.HasExited) {
            if ($lockProcess.ExitCode -eq 75) {
                throw "AgentLock stayed busy for $MaxWaitSeconds seconds; deployment wait timed out."
            }

            throw "AgentLock holder exited unexpectedly with code=$($lockProcess.ExitCode)."
        }

        $now = Get-Date

        if ($now -ge $waitDeadline) {
            throw "Timed out waiting for the AgentLock holder to become ready."
        }

        if (($now - $lastWaitMessage).TotalSeconds -ge 60) {
            $elapsed = [int](($now - $waitStarted).TotalSeconds)
            Write-Host "[WAIT] AgentLock is busy; waited ${elapsed}s so far."
            $lastWaitMessage = $now
        }

        Start-Sleep -Seconds $WaitIntervalSeconds
    }

    $waitedSeconds = [int](((Get-Date) - $waitStarted).TotalSeconds)
    Write-Host "[OK] AgentLock reserved for deployment after ${waitedSeconds}s."

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
        return
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

        $initDb = Join-Path $Repo "init_db.py"
        if (Test-Path $initDb) {
            Write-Host "[STEP] Applying database schema migrations..."
            & $Python $initDb
            if ($LASTEXITCODE -ne 0) {
                throw "Database migration failed."
            }
        }

        $repairResponseSync = Join-Path $Repo "tools\repair_false_response_sync_20260922.py"
        if (Test-Path $repairResponseSync) {
            Write-Host "[STEP] Repairing invalid response-sync states from 2026-09-22..."
            & $Python $repairResponseSync
            if ($LASTEXITCODE -ne 0) {
                throw "Response-sync repair failed."
            }
        }

        $taskHardener = Join-Path $Repo "deploy\harden_scheduled_tasks.ps1"
        if (Test-Path $taskHardener) {
            Write-Host "[STEP] Enforcing hidden/windowless scheduled tasks..."
            & $taskHardener -Root $Repo
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
}
finally {
    if ($lockProcess) {
        try {
            New-Item `
                -ItemType File `
                -Path $lockRelease `
                -Force | Out-Null

            if (-not $lockProcess.HasExited) {
                $exited = $lockProcess.WaitForExit(15000)
                if (-not $exited) {
                    Stop-Process `
                        -Id $lockProcess.Id `
                        -Force `
                        -ErrorAction SilentlyContinue
                }
            }
        }
        catch {
            Write-Host "[WARN] Failed to stop AgentLock holder cleanly: $($_.Exception.Message)"
        }
    }

    Remove-Item `
        -Path $lockWorkDir `
        -Recurse `
        -Force `
        -ErrorAction SilentlyContinue
}
