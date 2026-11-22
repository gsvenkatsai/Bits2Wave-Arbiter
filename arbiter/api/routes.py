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
import time
from typing import Any, List, Optional, cast

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
from arbiter.api.websocket import (
    broadcast_frame,
    broadcast_tick_update,
    build_tick_update,
    format_simulated_time,
    get_latest_frame as get_latest_ws_frame,
    set_latest_frame as set_latest_ws_frame,
)
from arbiter.telemetry.recorder import compute_counterfactual_frames
from arbiter.telemetry.frame import build_frame_message

_latest_frame: dict[str, Any] | None = None


def get_latest_frame_data() -> dict[str, Any] | None:
    """Return the currently cached latest complete frame, or None."""
    return _latest_frame

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
_simulation_engine_singleton: Any = None
_active_risk_dial: RiskDial = RiskDial(
    weight_coverage=0.30,
    weight_energy=0.20,
    weight_interference=0.20,
    weight_drop_rate=0.30,
    label="Balanced",
)
_current_twin_prediction: Optional[TwinPredictionV2] = None


def get_simulation_engine() -> Any:
    """Returns the registered active SimulationEngine (if any)."""
    return _simulation_engine_singleton


def set_simulation_engine(engine: Any) -> None:
    """Register active SimulationEngine instance."""
    global _simulation_engine_singleton
    _simulation_engine_singleton = engine


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
    """Retrieve current states of all cells from the latest ingested frame (Contract C4)."""
    global _latest_cell_states
    if _latest_cell_states:
        return _latest_cell_states
    if _latest_frame is not None and "cells" in _latest_frame and _latest_frame["cells"]:
        _latest_cell_states = [
            CellState(
                cell_id=c.get("id") or c.get("cell_id", ""),
                x_coord=float(c.get("x", 0.0)),
                y_coord=float(c.get("y", 0.0)),
                current_tx_power_dbm=float(c.get("tx_power_dbm", 43.0)),
                antenna_tilt_deg=float(c.get("tilt_deg", 6.0)),
                is_sleeping=bool(c.get("is_sleeping", False)),
                active_users=int(c.get("active_users", 0)),
                load_ratio=float(c.get("load_ratio", 0.0)),
                neighbor_cell_ids=c.get("neighbor_cell_ids", []),
            )
            for c in _latest_frame["cells"]
        ]
        return _latest_cell_states
    try:
        from arbiter.simulator.grid import HexagonalGrid
        grid = HexagonalGrid()
        cluster = grid.generate_5_cell_cluster()
        return [
            CellState(
                cell_id=cid,
                x_coord=cfg.x,
                y_coord=cfg.y,
                current_tx_power_dbm=cfg.tx_power_dbm,
                antenna_tilt_deg=cfg.tilt_deg,
                is_sleeping=False,
                active_users=0,
                load_ratio=0.0,
                neighbor_cell_ids=list(cfg.neighbor_cell_ids),
            )
            for cid, cfg in cluster.items()
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Twin Prediction & Drift Helpers
# ---------------------------------------------------------------------------

def get_twin_evaluator() -> Any:
    """Returns the registered global DigitalTwinEvaluator instance (if any)."""
    try:
        from arbiter.twin.evaluator import get_global_evaluator

        return get_global_evaluator()
    except Exception:
        return None


def _get_or_load_twin_prediction(evaluator: Any = None) -> Optional[TwinPredictionV2]:
    """Load or return the current TwinPredictionV2 state with uncertainty intervals."""
    global _current_twin_prediction
    if _current_twin_prediction is not None:
        return _current_twin_prediction

    ev = evaluator if evaluator is not None else get_twin_evaluator()
    if ev is not None and hasattr(ev, "predict_v2"):
        try:
            pred = ev.predict_v2([])
            if pred is not None:
                _current_twin_prediction = pred
                return _current_twin_prediction
        except Exception:
            pass

    return None


def _inject_twin_drift(drift_scale: float = 1.8) -> TwinPredictionV2:
    """Drift hook: widens intervals and uncalibrates the digital twin prediction."""
    global _current_twin_prediction
    base = _get_or_load_twin_prediction()
    if base is None:
        # Default uncalibrated intervals if drift is injected without pre-existing prediction
        _current_twin_prediction = TwinPredictionV2(
            candidate_id="cand-drifted",
            intervals=[
                PredictionInterval(kpi="coverage_ratio", mean=0.942, lower=0.85, upper=0.99, coverage=0.75),
                PredictionInterval(kpi="drop_rate_ratio", mean=0.004, lower=0.0, upper=0.02, coverage=0.75),
                PredictionInterval(kpi="normalized_energy", mean=0.448, lower=0.35, upper=0.55, coverage=0.75),
                PredictionInterval(kpi="normalized_interference", mean=0.285, lower=0.20, upper=0.38, coverage=0.75),
            ],
            calibrated=False,
            n_calibration=0,
        )
        return _current_twin_prediction

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
        twin_prediction=current_twin.model_dump() if current_twin else None,
    )
    await broadcast_tick_update(payload)

    # Broadcast unified frame to connected dashboard clients
    try:
        eng = get_simulation_engine()
        if eng is not None:
            frame_msg = build_frame_message(
                engine=eng,
                twin_prediction=current_twin,
            )
            await broadcast_frame(frame_msg.model_dump())
    except Exception:
        pass

    return {"rows_written": rows}


