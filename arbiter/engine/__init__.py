"""Branch 2 arbitration engine: contracts, mocks and (later) the ladder."""
from __future__ import annotations

from arbiter.engine.contracts import (
    FLOOR_COVERAGE,
    FLOOR_DROP_RATE,
    MAX_HOLD_TICKS,
    MAX_TWIN_EVALS,
    PINGPONG_WINDOW,
    TICK_MS,
    TWIN_TIMEOUT_MS,
    WEIGHT_PRESETS,
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ConflictType,
    ReasonCode,
    UtilityWeights,
)
from arbiter.engine.bisection import (
    BisectionResult,
    BoundedBisectionSolver,
    BudgetExceeded,
    EvaluationBudget,
)
from arbiter.engine.ladder import (
    DecisionLadderEngine,
    SafetyFloorEnforcer,
    UncertaintyGuard,
)
from arbiter.engine.planner import (
    ArbitrationPlanner,
    evaluate_precondition,
)
from arbiter.engine.plan_executor import (
    PlanExecutionResult,
    PlanExecutor,
    get_plan,
    list_plans,
)
from arbiter.shared.v2_contracts import (
    ArbitrationPlan,
    PlanStep,
    PredictionInterval,
    RiskDial,
    TwinPredictionV2,
    WeightSettable,
)
from arbiter.actuator.enforcer import ActuatorEnforcer
from arbiter.engine.actuator_adapter import ActuatorAdapter
from arbiter.engine.mocks.mock_actuator_client import AppliedChange, MockActuatorClient
from arbiter.engine.mocks.mock_twin_client import (
    MockDigitalTwinClient,
    ScriptedTwin,
    TwinClient,
    TwinTimeoutError,
)
from arbiter.engine.twin_adapter import TwinClientAdapter
from arbiter.engine.utility import UtilityCalculator
from arbiter.twin.evaluator import DigitalTwinEvaluator

__all__ = [
    "CommandProposal",
    "ArbitrationDecision",
    "UtilityWeights",
    "ActionType",
    "ConflictType",
    "ReasonCode",
    "WEIGHT_PRESETS",
    "TwinClient",
    "TwinClientAdapter",
    "DigitalTwinEvaluator",
    "MockDigitalTwinClient",
    "ScriptedTwin",
    "TwinTimeoutError",
    "ActuatorEnforcer",
    "ActuatorAdapter",
    "MockActuatorClient",
    "AppliedChange",
    "UtilityCalculator",
    "BoundedBisectionSolver",
    "BisectionResult",
    "EvaluationBudget",
    "BudgetExceeded",
    "DecisionLadderEngine",
    "SafetyFloorEnforcer",
    "UncertaintyGuard",
    "WeightSettable",
    "RiskDial",
    "TwinPredictionV2",
    "PredictionInterval",
    "ArbitrationPlan",
    "PlanStep",
    "ArbitrationPlanner",
    "PlanExecutor",
    "PlanExecutionResult",
    "evaluate_precondition",
    "list_plans",
    "get_plan",
    "FLOOR_COVERAGE",
    "FLOOR_DROP_RATE",
    "MAX_HOLD_TICKS",
    "PINGPONG_WINDOW",
    "TWIN_TIMEOUT_MS",
    "TICK_MS",
    "MAX_TWIN_EVALS",
]
