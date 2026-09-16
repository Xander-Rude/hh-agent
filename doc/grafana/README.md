# Grafana Cloud observability

HH Agent can ship local Windows logs from `C:\hh-agent\logs\*.log` to Grafana Cloud Loki through Grafana Alloy.

## Runtime

- Windows service: `Alloy`
- Alloy binary: `C:\Program Files\GrafanaLabs\Alloy\alloy-windows-amd64.exe`
- Alloy config: `C:\Program Files\GrafanaLabs\Alloy\config.alloy`
- local readiness endpoint: `http://127.0.0.1:12345/-/ready`
- log source: `C:\hh-agent\logs\*.log`

The production Alloy config is intentionally not stored in the repository because it contains Grafana Cloud credentials.

All HH Agent log streams use the label `service="hh-agent"`. New telemetry also uses `stream_version="v2"`; the v2 processing stage parses RFC3339 timestamps from log lines such as `[2026-09-16T18:57:49+03:00] ...` before forwarding them to Loki. This keeps dashboard time series aligned with the actual event time instead of the initial ingestion/backfill time.

The file glob is rescanned periodically, so newly created `*.log` files are picked up automatically. Existing watched files are tailed continuously.

## Dashboard

The importable dashboard is versioned in the repository:

[`hh-agent-observatory-v2.json`](hh-agent-observatory-v2.json)

Import it in Grafana through **Dashboards → New → Import → Import via dashboard JSON model**. During import select the primary Grafana Cloud Loki/Logs data source.

The dashboard includes:

- completed and failed pipeline runs;
- HH vacancies saved;
- LLM scoring and APPLY decisions;
- GPU deferrals;
- manual-required and error counters;
- pipeline and LLM-decision time series;
- attention/error log view;
- live HH Agent logs.

Dashboard queries intentionally filter on `stream_version="v2"` so an initial historical log backfill does not distort current operational counters.

## Google Drive read bridge

For ChatGPT-side diagnostics, a separate read bridge can query Grafana Cloud Loki and maintain a rolling 24-hour copy in Google Drive under `HH-Agent/observability`.

It produces stable `summary`, `errors-24h` and full `last-24h` files every five minutes. Grafana remains the source of truth; Drive is only a readable mirror. The bridge reuses the Loki credentials already present in the local Alloy config and does not commit or upload those credentials.

Setup and runtime details: [`drive-bridge.md`](drive-bridge.md).

## Useful LogQL

All fresh HH Agent telemetry:

```logql
{service="hh-agent", stream_version="v2"}
```

Processor events:

```logql
{service="hh-agent", stream_version="v2", filename="C:/hh-agent/logs/processor.log"}
```

GPU deferrals:

```logql
{service="hh-agent", stream_version="v2", filename="C:/hh-agent/logs/processor.log"} |= "[DEFER] GPU utilization"
```

Pipeline supervisor:

```logql
{service="hh-agent", stream_version="v2", filename="C:/hh-agent/logs/pipeline_supervisor.log"}
```

## Quick checks

PowerShell:

```powershell
Get-Service Alloy
Invoke-WebRequest http://127.0.0.1:12345/-/ready -UseBasicParsing |
    Select-Object StatusCode, Content
```

Expected state is `Running` and HTTP `200` / `Alloy is ready.`
