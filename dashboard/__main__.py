"""Run in the foreground only: python -m dashboard [--source-root C:\\hh-agent]."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Read-only local HH Agent dashboard")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    # No reloader, scheduler, worker process, startup writes or browser automation.
    import uvicorn
    from dashboard.server import create_app
    print(f"HH Agent dashboard: http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(create_app(args.source_root), host="127.0.0.1", port=args.port, workers=1,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
