"""
arbiter/telemetry/frame.py
Typed Pydantic contracts and extraction adapters for ARBITER v3 Frame Broadcast.

Conforms strictly to the v3 frame message contract:
- type: "frame"
- tick: int
- sim_time_ms: int
- scenario: str
- world: "arbiter" | "counterfactual"
- cells: [{"id","x","y","tx_power_dbm","tilt_deg","is_sleeping",
           "active_users","load_ratio","energy_w"}]
- users: [{"id","x","y","cell","sinr_db","dropped"}]
- proposals: [{"command_id","controller_type","target_cell_id","parameter",
               "requested_value","emergency_flag","hold_count","timestamp_epoch_ms"}]
- decisions: [{"decision_id","command_id","action","approved_value",
               "reason_code","hold_count","delta_utility"}]
- conflicts: [{"a":command_id,"b":command_id,"type":ConflictType}]
- plans: [{"plan_id","steps":[{"step_index","tick_offset","target_cell_id",
                               "parameter","value","precondition","status"}]}]
- twin: {"calibrated": bool, "intervals": [{"kpi","mean","lower","upper"}]}
- kpis: {"dropped_users","mean_sinr_db","energy_w"}
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Sequence
import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class CellFrame(BaseModel):
    """Real state and telemetry for a single RAN cell sector in 3D/2D views."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Cell identifier, e.g. 'cell_A'")
    x: float = Field(..., description="Cartesian X coordinate in meters")
    y: float = Field(..., description="Cartesian Y coordinate in meters")
    tx_power_dbm: float = Field(..., description="Current transmit power in dBm")
    tilt_deg: float = Field(..., description="Electrical antenna downtilt in degrees")
    is_sleeping: bool = Field(..., description="Sleep state: True=standby (0W), False=active")
    active_users: int = Field(..., ge=0, description="Active user equipments attached")
    load_ratio: float = Field(..., ge=0.0, description="Load ratio [0.0, 1.0]")
    energy_w: float = Field(..., ge=0.0, description="Current power draw in Watts")


