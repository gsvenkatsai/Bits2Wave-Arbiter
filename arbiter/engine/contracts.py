"""Single integration point for the Branch 2 contracts.

Every Branch 2 module (queue, detector, engine) imports its shared types from
this file only, so aligning with `arbiter.shared.models` is a one-file change.

At import time the module tries to adopt `arbiter.shared.models`. A shared
symbol is adopted only when the *whole* contract set is compatible with the
Branch 2 spec shape (all required enums members, all required model fields);
otherwise the spec-identical local definitions below are used. They are kept
as an all-or-nothing set so enums referenced by the local models can never be
mixed with shared ones.

The frozen spec constants also live here.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, List

from pydantic import BaseModel, Field, model_validator

__all__ = [
    # constants
    "FLOOR_COVERAGE",
    "FLOOR_DROP_RATE",
    "MAX_HOLD_TICKS",
    "PINGPONG_WINDOW",
    "TWIN_TIMEOUT_MS",
    "TICK_MS",
    "MAX_TWIN_EVALS",
    # enums
    "ActionType",
    "ConflictType",
    "ReasonCode",
    # models
    "ArbitrationDecision",
    "UtilityWeights",
    "CommandProposal",
    "WEIGHT_PRESETS",
    "SHARED_CONTRACTS_AVAILABLE",
    "contract_summary",
]

# ---------------------------------------------------------------------------
# Frozen spec constants
# ---------------------------------------------------------------------------
#: Coverage safety floor: a change may never push coverage below this value.
FLOOR_COVERAGE: float = 0.90
#: Drop-rate safety floor: a change may never push the drop ratio above this.
FLOOR_DROP_RATE: float = 0.02
#: A delayed command must be decided after at most three ticks.
MAX_HOLD_TICKS: int = 3
#: Sliding window (ticks) used for implicit ping-pong detection.
PINGPONG_WINDOW: int = 5
#: Twin evaluation budget before a decision is treated as timed out.
TWIN_TIMEOUT_MS: float = 100.0
#: Arbitration batch cadence (100 ms tick).
TICK_MS: int = 100
#: Hard cap on twin evaluations for one MODIFY search (NFR-3).
MAX_TWIN_EVALS: int = 6

# ---------------------------------------------------------------------------
# Local spec definitions (used when the shared contracts are unavailable or
# incompatible). TODO(INTEGRATION): drop these once arbiter.shared.models
# publishes the Branch 2 shapes.
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    """Decision-ladder rungs."""

    APPROVE = "APPROVE"
    MODIFY = "MODIFY"
    DELAY = "DELAY"
    REJECT = "REJECT"


class ConflictType(str, Enum):
    """Clash taxonomy carried by conflict-graph edges."""

    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    IMPLICIT_PINGPONG = "IMPLICIT_PINGPONG"


class ReasonCode(str, Enum):
    """Audit rationale attached to every decision."""

    NONE = "NONE"
    ACTIVE_HO = "ACTIVE_HO"
    HEADROOM = "HEADROOM"
    CYCLIC_PINGPONG = "CYCLIC_PINGPONG"
    TWIN_TIMEOUT = "TWIN_TIMEOUT"
    UNCERTAINTY_GUARD = "UNCERTAINTY_GUARD"
    FLOOR_BREACH = "FLOOR_BREACH"
    EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"


#: Named weight presets: (w1 coverage, w2 energy, w3 interference, w4 drop).
WEIGHT_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "default": (0.3, 0.2, 0.2, 0.3),
    "night_energy_saver": (0.2, 0.4, 0.2, 0.2),
    "coverage_first": (0.5, 0.15, 0.15, 0.2),
}


class UtilityWeights(BaseModel):
    """Linear multi-objective weights.

    ``U = w1*Coverage - w2*Energy - w3*Interference - w4*DropRate``.
    Weights must be non-negative and must not all be zero.
    """

    w1: float = Field(default=0.3, ge=0.0, description="Coverage weight")
    w2: float = Field(default=0.2, ge=0.0, description="Energy weight")
    w3: float = Field(default=0.2, ge=0.0, description="Interference weight")
    w4: float = Field(default=0.3, ge=0.0, description="Drop-rate weight")

    @model_validator(mode="after")
    def _require_positive_total(self) -> "UtilityWeights":
        if self.total <= 0.0:
            raise ValueError("UtilityWeights must sum to a positive value")
        return self

    @property
    def total(self) -> float:
        """Sum of all four weights."""
        return self.w1 + self.w2 + self.w3 + self.w4

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return ``(w1, w2, w3, w4)``."""
        return (self.w1, self.w2, self.w3, self.w4)

    @classmethod
    def from_preset(cls, name: str) -> "UtilityWeights":
        """Build weights from a named preset; unknown names raise ``ValueError``."""
        try:
            w1, w2, w3, w4 = WEIGHT_PRESETS[name]
        except KeyError as exc:
            raise ValueError(
                f"unknown weight preset {name!r}; "
                f"expected one of {sorted(WEIGHT_PRESETS)}"
            ) from exc
        return cls(w1=w1, w2=w2, w3=w3, w4=w4)


