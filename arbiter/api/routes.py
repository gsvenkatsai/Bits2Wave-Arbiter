"""
arbiter/api/routes.py
REST route handlers — Branch 4.

SRS traceability:
  FR-9.1  REST endpoints served to live dashboard and Branch 1 simulator.
  §4.1    Exact endpoint paths and request/response contracts (pages 4-5).

Responsibility (confirmed by user):
  All @router handler functions.  server.py includes this router.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from arbiter.benchmarks.runner import BenchmarkRunner
from arbiter.shared.models import CellState, TelemetrySample
from arbiter.telemetry.monitor import DB_PATH, KPITelemetryMonitor
from arbiter.api.websocket import broadcast_tick_update, build_tick_update

# ---------------------------------------------------------------------------
# ASSUMPTIONS — one constant / one place each; easy to change.
# ---------------------------------------------------------------------------
_ASSUMPTIONS: dict[str, str] = {
    "A1": (
        "ingest body is List[TelemetrySample] or single TelemetrySample. "
        "Confirmed by user; pending Akshay/Aaditya confirmation of actual POST shape."
    ),
    "A2": (
        "GET /api/v1/state/cells returns [] — STUB: Branch 3 not merged. "
        "Swap _get_cell_states() body only when Branch 3 integrates."
    ),
    "A3": (
        "POST /api/v1/benchmarks/run returns {status: accepted} — STUB Module 2. "
        "Wire BenchmarkRunner import in Module 3; one call to change."
    ),
    "A4": (
        "decisions/history returns raw DB dicts, NOT ArbitrationDecision model. "
        "audit_decisions DDL is missing twin_eval_count column (shared/database.py). "
        "Flagged for team — schema change requires all-dev sign-off."
    ),
}

# Version string — spec §4.1 page 4 (health endpoint).  One place to update.
API_VERSION: str = "1.0.0"

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Dependency providers (injectable via app.dependency_overrides in tests)
# ---------------------------------------------------------------------------

_monitor_singleton: KPITelemetryMonitor | None = None


def get_monitor() -> KPITelemetryMonitor:
    """
    Returns the shared KPITelemetryMonitor singleton.
    Override in tests: app.dependency_overrides[get_monitor] = lambda: test_monitor
    """
    global _monitor_singleton
    if _monitor_singleton is None:
        _monitor_singleton = KPITelemetryMonitor(db_path=DB_PATH)
    return _monitor_singleton


def get_db_path() -> str:
    """
    Returns the SQLite DB path for direct-query endpoints (decisions/history).
    Override in tests: app.dependency_overrides[get_db_path] = lambda: tmp_db
    Reads from monitor.DB_PATH, which respects the SQLITE_DB_PATH env var.
    """
    return DB_PATH


# ---------------------------------------------------------------------------
# STUB: cell state provider — ASSUMPTION A2
# ---------------------------------------------------------------------------

def _get_cell_states() -> list[CellState]:
    """
    STUB: Branch 3 not merged.

    Returns an empty list until Branch 3's actuator/storage layer populates
    cell state data.  Swap this function's body only; no other change required.
    Listed under STUBS in the Module 2 close-out for integration follow-up.
    """
    return []  # STUB: Branch 3 not merged — see ASSUMPTION A2


# ---------------------------------------------------------------------------
# Local request/response models (Branch 4 API layer only — not in shared/)
# ---------------------------------------------------------------------------

class BenchmarkRunRequest(BaseModel):
    """
    POST /api/v1/benchmarks/run request body.
    Source: spec §4.1 page 5 — exact JSON shape shown.
    """
    scenario: str = Field(
        ...,
        description="Benchmark scenario name, e.g. '2_AM_COLLISION'",
    )
    seeds_count: int = Field(
        default=10,
        description="Number of PRNG seeds (spec §5.2: 10)",
    )
    modes: list[str] = Field(
        default_factory=lambda: ["UNCOORDINATED", "STATIC_PRIORITY", "ARBITER"],
        description="Baseline modes to compare (spec §4.1 page 5)",
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.get("/health")
def health() -> dict:
    """
    GET /api/v1/health — Health probe.
    Source: spec §4.1 page 4.
    Response: {"status": "OK", "version": "1.0.0"} (exact spec text).
    """
    return {"status": "OK", "version": API_VERSION}


@router.post("/telemetry/ingest")
async def ingest_telemetry(
    request: Request,
    monitor: KPITelemetryMonitor = Depends(get_monitor),
) -> dict:
    """
    POST /api/v1/telemetry/ingest — Ingest telemetry from Branch 1 simulator.
    Source: spec §4.1 page 4.

    Accepts List[TelemetrySample] OR a single TelemetrySample (ASSUMPTION A1).
    Uses Request + model_validate to handle both shapes reliably in Pydantic v2
    (a typed Union body parameter has known serialisation edge cases in FastAPI).

    After persisting, broadcasts a TICK_UPDATE to all connected WebSocket clients.
    """
    raw = await request.json()

    if isinstance(raw, list):
        try:
            samples = [TelemetrySample.model_validate(item) for item in raw]
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors())
    elif isinstance(raw, dict):
        try:
            samples = [TelemetrySample.model_validate(raw)]
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors())
    else:
        raise HTTPException(
            status_code=422,
            detail=(
                "Body must be a TelemetrySample object or a list of "
                "TelemetrySample objects."
            ),
        )

    rows = monitor.ingest(samples)

    # Broadcast aggregate KPIs to connected dashboard clients (spec §4.2).
    payload = build_tick_update(samples=samples)
    await broadcast_tick_update(payload)

    return {"rows_written": rows}


@router.get("/state/cells")
def get_cells() -> list[dict]:
    """
    GET /api/v1/state/cells — Latest physical cell configurations.
    Source: spec §4.1 page 5.

    STUB: Returns [] until Branch 3 (actuator/storage) is merged.
    Wire by swapping _get_cell_states() body only — no other change required.
    ASSUMPTION A2.  Listed in Module 2 close-out STUBS section.
    """
    states = _get_cell_states()
    # Serialise CellState models to plain dicts for JSON response.
    return [s.model_dump() for s in states]


@router.get("/decisions/history")
def get_decisions_history(
    limit: int = 50,
    db_path: str = Depends(get_db_path),
) -> list[dict]:
    """
    GET /api/v1/decisions/history?limit=50 — Recent arbitration decisions.
    Source: spec §4.1 page 5.

    Queries audit_decisions table (shared/database.py::SCHEMA_DDL lines 35-45).
    Returns raw dict rows ordered newest-first.
    ASSUMPTION A4: does NOT cast to ArbitrationDecision — audit_decisions DDL
    is missing the twin_eval_count column present in models.py:59.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT *
            FROM   audit_decisions
            ORDER  BY decided_at_epoch_ms DESC
            LIMIT  ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


@router.post("/benchmarks/run", status_code=202)
def run_benchmarks(body: BenchmarkRunRequest) -> dict:
    """
    POST /api/v1/benchmarks/run — Trigger Monte Carlo benchmark run.
    Source: spec §4.1 page 5.
    Wired to arbiter.benchmarks.runner.BenchmarkRunner in Module 3.
    """
    seeds = list(range(101, 101 + body.seeds_count))
    runner = BenchmarkRunner()
    results = runner.run(
        scenario=body.scenario,
        modes=body.modes,
        seeds=seeds,
    )
    return {
        "status": "accepted",
        "scenario": body.scenario,
        "seeds_count": body.seeds_count,
        "modes": body.modes,
        "results": {mode: res.model_dump() for mode, res in results.items()},
    }
