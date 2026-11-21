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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from arbiter.shared.database import init_database
from arbiter.telemetry.monitor import DB_PATH
from arbiter.api.routes import router as rest_router
from arbiter.api.websocket import router as ws_router

# CORS origins — from env (see .env.example::CORS_ORIGINS) or hardcoded default.
_cors_raw: str = os.environ.get(
    "CORS_ORIGINS",
    '["http://localhost:8000","http://127.0.0.1:8000"]',
)
CORS_ORIGINS: list[str] = json.loads(_cors_raw)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
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

# Dashboard static files — MUST be mounted LAST (catch-all "/" route).
# Conditional: arbiter/dashboard/ is created in Module 4; skip if absent.
# Dashboard (Module 4) must handle empty cells list gracefully per user decision.
_DASHBOARD_DIR: str = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dashboard")
)
if os.path.isdir(_DASHBOARD_DIR):
    app.mount(
        "/",
        StaticFiles(directory=_DASHBOARD_DIR, html=True),
        name="dashboard",
    )