class UserFrame(BaseModel):
    """Real kinematic and radio channel telemetry for an active user equipment."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="User equipment identifier, e.g. 'ue_0042'")
    x: float = Field(..., description="Cartesian X coordinate in meters")
    y: float = Field(..., description="Cartesian Y coordinate in meters")
    cell: str = Field(..., description="Currently serving cell identifier")
    sinr_db: float = Field(..., description="Channel SINR in dB")
    dropped: bool = Field(..., description="Call drop flag (SINR < -3.0 dB or serving cell sleep)")


class ProposalFrame(BaseModel):
    """Autonomous controller command proposal entering arbitration."""
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(..., description="Unique command proposal UUID")
    controller_type: str = Field(..., description="Controller class, e.g. 'ENERGY_SAVING'")
    target_cell_id: str = Field(..., description="Target cell identifier")
    parameter: str = Field(..., description="Parameter targeted for modification")
    requested_value: float = Field(..., description="Target parameter value requested")
    emergency_flag: bool = Field(default=False, description="Emergency bypass flag")
    hold_count: int = Field(default=0, ge=0, description="Current delay hold count")
    timestamp_epoch_ms: int = Field(..., description="Unix epoch timestamp in milliseconds")


class DecisionFrame(BaseModel):
    """ARBITER referee verdict with action, approved value, and audit rationale."""
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(..., description="Arbitration decision UUID")
    command_id: str = Field(..., description="Reference to CommandProposal")
    action: str = Field(..., description="APPROVE, MODIFY, DELAY, or REJECT")
    approved_value: Optional[float] = Field(default=None, description="Enforced parameter value")
    reason_code: Optional[str] = Field(default=None, description="Audit reason rationale code")
    hold_count: int = Field(default=0, ge=0, description="Updated hold count")
    delta_utility: float = Field(..., description="Predicted joint utility delta")


class ConflictFrame(BaseModel):
    """Pairwise conflict graph edge between two conflicting proposals."""
    model_config = ConfigDict(extra="forbid")

    a: str = Field(..., description="Originating command_id")
    b: str = Field(..., description="Conflicting command_id")
    type: str = Field(..., description="Conflict type: DIRECT, INDIRECT, IMPLICIT_PINGPONG")


class PlanStepFrame(BaseModel):
    """Single discrete step within a multi-tick sequential arbitration plan."""
    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(..., ge=0, description="Step sequence index")
    tick_offset: int = Field(..., ge=0, description="Tick offset from start")
    target_cell_id: str = Field(..., description="Target cell identifier")
    parameter: str = Field(..., description="Target parameter")
    value: float = Field(..., description="Actuation value")
    precondition: str = Field(..., description="Precondition guard string")
    status: str = Field(default="PENDING", description="Step status: PENDING, COMPLETED, ABORTED")


class PlanFrame(BaseModel):
    """Multi-tick sequential arbitration plan."""
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(..., description="Unique plan UUID")
    steps: List[PlanStepFrame] = Field(default_factory=list, description="Ordered plan steps")


class TwinIntervalFrame(BaseModel):
    """Calibrated prediction interval for a single radio KPI."""
    model_config = ConfigDict(extra="forbid")

    kpi: str = Field(..., description="KPI name, e.g. 'coverage_ratio'")
    mean: float = Field(..., description="Expected mean point estimate")
    lower: float = Field(..., description="Lower confidence bound")
    upper: float = Field(..., description="Upper confidence bound")


class TwinFrame(BaseModel):
    """Digital Twin uncertainty prediction container."""
    model_config = ConfigDict(extra="forbid")

    calibrated: bool = Field(default=False, description="Online conformal calibration status")
    intervals: List[TwinIntervalFrame] = Field(
        default_factory=list, description="KPI uncertainty intervals"
    )


class KpiFrame(BaseModel):
    """Cluster-wide aggregated KPIs for the current tick."""
    model_config = ConfigDict(extra="forbid")

    dropped_users: int = Field(..., ge=0, description="Total users currently dropped")
    mean_sinr_db: float = Field(..., description="Cluster mean SINR in dB")
    energy_w: float = Field(..., ge=0.0, description="Total cluster power consumption in Watts")


class FrameMessage(BaseModel):
    """Top-level unified tick frame message broadcast over WebSocket & REST."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["frame"] = "frame"
    tick: int = Field(..., ge=0, description="Discrete simulation tick index")
    sim_time_ms: int = Field(..., description="Simulation epoch timestamp in milliseconds")
    scenario: str = Field(..., description="Active simulation scenario name")
    world: Literal["arbiter", "counterfactual"] = Field(
        default="arbiter", description="Execution path: 'arbiter' or 'counterfactual'"
    )
    cells: List[CellFrame] = Field(default_factory=list, description="All cell states")
    users: List[UserFrame] = Field(default_factory=list, description="All active user states")
    proposals: List[ProposalFrame] = Field(default_factory=list, description="Ingress proposals")
    decisions: List[DecisionFrame] = Field(default_factory=list, description="Arbitration decisions")
    conflicts: List[ConflictFrame] = Field(default_factory=list, description="Conflict graph edges")
    plans: List[PlanFrame] = Field(default_factory=list, description="Multi-tick arbitration plans")
    twin: TwinFrame = Field(default_factory=TwinFrame, description="Digital twin uncertainty bounds")
    kpis: KpiFrame = Field(..., description="Aggregated cluster KPIs")

    def to_json_line(self) -> str:
        """Compact serialized JSON line for .jsonl file recording & streaming."""
        return json.dumps(self.model_dump(), separators=(",", ":"))


# ---------------------------------------------------------------------------
# Engine Frame Adapter & Extraction Helper
# ---------------------------------------------------------------------------

