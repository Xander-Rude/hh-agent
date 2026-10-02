import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows ScheduledTasks CIM objects required")
class PipelineRecoverySettingsTests(unittest.TestCase):
    def test_recovery_uses_scheduler_duration_and_preserves_other_settings(self) -> None:
        script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path (Get-Location) 'deploy/harden_scheduled_tasks.ps1'),
    [ref]$tokens, [ref]$errors
)
$helper = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Set-PipelineTaskRecovery'
}, $true)
if (-not $helper) { throw 'Pipeline recovery helper is missing' }

$settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$settings.Enabled = $false
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit 0'
$script:probeTask = New-ScheduledTask -Action $action -Settings $settings
$expected = New-ScheduledTaskSettingsSet -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$before = @{}
foreach ($name in @('Hidden', 'Enabled', 'MultipleInstances', 'ExecutionTimeLimit')) {
    $before[$name] = $script:probeTask.Settings.$name
}
$script:updated = $false

# Use real CIM objects while keeping task reads/writes isolated from the host.
function Get-ScheduledTask {
    param($TaskName, $ErrorAction)
    if ($TaskName -ne 'HH Agent - Pipeline') { throw 'Wrong task selected' }
    return $script:probeTask
}
function Set-ScheduledTask {
    param($InputObject)
    if ($InputObject -ne $script:probeTask) { throw 'Task object replaced' }
    if ($InputObject.Settings.RestartCount -ne $expected.RestartCount) {
        throw 'Unexpected restart count'
    }
    if ($InputObject.Settings.RestartInterval -ne $expected.RestartInterval) {
        throw ('Invalid scheduler duration: ' + $InputObject.Settings.RestartInterval)
    }
    foreach ($name in $before.Keys) {
        if ($InputObject.Settings.$name -ne $before[$name]) {
            throw ('Unrelated setting changed: ' + $name)
        }
    }
    $script:updated = $true
}
Invoke-Expression $helper.Extent.Text
Set-PipelineTaskRecovery
if (-not $script:updated) { throw 'Task update was skipped' }
Write-Output 'RECOVERY_SETTINGS_OK'
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RECOVERY_SETTINGS_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
