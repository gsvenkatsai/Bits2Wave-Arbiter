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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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


app.mount(
    "/dashboard",
    StaticFiles(directory=_DASHBOARD_DIR, html=True),
    name="dashboard_prefix",
)
app.mount(
    "/",
    StaticFiles(directory=_DASHBOARD_DIR, html=True),
    name="dashboard",
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