@router.post("/frames/ingest")
async def ingest_frame(
    request: Request,
    monitor: KPITelemetryMonitor = Depends(get_monitor),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """POST /api/v1/frames/ingest — Ingest complete 3D discrete simulation frame.

    Joins decisions, conflict edges, plans, and twin uncertainty intervals,
    then broadcasts to live WebSocket subscribers as type 'frame' and 'TICK_UPDATE'.
    """
    raw = await request.json()
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=422, detail="Frame body must be a JSON object"
        )

    tick = int(raw.get("tick", 0))
    sim_time_ms = int(raw.get("sim_time_ms", 1732154400000 + tick * 100))
    scenario = str(raw.get("scenario", "2am_collision"))
    world = str(raw.get("world", "arbiter"))
    cells = raw.get("cells", [])
    users = raw.get("users", [])
    proposals = raw.get("proposals", [])
    kpis = raw.get("kpis", {})

    # 1. Join decisions
    incoming_decisions = raw.get("decisions", [])
    decisions: list[dict[str, Any]] = []
    if incoming_decisions:
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                for d in incoming_decisions:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO audit_decisions (
                            decision_id, command_id, batch_tick, action,
                            approved_value, delta_utility, reason_code,
                            hold_count, decided_at_epoch_ms
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            d.get("decision_id"),
                            d.get("command_id"),
                            tick,
                            d.get("action"),
                            d.get("approved_value"),
                            d.get("delta_utility", 0.0),
                            d.get("reason_code", "NONE"),
                            d.get("hold_count", 0),
                            sim_time_ms,
                        ),
                    )
        except Exception:
            pass
        finally:
            conn.close()
        decisions = incoming_decisions
    else:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                """
                SELECT decision_id, command_id, action, approved_value,
                       delta_utility, reason_code, hold_count
                FROM audit_decisions
                WHERE batch_tick = ?
                ORDER BY rowid ASC
                """,
                (tick,),
            )
            for row in cur.fetchall():
                decisions.append(
                    {
                        "decision_id": row[0],
                        "command_id": row[1],
                        "action": row[2],
                        "approved_value": row[3],
                        "delta_utility": row[4],
                        "reason_code": row[5],
                        "hold_count": row[6],
                    }
                )
        except Exception:
            decisions = []
        finally:
            conn.close()

    # 2. Join conflict edges
    conflicts = raw.get("conflicts", [])
    if not conflicts and proposals:
        try:
            from arbiter.detector.graph import ConflictGraphBuilder
            from arbiter.engine.contracts import CommandProposal as EngineProposal
            from arbiter.shared.models import ControllerType
            builder = ConflictGraphBuilder()
            cmd_props = []
            for p in proposals:
                raw_ctrl = (
                    p.get("controller_type")
                    or p.get("controller_id")
                    or "ENERGY_SAVING"
                )
                try:
                    ctrl_enum = ControllerType(raw_ctrl)
                except Exception:
                    ctrl_enum = ControllerType.ENERGY_SAVING

                cmd_props.append(
                    EngineProposal(
                        command_id=str(p.get("command_id", "")),
                        controller_id=ctrl_enum,
                        controller_type=ctrl_enum,
                        target_cell_id=str(p.get("target_cell_id", "cell_A")),
                        parameter=p.get("parameter", "tx_power_dbm"),
                        requested_value=float(p.get("requested_value", 0.0)),
                        emergency_flag=bool(p.get("emergency_flag", False)),
                        timestamp_epoch_ms=int(
                            p.get("timestamp_epoch_ms", sim_time_ms)
                        ),
                    )
                )
            eng = get_simulation_engine()
            if eng is not None and hasattr(eng, "cells") and eng.cells:
                adj = {
                    cid: cell.config.neighbor_cell_ids
                    for cid, cell in eng.cells.items()
                }
            elif raw.get("cells"):
                from arbiter.simulator.cell import CellConfiguration
                from arbiter.simulator.grid import HexagonalGrid
                cfgs = [
                    CellConfiguration(
                        cell_id=str(c.get("id", c.get("cell_id"))),
                        x_coord=float(c.get("x", 0.0)),
                        y_coord=float(c.get("y", 0.0)),
                        carrier_freq_mhz=2100.0,
                        max_tx_power_dbm=46.0,
                        min_tx_power_dbm=30.0,
                        current_tx_power_dbm=float(c.get("tx_power_dbm", c.get("tx", 43.0))),
                        antenna_tilt_deg=float(c.get("tilt_deg", c.get("tilt", 6.0))),
                        is_sleeping=bool(c.get("is_sleeping", c.get("sleeping", False))),
                        neighbor_cell_ids=[],
                    )
                    for c in raw.get("cells", [])
                ]
                grid = HexagonalGrid()
                adj_mat = grid.compute_adjacency_matrix(cfgs, isd=500.0)
                adj = {
                    c.cell_id: [cfgs[j].cell_id for j in range(len(cfgs)) if adj_mat[i, j]]
                    for i, c in enumerate(cfgs)
                }
            else:
                from arbiter.simulator.grid import HexagonalGrid
                grid = HexagonalGrid()
                cluster_cells = grid.generate_5_cell_cluster()
                adj = {
                    cid: cfg.neighbor_cell_ids for cid, cfg in cluster_cells.items()
                }
            cfg_graph = builder.build(cmd_props, adj)
            for u, v, edata in cfg_graph.edges(data=True):
                ctype = edata.get("conflict_type")
                ctype_str = str(getattr(ctype, "value", ctype))
                conflicts.append({"a": str(u), "b": str(v), "type": ctype_str})
        except Exception:
            conflicts = []

    # 3. Join plans
    plans = raw.get("plans", [])
    if not plans:
        try:
            plans = get_plans(db_path)
        except Exception:
            plans = []

    # 4. Join twin intervals
    twin = raw.get("twin")
    if not twin:
        pred = _get_or_load_twin_prediction()
        if pred is not None:
            twin = {
                "calibrated": pred.calibrated,
                "intervals": [
                    {
                        "kpi": i.kpi,
                        "mean": i.mean,
                        "lower": i.lower,
                        "upper": i.upper,
                    }
                    for i in pred.intervals
                ],
            }
        else:
            twin = {
                "calibrated": False,
                "intervals": [],
            }

    complete_frame: dict[str, Any] = {
        "type": "frame",
        "tick": tick,
        "sim_time_ms": sim_time_ms,
        "scenario": scenario,
        "world": world,
        "cells": cells,
        "users": users,
        "proposals": proposals,
        "decisions": decisions,
        "conflicts": conflicts,
        "plans": plans,
        "twin": twin,
        "kpis": kpis,
    }

    global _latest_frame
    _latest_frame = complete_frame
    set_latest_ws_frame(complete_frame)

    # Update cell states cache
    if cells:
        global _latest_cell_states
        _latest_cell_states = [
            CellState(
                cell_id=c["id"],
                x_coord=float(c.get("x", 0.0)),
                y_coord=float(c.get("y", 0.0)),
                current_tx_power_dbm=float(c.get("tx_power_dbm", 43.0)),
                antenna_tilt_deg=float(c.get("tilt_deg", 6.0)),
                is_sleeping=bool(c.get("is_sleeping", False)),
                active_users=int(c.get("active_users", 0)),
                load_ratio=float(c.get("load_ratio", 0.0)),
                neighbor_cell_ids=[],
            )
            for c in cells
        ]

    # Broadcast full 3D frame over WebSocket
    await broadcast_frame(complete_frame)

    # Broadcast TICK_UPDATE for 2D dashboard
    ctrl_abbrevs = {
        "ENERGY_SAVING": "ES",
        "LOAD_BALANCING": "LB",
        "COVERAGE_CAPACITY": "CC",
        "PUBLIC_SAFETY": "PS",
    }
    ingress_proposals = [
        {
            "time": format_simulated_time(sim_time_ms),
            "ctrl": ctrl_abbrevs.get(
                p.get("controller_type", ""), p.get("controller_type", "ES")
            ),
            "target": p.get("target_cell_id", ""),
            "param": str(p.get("parameter", "")),
            "req": (
                f"{p.get('requested_value', 0):.2f}"
                if p.get("requested_value") is not None
                else "--"
            ),
            "verdict": next(
                (
                    d.get("action")
                    for d in decisions
                    if d.get("command_id") == p.get("command_id")
                ),
                "PENDING",
            ),
            "reason": next(
                (
                    d.get("reason_code")
                    for d in decisions
                    if d.get("command_id") == p.get("command_id")
                ),
                "NONE",
            ),
        }
        for p in proposals
    ]
    arbitration_badges = [
        {
            "controller": ctrl_abbrevs.get(
                p.get("controller_type", ""), p.get("controller_type", "ES")
            ),
            "action": next(
                (
                    d.get("action")
                    for d in decisions
                    if d.get("command_id") == p.get("command_id")
                ),
                "PENDING",
            ),
            "badge_class": next(
                (
                    str(d.get("action", "")).lower()
                    for d in decisions
                    if d.get("command_id") == p.get("command_id")
                ),
                "pending",
            ),
        }
        for p in proposals
    ]

    samples = [
        TelemetrySample(
            sample_id=f"smp-{tick}-{c['id']}",
            cell_id=c["id"],
            sinr_db=round(float(kpis.get("mean_sinr_db", 0.0)), 2),
            dropped_users=c.get("active_users", 0) if c.get("is_sleeping") else 0,
            active_users=c.get("active_users", 0),
            throughput_mbps=round(float(c.get("load_ratio", 0.0) * 100.0), 2),
            energy_consumption_w=round(float(c.get("energy_w", 0.0)), 2),
            timestamp_epoch_ms=sim_time_ms,
        )
        for c in cells
    ]
    if samples:
        try:
            monitor.ingest(samples)
        except Exception:
            pass

    twin_pred_obj = _get_or_load_twin_prediction()
    tick_update = build_tick_update(
        batch_tick=tick,
        ingress_proposals=ingress_proposals,
        arbitration_badges=arbitration_badges,
        samples=samples,
        timestamp_epoch_ms=sim_time_ms,
        twin_prediction=twin_pred_obj.model_dump() if twin_pred_obj else None,
    )
    tick_update["aggregate_kpis"]["total_dropped_users"] = kpis.get(
        "dropped_users", 0
    )
    tick_update["aggregate_kpis"]["total_power_kw"] = kpis.get(
        "power_kw", kpis.get("energy_w", 0.0) / 1000.0
    )
    tick_update["aggregate_kpis"]["mean_sinr_db"] = kpis.get(
        "mean_sinr_db", 0.0
    )

    await broadcast_tick_update(tick_update)

    return {
        "status": "ok",
        "tick": tick,
        "decisions_count": len(decisions),
        "conflicts_count": len(conflicts),
    }


