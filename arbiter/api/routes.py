"""
arbiter/api/routes.py
REST route handlers — Branch 4 (ARBITER v1 & v2 Feature APIs).

SRS traceability:
  FR-9.1  REST endpoints served to live dashboard and Branch 1 simulator.
  §4.1    Exact endpoint paths and request/response contracts (pages 4-5).
  v2 Features:
    #4 Flight Recorder UI & API (decisions, counterfactual replay, multi-tick plans)
    #5 Uncertainty View (calibrated twin intervals, drift injection hook)
    #6 Dynamic RiskDial & Multi-Objective Pareto Frontier
    #7 Environmental and Economic Impact Telemetry & Auditing
    #8 Offline Optimality Gap Benchmark Integration
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from arbiter.benchmarks.pareto import ParetoFrontierResult, ParetoFrontierSweeper
from arbiter.benchmarks.runner import BenchmarkRunner
from arbiter.shared.models import CellState, TelemetrySample
from arbiter.shared.v2_contracts import (
    ArbitrationPlan,
    CounterfactualResult,
    ImpactRecord,
    OptimalityResult,
    PredictionInterval,
    RiskDial,
    TwinPredictionV2,
)
from arbiter.telemetry.impact import (
    IMPACT_ASSUMPTIONS,
    ImpactCalculator,
    fetch_latest_impact,
    store_impact_record,
)
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
    "A5_V2_COUNTERFACTUAL": (
        "GET /api/v1/recorder/counterfactual/{decision_id} invokes "
        "arbiter.simulator.counterfactual.compute_counterfactual if present, "
        "checking v2_counterfactuals cache and fixtures/v2/counterfactual.json as fallback. "
        "Aged-out/missing snapshot raises HTTP 404/410."
    ),
}

# Version string — spec §4.1 page 4 (health endpoint).
API_VERSION: str = "1.0.0"

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Dependency providers (injectable via app.dependency_overrides in tests)
# ---------------------------------------------------------------------------

_monitor_singleton: KPITelemetryMonitor | None = None
_decision_engine_singleton: Any = None
_active_risk_dial: RiskDial = RiskDial(
    weight_coverage=0.30,
    weight_energy=0.20,
    weight_interference=0.20,
    weight_drop_rate=0.30,
    label="Balanced",
)
_current_twin_prediction: Optional[TwinPredictionV2] = None


def get_monitor() -> KPITelemetryMonitor:
    """Returns the shared KPITelemetryMonitor singleton."""
    global _monitor_singleton
    if _monitor_singleton is None:
        _monitor_singleton = KPITelemetryMonitor(db_path=DB_PATH)
    return _monitor_singleton


def get_db_path() -> str:
    """Returns the SQLite DB path for direct-query endpoints."""
    return DB_PATH


def get_decision_engine() -> Any:
    """Returns the registered decision engine (if injected or registered)."""
    global _decision_engine_singleton
    if _decision_engine_singleton is None:
        try:
            from arbiter.engine.ladder import DecisionLadderEngine

            _decision_engine_singleton = DecisionLadderEngine()
        except Exception:
            pass
    return _decision_engine_singleton


def set_decision_engine(engine: Any) -> None:
    """Register active decision engine instance."""
    global _decision_engine_singleton
    _decision_engine_singleton = engine


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
# Twin Prediction & Drift Helpers
# ---------------------------------------------------------------------------

def _get_or_load_twin_prediction() -> TwinPredictionV2:
    """Load or return the current TwinPredictionV2 state with uncertainty intervals."""
    global _current_twin_prediction
    if _current_twin_prediction is not None:
        return _current_twin_prediction

    from arbiter.twin.evaluator import get_global_evaluator

    evaluator = get_global_evaluator()
    if evaluator is not None and hasattr(evaluator, "predict_v2"):
        try:
            pred = evaluator.predict_v2([])
            if pred is not None:
                _current_twin_prediction = pred
                return _current_twin_prediction
        except Exception:
            pass

    fixture_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "v2", "twin_prediction_v2.json")
    )
    if os.path.exists(fixture_path):
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _current_twin_prediction = TwinPredictionV2.model_validate(data)
    else:
        _current_twin_prediction = TwinPredictionV2(
            candidate_id="cand-2am-lb-offload-modify",
            intervals=[
                PredictionInterval(kpi="coverage_ratio", mean=0.942, lower=0.915, upper=0.965, coverage=0.90),
                PredictionInterval(kpi="drop_rate_ratio", mean=0.004, lower=0.001, upper=0.008, coverage=0.90),
                PredictionInterval(kpi="normalized_energy", mean=0.448, lower=0.410, upper=0.485, coverage=0.90),
                PredictionInterval(kpi="normalized_interference", mean=0.285, lower=0.250, upper=0.320, coverage=0.90),
            ],
            calibrated=True,
            n_calibration=50,
        )
    return _current_twin_prediction


def _inject_twin_drift(drift_scale: float = 1.8) -> TwinPredictionV2:
    """Drift hook: widens intervals and uncalibrates the digital twin prediction."""
    global _current_twin_prediction
    base = _get_or_load_twin_prediction()

    scale = max(1.1, float(drift_scale))
    widened_intervals: List[PredictionInterval] = []
    for intv in base.intervals:
        diff_lower = intv.mean - intv.lower
        diff_upper = intv.upper - intv.mean
        if intv.upper <= 1.0:
            new_upper = round(min(1.0, intv.mean + diff_upper * scale), 4)
        else:
            new_upper = round(intv.mean + diff_upper * scale, 4)
        new_upper = max(new_upper, intv.upper)

        if intv.lower >= 0.0:
            new_lower = round(max(0.0, intv.mean - diff_lower * scale), 4)
        else:
            new_lower = round(intv.mean - diff_lower * scale, 4)
        new_lower = min(new_lower, intv.lower)

        widened_intervals.append(
            PredictionInterval(
                kpi=intv.kpi,
                mean=intv.mean,
                lower=new_lower,
                upper=new_upper,
                coverage=round(max(0.5, intv.coverage - 0.15), 2),
            )
        )

    _current_twin_prediction = TwinPredictionV2(
        candidate_id=base.candidate_id,
        intervals=widened_intervals,
        calibrated=False,
        n_calibration=0,
    )
    return _current_twin_prediction


# ---------------------------------------------------------------------------
# Local request/response models
# ---------------------------------------------------------------------------

class BenchmarkRunRequest(BaseModel):
    """POST /api/v1/benchmarks/run request body."""
    scenario: str = Field(..., description="Benchmark scenario name, e.g. '2_AM_COLLISION'")
    seeds_count: int = Field(default=10, description="Number of PRNG seeds")
    modes: list[str] = Field(
        default_factory=lambda: ["UNCOORDINATED", "STATIC_PRIORITY", "ARBITER"],
        description="Baseline modes to compare",
    )


class TwinDriftRequest(BaseModel):
    """POST /api/v1/twin/drift request body."""
    drift_scale: float = Field(default=1.8, ge=1.0, le=5.0, description="Scale factor to widen bounds")


# ---------------------------------------------------------------------------
# Route handlers: Core & Telemetry
# ---------------------------------------------------------------------------

@router.get("/health")
def health() -> dict[str, Any]:
    """GET /api/v1/health — Health probe."""
    return {"status": "OK", "version": API_VERSION}


@router.post("/telemetry/ingest")
async def ingest_telemetry(
    request: Request,
    monitor: KPITelemetryMonitor = Depends(get_monitor),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """
    POST /api/v1/telemetry/ingest — Ingest telemetry from simulator.
    Persists samples and broadcasts real-time TICK_UPDATE with uncertainty & impact.
    """
    raw = await request.json()
    batch_tick = 0
    ingress_proposals: list[dict[str, Any]] = []
    arbitration_badges: list[dict[str, Any]] = []
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
                conn = sqlite3.connect(db_path)
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
            detail="Body must be a TelemetrySample object or a list of TelemetrySample objects.",
        )

    rows = monitor.ingest(samples)

    # Compute and store environmental impact if baseline power available
    if uncoord_power is not None and samples:
        arb_power_w = sum(s.energy_consumption_w for s in samples)
        uncoord_power_w = uncoord_power * 1000.0
        energy_saved_w = max(0.0, uncoord_power_w - arb_power_w)
        try:
            conn = sqlite3.connect(db_path)
            try:
                calc = ImpactCalculator()
                impact_record = calc.calculate_tick_impact(
                    tick=batch_tick, energy_saved_w=energy_saved_w
                )
                store_impact_record(conn, impact_record)
            finally:
                conn.close()
        except Exception:
            pass

    # Broadcast aggregate KPIs to connected dashboard clients
    current_twin = _get_or_load_twin_prediction()
    payload = build_tick_update(
        batch_tick=batch_tick,
        ingress_proposals=ingress_proposals,
        arbitration_badges=arbitration_badges,
        samples=samples,
        timestamp_epoch_ms=timestamp_epoch_ms,
        uncoord_dropped_users=uncoord_dropped,
        uncoord_power_kw=uncoord_power,
        twin_prediction=current_twin.model_dump(),
    )
    await broadcast_tick_update(payload)

    return {"rows_written": rows}


@router.get("/state/cells")
def get_cells() -> list[dict[str, Any]]:
    """GET /api/v1/state/cells — Latest physical cell configurations."""
    states = _get_cell_states()
    return [s.model_dump() for s in states]


@router.get("/decisions/history")
def get_decisions_history(
    limit: int = 50,
    db_path: str = Depends(get_db_path),
) -> list[dict[str, Any]]:
    """GET /api/v1/decisions/history?limit=50 — Recent arbitration decisions."""
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


# ---------------------------------------------------------------------------
# Feature #4: Flight Recorder UI & API
# ---------------------------------------------------------------------------

@router.get("/recorder/decisions")
def get_recorder_decisions(
    limit: int = 50,
    db_path: str = Depends(get_db_path),
) -> list[dict[str, Any]]:
    """
    GET /api/v1/recorder/decisions — Decisions with ids, ticks, actions, and utilities.
    Source: Feature #4 Flight Recorder.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT decision_id, command_id, batch_tick, action, approved_value,
                   delta_utility, reason_code, hold_count, decided_at_epoch_ms
            FROM   audit_decisions
            ORDER  BY batch_tick DESC, decided_at_epoch_ms DESC
            LIMIT  ?
            """,
            (max(1, limit),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            return rows

        # Fallback to fixture counterfactual decision for demo if DB is empty
        fixture_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "v2", "counterfactual.json")
        )
        if os.path.exists(fixture_path):
            with open(fixture_path, "r", encoding="utf-8") as f:
                cf = json.load(f)
            return [
                {
                    "decision_id": cf.get("decision_id", "dec-2am-collision-06"),
                    "command_id": "cmd-es-cell-b-sleep",
                    "batch_tick": cf.get("batch_tick", 6),
                    "action": "DELAY",
                    "approved_value": None,
                    "delta_utility": cf.get("diff", {}).get("utility", 0.463),
                    "reason_code": "DELAY_UNTIL_OFFLOAD_SETTLES",
                    "hold_count": 2,
                    "decided_at_epoch_ms": 1700000006000,
                }
            ]
        return []
    finally:
        conn.close()


@router.get("/recorder/counterfactual/{decision_id}")
def get_recorder_counterfactual(
    decision_id: str,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """
    GET /api/v1/recorder/counterfactual/{decision_id}
    Retrieves counterfactual rollout comparing ARBITER against raw actuation.
    Caches results in v2_counterfactuals table; returns clear HTTP 404/410 if snapshot aged out.
    """
    conn = sqlite3.connect(db_path)
    try:
        # 1. Check v2_counterfactuals SQLite cache
        cur = conn.execute(
            "SELECT payload FROM v2_counterfactuals WHERE decision_id = ?",
            (decision_id,),
        )
        row = cur.fetchone()
        if row:
            return json.loads(row[0])  # type: ignore[no-any-return]

        # 2. Try teammate's counterfactual engine if landed
        try:
            is_2am_match = decision_id in ("dec-2am-collision-06", "latest")
            cur_check = conn.execute(
                "SELECT batch_tick, action, approved_value, delta_utility, "
                "reason_code FROM audit_decisions WHERE decision_id = ?",
                (decision_id,),
            )
            audit_row = cur_check.fetchone()

            if is_2am_match or audit_row is not None:
                from arbiter.controllers import trigger_2am_collision
                from arbiter.shared.models import (
                    ActionType,
                    ArbitrationDecision,
                    ReasonCode,
                )
                from arbiter.simulator.counterfactual import (
                    compute_counterfactual,
                )
                from arbiter.simulator.engine import SimulationEngine

                engine = SimulationEngine(seed=42)
                target_tick = audit_row[0] if audit_row else 6
                for _ in range(target_tick):
                    engine.step()

                proposals = trigger_2am_collision(
                    engine, base_ts_ms=engine.simulated_epoch_ms
                )
                if audit_row:
                    decs = [
                        ArbitrationDecision(
                            decision_id=decision_id,
                            command_id="cmd-es-sleep-01",
                            batch_tick=target_tick,
                            action=ActionType(audit_row[1]),
                            approved_value=audit_row[2],
                            delta_utility=audit_row[3],
                            reason_code=ReasonCode(audit_row[4]),
                            hold_count=1,
                            decided_at_epoch_ms=engine.simulated_epoch_ms,
                        )
                    ]
                else:
                    decs = [
                        ArbitrationDecision(
                            decision_id=decision_id,
                            command_id="cmd-es-sleep-01",
                            batch_tick=6,
                            action=ActionType.DELAY,
                            approved_value=None,
                            delta_utility=0.0,
                            reason_code=ReasonCode.ACTIVE_HO,
                            hold_count=1,
                            decided_at_epoch_ms=engine.simulated_epoch_ms,
                        )
                    ]

                cf_res = compute_counterfactual(
                    snapshot=engine,
                    proposals=proposals,
                    decisions=decs,
                    horizon_ticks=10,
                    decision_id=decision_id,
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO v2_counterfactuals (
                        decision_id, batch_tick, horizon_ticks, payload
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        cf_res.decision_id,
                        cf_res.batch_tick,
                        cf_res.horizon_ticks,
                        cf_res.model_dump_json(),
                    ),
                )
                conn.commit()
                return cf_res.model_dump()
        except Exception:
            pass

        # 3. Check fixture fallback
        fixture_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "v2", "counterfactual.json")
        )
        if os.path.exists(fixture_path):
            with open(fixture_path, "r", encoding="utf-8") as f:
                cf_data = json.load(f)
            if cf_data.get("decision_id") == decision_id or decision_id == "latest":
                result_cf = CounterfactualResult.model_validate(cf_data)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO v2_counterfactuals (decision_id, batch_tick, horizon_ticks, payload)
                    VALUES (?, ?, ?, ?)
                    """,
                    (result_cf.decision_id, result_cf.batch_tick, result_cf.horizon_ticks, result_cf.model_dump_json()),
                )
                conn.commit()
                return result_cf.model_dump()

        # 4. Snapshot aged out / not found
        raise HTTPException(
            status_code=404,
            detail=(
                f"Counterfactual simulation snapshot for decision '{decision_id}' "
                "has aged out of the snapshot ring or does not exist."
            ),
        )
    finally:
        conn.close()


