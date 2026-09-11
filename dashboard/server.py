"""GET-only local API and same-origin static frontend."""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from dashboard.sources import LOGS, SnapshotReader, read_log


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

    @app.get("/api/logs/{name}")
    def log_tail(name: str, lines: int = Query(80, ge=1, le=200)):
        if name not in LOGS:
            raise HTTPException(status_code=404, detail="Unknown log")
        return read_log(reader.root, name, lines)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