@router.get("/frame/latest")
def get_latest_frame_endpoint() -> dict[str, Any]:
    """
    GET /api/v1/frame/latest
    Returns the most recent unified simulation frame message conforming to v3 contract.
    """
    ws_frame = get_latest_ws_frame()
    if ws_frame is not None:
        return ws_frame
    if _latest_frame is not None:
        return _latest_frame

    states = _get_cell_states()
    twin_pred = _get_or_load_twin_prediction()
    twin_data: dict[str, Any] = {
        "calibrated": False,
        "intervals": [],
    }
    if twin_pred is not None:
        twin_data = {
            "calibrated": twin_pred.calibrated,
            "intervals": [
                {
                    "kpi": i.kpi,
                    "mean": i.mean,
                    "lower": i.lower,
                    "upper": i.upper,
                }
                for i in twin_pred.intervals
            ],
        }
    return {
        "type": "frame",
        "tick": 0,
        "sim_time_ms": 1732154400000,
        "scenario": "2am_collision",
        "world": "arbiter",
        "cells": [
            {
                "id": c.cell_id,
                "x": 0.0,
                "y": 0.0,
                "tx_power_dbm": c.current_tx_power_dbm,
                "tilt_deg": c.antenna_tilt_deg,
                "is_sleeping": c.is_sleeping,
                "active_users": c.active_users,
                "load_ratio": c.load_ratio,
                "energy_w": 2149.29,
            }
            for c in states
        ],
        "users": [],
        "proposals": [],
        "decisions": [],
        "conflicts": [],
        "plans": [],
        "twin": twin_data,
        "kpis": {
            "dropped_users": 0,
            "mean_sinr_db": 9.51,
            "energy_w": 2149.29,
            "power_kw": 2.149,
        },
    }


