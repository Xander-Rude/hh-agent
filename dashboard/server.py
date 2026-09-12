"""GET-only local API and same-origin static frontend."""
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from dashboard.sources import LOGS, RESUME_ID, SnapshotReader, read_log


def _telemetry(root: Path) -> dict:
    result = {
        "hard_filter_rejects": None,
        "resume": {
            "views": None,
            "invitations": None,
            "raises": None,
            "observed_at": None,
            "raises_tracking_since": None,
        },
    }
    try:
        connection = sqlite3.connect(root / "data" / "hh_agent.db", timeout=0.1)
        connection.execute("PRAGMA query_only=ON")
        result["hard_filter_rejects"] = connection.execute(
            "SELECT count(*) FROM evaluations e WHERE e.decision='reject' "
            "AND e.id=(SELECT e2.id FROM evaluations e2 WHERE e2.vacancy_id=e.vacancy_id "
            "ORDER BY e2.created_at DESC,e2.id DESC LIMIT 1) "
            "AND (e.model LIKE 'hard-filter/%' OR e.model LIKE 'hard-filter+appeal/%' "
            "OR e.model LIKE 'hard-filter+appeal-backfill/%')"
        ).fetchone()[0]
        try:
            latest = connection.execute(
                "SELECT views,invitations,observed_at FROM resume_metrics "
                "WHERE resume_id=? AND (views IS NOT NULL OR invitations IS NOT NULL) "
                "ORDER BY observed_at DESC,id DESC LIMIT 1",
                (RESUME_ID,),
            ).fetchone()
            if latest:
                result["resume"]["views"] = latest[0]
                result["resume"]["invitations"] = latest[1]
                result["resume"]["observed_at"] = latest[2]
            raises = connection.execute(
                "SELECT COALESCE(sum(raises_delta),0),min(observed_at) FROM resume_metrics "
                "WHERE resume_id=? AND raises_delta>0",
                (RESUME_ID,),
            ).fetchone()
            result["resume"]["raises"] = int(raises[0] or 0)
            result["resume"]["raises_tracking_since"] = raises[1]
        except sqlite3.Error:
            pass
        connection.close()
    except (sqlite3.Error, OSError):
        pass
    return result


def create_app(source_root: Path | None = None) -> FastAPI:
    root = source_root or Path(__file__).resolve().parent.parent
    reader = SnapshotReader(root)
    static = Path(__file__).resolve().parent / "static"
    app = FastAPI(title="HH Agent • Observatory", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.get("/api/snapshot")
    def snapshot():
        return reader.snapshot()

    @app.get("/api/telemetry")
    def telemetry():
        return _telemetry(reader.root)

    @app.get("/api/logs/{name}")
    def log_tail(name: str, lines: int = Query(80, ge=1, le=200)):
        if name not in LOGS:
            raise HTTPException(status_code=404, detail="Unknown log")
        return read_log(reader.root, name, lines)

    @app.get("/")
    def index():
        html = (static / "index.html").read_text(encoding="utf-8")
        html = html.replace(
            "</body>",
            '<script src="/static/telemetry.js" defer></script></body>',
        )
        return HTMLResponse(html)

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
