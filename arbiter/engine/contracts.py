"""Single integration point for the Branch 2 contracts.

Branch 2 now sits directly on the real shared models from
``arbiter.shared.models``:

* ``ActionType``, ``ConflictType`` and ``ReasonCode`` are imported verbatim, so
  a decision produced here is comparable with any other branch's decision;
* ``ArbitrationDecision`` is imported verbatim (the Branch 2 spec shape and the
  shared shape are field-for-field identical);
* ``CommandProposal`` is a *superset* subclass of the shared model: it keeps the
  shared field names/aliases and adds the six fields arbitration needs
  (``current_value``, ``is_binary``, ``created_tick``, ``handover_from``,
  ``handover_to``, ``must_decide``). Legacy Branch 2 attribute names
  (``controller``, ``target_cell``, ``is_emergency``) are read-only properties
  over the shared fields, so call sites do not care which name is authoritative.

``UtilityWeights`` has no shared counterpart yet, so it stays local (see
``INTEGRATION.md`` §4).

The frozen spec constants also live here.
"""
from __future__ import annotations

from typing import List

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from arbiter.shared.models import (
    ActionType,
    ArbitrationDecision,
    CommandProposal as SharedCommandProposal,
    ConflictType,
    ControllerType,
    ParameterType,
    ReasonCode,
)

__all__ = [
    # constants
    "FLOOR_COVERAGE",
    "FLOOR_DROP_RATE",
    "MAX_HOLD_TICKS",
    "PINGPONG_WINDOW",
    "TWIN_TIMEOUT_MS",
    "TICK_MS",
    "MAX_TWIN_EVALS",
    # enums (re-exported from arbiter.shared.models)
    "ActionType",
    "ConflictType",
    "ControllerType",
    "ParameterType",
    "ReasonCode",
    # models
    "ArbitrationDecision",
    "UtilityWeights",
    "CommandProposal",
    "SharedCommandProposal",
    "WEIGHT_PRESETS",
    "SHARED_CONTRACTS_AVAILABLE",
    "CONTRACTS_SOURCE",
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

#: Contract source in effect; the shared models are the single source of truth.
CONTRACTS_SOURCE: str = "arbiter.shared.models"
SHARED_CONTRACTS_AVAILABLE: bool = True


# ---------------------------------------------------------------------------
# Branch 2 proposal: the shared proposal plus the arbitration-only fields
# ---------------------------------------------------------------------------
class CommandProposal(SharedCommandProposal):
    """A controller request to change one radio parameter of one cell.

    The shared fields keep their shared names (``controller_id``,
    ``target_cell_id``, ``emergency_flag``, ``parameter``, ``requested_value``,
    ``timestamp_epoch_ms``, ``hold_count``) and also accept the Branch 2
    spellings (``controller``, ``target_cell``, ``is_emergency``) as validation
    aliases, so an ingestion route can use either vocabulary.

    ``created_tick`` is the arbitration clock. When it is left at its default
    and ``timestamp_epoch_ms`` is supplied, it is derived as
    ``timestamp_epoch_ms // TICK_MS``.
    """

    model_config = ConfigDict(populate_by_name=True)

    controller_id: str = Field(
        ...,
        validation_alias=AliasChoices("controller_id", "controller"),
        description="Originating controller identifier",
    )
    target_cell_id: str = Field(
        ...,
        validation_alias=AliasChoices("target_cell_id", "target_cell"),
        description="Cell the change applies to",
    )
    emergency_flag: bool = Field(
        default=False,
        validation_alias=AliasChoices("emergency_flag", "is_emergency"),
        description="Bypass arbitration; audited post-hoc",
    )

    current_value: float = Field(
        default=0.0, description="Value currently in effect on the target"
    )
    is_binary: bool = Field(
        default=False, description="Binary parameter (e.g. cell OFF) skips MODIFY"
    )
    created_tick: int = Field(
        default=0, description="Arbitration tick the proposal was created"
    )
    handover_from: str | None = Field(
        default=None, description="Source cell for a handover-style action"
    )
    handover_to: str | None = Field(
        default=None, description="Destination cell for a handover-style action"
    )
    must_decide: bool = Field(
        default=False,
        description="Latched by the queue once the hold budget is exhausted",
    )

    @model_validator(mode="after")
    def _derive_created_tick(self) -> "CommandProposal":
        """Derive the tick index when only the shared timestamp is available."""
        if self.created_tick == 0 and self.timestamp_epoch_ms > 0:
            self.created_tick = int(self.timestamp_epoch_ms) // TICK_MS
        return self

    @property
    def controller(self) -> str:
        """Branch 2 alias for the shared ``controller_id``."""
        return self.controller_id

    @property
    def target_cell(self) -> str:
        """Branch 2 alias for the shared ``target_cell_id``."""
        return self.target_cell_id

    @property
    def is_emergency(self) -> bool:
        """Branch 2 alias for the shared ``emergency_flag``."""
        return self.emergency_flag


# ---------------------------------------------------------------------------
# Weights (no shared counterpart yet)
# ---------------------------------------------------------------------------
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


def contract_summary() -> dict[str, List[str]]:
    """Diagnostic helper proving which contract source is in use."""
    return {
        "contracts_source": [CONTRACTS_SOURCE],
        "shared_available": [str(SHARED_CONTRACTS_AVAILABLE)],
        "actions": [action.value for action in ActionType],
        "conflicts": [conflict.value for conflict in ConflictType],
        "reasons": [reason.value for reason in ReasonCode],
        "presets": sorted(WEIGHT_PRESETS),
    }