@router.get("/plans")
def get_plans(db_path: str = Depends(get_db_path)) -> list[dict[str, Any]]:
    """
    GET /api/v1/plans — Multi-tick sequential arbitration plans.
    Reads from v2_plans table or falls back to fixtures/v2/plan.json.
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT payload FROM v2_plans ORDER BY created_tick DESC")
        rows = cur.fetchall()
        if rows:
            return [json.loads(r[0]) for r in rows]

        # Fixture fallback
        fixture_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "v2", "plan.json")
        )
        if os.path.exists(fixture_path):
            with open(fixture_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
            plan = ArbitrationPlan.model_validate(plan_data)
            conn.execute(
                """
                INSERT OR REPLACE INTO v2_plans (plan_id, source_command_id, created_tick, expires_tick, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plan.plan_id, plan.source_command_id, plan.created_tick, plan.expires_tick, plan.model_dump_json()),
            )
            conn.commit()
            return [plan.model_dump()]
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feature #5: Uncertainty View & Digital Twin Drift
# ---------------------------------------------------------------------------

@router.get("/twin/prediction")
def get_twin_prediction() -> dict[str, Any]:
    """
    GET /api/v1/twin/prediction — Calibrated digital twin prediction intervals.
    """
    pred = _get_or_load_twin_prediction()
    return pred.model_dump()


