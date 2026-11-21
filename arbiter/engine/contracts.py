"""Single integration point for the frozen shared contracts.

Every module on Branch 2 (queue, detector, engine) imports its contract types
from this file only.  When ``arbiter.shared.models`` lands (or changes), the
only edit required for integration is deleting the local fallbacks below:
they are re-bound automatically at import time when the shared module
provides the symbol.

Fallback definitions are byte-for-byte equivalent to the Branch 2 spec so the
engine behaves identically before and after integration.
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field

__all__ = [
    "ActionType",
    "ConflictType",
    "ReasonCode",
    "ArbitrationDecision",
    "UtilityWeights",
    "CommandProposal",
    "ControllerType",
    "ParameterType",
    "SHARED_CONTRACTS_AVAILABLE",
]

# ---------------------------------------------------------------------------
# Enum / model imports from the shared package, with spec-identical fallbacks
# ---------------------------------------------------------------------------
SHARED_CONTRACTS_AVAILABLE: bool = False

try:  # pragma: no cover - exercised by the integration environment, not here
    from arbiter.shared.models import (
        ActionType,
        ArbitrationDecision,
        CommandProposal,
        ConflictType,
        ControllerType,
        ParameterType,
        ReasonCode,
    )

    SHARED_CONTRACTS_AVAILABLE = True
except Exception:  # pragma: no cover - fallback path covered by unit tests
    # TODO(INTEGRATION): remove these fallbacks once arbiter.shared.models is frozen.
    from enum import Enum

    class ActionType(str, Enum):  # type: ignore[no-redef]
        """Decision ladder rungs."""

        APPROVE = "APPROVE"
        MODIFY = "MODIFY"
        DELAY = "DELAY"
        REJECT = "REJECT"

    class ConflictType(str, Enum):  # type: ignore[no-redef]
        """Clash taxonomy used by the conflict graph."""

        DIRECT = "DIRECT"
        INDIRECT = "INDIRECT"
        IMPLICIT_PINGPONG = "IMPLICIT_PINGPONG"

    class ReasonCode(str, Enum):  # type: ignore[no-redef]
        """Audit rationale codes."""

        NONE = "NONE"
        ACTIVE_HO = "ACTIVE_HO"
        HEADROOM = "HEADROOM"
        CYCLIC_PINGPONG = "CYCLIC_PINGPONG"
        TWIN_TIMEOUT = "TWIN_TIMEOUT"
        UNCERTAINTY_GUARD = "UNCERTAINTY_GUARD"
        FLOOR_BREACH = "FLOOR_BREACH"
        EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"

    class ControllerType(str, Enum):  # type: ignore[no-redef]
        """Controller classes competing for the same radio parameters."""

        ENERGY_SAVING = "ENERGY_SAVING"
        LOAD_BALANCING = "LOAD_BALANCING"
        COVERAGE_CAPACITY = "COVERAGE_CAPACITY"
        PUBLIC_SAFETY = "PUBLIC_SAFETY"

    class ParameterType(str, Enum):  # type: ignore[no-redef]
        """Actuatable radio parameters."""

        TX_POWER_DBM = "TX_POWER_DBM"
        TILT_DEG = "TILT_DEG"
        SLEEP_STATE = "SLEEP_STATE"
        OFFLOAD_RATIO = "OFFLOAD_RATIO"

    class CommandProposal(BaseModel):  # type: ignore[no-redef]
        """A controller's request to change one radio parameter of one cell."""

        command_id: str = Field(..., description="Unique UUID for command proposal")
        controller_id: str = Field(..., description="Originating controller identifier")
        controller_type: ControllerType = Field(..., description="Controller class")
        target_cell_id: str = Field(..., description="Target cell identifier")
        parameter: ParameterType = Field(..., description="Parameter targeted for alteration")
        requested_value: float = Field(..., description="Target value requested")
        emergency_flag: bool = Field(
            default=False, description="Bypass flag for emergency overrides"
        )
        timestamp_epoch_ms: int = Field(
            ..., description="Unix epoch timestamp in milliseconds"
        )
        hold_count: int = Field(
            default=0, description="Number of ticks this command has been delayed"
        )

    class ArbitrationDecision(BaseModel):  # type: ignore[no-redef]
        """The referee's verdict for one command in one tick batch."""

        decision_id: str = Field(..., description="Unique UUID for arbitration outcome")
        command_id: str = Field(..., description="Reference to CommandProposal")
        batch_tick: int = Field(..., description="Simulation tick index")
        action: ActionType = Field(..., description="APPROVE, MODIFY, DELAY, or REJECT")
        approved_value: Optional[float] = Field(None, description="Enforced parameter value")
        delta_utility: float = Field(..., description="Predicted joint utility improvement")
        reason_code: ReasonCode = Field(..., description="Audit rationale code")
        hold_count: int = Field(default=0, description="Hold counter state")
        twin_eval_count: int = Field(
            default=0, description="Number of twin evaluations consumed (<=6)"
        )
        decided_at_epoch_ms: int = Field(..., description="Decision timestamp")

# ---------------------------------------------------------------------------
# UtilityWeights: Branch 2 owns this shape and it is not part of the shared
# model freeze, so it is always defined locally (still re-bound if shared
# ever publishes an identical class).
# ---------------------------------------------------------------------------
# TODO(INTEGRATION): switch to arbiter.shared.models.UtilityWeights if added.
WeightsLike: Any = None

try:  # pragma: no cover - only when shared publishes the class
    from arbiter.shared.models import UtilityWeights  # type: ignore[attr-defined]

    WeightsLike = UtilityWeights
except Exception:  # pragma: no cover - normal pre-integration path
    WeightsLike = None


if WeightsLike is None:

    class UtilityWeights(BaseModel):  # type: ignore[no-redef]
        """Linear multi-objective weights; each term is in [0, 1].

        ``U = w1*Coverage - w2*Energy - w3*Interference - w4*DropRate``
        """

        coverage: float = Field(default=0.3, ge=0.0, le=1.0, alias="w1")
        energy: float = Field(default=0.2, ge=0.0, le=1.0, alias="w2")
        interference: float = Field(default=0.2, ge=0.0, le=1.0, alias="w3")
        drop_rate: float = Field(default=0.3, ge=0.0, le=1.0, alias="w4")

        model_config = {"populate_by_name": True}

        def as_tuple(self) -> "tuple[float, float, float, float]":
            """Return ``(w1, w2, w3, w4)`` for allocation-free evaluation."""
            return (self.coverage, self.energy, self.interference, self.drop_rate)

else:  # pragma: no cover - shared class available
    UtilityWeights = WeightsLike


def contract_summary() -> "dict[str, List[str]]":
    """Small helper used by tests and diagnostics to prove which path loaded."""
    return {
        "shared_available": [str(SHARED_CONTRACTS_AVAILABLE)],
        "actions": [a.value for a in ActionType],
        "conflicts": [c.value for c in ConflictType],
        "reasons": [r.value for r in ReasonCode],
    }
