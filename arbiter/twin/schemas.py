"""DTO schemas and standalone integration protocol interfaces for Branch 3.

Target SRS: FR-5.1, FR-5.2, FR-7.1.
Branch 3 merges first, establishing these contracts cleanly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, runtime_checkable
import numpy as np
from pydantic import BaseModel, Field

# Concrete DTO Models matching SRS and technical specification
class CandidateDelta(BaseModel):
    """Command parameter delta evaluated by the digital twin."""
    cell_id: str = Field(..., description="Target cell identifier")
    parameter: str = Field(..., description="Parameter targeted for alteration (e.g. TX_POWER_DBM, SLEEP_STATE)")
    proposed_value: float = Field(..., description="Proposed numeric value")


class PredictedKPIs(BaseModel):
    """Predicted joint multi-cell KPIs across affected cluster."""
    coverage_ratio: float = Field(..., ge=0.0, le=1.0, description="Estimated coverage ratio [0, 1]")
    normalized_energy: float = Field(..., ge=0.0, le=1.0, description="Normalized active power consumption [0, 1]")
    normalized_interference: float = Field(..., ge=0.0, le=1.0, description="Normalized interference level [0, 1]")
    drop_rate_ratio: float = Field(..., ge=0.0, le=1.0, description="Estimated user drop rate ratio [0, 1]")
    predicted_sinr_avg_db: float = Field(..., description="Predicted average SINR in dB")
    affected_cell_ids: List[str] = Field(..., description="List of cells affected by evaluation")


class TwinEvaluationResponse(BaseModel):
    """Complete response returned by twin evaluation pipeline."""
    evaluation_id: str = Field(..., description="Unique evaluation identifier")
    computation_time_ms: float = Field(..., description="Latency consumed by twin computation in milliseconds")
    predicted_kpis: PredictedKPIs = Field(..., description="Predicted multi-cell KPIs")
    safety_floors_satisfied: bool = Field(..., description="True if predicted KPIs meet all safety floors")
    confidence_margin: float = Field(..., description="Calibrated error confidence guard margin")


class TwinEvaluationRequest(BaseModel):
    """Incoming request payload for twin evaluation endpoint."""
    evaluation_id: str = Field(..., description="Unique evaluation identifier")
    candidate_deltas: List[CandidateDelta] = Field(..., description="List of proposed parameter changes")
    timeout_budget_ms: Optional[float] = Field(default=None, description="Optional maximum latency budget in ms")


class ActuatorCommitRequest(BaseModel):
    """Incoming request payload for actuator commit endpoint."""
    commit_id: str = Field(..., description="Unique UUID for commit execution")
    decision_id: str = Field(..., description="Reference to decision")
    command_id: str = Field(..., description="Reference to command proposal")
    target_cell_id: str = Field(..., description="Target cell identifier")
    parameter: str = Field(..., description="Target parameter name")
    old_value: float = Field(..., description="Baseline value before actuation")
    new_value: float = Field(..., description="Target value to apply")
    timestamp_epoch_ms: int = Field(..., description="Unix epoch timestamp in milliseconds")


class ActuatorCommitResponse(BaseModel):
    """Response returned by actuator commit execution."""
    status: str = Field(..., description="COMMITTED or ROLLED_BACK")
    commit_seq: int = Field(..., description="Sequential index in commit log")
    applied: bool = Field(..., description="True if parameter was applied to target")


# Standalone integration seams (Critical for merge order)
@dataclass
class ClusterSnapshot:
    """In-memory snapshot of cluster radio state for twin evaluation."""
    version: int
    cell_ids: List[str]
    coupling_matrix: np.ndarray  # Shape: (N, N) coupling / distance-decay gains
    tx_power_dbm: Dict[str, float]
    is_sleeping: Dict[str, bool]
    signal_powers_w: np.ndarray  # Shape: (N,) or (N_ue,) reference signal powers
    current_interference_w: np.ndarray  # Shape: (N,) or (N_ue,) baseline interference
    ue_counts: Dict[str, int]
    noise_floor_w: float
    neighbor_map: Dict[str, List[str]]
    offload_ratios: Dict[str, float] = field(default_factory=dict)
    cell_loads: Dict[str, float] = field(default_factory=dict)
    antenna_tilt_deg: Dict[str, float] = field(default_factory=dict)


@runtime_checkable
class NetworkStateProvider(Protocol):
    """Protocol for reading instantaneous cellular cluster state."""
    def get_snapshot(self) -> ClusterSnapshot:
        """Return the current cluster state snapshot."""
        ...


@runtime_checkable
class NetworkActuationTarget(Protocol):
    """Protocol for applying parameter modifications to target radio entities."""
    def apply_parameter(self, cell_id: str, parameter: str, value: float) -> bool:
        """Apply parameter change to cell. Return True if successful."""
        ...

    def get_parameter(self, cell_id: str, parameter: str) -> float:
        """Retrieve current parameter value for cell."""
        ...