@router.post("/twin/drift")
def post_twin_drift(
    body: Optional[TwinDriftRequest] = None,
) -> dict[str, Any]:
    """
    POST /api/v1/twin/drift — Inject twin drift demo hook.
    Widens uncertainty bounds and toggles uncalibrated status.
    """
    scale = body.drift_scale if body else 1.8
    updated = _inject_twin_drift(drift_scale=scale)
    return updated.model_dump()


# ---------------------------------------------------------------------------
# Feature #6: Dynamic RiskDial & Pareto Frontier
# ---------------------------------------------------------------------------

@router.post("/risk-dial")
def set_risk_dial(
    dial: RiskDial,
    engine: Any = Depends(get_decision_engine),
) -> dict[str, Any]:
    """
    POST /api/v1/risk-dial — Set dynamic multi-objective RiskDial weights.
    Applies weights to the decision engine via set_weights.
    """
    global _active_risk_dial
    _active_risk_dial = dial

    # Apply to decision engine if engine implements set_weights
    if engine is not None and hasattr(engine, "set_weights"):
        engine.set_weights(dial)

    return dial.model_dump()


@router.get("/risk-dial")
def get_risk_dial() -> dict[str, Any]:
    """
    GET /api/v1/risk-dial — Get currently active RiskDial weights.
    """
    return _active_risk_dial.model_dump()