class CommandProposal(BaseModel):
    """A controller's request to change one radio parameter of one cell."""

    command_id: str = Field(..., description="Unique id for this proposal")
    controller: str = Field(..., description="Originating controller identifier")
    target_cell: str = Field(..., description="Cell the change applies to")
    parameter: str = Field(..., description="Radio parameter to change")
    requested_value: float = Field(..., description="Value the controller wants")
    current_value: float = Field(..., description="Value currently in effect")
    is_emergency: bool = Field(
        default=False, description="Bypass arbitration; audited post-hoc"
    )
    is_binary: bool = Field(
        default=False, description="Binary parameter (e.g. cell OFF) skips MODIFY"
    )
    created_tick: int = Field(..., description="Tick the proposal was created")
    hold_count: int = Field(default=0, description="Ticks this command was delayed")
    handover_from: str | None = Field(
        default=None, description="Source cell for a handover-style action"
    )
    handover_to: str | None = Field(
        default=None, description="Destination cell for a handover-style action"
    )


class ArbitrationDecision(BaseModel):
    """The referee's verdict for one command in one tick batch."""

    decision_id: str = Field(..., description="Unique id for this decision")
    command_id: str = Field(..., description="Reference to the CommandProposal")
    batch_tick: int = Field(..., description="Simulation tick index")
    action: ActionType = Field(..., description="APPROVE, MODIFY, DELAY or REJECT")
    approved_value: float | None = Field(
        default=None, description="Enforced parameter value (None if not applied)"
    )
    delta_utility: float = Field(..., description="Predicted joint utility change")
    reason_code: ReasonCode = Field(..., description="Audit rationale")
    hold_count: int = Field(default=0, description="Hold counter after the decision")
    twin_eval_count: int = Field(
        default=0, description="Twin evaluations consumed (<= MAX_TWIN_EVALS)"
    )
    decided_at_epoch_ms: int = Field(..., description="Decision timestamp (epoch ms)")


# ---------------------------------------------------------------------------
# Shared-contract adoption
# ---------------------------------------------------------------------------
CONTRACT_NAMES: tuple[str, ...] = (
    "ActionType",
    "ConflictType",
    "ReasonCode",
    "UtilityWeights",
    "CommandProposal",
    "ArbitrationDecision",
)

_REQUIRED_ENUM_MEMBERS: dict[str, frozenset[str]] = {
    "ActionType": frozenset({"APPROVE", "MODIFY", "DELAY", "REJECT"}),
    "ConflictType": frozenset({"DIRECT", "INDIRECT", "IMPLICIT_PINGPONG"}),
    "ReasonCode": frozenset(
        {
            "NONE",
            "ACTIVE_HO",
            "HEADROOM",
            "CYCLIC_PINGPONG",
            "TWIN_TIMEOUT",
            "UNCERTAINTY_GUARD",
            "FLOOR_BREACH",
            "EMERGENCY_OVERRIDE",
        }
    ),
}

_REQUIRED_MODEL_FIELDS: dict[str, frozenset[str]] = {
    "CommandProposal": frozenset(
        {
            "command_id",
            "controller",
            "target_cell",
            "parameter",
            "requested_value",
            "current_value",
            "is_emergency",
            "is_binary",
            "created_tick",
            "hold_count",
            "handover_from",
            "handover_to",
        }
    ),
    "ArbitrationDecision": frozenset(
        {
            "decision_id",
            "command_id",
            "batch_tick",
            "action",
            "approved_value",
            "delta_utility",
            "reason_code",
            "hold_count",
            "twin_eval_count",
            "decided_at_epoch_ms",
        }
    ),
    "UtilityWeights": frozenset({"w1", "w2", "w3", "w4"}),
}

SHARED_CONTRACTS_AVAILABLE: bool = False


def _is_spec_compatible(shared: Any) -> bool:
    """True when the shared module publishes every Branch 2 shape unchanged."""
    for name, members in _REQUIRED_ENUM_MEMBERS.items():
        candidate = getattr(shared, name, None)
        if not (isinstance(candidate, type) and issubclass(candidate, Enum)):
            return False
        if not members <= {member.value for member in candidate}:
            return False
    for name, fields in _REQUIRED_MODEL_FIELDS.items():
        candidate = getattr(shared, name, None)
        if not (isinstance(candidate, type) and issubclass(candidate, BaseModel)):
            return False
        if not fields <= set(candidate.model_fields):
            return False
    return callable(getattr(getattr(shared, "UtilityWeights", None), "from_preset", None))


def _adopt_shared_contracts() -> None:
    """Rebind the module contracts to `arbiter.shared.models` when compatible."""
    global SHARED_CONTRACTS_AVAILABLE
    try:
        from arbiter.shared import models as shared
    except Exception:  # pragma: no cover - shared module always present today
        return
    if not _is_spec_compatible(shared):
        return
    for name in CONTRACT_NAMES:  # pragma: no cover - integration path only
        globals()[name] = getattr(shared, name)
    SHARED_CONTRACTS_AVAILABLE = True


_adopt_shared_contracts()


def contract_summary() -> dict[str, List[str]]:
    """Diagnostic helper proving which contract source is in use."""
    return {
        "shared_available": [str(SHARED_CONTRACTS_AVAILABLE)],
        "actions": [action.value for action in ActionType],
        "conflicts": [conflict.value for conflict in ConflictType],
        "reasons": [reason.value for reason in ReasonCode],
        "presets": sorted(WEIGHT_PRESETS),
    }
