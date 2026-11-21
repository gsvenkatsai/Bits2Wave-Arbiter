"""Branch 2 arbitration engine public surface."""
from __future__ import annotations

from arbiter.engine.arbitrator import ArbitrationConfig, Arbitrator, BatchOutcome
from arbiter.engine.compromise import (
    CompromiseResult,
    CompromiseSearch,
    TwinBudget,
    TwinBudgetExceeded,
)
from arbiter.engine.constants import (
    BINARY_PARAMETERS,
    BISECTION_MAX_EVALS,
    MAX_DROP_RATE_RATIO,
    MAX_HOLD_TICKS,
    MIN_COVERAGE_RATIO,
    PINGPONG_WINDOW_TICKS,
    TWIN_TIMEOUT_MS,
)
from arbiter.engine.contracts import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ConflictType,
    ReasonCode,
    UtilityWeights,
)
from arbiter.engine.deltas import CandidateDelta, build_delta
from arbiter.engine.mocks.mock_twin_client import (
    MockTwinClient,
    TwinClient,
    TwinEvaluationError,
)
from arbiter.engine.utility import compute_utility, floors_satisfied, utility_delta

__all__ = [
    "Arbitrator",
    "ArbitrationConfig",
    "BatchOutcome",
    "CompromiseSearch",
    "CompromiseResult",
    "TwinBudget",
    "TwinBudgetExceeded",
    "TwinClient",
    "MockTwinClient",
    "TwinEvaluationError",
    "ActionType",
    "ArbitrationDecision",
    "CommandProposal",
    "ConflictType",
    "ReasonCode",
    "UtilityWeights",
    "CandidateDelta",
    "build_delta",
    "compute_utility",
    "utility_delta",
    "floors_satisfied",
    "BISECTION_MAX_EVALS",
    "MAX_HOLD_TICKS",
    "TWIN_TIMEOUT_MS",
    "MIN_COVERAGE_RATIO",
    "MAX_DROP_RATE_RATIO",
    "PINGPONG_WINDOW_TICKS",
    "BINARY_PARAMETERS",
]