@router.get("/benchmarks/pareto")
def get_pareto_frontier(
    seed: int = Query(default=42, description="PRNG seed for sweep evaluation"),
) -> dict[str, Any]:
    """
    GET /api/v1/benchmarks/pareto — Sweeps energy-vs-drop-risk Pareto frontier.
    Returns computed points with the active dial setting highlighted.
    """
    sweeper = ParetoFrontierSweeper(seed=seed)
    frontier_result: ParetoFrontierResult = sweeper.sweep(active_dial=_active_risk_dial)
    return frontier_result.model_dump()


# ---------------------------------------------------------------------------
# Feature #7: Environmental and Economic Impact
# ---------------------------------------------------------------------------

@router.get("/impact")
def get_impact(
    db_path: str = Depends(get_db_path),
    hour: int = Query(default=2, ge=0, le=23, description="Simulation hour of day"),
) -> dict[str, Any]:
    """
    GET /api/v1/impact — Impact telemetry (kWh, ₹, gCO2) and 1,000-site annual extrapolation.
    """
    conn = sqlite3.connect(db_path)
    try:
        calc = ImpactCalculator()
        latest = fetch_latest_impact(conn)
        if latest is None:
            fixture_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "v2", "impact.json")
            )
            if os.path.exists(fixture_path):
                with open(fixture_path, "r", encoding="utf-8") as f:
                    fix_data = json.load(f)
                latest = ImpactRecord.model_validate(fix_data)
            else:
                latest = calc.calculate_tick_impact(
                    tick=0, energy_saved_w=20140.0, duration_s=0.1, hour=hour
                )
            store_impact_record(conn, latest)

        # Compute cluster power saved in Watts from kWh over 100ms or nominal delta
        cluster_saved_w = (latest.energy_saved_kwh * 3_600_000.0) / 0.1 if latest.energy_saved_kwh > 0 else 20140.0

        extrapolation = calc.calculate_extrapolation_1000_sites(
            cluster_energy_saved_w=cluster_saved_w,
            cluster_sites=5,
            target_sites=1000,
        )

        return {
            "latest": latest.model_dump(),
            "extrapolation_1000_sites": extrapolation,
            "assumptions": IMPACT_ASSUMPTIONS,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feature #8: Offline Optimality Gap Benchmark
# ---------------------------------------------------------------------------

@router.get("/optimality")
@router.get("/benchmarks/optimality")
def get_optimality(
    compute: bool = Query(
        default=False, description="Run on-demand live optimality computation"
    ),
    seed: int = Query(default=42, description="PRNG seed for optimality run"),
) -> dict[str, Any]:
    """
    GET /api/v1/optimality and /api/v1/benchmarks/optimality.
    Optimality benchmark comparing ARBITER against offline solver.
    Reads from fixtures/v2/optimality.json or computes live when compute=True.
    """
    if compute:
        try:
            from arbiter.simulator.optimality import compute_optimality

            computed = compute_optimality(seed=seed)
            return computed.model_dump()
        except Exception:
            pass

    opt_file = os.environ.get(
        "ARBITER_OPTIMALITY_FILE",
        os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "fixtures",
                "v2",
                "optimality.json",
            )
        ),
    )
    if os.path.exists(opt_file):
        with open(opt_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = OptimalityResult.model_validate(data)
        return result.model_dump()

    try:
        from arbiter.simulator.optimality import compute_optimality

        computed = compute_optimality(seed=seed)
        return computed.model_dump()
    except Exception:
        raise HTTPException(
            status_code=404,
            detail="Optimality benchmark results file not found.",
        )


# ---------------------------------------------------------------------------
# Benchmarks execution
# ---------------------------------------------------------------------------

@router.post("/benchmarks/run", status_code=202)
def run_benchmarks(body: BenchmarkRunRequest) -> dict[str, Any]:
    """POST /api/v1/benchmarks/run — Trigger Monte Carlo benchmark run."""
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
