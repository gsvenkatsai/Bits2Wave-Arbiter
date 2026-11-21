"""Frozen v2 contracts for project ARBITER.

Establishes schemas and protocols for multi-developer v2 feature parallelization:
- Digital Twin calibrated uncertainty intervals
- Multi-tick sequential arbitration plans
- Counterfactual replay & explainability
- Offline optimality gap analysis
- Dynamic RiskDial weights & energy impact
- Simulator checkpoint/restore protocols
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from pydantic import BaseModel, Field, model_validator

from arbiter.shared.models import ParameterType


class PredictionInterval(BaseModel):
    """Calibrated prediction interval for a single radio KPI."""

    kpi: str = Field(..., description="Target KPI metric identifier")
    mean: float = Field(..., description="Expected mean point estimate")
    lower: float = Field(..., description="Lower confidence bound")
    upper: float = Field(..., description="Upper confidence bound")
    coverage: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Nominal coverage probability"
    )


class TwinPredictionV2(BaseModel):
    """Digital twin prediction payload carrying calibrated uncertainty bounds."""

    candidate_id: str = Field(..., description="Evaluated candidate delta ID")
    intervals: List[PredictionInterval] = Field(
        default_factory=list, description="Per-KPI uncertainty intervals"
    )
    calibrated: bool = Field(
        default=False, description="Flag indicating online conformal calibration"
    )
    n_calibration: int = Field(
        default=0, ge=0, description="Number of calibration residuals consumed"
    )


class PlanStep(BaseModel):
    """Single discrete step within a multi-tick arbitration plan."""

    step_index: int = Field(..., ge=0, description="Sequence index in plan")
    tick_offset: int = Field(
        ..., ge=0, description="Relative tick offset from plan execution start"
    )
    target_cell_id: str = Field(..., description="Target cell identifier")
    parameter: ParameterType = Field(..., description="Target RAN parameter")
    value: float = Field(..., description="Actuation parameter value")
    precondition: str = Field(
        ..., description="Guard condition string, e.g. 'load(cell_A)<0.60'"
    )
    status: str = Field(
        default="PENDING", description="Step status: PENDING, COMPLETED, ABORTED"
    )


class ArbitrationPlan(BaseModel):
    """Multi-tick sequential arbitration plan for coordinating complex actions."""

    plan_id: str = Field(..., description="Unique plan UUID")
    source_command_id: str = Field(..., description="Originating proposal ID")
    created_tick: int = Field(..., ge=0, description="Creation tick index")
    expires_tick: int = Field(..., ge=0, description="Expiration tick index")
    steps: List[PlanStep] = Field(
        default_factory=list, description="Ordered steps in arbitration plan"
    )
    rationale: str = Field(
        ..., description="Architectural rationale for plan decomposition"
    )


class KpiSummary(BaseModel):
    """Aggregated cluster-wide KPI state at a given tick."""

    dropped_users: int = Field(..., ge=0, description="Total dropped users")
    mean_sinr_db: float = Field(..., description="Cluster mean SINR in dB")
    energy_w: float = Field(..., ge=0.0, description="Total cluster power in Watts")
    interference_db: float = Field(
        ..., description="Average interference level in dBm/dB"
    )
    utility: float = Field(..., description="Multi-objective scalar utility")


class CounterfactualResult(BaseModel):
    """Comparative counterfactual evaluation comparing ARBITER against raw actuation."""

    decision_id: str = Field(..., description="Arbitration decision identifier")
    batch_tick: int = Field(..., ge=0, description="Arbitration trigger tick")
    horizon_ticks: int = Field(
        ..., ge=1, description="Number of ticks in counterfactual rollout"
    )
    with_arbiter: KpiSummary = Field(
        ..., description="KPI outcome under ARBITER coordination"
    )
    without_arbiter: KpiSummary = Field(
        ..., description="Counterfactual KPI outcome without ARBITER"
    )
    diff: Dict[str, float] = Field(
        default_factory=dict, description="Metric differentials (with - without)"
    )
    explanation: str = Field(
        ..., description="Natural language attribution summary"
    )


class OptimalityResult(BaseModel):
    """Benchmark comparing ARBITER against offline Pareto-optimal combinatorial solver."""

    scenario: str = Field(..., description="Scenario identifier")
    seed: int = Field(..., description="PRNG seed")
    utility_baseline: float = Field(
        ..., description="Utility under uncoordinated baseline"
    )
    utility_arbiter: float = Field(
        ..., description="Utility achieved by ARBITER"
    )
    utility_optimum: float = Field(
        ..., description="Theoretical maximum offline utility"
    )
    gap_pct: float = Field(
        ..., description="Optimality gap percentage relative to optimal"
    )
    arbiter_ms: float = Field(
        ..., ge=0.0, description="ARBITER runtime in milliseconds"
    )
    optimum_s: float = Field(
        ..., ge=0.0, description="Offline solver runtime in seconds"
    )
    combos_evaluated: int = Field(
        ..., ge=0, description="Total combinatorial candidate states explored"
    )


class RiskDial(BaseModel):
    """User-facing multi-objective weighting dial for arbitration objectives."""

    weight_coverage: float = Field(
        ..., ge=0.0, le=1.0, description="Coverage optimization weight"
    )
    weight_energy: float = Field(
        ..., ge=0.0, le=1.0, description="Energy saving optimization weight"
    )
    weight_interference: float = Field(
        ..., ge=0.0, le=1.0, description="Interference mitigation weight"
    )
    weight_drop_rate: float = Field(
        ..., ge=0.0, le=1.0, description="Drop-rate penalty weight"
    )
    label: str = Field(default="balanced", description="Semantic dial profile label")

    @model_validator(mode="after")
    def _validate_weights_sum(self) -> "RiskDial":
        total = (
            self.weight_coverage
            + self.weight_energy
            + self.weight_interference
            + self.weight_drop_rate
        )
        if abs(total - 1.0) > 1e-4:
            raise ValueError(
                f"RiskDial weights must sum to 1.0 (got {total:.6f})"
            )
        return self

    @property
    def total(self) -> float:
        """Sum of all four dial weights."""
        return (
            self.weight_coverage
            + self.weight_energy
            + self.weight_interference
            + self.weight_drop_rate
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return weights as a 4-tuple."""
        return (
            self.weight_coverage,
            self.weight_energy,
            self.weight_interference,
            self.weight_drop_rate,
        )