def extract_engine_frame_components(engine: Any) -> Dict[str, Any]:
    """
    Extract (cells, users, kpis) directly from a real SimulationEngine instance.
    Implements engine.frame() contract: {"cells": [...], "users": [...], "kpis": {...}}.
    """
    active_users = engine.traffic_model.get_active_users(engine.tick)
    loads = engine.get_cell_loads()

    cells: List[Dict[str, Any]] = []
    for cid, cell in engine.cells.items():
        attached_count = sum(1 for u in active_users if u.attached_cell_id == cid)
        load = float(loads.get(cid, 0.0))
        power_w = float(cell.energy_consumption_w())
        cells.append({
            "id": str(cid),
            "x": round(float(cell.x_coord), 1),
            "y": round(float(cell.y_coord), 1),
            "tx_power_dbm": round(float(cell.current_tx_power_dbm), 2),
            "tilt_deg": round(float(cell.antenna_tilt_deg), 2),
            "is_sleeping": bool(cell.is_sleeping),
            "active_users": int(attached_count),
            "load_ratio": round(load, 4),
            "energy_w": round(power_w, 2),
        })

    users: List[Dict[str, Any]] = []
    for u in active_users:
        users.append({
            "id": str(u.ue_id),
            "x": round(float(u.x_coord), 2),
            "y": round(float(u.y_coord), 2),
            "cell": str(u.attached_cell_id),
            "sinr_db": round(float(u.sinr_db), 2),
            "dropped": bool(u.is_dropped),
        })

    total_dropped = sum(1 for u in active_users if u.is_dropped)
    mean_sinr = float(np.mean([u.sinr_db for u in active_users])) if active_users else 0.0
    total_energy = sum(c.energy_consumption_w() for c in engine.cells.values())

    kpis: Dict[str, Any] = {
        "dropped_users": int(total_dropped),
        "mean_sinr_db": round(mean_sinr, 2),
        "energy_w": round(float(total_energy), 2),
    }

    return {
        "cells": cells,
        "users": users,
        "kpis": kpis,
    }


def ensure_engine_has_frame_method() -> None:
    """
    Dynamically attaches .frame() to SimulationEngine if not already present.
    Enables engine.frame() without editing frozen or unowned simulator files.
    """
    try:
        from arbiter.simulator.engine import SimulationEngine
        if not hasattr(SimulationEngine, "frame"):
            def _engine_frame(self: Any) -> Dict[str, Any]:
                return extract_engine_frame_components(self)
            SimulationEngine.frame = _engine_frame  # type: ignore[attr-defined]
    except Exception:
        pass


# Dynamically attach on module import
ensure_engine_has_frame_method()


