"""
arbiter/api/server.py
FastAPI application — Branch 4.

SRS traceability:
  FR-9.1  Live "2 AM Collision Monitor" dashboard with ARBITER action badges.
  §4.1    REST endpoints (pages 4-5).
  §4.2    WebSocket broadcast (pages 5-6).

Responsibility (confirmed by user):
  - Create the FastAPI `app` instance.
  - Register lifespan (init_database on startup).
  - Add CORS middleware.
  - Include REST router (routes.py) and WS router (websocket.py).
  - Mount dashboard static files LAST (catch-all "/" route).
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from arbiter.shared.database import init_database
from arbiter.telemetry.monitor import DB_PATH
from arbiter.api.routes import router as rest_router
from arbiter.api.websocket import router as ws_router

# CORS origins — from env (see .env.example) or hardcoded default.
_cors_raw: str = os.environ.get(
    "CORS_ORIGINS",
    '["http://localhost:8000","http://127.0.0.1:8000"]',
)
CORS_ORIGINS: list[str] = json.loads(_cors_raw)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Initialise SQLite schema on startup (idempotent — shared DDL uses
    CREATE TABLE IF NOT EXISTS).  Schema source: shared/database.py.
    """
    init_database(DB_PATH)
    yield


app = FastAPI(
    title="ARBITER API",
    description=(
        "KPI Telemetry Monitor, Live 2 AM Collision Dashboard "
        "& Benchmarking Suite — Branch 4"
    ),
    version="1.0.0",   # spec §4.1 page 4 — matches /api/v1/health response
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(ws_router)

# Dashboard static files — mounted at /dashboard and / (catch-all route).
_DASHBOARD_DIR: str = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dashboard")
)
os.makedirs(_DASHBOARD_DIR, exist_ok=True)

_FIXTURES_DIR: str = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "fixtures")
)
if os.path.exists(_FIXTURES_DIR):
    app.mount(
        "/fixtures",
        StaticFiles(directory=_FIXTURES_DIR),
        name="fixtures",
    )


@app.get("/3d", response_class=FileResponse)
def get_3d_dashboard() -> FileResponse:
    """Serve the 3D Cellular RAN Visualization Dashboard."""
    file_path = os.path.join(_DASHBOARD_DIR, "3d.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="3d.html not found")
    return FileResponse(file_path)


_SITE_DIR: str = os.path.abspath(
    os.path.join(_DASHBOARD_DIR, "site")
)
os.makedirs(_SITE_DIR, exist_ok=True)


@app.get("/problem", response_class=FileResponse)
def get_site_problem_page() -> FileResponse:
    file_path = os.path.join(_SITE_DIR, "problem.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="problem.html not found")
    return FileResponse(file_path)


@app.get("/how-it-works", response_class=FileResponse)
def get_site_how_it_works_page() -> FileResponse:
    file_path = os.path.join(_SITE_DIR, "how-it-works.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="how-it-works.html not found")
    return FileResponse(file_path)


@app.get("/results", response_class=FileResponse)
def get_site_results_page() -> FileResponse:
    file_path = os.path.join(_SITE_DIR, "results.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="results.html not found")
    return FileResponse(file_path)


@app.get("/monitor", response_class=FileResponse)
def get_site_monitor_page() -> FileResponse:
    file_path = os.path.join(_SITE_DIR, "monitor.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="monitor.html not found")
    return FileResponse(file_path)


@app.get("/team", response_class=FileResponse)
def get_site_team_page() -> FileResponse:
    file_path = os.path.join(_SITE_DIR, "team.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="team.html not found")
    return FileResponse(file_path)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"], include_in_schema=False)
def api_not_found(path: str) -> None:
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/404", response_class=FileResponse, include_in_schema=False)
def get_site_404_page() -> FileResponse:
    file_path = os.path.join(_SITE_DIR, "404.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="404.html not found")
    return FileResponse(file_path, status_code=404)


@app.get("/favicon.ico", include_in_schema=False)
def get_favicon() -> FileResponse:
    fav_path = os.path.join(_SITE_DIR, "img", "favicon.svg")
    if os.path.exists(fav_path):
        return FileResponse(fav_path, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: Exception) -> Response:
    if request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", "Not Found")
        return JSONResponse(status_code=404, content={"detail": detail})
    file_path = os.path.join(_SITE_DIR, "404.html")
    if os.path.exists(file_path):
        return FileResponse(file_path, status_code=404)
    detail = getattr(exc, "detail", "Not Found")
    return JSONResponse(status_code=404, content={"detail": detail})


app.mount(
    "/dashboard",
    StaticFiles(directory=_DASHBOARD_DIR, html=True),
    name="dashboard_prefix",
)
app.mount(
    "/",
    StaticFiles(directory=_SITE_DIR, html=True),
    name="site_root",
)


def main() -> None:
    """CLI entry point for starting the server or recording offline 2 AM collision frames."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ARBITER API Server & Offline 2 AM Collision Recorder"
    )
    parser.add_argument(
        "--record",
        type=str,
        default=None,
        metavar="FILE",
        help="Record real 2 AM collision run (seed 42, 80 ticks) to target .jsonl file",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Server bind host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server bind port (default: 8000)",
    )
    args = parser.parse_args()

    if args.record:
        from arbiter.telemetry.recorder import record_2am_collision

        print("[RECORDER] Simulating real 2 AM collision run (seed 42, 80 ticks)...")
        frames = record_2am_collision(output_path=args.record, seed=42, ticks=80)
        print(f"[RECORDER] Successfully recorded {len(frames)} frames to {args.record}")
        return

    import uvicorn

    uvicorn.run("arbiter.api.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
