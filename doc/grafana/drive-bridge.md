# Grafana Cloud -> Google Drive bridge

This bridge makes the Grafana Cloud log stream readable through the connected Google Drive integration without exposing Loki publicly.

## Data flow

```text
C:\hh-agent\logs\*.log
        |
        v
Grafana Alloy
        |
        v
Grafana Cloud Loki
        |
        | query_range, rolling 24h
        v
tools/grafana_drive_bridge.py
        |
        | rclone / Google OAuth
        v
Google Drive / HH-Agent / observability
```

Grafana remains the source of truth. The Drive copy is a rolling read bridge for ChatGPT and quick diagnostics.

## Drive files

The bridge overwrites three stable files every run:

- `hh-agent-summary.json` - counters, active files, recent pipeline events and the latest attention events;
- `hh-agent-errors-24h.jsonl` - errors, warnings, manual-required and deferred events from the last 24 hours;
- `hh-agent-last-24h.jsonl` - all `v2` HH Agent log lines from the last 24 hours.

Each JSONL row contains:

```json
{"ts":"2026-09-16T17:42:11.123456789Z","file":"processor.log","line":"..."}
```

The default Loki selector is:

```logql
{service="hh-agent",stream_version="v2"}
```

The exporter queries Loki in one-hour windows. If a window reaches Loki's line limit, it recursively splits that interval so busy periods are not silently truncated.

## Credentials

No Grafana credential is committed to Git.

The bridge reads the existing Loki URL, username and token at runtime from:

```text
C:\Program Files\GrafanaLabs\Alloy\config.alloy
```

It supports either literal Alloy values or `sys.env("...")` references. The token is used only for the Loki API request and is never written to the Drive snapshots or bridge log.

Google Drive authorization is stored by `rclone` in the Windows user profile. The scheduled task runs as the same interactive Windows user so it can use that OAuth configuration.

## Install

After the code is present in `C:\hh-agent`, open PowerShell as Administrator and run:

```powershell
powershell -ExecutionPolicy Bypass -File C:\hh-agent\tools\setup_grafana_drive_bridge.ps1
```

The setup script:

1. downloads `rclone` into `C:\ProgramData\HHAgentGrafanaBridge` if needed;
2. opens the one-time Google Drive authorization wizard when the `hh-agent-drive` remote is missing;
3. verifies `HH-Agent/observability` on Drive;
4. performs the first Grafana -> Drive export;
5. creates `HH Agent - Grafana Drive Bridge`, scheduled every 5 minutes.

For the rclone wizard create a remote named `hh-agent-drive`, choose Google Drive, leave custom client credentials blank, authorize in the browser, use normal My Drive rather than a Shared Drive, then save and quit.

## Runtime

Local state is intentionally outside the repository and outside Alloy's `logs/*.log` glob:

```text
C:\ProgramData\HHAgentGrafanaBridge\
    rclone.exe
    bridge.log
    hh-agent-summary.json
    hh-agent-errors-24h.jsonl
    hh-agent-last-24h.jsonl
```

This avoids a feedback loop where the bridge's own log would be re-ingested into Loki.

Check the task:

```powershell
Get-ScheduledTask -TaskName "HH Agent - Grafana Drive Bridge"
```

Run the bridge manually:

```powershell
C:\hh-agent\.venv\Scripts\python.exe C:\hh-agent\tools\grafana_drive_bridge.py
```

Test Loki/query generation without uploading to Drive:

```powershell
C:\hh-agent\.venv\Scripts\python.exe C:\hh-agent\tools\grafana_drive_bridge.py --no-upload
```

The retention window defaults to 24 hours and can be overridden with `HH_GRAFANA_DRIVE_HOURS` or `--hours`.