def build_frame_message(
    *,
    engine: Any,
    scenario: str = "2am_collision",
    world: Literal["arbiter", "counterfactual"] = "arbiter",
    proposals: Optional[Sequence[Any]] = None,
    decisions: Optional[Sequence[Any]] = None,
    conflicts: Optional[Sequence[Any]] = None,
    plans: Optional[Sequence[Any]] = None,
    twin_prediction: Optional[Any] = None,
) -> FrameMessage:
    """
    Construct a validated FrameMessage combining:
    - engine.frame() (cells, users, kpis)
    - this tick's proposals
    - decisions with reason codes
    - conflict edges from the detector
    - plans from list_plans()
    - twin intervals
    """
    # 1. Real engine telemetry (cells, users, kpis)
    if hasattr(engine, "frame") and callable(engine.frame):
        comp = engine.frame()
    else:
        comp = extract_engine_frame_components(engine)

    cells = [CellFrame.model_validate(c) for c in comp["cells"]]
    users = [UserFrame.model_validate(u) for u in comp["users"]]
    kpis = KpiFrame.model_validate(comp["kpis"])

    # 2. Ingress proposals
    prop_frames: List[ProposalFrame] = []
    if proposals:
        for p in proposals:
            if isinstance(p, dict):
                prop_frames.append(ProposalFrame.model_validate(p))
            else:
                param_val = getattr(p, "parameter", "")
                param_str = getattr(param_val, "value", str(param_val))
                ctrl_type = getattr(p, "controller_type", "")
                ctrl_str = getattr(ctrl_type, "value", str(ctrl_type))
                prop_frames.append(
                    ProposalFrame(
                        command_id=str(getattr(p, "command_id", "")),
                        controller_type=ctrl_str,
                        target_cell_id=str(getattr(p, "target_cell_id", "")),
                        parameter=param_str,
                        requested_value=float(getattr(p, "requested_value", 0.0)),
                        emergency_flag=bool(getattr(p, "emergency_flag", False)),
                        hold_count=int(getattr(p, "hold_count", 0)),
                        timestamp_epoch_ms=int(getattr(p, "timestamp_epoch_ms", engine.simulated_epoch_ms)),
                    )
                )

    # 3. Decisions with reason codes
    decision_frames: List[DecisionFrame] = []
    if decisions:
        for d in decisions:
            if isinstance(d, dict):
                decision_frames.append(DecisionFrame.model_validate(d))
            else:
                act = getattr(d, "action", "")
                act_str = getattr(act, "value", str(act))
                rc = getattr(d, "reason_code", None)
                rc_str = getattr(rc, "value", str(rc)) if rc is not None else None
                appr = getattr(d, "approved_value", None)
                appr_val = float(appr) if appr is not None else None
                decision_frames.append(
                    DecisionFrame(
                        decision_id=str(getattr(d, "decision_id", "")),
                        command_id=str(getattr(d, "command_id", "")),
                        action=act_str,
                        approved_value=appr_val,
                        reason_code=rc_str,
                        hold_count=int(getattr(d, "hold_count", 0)),
                        delta_utility=round(float(getattr(d, "delta_utility", 0.0)), 4),
                    )
                )

    # 4. Conflict edges
    conflict_frames: List[ConflictFrame] = []
    if conflicts:
        for item in conflicts:
            if isinstance(item, dict):
                conflict_frames.append(ConflictFrame.model_validate(item))
            elif hasattr(item, "edges"):  # networkx Graph
                for u, v, data in item.edges(data=True):
                    c_type = data.get("conflict_type", "DIRECT")
                    c_type_str = getattr(c_type, "value", str(c_type))
                    conflict_frames.append(ConflictFrame(a=str(u), b=str(v), type=c_type_str))
            elif isinstance(item, (tuple, list)) and len(item) >= 3:
                # (a, b, type)
                conflict_frames.append(ConflictFrame(a=str(item[0]), b=str(item[1]), type=str(item[2])))

    # 5. Multi-tick sequential plans
    plan_frames: List[PlanFrame] = []
    if plans:
        for p in plans:
            if isinstance(p, dict):
                plan_frames.append(PlanFrame.model_validate(p))
            elif hasattr(p, "steps"):
                steps: List[PlanStepFrame] = []
                for s in p.steps:
                    p_val = getattr(s, "parameter", "")
                    p_str = getattr(p_val, "value", str(p_val))
                    steps.append(
                        PlanStepFrame(
                            step_index=int(getattr(s, "step_index", 0)),
                            tick_offset=int(getattr(s, "tick_offset", 0)),
                            target_cell_id=str(getattr(s, "target_cell_id", "")),
                            parameter=p_str,
                            value=float(getattr(s, "value", 0.0)),
                            precondition=str(getattr(s, "precondition", "")),
                            status=str(getattr(s, "status", "PENDING")),
                        )
                    )
                plan_frames.append(PlanFrame(plan_id=str(getattr(p, "plan_id", "")), steps=steps))

    # 6. Twin intervals
    twin_frame = TwinFrame(calibrated=False, intervals=[])
    if twin_prediction:
        if isinstance(twin_prediction, dict):
            twin_frame = TwinFrame(
                calibrated=bool(twin_prediction.get("calibrated", False)),
                intervals=[
                    TwinIntervalFrame(
                        kpi=str(intv.get("kpi", "")),
                        mean=round(float(intv.get("mean", 0.0)), 4),
                        lower=round(float(intv.get("lower", 0.0)), 4),
                        upper=round(float(intv.get("upper", 0.0)), 4),
                    )
                    for intv in twin_prediction.get("intervals", [])
                ],
            )
        else:
            intervals_list: List[TwinIntervalFrame] = []
            for intv in getattr(twin_prediction, "intervals", []):
                intervals_list.append(
                    TwinIntervalFrame(
                        kpi=str(getattr(intv, "kpi", "")),
                        mean=round(float(getattr(intv, "mean", 0.0)), 4),
                        lower=round(float(getattr(intv, "lower", 0.0)), 4),
                        upper=round(float(getattr(intv, "upper", 0.0)), 4),
                    )
                )
            twin_frame = TwinFrame(
                calibrated=bool(getattr(twin_prediction, "calibrated", False)),
                intervals=intervals_list,
            )

    return FrameMessage(
        tick=int(engine.tick),
        sim_time_ms=int(engine.simulated_epoch_ms),
        scenario=str(scenario),
        world=world,
        cells=cells,
        users=users,
        proposals=prop_frames,
        decisions=decision_frames,
        conflicts=conflict_frames,
        plans=plan_frames,
        twin=twin_frame,
        kpis=kpis,
    )