class ImpactRecord(BaseModel):
    """Environmental and economic impact record for energy optimization auditing."""

    tick: int = Field(..., ge=0, description="Simulation tick index")
    energy_saved_kwh: float = Field(
        ..., ge=0.0, description="Cumulative or tick energy saved in kWh"
    )
    cost_inr: float = Field(..., description="Monetary savings in INR")
    co2_g: float = Field(..., ge=0.0, description="Avoided CO2 emissions in grams")
    tariff_label: str = Field(
        ..., description="Electricity tariff classification label"
    )


class SimulationSnapshot(BaseModel):
    """Opaque checkpoint payload capturing complete simulator state."""

    tick: int = Field(..., ge=0, description="Tick index at snapshot capture")
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque state dictionary owned exclusively by simulator",
    )


@runtime_checkable
class SnapshotCapable(Protocol):
    """Protocol for simulation engines supporting deterministic state checkpoints."""

    def snapshot(self) -> SimulationSnapshot:
        """Capture an opaque state snapshot of the running simulation."""
        ...

    def restore(self, snap: SimulationSnapshot) -> None:
        """Restore engine state from a previously captured snapshot."""
        ...

    def fork(self, seed: Optional[int] = None) -> Any:
        """Fork an independent, identically stateful copy of the simulator."""
        ...


@runtime_checkable
class WeightSettable(Protocol):
    """Protocol for decision engines supporting dynamic RiskDial weight tuning."""

    def set_weights(self, dial: RiskDial) -> None:
        """Apply dynamic weights from a user-configured RiskDial."""
        ...

    def get_weights(self) -> RiskDial:
        """Retrieve the currently active RiskDial weights."""
        ...

