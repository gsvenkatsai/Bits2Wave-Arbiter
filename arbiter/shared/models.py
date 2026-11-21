"""Frozen Pydantic schemas and enums across all 4 branches."""
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class ControllerType(str, Enum):
    ENERGY_SAVING = "ENERGY_SAVING"
    LOAD_BALANCING = "LOAD_BALANCING"
    COVERAGE_CAPACITY = "COVERAGE_CAPACITY"
    PUBLIC_SAFETY = "PUBLIC_SAFETY"

class ParameterType(str, Enum):
    TX_POWER_DBM = "TX_POWER_DBM"
    TILT_DEG = "TILT_DEG"
    SLEEP_STATE = "SLEEP_STATE"
    OFFLOAD_RATIO = "OFFLOAD_RATIO"

class ActionType(str, Enum):
    APPROVE = "APPROVE"
    MODIFY = "MODIFY"
    DELAY = "DELAY"
    REJECT = "REJECT"

class ConflictType(str, Enum):
    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    IMPLICIT_PINGPONG = "IMPLICIT_PINGPONG"

class ReasonCode(str, Enum):
    NONE = "NONE"
    ACTIVE_HO = "ACTIVE_HO"
    HEADROOM = "HEADROOM"
    CYCLIC_PINGPONG = "CYCLIC_PINGPONG"
    TWIN_TIMEOUT = "TWIN_TIMEOUT"
    UNCERTAINTY_GUARD = "UNCERTAINTY_GUARD"
    FLOOR_BREACH = "FLOOR_BREACH"
    EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"

class CommandProposal(BaseModel):
    command_id: str = Field(..., description="Unique UUID for command proposal")
    controller_id: str = Field(..., description="Originating controller identifier")
    controller_type: ControllerType = Field(..., description="Controller class")
    target_cell_id: str = Field(..., description="Target cell identifier")
    parameter: ParameterType = Field(..., description="Parameter targeted for alteration")
    requested_value: float = Field(..., description="Target value requested")
    emergency_flag: bool = Field(default=False, description="Bypass flag for emergency overrides")
    timestamp_epoch_ms: int = Field(..., description="Unix epoch timestamp in milliseconds")
    hold_count: int = Field(default=0, description="Number of ticks this command has been delayed")

class ArbitrationDecision(BaseModel):
    decision_id: str = Field(..., description="Unique UUID for arbitration outcome")
    command_id: str = Field(..., description="Reference to CommandProposal")
    batch_tick: int = Field(..., description="Simulation tick index")
    action: ActionType = Field(..., description="APPROVE, MODIFY, DELAY, or REJECT")
    approved_value: Optional[float] = Field(None, description="Enforced parameter value")
    delta_utility: float = Field(..., description="Predicted joint utility improvement")
    reason_code: ReasonCode = Field(..., description="Audit rationale code")
    hold_count: int = Field(default=0, description="Hold counter state")
    twin_eval_count: int = Field(default=0, description="Number of twin evaluations consumed (<=6)")
    decided_at_epoch_ms: int = Field(..., description="Decision timestamp")

class TelemetrySample(BaseModel):
    sample_id: str
    cell_id: str
    sinr_db: float
    dropped_users: int
    active_users: int
    throughput_mbps: float
    energy_consumption_w: float
    timestamp_epoch_ms: int

class CellState(BaseModel):
    cell_id: str
    x_coord: float
    y_coord: float
    current_tx_power_dbm: float
    antenna_tilt_deg: float
    is_sleeping: bool
    active_users: int
    load_ratio: float
    neighbor_cell_ids: List[str]