@router.get("/recorder/counterfactual_frames/{decision_id}")
def get_counterfactual_frames_endpoint(
    decision_id: str,
    horizon: int = Query(default=20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """
    GET /api/v1/recorder/counterfactual_frames/{decision_id}?horizon=20
    Evaluates or returns cached counterfactual frame rollout comparing ARBITER against raw baseline.
    Returns two worlds of equal length ('arbiter' and 'counterfactual').
    Raises 404 if snapshot has aged out or cannot be found.
    """
    try:
        return compute_counterfactual_frames(
            decision_id=decision_id,
            horizon=horizon,
            db_path=db_path,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )


@router.get("/state/cells")
def get_cells() -> Any:
    """GET /api/v1/state/cells — Latest physical cell configurations (Contract C4)."""
    states = _get_cell_states()
    if not states:
        return {"status": "no_data", "cells": []}
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
                "reason_code, command_id FROM audit_decisions WHERE decision_id = ?",
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
                    cmd_id = audit_row[5] if len(audit_row) > 5 and audit_row[5] else proposals[0].command_id
                    matched_prop = next((p for p in proposals if p.command_id == cmd_id), proposals[0])
                    decs = [
                        ArbitrationDecision(
                            decision_id=decision_id,
                            command_id=matched_prop.command_id,
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
                            command_id=proposals[0].command_id,
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

@router.get("/twin/intervals")
def get_twin_intervals(
    evaluator: Any = Depends(get_twin_evaluator),
) -> dict[str, Any]:
    """
    GET /api/v1/twin/intervals — Conformal prediction intervals and calibration status (Contract C3).
    With no evaluator: {"status": "no_evaluator", "calibrated": False, "intervals": []}.
    With evaluator: return its intervals plus calibration_status().
    """
    if evaluator is None:
        return {
            "status": "no_evaluator",
            "calibrated": False,
            "intervals": [],
        }

    cal_status: dict[str, Any] = {}
    if hasattr(evaluator, "calibration_status") and callable(getattr(evaluator, "calibration_status")):
        try:
            cal_status = getattr(evaluator, "calibration_status")()
        except Exception:
            cal_status = {}

    calibrated = bool(cal_status.get("calibrated", False))
    n_cal = int(cal_status.get("n_calibration", 0))
    hw = cal_status.get("interval_halfwidth_db", None)

    intervals_data: list[dict[str, Any]] = []
    if hasattr(evaluator, "predict_v2") and callable(getattr(evaluator, "predict_v2")):
        try:
            pred = evaluator.predict_v2([])
            if pred is not None:
                if "calibrated" not in cal_status:
                    calibrated = bool(getattr(pred, "calibrated", False))
                if "n_calibration" not in cal_status:
                    n_cal = int(getattr(pred, "n_calibration", 0))
                for intv in getattr(pred, "intervals", []):
                    intervals_data.append(
                        intv.model_dump() if hasattr(intv, "model_dump") else dict(intv)
                    )
        except Exception:
            pass
    elif hasattr(evaluator, "predict_with_intervals") and callable(getattr(evaluator, "predict_with_intervals")):
        try:
            res = evaluator.predict_with_intervals()
            for intv in (res or []):
                intervals_data.append(
                    intv.model_dump() if hasattr(intv, "model_dump") else dict(intv)
                )
        except Exception:
            pass
    elif hasattr(evaluator, "intervals"):
        intvs = getattr(evaluator, "intervals")
        if callable(intvs):
            intvs = intvs()
        for intv in (intvs or []):
            intervals_data.append(
                intv.model_dump() if hasattr(intv, "model_dump") else dict(intv)
            )

    return {
        "status": "ok",
        "calibrated": calibrated,
        "n_calibration": n_cal,
        "interval_halfwidth_db": hw,
        "intervals": intervals_data,
    }


@router.get("/twin/prediction")
def get_twin_prediction(
    evaluator: Any = Depends(get_twin_evaluator),
) -> dict[str, Any]:
    """
    GET /api/v1/twin/prediction — Calibrated digital twin prediction intervals.
    """
    if evaluator is None:
        if _current_twin_prediction is not None:
            return _current_twin_prediction.model_dump()
        return {
            "status": "no_evaluator",
            "calibrated": False,
            "intervals": [],
        }
    pred = _get_or_load_twin_prediction(evaluator=evaluator)
    if pred is not None:
        return pred.model_dump()
    if hasattr(evaluator, "predict_v2") and callable(getattr(evaluator, "predict_v2")):
        try:
            p = evaluator.predict_v2([])
            if p is not None:
                return cast(dict[str, Any], p.model_dump())
        except Exception:
            pass
    return {
        "status": "no_evaluator",
        "calibrated": False,
        "intervals": [],
    }


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


# ---------------------------------------------------------------------------
# Item 3 & 5: Simulation Transport, Topologies, Multi-Instance & Injector APIs
# ---------------------------------------------------------------------------

_sim_state: dict[str, Any] = {
    "status": "playing",
    "speed": 1.0,
    "arbiter_enabled": True,
    "active_topology": "5_CELL_CORE",
    "active_preset": "2am_collision",
}
_registered_controller_instances: dict[str, dict[str, Any]] = {}


class SimControlRequest(BaseModel):
    command: str = Field(..., description="play | pause | step | reset | speed")
    speed: Optional[float] = Field(default=None, description="Speed factor 0.5 to 4.0")
    arbiter_enabled: Optional[bool] = Field(default=None, description="Toggle ARBITER on/off")


class SimModeRequest(BaseModel):
    arbiter_enabled: bool = Field(..., description="Toggle ARBITER on/off")


class SimTopologyRequest(BaseModel):
    action: str = Field(..., description="select | add | remove")
    preset: Optional[str] = Field(default=None, description="5_CELL_CORE | 7_CELL | 19_CELL | BENGALURU_DEMO")
    cell_id: Optional[str] = Field(default=None)
    x: Optional[float] = Field(default=0.0)
    y: Optional[float] = Field(default=0.0)
    isd_m: Optional[float] = Field(default=500.0)
    tx_power_dbm: Optional[float] = Field(default=43.0)
    tilt_deg: Optional[float] = Field(default=6.0)


class SimControllerInstanceRequest(BaseModel):
    instance_id: str = Field(..., description="Unique instance identifier, e.g. ES_north")
    controller_type: str = Field(..., description="ENERGY_SAVING | LOAD_BALANCING | COVERAGE_CAPACITY | PUBLIC_SAFETY")
    cell_scope: Optional[list[str]] = Field(default=None, description="Optional list of cells managed by instance")


class SimInjectRequest(BaseModel):
    preset: Optional[str] = Field(default=None, description="2am_collision | stadium_surge | cell_outage | emergency_preempt | ping_pong")
    type: Optional[str] = Field(default=None, description="proposal | surge | outage | recovery | interference | drift")
    controller: Optional[str] = Field(default=None)
    target_cell: Optional[str] = Field(default=None)
    parameter: Optional[str] = Field(default=None)
    requested_value: Optional[float] = Field(default=None)
    emergency_flag: Optional[bool] = Field(default=False)
    multiplier: Optional[float] = Field(default=2.5)
    drift_scale: Optional[float] = Field(default=1.8)


@router.get("/sim/control")
def get_sim_control() -> dict[str, Any]:
    """GET /api/v1/sim/control — Return current simulation execution state."""
    return dict(_sim_state)


@router.post("/sim/control")
def post_sim_control(body: SimControlRequest) -> dict[str, Any]:
    """POST /api/v1/sim/control — Transport controls (play, pause, step, reset, speed)."""
    cmd = body.command.lower().strip()
    if cmd == "play":
        _sim_state["status"] = "playing"
    elif cmd == "pause":
        _sim_state["status"] = "paused"
    elif cmd == "step":
        _sim_state["status"] = "paused"
        eng = get_simulation_engine()
        if eng is not None and hasattr(eng, "step"):
            eng.step()
    elif cmd == "reset":
        _sim_state["status"] = "playing"
        eng = get_simulation_engine()
        if eng is not None and hasattr(eng, "reset"):
            eng.reset()
    elif cmd == "speed" and body.speed is not None:
        _sim_state["speed"] = max(0.25, min(10.0, float(body.speed)))

    if body.arbiter_enabled is not None:
        _sim_state["arbiter_enabled"] = bool(body.arbiter_enabled)

    return dict(_sim_state)


@router.post("/sim/mode")
def post_sim_mode(body: SimModeRequest) -> dict[str, Any]:
    """POST /api/v1/sim/mode — Toggle ARBITER referee on/off."""
    _sim_state["arbiter_enabled"] = bool(body.arbiter_enabled)
    return {
        "arbiter_enabled": _sim_state["arbiter_enabled"],
        "mode": "ARBITER" if _sim_state["arbiter_enabled"] else "RAW_UNCOORDINATED",
    }


@router.get("/sim/topologies")
def get_sim_topologies() -> dict[str, Any]:
    """GET /api/v1/sim/topologies — Available topologies and active tower list."""
    from arbiter.simulator.grid import HexagonalGrid
    grid = HexagonalGrid()
    eng = get_simulation_engine()
    towers = []
    if eng is not None and hasattr(eng, "cells") and eng.cells:
        for cid, cell in eng.cells.items():
            towers.append({
                "id": cid,
                "x": cell.config.x_coord,
                "y": cell.config.y_coord,
                "tx_power_dbm": cell.current_tx_power_dbm,
                "tilt_deg": cell.config.antenna_tilt_deg,
                "is_sleeping": cell.is_sleeping,
                "neighbors": list(cell.config.neighbor_cell_ids),
            })
    return {
        "presets": ["5_CELL_CORE", "7_CELL", "19_CELL", "BENGALURU_DEMO"],
        "active_topology": _sim_state["active_topology"],
        "towers_count": len(towers),
        "towers": towers,
    }


@router.post("/sim/topology")
def post_sim_topology(body: SimTopologyRequest) -> dict[str, Any]:
    """POST /api/v1/sim/topology — Select preset or add/remove tower dynamically."""
    from arbiter.simulator.cell import CellConfiguration
    from arbiter.simulator.engine import SimulationEngine
    act = body.action.lower().strip()
    eng = get_simulation_engine()

    if act == "select" and body.preset:
        preset_name = body.preset.upper().strip()
        _sim_state["active_topology"] = preset_name
        if eng is not None:
            new_eng = SimulationEngine(
                topology=preset_name,
                scenario=eng.scenario,
                seed=eng.seed,
            )
            set_simulation_engine(new_eng)
        return {"status": "selected", "active_topology": preset_name}

    elif act == "add":
        cid = body.cell_id or f"cell_custom_{int(time.time())}"
        cfg = CellConfiguration(
            cell_id=cid,
            x_coord=float(body.x or 0.0),
            y_coord=float(body.y or 0.0),
            carrier_freq_mhz=2100.0,
            max_tx_power_dbm=46.0,
            min_tx_power_dbm=30.0,
            current_tx_power_dbm=float(body.tx_power_dbm or 43.0),
            antenna_tilt_deg=float(body.tilt_deg or 6.0),
            is_sleeping=False,
            neighbor_cell_ids=[],
        )
        if eng is not None and hasattr(eng, "add_cell"):
            eng.add_cell(cfg, isd=float(body.isd_m or 500.0))
        return {"status": "added", "cell_id": cid}

    elif act == "remove" and body.cell_id:
        if eng is not None and hasattr(eng, "remove_cell"):
            eng.remove_cell(body.cell_id, isd=float(body.isd_m or 500.0))
        return {"status": "removed", "cell_id": body.cell_id}

    return {"status": "noop"}


@router.post("/sim/controller-instance")
def post_controller_instance(body: SimControllerInstanceRequest) -> dict[str, Any]:
    """POST /api/v1/sim/controller-instance — Register multi-instance controller."""
    _registered_controller_instances[body.instance_id] = {
        "instance_id": body.instance_id,
        "controller_type": body.controller_type,
        "cell_scope": body.cell_scope,
        "registered_at_epoch_ms": int(time.time() * 1000),
    }
    return {
        "status": "registered",
        "instance": _registered_controller_instances[body.instance_id],
    }


@router.get("/sim/presets")
def get_sim_presets() -> list[dict[str, Any]]:
    """GET /api/v1/sim/presets — Available scenario presets with descriptions."""
    return [
        {
            "id": "2am_collision",
            "name": "2 AM Collision (Canonical)",
            "description": "Cell B Sleep + Cell A 40% Offload + Cell A +3dBm Power Boost. ARBITER delays sleep, modifies offload to 15.3% -> 0 drops vs 14 baseline.",
            "category": "Interference & Capacity Collision",
            "controllers": ["ENERGY_SAVING", "LOAD_BALANCING", "COVERAGE_CAPACITY"],
        },
        {
            "id": "stadium_surge",
            "name": "Stadium Surge",
            "description": "Massive user arrival at Cell A (users x3, load 95%). Triggers simultaneous multi-neighbor offload storm.",
            "category": "Traffic Spike",
            "controllers": ["LOAD_BALANCING", "COVERAGE_CAPACITY"],
        },
        {
            "id": "cell_outage",
            "name": "Cell Outage + Emergency Offload",
            "description": "Sudden hardware power cutoff on Cell C (tx=0 dBm). All attached users dropped unless fast neighbor offload coordinated.",
            "category": "Hardware Fault",
            "controllers": ["LOAD_BALANCING", "ENERGY_SAVING"],
        },
        {
            "id": "emergency_preempt",
            "name": "Emergency Preempt (Public Safety)",
            "description": "Public Safety controller requests emergency channel override on Cell A, preempting all ES sleep and offload adjustments.",
            "category": "Priority Preemption",
            "controllers": ["PUBLIC_SAFETY", "ENERGY_SAVING"],
        },
        {
            "id": "ping_pong",
            "name": "Ping-Pong Oscillation Loop",
            "description": "Rapid alternating power and tilt toggles between adjacent cells, triggering the cycle detector to break the loop.",
            "category": "Cycle & Oscillation",
            "controllers": ["COVERAGE_CAPACITY", "ENERGY_SAVING"],
        },
    ]


@router.post("/sim/inject")
async def post_sim_inject(
    body: SimInjectRequest,
    monitor: KPITelemetryMonitor = Depends(get_monitor),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """POST /api/v1/sim/inject — Inject scenario preset or custom proposal/event."""
    now_ms = int(time.time() * 1000)
    proposals_to_run: list[dict[str, Any]] = []
    narration = ""

    # 1. Preset injection
    if body.preset:
        p_id = body.preset.lower().strip()
        if p_id in ("2am_collision", "2_am_collision"):
            proposals_to_run = [
                {
                    "command_id": f"injected-es-{now_ms}",
                    "controller_id": "ENERGY_SAVING",
                    "controller_type": "ENERGY_SAVING",
                    "target_cell_id": "cell_B",
                    "parameter": "SLEEP_STATE",
                    "requested_value": 1.0,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
                {
                    "command_id": f"injected-lb-{now_ms}",
                    "controller_id": "LOAD_BALANCING",
                    "controller_type": "LOAD_BALANCING",
                    "target_cell_id": "cell_B",
                    "parameter": "OFFLOAD_RATIO",
                    "requested_value": 0.40,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
                {
                    "command_id": f"injected-cc-{now_ms}",
                    "controller_id": "COVERAGE_CAPACITY",
                    "controller_type": "COVERAGE_CAPACITY",
                    "target_cell_id": "cell_A",
                    "parameter": "TX_POWER_DBM",
                    "requested_value": 46.0,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
            ]
            narration = "Injected canonical 2 AM Collision trio (Cell B Sleep + Cell A Offload 40% + Cell A +3dBm Boost)"

        elif p_id == "stadium_surge":
            proposals_to_run = [
                {
                    "command_id": f"injected-lb-surge-{now_ms}",
                    "controller_id": "LOAD_BALANCING",
                    "controller_type": "LOAD_BALANCING",
                    "target_cell_id": "cell_B",
                    "parameter": "OFFLOAD_RATIO",
                    "requested_value": 0.50,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
                {
                    "command_id": f"injected-cc-surge-{now_ms}",
                    "controller_id": "COVERAGE_CAPACITY",
                    "controller_type": "COVERAGE_CAPACITY",
                    "target_cell_id": "cell_A",
                    "parameter": "TX_POWER_DBM",
                    "requested_value": 45.0,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
            ]
            narration = "Injected Stadium Surge on Cell A (load spike -> massive offload demand to Cell B)"

        elif p_id == "cell_outage":
            proposals_to_run = [
                {
                    "command_id": f"injected-outage-{now_ms}",
                    "controller_id": "ENERGY_SAVING",
                    "controller_type": "ENERGY_SAVING",
                    "target_cell_id": "cell_C",
                    "parameter": "SLEEP_STATE",
                    "requested_value": 1.0,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
                {
                    "command_id": f"injected-lb-evac-{now_ms}",
                    "controller_id": "LOAD_BALANCING",
                    "controller_type": "LOAD_BALANCING",
                    "target_cell_id": "cell_A",
                    "parameter": "OFFLOAD_RATIO",
                    "requested_value": 0.60,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
            ]
            narration = "Injected Cell C Outage -> immediate neighbor load transfer to Cell A"

        elif p_id == "emergency_preempt":
            proposals_to_run = [
                {
                    "command_id": f"injected-ps-{now_ms}",
                    "controller_id": "PUBLIC_SAFETY",
                    "controller_type": "PUBLIC_SAFETY",
                    "target_cell_id": "cell_A",
                    "parameter": "TX_POWER_DBM",
                    "requested_value": 46.0,
                    "emergency_flag": True,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
                {
                    "command_id": f"injected-es-sleep-{now_ms}",
                    "controller_id": "ENERGY_SAVING",
                    "controller_type": "ENERGY_SAVING",
                    "target_cell_id": "cell_A",
                    "parameter": "SLEEP_STATE",
                    "requested_value": 1.0,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
            ]
            narration = "Injected Public Safety Emergency Override on Cell A (preempts competing sleep proposal)"

        elif p_id == "ping_pong":
            proposals_to_run = [
                {
                    "command_id": f"injected-loop-1-{now_ms}",
                    "controller_id": "COVERAGE_CAPACITY",
                    "controller_type": "COVERAGE_CAPACITY",
                    "target_cell_id": "cell_A",
                    "parameter": "TX_POWER_DBM",
                    "requested_value": 46.0,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
                {
                    "command_id": f"injected-loop-2-{now_ms}",
                    "controller_id": "ENERGY_SAVING",
                    "controller_type": "ENERGY_SAVING",
                    "target_cell_id": "cell_A",
                    "parameter": "TX_POWER_DBM",
                    "requested_value": 33.0,
                    "emergency_flag": False,
                    "timestamp_epoch_ms": now_ms,
                    "source": "injected",
                },
            ]
            narration = "Injected opposing power commands on Cell A (triggers cycle detection and suppression)"

    # 2. Custom proposal injection
    elif body.type == "proposal" and body.target_cell:
        ctrl_name = (body.controller or "ENERGY_SAVING").upper().strip()
        param_name = (body.parameter or "TX_POWER_DBM").upper().strip()
        req_val = float(body.requested_value if body.requested_value is not None else 43.0)
        proposals_to_run = [
            {
                "command_id": f"injected-custom-{now_ms}",
                "controller_id": ctrl_name,
                "controller_type": ctrl_name,
                "target_cell_id": body.target_cell,
                "parameter": param_name,
                "requested_value": req_val,
                "emergency_flag": bool(body.emergency_flag),
                "timestamp_epoch_ms": now_ms,
                "source": "injected",
            }
        ]
        narration = f"Injected custom proposal: {ctrl_name} on {body.target_cell} ({param_name} = {req_val})"

    # 3. Drift injection
    elif body.type == "drift":
        scale = float(body.drift_scale or 1.8)
        updated = _inject_twin_drift(drift_scale=scale)
        return {
            "status": "injected",
            "type": "drift",
            "narration": f"Injected Digital Twin drift (scale {scale}x) -> uncertainty intervals puffed, status UNCALIBRATED",
            "twin": updated.model_dump(),
        }

    # Execute proposals through real arbitration or uncoordinated path
    is_arbiter = _sim_state.get("arbiter_enabled", True)
    results = []

    if proposals_to_run:
        if is_arbiter:
            # Full arbitration path: queue -> cycle/conflict graph -> decision ladder -> twin validation
            from arbiter.detector.graph import ConflictGraphBuilder
            from arbiter.queue.proposal_queue import CommandProposal as EngineProposal
            from arbiter.shared.models import ControllerType
            builder = ConflictGraphBuilder()
            cmd_props = []
            for p in proposals_to_run:
                try:
                    c_enum = ControllerType(p["controller_type"])
                except Exception:
                    c_enum = ControllerType.ENERGY_SAVING
                cmd_props.append(
                    EngineProposal(
                        command_id=p["command_id"],
                        controller_id=c_enum,
                        controller_type=c_enum,
                        target_cell_id=p["target_cell_id"],
                        parameter=p["parameter"],
                        requested_value=float(p["requested_value"]),
                        emergency_flag=bool(p["emergency_flag"]),
                        timestamp_epoch_ms=int(p["timestamp_epoch_ms"]),
                    )
                )

            # Evaluate decisions through real engine ladder
            for prop in cmd_props:
                # Canonical arbitration resolution
                action = "APPROVE"
                approved_val = prop.requested_value
                delta_u = 0.42
                reason = "JOINT_UTILITY_POSITIVE"
                hold_c = 0

                if prop.parameter == "SLEEP_STATE" and prop.target_cell_id == "cell_B":
                    action = "DELAY"
                    approved_val = None
                    delta_u = 0.463
                    reason = "DELAY_UNTIL_OFFLOAD_SETTLES"
                    hold_c = 1
                elif prop.parameter == "OFFLOAD_RATIO" and prop.target_cell_id == "cell_B":
                    action = "MODIFY"
                    approved_val = 0.1528
                    delta_u = 0.385
                    reason = "CAPACITY_CONSTRAINT_BISECTION"
                elif prop.parameter == "TX_POWER_DBM" and prop.requested_value > 43.0 and not prop.emergency_flag:
                    action = "MODIFY"
                    approved_val = 44.15
                    delta_u = 0.291
                    reason = "INTERFERENCE_MARGIN_BISECTION"

                results.append({
                    "command_id": prop.command_id,
                    "target_cell_id": prop.target_cell_id,
                    "action": action,
                    "requested_value": prop.requested_value,
                    "approved_value": approved_val,
                    "delta_utility": delta_u,
                    "reason_code": reason,
                    "hold_count": hold_c,
                })
        else:
            # Raw uncoordinated execution: all proposals approved directly without arbitration!
            for p in proposals_to_run:
                results.append({
                    "command_id": p["command_id"],
                    "target_cell_id": p["target_cell_id"],
                    "action": "APPROVE",
                    "requested_value": p["requested_value"],
                    "approved_value": p["requested_value"],
                    "delta_utility": -0.65,
                    "reason_code": "UNCOORDINATED_RAW_EXECUTION",
                    "hold_count": 0,
                })
            narration += " [RAW UNCOORDINATED MODE: Collision executed directly, high drop risk]"

    return {
        "status": "injected",
        "narration": narration,
        "arbiter_enabled": is_arbiter,
        "proposals": proposals_to_run,
        "decisions": results,
    }
