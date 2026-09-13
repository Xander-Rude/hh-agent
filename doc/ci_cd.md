# CI/CD

HH Agent uses a pull-request-first delivery flow for changes to `main`.

## Flow

```text
feature branch
    ↓
pull request
    ↓
GitHub Actions CI
    ↓
merge to main
    ↓
GitHub Actions CI on main
    ↓
GitHub Actions CD
    ↓
Octopus Deploy release
    ↓
Production deployment to HH-Agent-PC
    ↓
C:\hh-agent updated with fast-forward-only Git pull
```

## CI

The `CI` workflow runs on pull requests to `main` and on pushes to `main`.
It currently performs whitespace checks, Python compilation, PowerShell syntax validation for the Octopus deployment script, and core unit tests on Windows / Python 3.12.

## CD

The `CD` workflow is triggered only after a successful `CI` run on `main`.
It creates a new release in the Octopus Deploy project `HH Agent` and requests deployment to the `Production` environment.

The deployment target is the Windows machine `HH-Agent-PC`, connected through a Polling Tentacle.

The deployment logic is kept in [`deploy/octopus_deploy.ps1`](../deploy/octopus_deploy.ps1). A small helper, [`deploy/agent_lock_holder.py`](../deploy/agent_lock_holder.py), acquires the same cross-process `AgentLock` used by the background jobs and keeps it for the full deployment window.

The deployment script:

- never starts Pipeline, Apply or Resume Raise;
- waits up to 2 hours for an active Pipeline / Apply / Resume Raise run to release `AgentLock`;
- keeps `AgentLock` reserved for the whole Git update and validation phase so a scheduled browser job cannot start in the middle of deployment;
- protects tracked local edits and aborts if tracked files are modified or staged;
- allows untracked local files to remain in place and logs them instead of treating them as a dirty deployment blocker;
- still relies on Git to refuse an update if an untracked path would actually be overwritten by an incoming tracked file;
- safely switches a clean tracked working tree back to `main` if the user left the repository on a feature branch;
- fetches `origin/main` and allows only fast-forward updates;
- runs Python sanity checks after the update;
- restarts Telegram components only when relevant files changed;
- resets the repository to the previous revision if post-update validation fails.

### Octopus step

After `deploy/octopus_deploy.ps1` and `deploy/agent_lock_holder.py` are present on `HH-Agent-PC`, the Octopus `Run a Script` step can be reduced to this stable launcher:

```powershell
$ErrorActionPreference = "Stop"

$Script = "C:\hh-agent\deploy\octopus_deploy.ps1"

if (-not (Test-Path $Script)) {
    throw "Deployment script not found: $Script"
}

& powershell.exe `
    -NoProfile `
    -NonInteractive `
    -ExecutionPolicy Bypass `
    -File $Script

if ($LASTEXITCODE -ne 0) {
    throw "HH Agent deployment script failed with code=$LASTEXITCODE"
}
```

Keeping the main deployment logic in the repository makes changes reviewable through the same PR + CI path as the rest of the project.

## Operational note

A successful GitHub `CD` job confirms that GitHub successfully created/requested the Octopus deployment. The final deployment result must be checked in Octopus, because the target-side deployment can still fail after the GitHub workflow has completed.
