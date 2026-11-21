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
from typing import Any

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
        "POST /api/v1/benchmarks/run executes Monte Carlo runs via BenchmarkRunner. "
        "Returns status='accepted' along with benchmark run results."
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
# Physical cell state provider — wired with 5-cell cluster state
# ---------------------------------------------------------------------------

_latest_cell_states: list[CellState] = []


def _get_cell_states() -> list[CellState]:
    """Retrieve current states of all cells."""
    global _latest_cell_states
    if _latest_cell_states:
        return _latest_cell_states
    from arbiter.simulator.grid import HexagonalGrid
    from arbiter.simulator.cell import CellEntity
    grid = HexagonalGrid()
    cells = grid.generate_5_cell_cluster(isd=500.0)
    baseline_stats = {
        "cell_A": (35, 0.875),
        "cell_B": (4, 0.100),
        "cell_C": (4, 0.100),
        "cell_D": (6, 0.150),
        "cell_E": (5, 0.125),
    }
    _latest_cell_states = [
        CellEntity(c).to_cell_state(
            active_users=baseline_stats.get(c.cell_id, (0, 0.0))[0],
            load_ratio=baseline_stats.get(c.cell_id, (0, 0.0))[1],
        )
        for c in cells.values()
    ]
    return _latest_cell_states


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
def health() -> dict[str, Any]:
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
) -> dict[str, Any]:
    """
    POST /api/v1/telemetry/ingest — Ingest telemetry from Branch 1 simulator.
    Source: spec §4.1 page 4.

    Accepts List[TelemetrySample] OR a single TelemetrySample (ASSUMPTION A1),
    or rich simulation tick updates containing samples and arbitration badges.
    """
    raw = await request.json()
    batch_tick = 0
    ingress_proposals = []
    arbitration_badges = []
    timestamp_epoch_ms = None
    uncoord_dropped = None
    uncoord_power = None

    if isinstance(raw, list):
        try:
            samples = [TelemetrySample.model_validate(item) for item in raw]
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors())
    elif isinstance(raw, dict) and "samples" in raw:
        try:
            samples = [
                TelemetrySample.model_validate(item) for item in raw["samples"]
            ]
            batch_tick = raw.get("batch_tick", 0)
            ingress_proposals = raw.get("ingress_proposals", [])
            arbitration_badges = raw.get("arbitration_badges", [])
            timestamp_epoch_ms = raw.get("timestamp_epoch_ms")
            uncoord_dropped = raw.get("uncoord_dropped_users")
            uncoord_power = raw.get("uncoord_power_kw")

            if "cell_states" in raw:
                global _latest_cell_states
                _latest_cell_states = [
                    CellState.model_validate(c) for c in raw["cell_states"]
                ]
            if "decisions" in raw:
                conn = sqlite3.connect(get_db_path())
                try:
                    with conn:
                        for d in raw["decisions"]:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO audit_decisions (
                                    decision_id, command_id, batch_tick, action,
                                    approved_value, delta_utility, reason_code,
                                    hold_count, decided_at_epoch_ms
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    d["decision_id"], d["command_id"],
                                    d["batch_tick"], d["action"],
                                    d.get("approved_value"),
                                    d["delta_utility"], d["reason_code"],
                                    d.get("hold_count", 0),
                                    d["decided_at_epoch_ms"],
                                ),
                            )
                finally:
                    conn.close()
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
    payload = build_tick_update(
        batch_tick=batch_tick,
        ingress_proposals=ingress_proposals,
        arbitration_badges=arbitration_badges,
        samples=samples,
        timestamp_epoch_ms=timestamp_epoch_ms,
        uncoord_dropped_users=uncoord_dropped,
        uncoord_power_kw=uncoord_power,
    )
    await broadcast_tick_update(payload)

    return {"rows_written": rows}


@router.get("/state/cells")
def get_cells() -> list[dict[str, Any]]:
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
) -> list[dict[str, Any]]:
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
def run_benchmarks(body: BenchmarkRunRequest) -> dict[str, Any]:
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
