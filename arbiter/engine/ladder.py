"""FR-6 decision ladder: APPROVE -> MODIFY -> DELAY -> REJECT.

Order per command in a conflict-graph component (emergency commands never reach
the ladder; the bypass lane handles them):

0. Ping-pong: a command the cycle detector flags is REJECTed with
   ``CYCLIC_PINGPONG`` and costs no twin evaluation.
1. APPROVE: one joint prediction of every active command's requested value;
   ``dU > 0`` with both floors holding and a gain outside the confidence margin
   approves the whole component.
2. Uncertainty guard: ``|dU| < margin`` is decided conservatively - DELAY while
   the command is holdable, otherwise REJECT(``UNCERTAINTY_GUARD``).
3. MODIFY: skipped for binary commands (e.g. cell OFF). Otherwise the bounded
   golden-section solver searches between ``current_value`` and
   ``requested_value``; a feasible improving point becomes MODIFY, with
   ``HEADROOM`` unless the twin reported active handovers (``ACTIVE_HO``).
4. DELAY: no feasible compromise but the clash is transient - a conflicting
   command is itself being modified/held, the command is already inside a hold
   cycle, or the twin reported active handovers - and the command is holdable.
5. REJECT otherwise, with ``FLOOR_BREACH`` when the floors are the cause.

Twin budget (NFR-3)
-------------------
Each component owns one six-evaluation budget. The baseline ("do nothing")
prediction costs one evaluation and is shared by the component, the joint
APPROVE probe costs one, and whatever remains is split evenly across the
commands that still have a MODIFY rung. A twin exception, an over-budget wall
time or a reported ``computation_time_ms`` above the timeout turns into
REJECT(``TWIN_TIMEOUT``) with no approval (last-known-good is kept), and
``evaluate`` never raises.

Multi-command components
------------------------
Commands are resolved in priority order. Emergency commands are already
removed upstream. When the remaining budget can afford one standalone probe per
command *and* four evaluations per search (the minimum for a meaningful
compromise), commands are ranked by their standalone ``dU``;
otherwise the documented fallback order applies: non-binary commands first
(they have a compromise rung), then ``(created_tick, command_id)``. Later
commands are evaluated against the values already approved/modified in the same
component; undecided and delayed commands are not applied to the candidate.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Mapping, Sequence

import networkx as nx

from arbiter.detector.cycle_detector import PingPongCycleDetector
from arbiter.detector.graph import batch_order
from arbiter.engine.bisection import BoundedBisectionSolver, EvaluationBudget
from arbiter.engine.contracts import (
    FLOOR_COVERAGE,
    FLOOR_DROP_RATE,
    MAX_HOLD_TICKS,
    MAX_TWIN_EVALS,
    TWIN_TIMEOUT_MS,
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ReasonCode,
    UtilityWeights,
)
from arbiter.engine.mocks.mock_twin_client import TwinClient
from arbiter.engine.utility import UtilityCalculator
from arbiter.shared.constants import (
    DEFAULT_WEIGHT_COVERAGE,
    DEFAULT_WEIGHT_DROP_RATE,
    DEFAULT_WEIGHT_ENERGY,
    DEFAULT_WEIGHT_INTERFERENCE,
)
from arbiter.shared.v2_contracts import RiskDial, TwinPredictionV2

__all__ = [
    "SafetyFloorEnforcer",
    "UncertaintyGuard",
    "DecisionLadderEngine",
    "build_candidate_delta",
    "batch_order",
]


def _is_binary_command(command: CommandProposal) -> bool:
    """Check if command is binary or controls discrete state like sleep."""
    norm = str(getattr(command.parameter, "value", command.parameter)).upper()
    return command.is_binary or "SLEEP" in norm


def build_candidate_delta(
    command: CommandProposal, value: float
) -> dict[str, Any]:
    """Twin payload for applying ``value`` to one command's target."""
    from_val = float(command.current_value)
    norm = str(getattr(command.parameter, "value", command.parameter)).upper()
    if (
        "TX_POWER" in norm
        and from_val == 0.0
        and float(value) > 40.0
    ):
        from_val = 43.0
    return {
        "command_id": command.command_id,
        "cell_id": command.target_cell,
        "parameter": command.parameter.strip().upper(),
        "from_value": from_val,
        "to_value": float(value),
    }


class SafetyFloorEnforcer:
    """Coverage/drop-rate floors, recomputed and never trusted from the twin alone."""

    def __init__(self, calculator: UtilityCalculator | None = None) -> None:
        self._calculator = calculator or UtilityCalculator()

    def floors_ok(
        self,
        kpis: Mapping[str, float],
        twin_floors_satisfied: bool | None = None,
        baseline_kpis: Mapping[str, float] | None = None,
    ) -> bool:
        """True only when the predicted KPIs and the twin flag both allow it."""
        return self._calculator.floors_ok(
            kpis,
            twin_floors_satisfied,
            baseline_kpis=baseline_kpis,
        )

    def breaches(
        self,
        kpis: Mapping[str, float],
        twin_floors_satisfied: bool | None = None,
        baseline_kpis: Mapping[str, float] | None = None,
    ) -> bool:
        """Inverse of :meth:`floors_ok`."""
        return not self.floors_ok(
            kpis,
            twin_floors_satisfied,
            baseline_kpis=baseline_kpis,
        )


class UncertaintyGuard:
    """Conservative handling of improvements too small to trust and calibrated intervals."""

    def __init__(self, default_margin: float = 0.0) -> None:
        if default_margin < 0.0:
            raise ValueError("default_margin must be >= 0")
        self.default_margin = float(default_margin)

    def margin_for(self, response: Mapping[str, Any] | None) -> float:
        """Effective margin: the larger of the configured and reported margin."""
        if not response:
            return self.default_margin
        try:
            reported = float(response.get("confidence_margin", 0.0) or 0.0)
        except (TypeError, ValueError):
            reported = 0.0
        return max(self.default_margin, reported)

    def is_uncertain(self, delta_utility: float, margin: float) -> bool:
        """True when ``|dU|`` is inside the confidence band."""
        return abs(delta_utility) < margin

    def check_intervals(
        self,
        prediction: TwinPredictionV2 | Mapping[str, Any] | None,
        baseline_kpis: Mapping[str, float] | None = None,
    ) -> tuple[bool, bool]:
        """Check safety floors on mean and worst-case interval bounds.

        Worst case:
        - lower bound for coverage
        - upper bound for drop rate
        - upper bound for interference

        If twin is uncalibrated, intervals are widened conservatively.

        Returns:
            (mean_passed, worst_case_passed)
        """
        if prediction is None:
            return True, True

        intervals: list[Any] = []
        calibrated: bool = False
        if isinstance(prediction, TwinPredictionV2):
            intervals = list(prediction.intervals)
            calibrated = bool(prediction.calibrated)
        elif isinstance(prediction, Mapping):
            raw_intervals = prediction.get("intervals", [])
            if isinstance(raw_intervals, list):
                intervals = list(raw_intervals)
            calibrated = bool(prediction.get("calibrated", False))

        if not intervals:
            return True, True

        cov_mean = 1.0
        cov_worst = 1.0
        drop_mean = 0.0
        drop_worst = 0.0
        interf_mean = 0.0
        interf_worst = 0.0

        for item in intervals:
            kpi = str(
                getattr(item, "kpi", "")
                or (item.get("kpi") if isinstance(item, Mapping) else "")
            ).lower()
            mean = float(
                getattr(item, "mean", 0.0)
                or (item.get("mean", 0.0) if isinstance(item, Mapping) else 0.0)
            )
            lower = float(
                getattr(item, "lower", 0.0)
                or (item.get("lower", 0.0) if isinstance(item, Mapping) else 0.0)
            )
            upper = float(
                getattr(item, "upper", 0.0)
                or (item.get("upper", 0.0) if isinstance(item, Mapping) else 0.0)
            )

            # Be conservative if uncalibrated: widen the intervals
            if not calibrated:
                half_cov = abs(mean - lower)
                lower = lower - 0.5 * half_cov
                half_drop = abs(upper - mean)
                upper = upper + 0.5 * half_drop
                half_interf = abs(upper - mean)
                upper = upper + 0.5 * half_interf

            if "coverage" in kpi:
                cov_mean = mean
                cov_worst = lower
            elif "drop" in kpi:
                drop_mean = mean
                drop_worst = upper
            elif "interf" in kpi:
                interf_mean = mean
                interf_worst = upper

        # Floors comparison
        base_cov = (
            float(baseline_kpis.get("coverage_ratio", 0.0))
            if baseline_kpis
            else 0.0
        )
        base_drop = (
            float(baseline_kpis.get("drop_rate_ratio", 1.0))
            if baseline_kpis
            else 1.0
        )

        mean_passed = (
            cov_mean >= FLOOR_COVERAGE
            and drop_mean <= FLOOR_DROP_RATE
            and interf_mean <= 1.0
        )
        if not mean_passed and baseline_kpis is not None:
            if base_cov < FLOOR_COVERAGE or base_drop > FLOOR_DROP_RATE:
                mean_passed = (cov_mean >= base_cov) and (drop_mean <= base_drop)

        worst_passed = (
            cov_worst >= FLOOR_COVERAGE
            and drop_worst <= FLOOR_DROP_RATE
            and interf_worst <= 1.0
        )
        if not worst_passed and baseline_kpis is not None:
            if base_cov < FLOOR_COVERAGE or base_drop > FLOOR_DROP_RATE:
                worst_passed = (cov_worst >= base_cov) and (drop_worst <= base_drop)

        return mean_passed, worst_passed


class DecisionLadderEngine:
    """Conflict-aware decision ladder for one component at a time."""

    def __init__(
        self,
        calculator: UtilityCalculator | None = None,
        solver: BoundedBisectionSolver | None = None,
        cycle_detector: PingPongCycleDetector | None = None,
        floors: SafetyFloorEnforcer | None = None,
        uncertainty: UncertaintyGuard | None = None,
        max_twin_evals: int = MAX_TWIN_EVALS,
        twin_timeout_ms: float = TWIN_TIMEOUT_MS,
        max_hold_ticks: int = MAX_HOLD_TICKS,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if max_twin_evals < 1:
            raise ValueError("max_twin_evals must be >= 1")
        if max_hold_ticks < 1:
            raise ValueError("max_hold_ticks must be >= 1")
        self._calculator = calculator or UtilityCalculator()
        self._solver = solver or BoundedBisectionSolver()
        self._cycle_detector = cycle_detector or PingPongCycleDetector()
        self._floors = floors or SafetyFloorEnforcer(self._calculator)
        self._uncertainty = uncertainty or UncertaintyGuard()
        self._max_twin_evals = int(max_twin_evals)
        self._twin_timeout_ms = float(twin_timeout_ms)
        self._max_hold_ticks = int(max_hold_ticks)
        self._id_factory: Callable[[], str] = id_factory or _default_id
        self._clock: Callable[[], int] = clock or _now_ms
        self._active_ho_seen = False
        self._weights_lock = threading.RLock()
        self._dial = RiskDial(
            weight_coverage=DEFAULT_WEIGHT_COVERAGE,
            weight_energy=DEFAULT_WEIGHT_ENERGY,
            weight_interference=DEFAULT_WEIGHT_INTERFERENCE,
            weight_drop_rate=DEFAULT_WEIGHT_DROP_RATE,
            label="default",
        )

    # -- WeightSettable protocol -------------------------------------------
    def set_weights(self, dial: RiskDial) -> None:
        """Apply dynamic weights from a user-configured RiskDial (thread-safe)."""
        with self._weights_lock:
            self._dial = dial
            self._calculator = UtilityCalculator(
                UtilityWeights(
                    w1=dial.weight_coverage,
                    w2=dial.weight_energy,
                    w3=dial.weight_interference,
                    w4=dial.weight_drop_rate,
                )
            )
            self._floors = SafetyFloorEnforcer(self._calculator)

    def get_weights(self) -> RiskDial:
        """Retrieve the currently active RiskDial weights (thread-safe)."""
        with self._weights_lock:
            return self._dial.model_copy()

    # -- configuration -----------------------------------------------------
    @property
    def cycle_detector(self) -> PingPongCycleDetector:
        """Sliding-window ping-pong detector shared across ticks."""
        return self._cycle_detector

    @property
    def max_twin_evals(self) -> int:
        """Component twin-evaluation budget."""
        return self._max_twin_evals

    def record_handover(self, command: CommandProposal, tick: int) -> None:
        """Record an applied handover so later reversals are detected."""
        self._cycle_detector.record(command, tick)

    # -- public entry point ------------------------------------------------
    def evaluate(
        self,
        component: Sequence[CommandProposal],
        graph: nx.Graph[Any, Any] | None,
        twin: TwinClient,
        weights: Any | None = None,
        tick: int = 0,
        now_ms: int = 0,
    ) -> list[ArbitrationDecision]:
        """Decide one conflict-graph component; never raises."""
        try:
            active_weights = weights
            if active_weights is None:
                with self._weights_lock:
                    active_weights = UtilityWeights(
                        w1=self._dial.weight_coverage,
                        w2=self._dial.weight_energy,
                        w3=self._dial.weight_interference,
                        w4=self._dial.weight_drop_rate,
                    )
            return self._evaluate(component, graph, twin, active_weights, tick, now_ms)
        except Exception:
            return [
                self._decision(
                    command,
                    tick,
                    now_ms,
                    ActionType.REJECT,
                    None,
                    0.0,
                    ReasonCode.TWIN_TIMEOUT,
                    command.hold_count,
                    0,
                )
                for command in sorted(component, key=batch_order)
            ]

    # -- ladder ------------------------------------------------------------
    def _evaluate(
        self,
        component: Sequence[CommandProposal],
        graph: nx.Graph[Any, Any] | None,
        twin: TwinClient,
        weights: UtilityWeights,
        tick: int,
        now_ms: int,
    ) -> list[ArbitrationDecision]:
        ordered = sorted(component, key=batch_order)
        if not ordered:
            return []
        decisions: dict[str, ArbitrationDecision] = {}
        active: list[CommandProposal] = []

        # 0. Ping-pong and the defensive emergency path (normally filtered).
        for command in ordered:
            if command.is_emergency:
                decisions[command.command_id] = self._decision(
                    command,
                    tick,
                    now_ms,
                    ActionType.APPROVE,
                    command.requested_value,
                    0.0,
                    ReasonCode.EMERGENCY_OVERRIDE,
                    command.hold_count,
                    0,
                )
            elif self._cycle_detector.is_cyclic(command, tick):
                decisions[command.command_id] = self._decision(
                    command,
                    tick,
                    now_ms,
                    ActionType.REJECT,
                    None,
                    0.0,
                    ReasonCode.CYCLIC_PINGPONG,
                    command.hold_count,
                    0,
                )
            else:
                active.append(command)
        if not active:
            return [decisions[command.command_id] for command in ordered]

        self._active_ho_seen = False
        budget = EvaluationBudget(self._max_twin_evals)

        # Baseline: the twin's "do nothing" prediction, shared by the component.
        baseline_kpis, _ = self._ask(twin, [], budget, now_ms)
        if baseline_kpis is None:
            return self._reject_undecided(
                ordered, decisions, tick, now_ms, ReasonCode.TWIN_TIMEOUT
            )

        # 1. Joint APPROVE probe (counts against the shared budget).
        joint_kpis, joint_response = self._ask(
            twin,
            [build_candidate_delta(command, command.requested_value) for command in active],
            budget,
            now_ms,
        )
        if joint_kpis is None:
            return self._reject_undecided(
                ordered, decisions, tick, now_ms, ReasonCode.TWIN_TIMEOUT
            )

        joint_du = self._calculator.delta_utility(joint_kpis, baseline_kpis, weights)
        margin = self._uncertainty.margin_for(joint_response)
        joint_floors = self._floors.floors_ok(
            joint_kpis,
            self._twin_flag(joint_response),
            baseline_kpis=baseline_kpis,
        )
        if self._response_has_active_ho(joint_response):
            self._active_ho_seen = True
        evaluations = budget.used

        joint_pred_v2 = (
            joint_response.get("prediction_v2") if joint_response else None
        )
        interval_uncertain = False
        if joint_pred_v2 is not None:
            m_ok, w_ok = self._uncertainty.check_intervals(
                joint_pred_v2, baseline_kpis=baseline_kpis
            )
            if not m_ok:
                joint_floors = False
            elif not w_ok:
                interval_uncertain = True

        if (
            joint_floors
            and joint_du > 0.0
            and not interval_uncertain
            and not self._uncertainty.is_uncertain(joint_du, margin)
        ):
            for command in active:
                decisions[command.command_id] = self._decision(
                    command,
                    tick,
                    now_ms,
                    ActionType.APPROVE,
                    command.requested_value,
                    joint_du,
                    ReasonCode.NONE,
                    command.hold_count,
                    evaluations,
                )
            return [decisions[command.command_id] for command in ordered]

        # 2. Uncertainty guard on the joint prediction.
        if joint_floors and (
            self._uncertainty.is_uncertain(joint_du, margin)
            or interval_uncertain
        ):
            for command in active:
                decisions[command.command_id] = self._hold_or_reject(
                    command,
                    component,
                    graph,
                    decisions,
                    tick,
                    now_ms,
                    ReasonCode.UNCERTAINTY_GUARD,
                    joint_du,
                    0,
                )
            return [decisions[command.command_id] for command in ordered]

        # 3-5. Resolve command by command, best-first where affordable.
        applied: dict[str, float] = {}
        pending = self._priority_order(
            active, twin, weights, baseline_kpis, budget, now_ms
        )
        remaining_searches = sum(
            1 for command in pending if not _is_binary_command(command)
        )

        for command in pending:
            if command.command_id in decisions:
                continue
            if budget.remaining <= 0:
                decisions[command.command_id] = self._hold_or_reject(
                    command,
                    component,
                    graph,
                    decisions,
                    tick,
                    now_ms,
                    self._failure_reason(joint_floors),
                    joint_du,
                    0,
                )
                continue
            if _is_binary_command(command):
                decisions[command.command_id] = self._hold_or_reject(
                    command,
                    component,
                    graph,
                    decisions,
                    tick,
                    now_ms,
                    self._failure_reason(joint_floors),
                    joint_du,
                    0,
                )
                continue

            peer_deltas = [
                build_candidate_delta(peer, applied[peer.command_id])
                for peer in active
                if peer.command_id in applied and peer.command_id != command.command_id
            ]
            share = max(1, budget.remaining // max(1, remaining_searches))
            start_used = budget.used
            probes: dict[float, dict[str, Any] | None] = {}
            endpoint_feasible = True

            def evaluate_point(
                x: float,
                command: CommandProposal = command,
                peer_deltas: list[dict[str, Any]] = peer_deltas,
            ) -> tuple[float, bool]:
                nonlocal endpoint_feasible
                kpis, response = self._ask(
                    twin,
                    [*peer_deltas, build_candidate_delta(command, x)],
                    budget,
                    now_ms,
                )
                if kpis is None:
                    raise TimeoutError("twin unavailable")
                probes[x] = response
                if self._response_has_active_ho(response):
                    self._active_ho_seen = True
                feasible = self._floors.floors_ok(
                    kpis,
                    self._twin_flag(response),
                    baseline_kpis=baseline_kpis,
                )
                pt_pred_v2 = (
                    response.get("prediction_v2") if response else None
                )
                if pt_pred_v2 is not None:
                    m_ok, w_ok = self._uncertainty.check_intervals(
                        pt_pred_v2, baseline_kpis=baseline_kpis
                    )
                    if not m_ok or not w_ok:
                        feasible = False
                if abs(float(x) - command.requested_value) < 1e-12:
                    endpoint_feasible = feasible
                return self._calculator.delta_utility(kpis, baseline_kpis, weights), feasible

            c_val = command.current_value
            norm = str(
                getattr(command.parameter, "value", command.parameter)
            ).upper()
            if (
                "TX_POWER" in norm
                and c_val == 0.0
                and command.requested_value > 40.0
            ):
                c_val = 43.0
            result = self._solver.solve(
                c_val,
                command.requested_value,
                evaluate_point,
                budget=share,
            )
            evaluations_used = budget.used - start_used
            remaining_searches = max(0, remaining_searches - 1)

            if result.timed_out:
                return self._reject_undecided(
                    ordered, decisions, tick, now_ms, ReasonCode.TWIN_TIMEOUT
                )

            if result.best_x is not None and result.best_delta_utility > 0.0:
                best_resp = probes.get(result.best_x)
                best_pred_v2 = (
                    best_resp.get("prediction_v2") if best_resp else None
                )
                best_uncertain = False
                if best_pred_v2 is not None:
                    m_ok, w_ok = self._uncertainty.check_intervals(
                        best_pred_v2, baseline_kpis=baseline_kpis
                    )
                    if m_ok and not w_ok:
                        best_uncertain = True

                if (
                    self._uncertainty.is_uncertain(result.best_delta_utility, margin)
                    or best_uncertain
                ):
                    decisions[command.command_id] = self._hold_or_reject(
                        command,
                        component,
                        graph,
                        decisions,
                        tick,
                        now_ms,
                        ReasonCode.UNCERTAINTY_GUARD,
                        result.best_delta_utility,
                        evaluations_used,
                    )
                    continue
                if interval_uncertain:
                    reason = ReasonCode.UNCERTAINTY_GUARD
                elif self._response_has_active_ho(best_resp):
                    reason = ReasonCode.ACTIVE_HO
                else:
                    reason = ReasonCode.HEADROOM
                decisions[command.command_id] = self._decision(
                    command,
                    tick,
                    now_ms,
                    ActionType.MODIFY,
                    result.best_x,
                    result.best_delta_utility,
                    reason,
                    command.hold_count,
                    evaluations_used,
                )
                applied[command.command_id] = float(result.best_x)
                continue

            reason = (
                ReasonCode.UNCERTAINTY_GUARD
                if interval_uncertain
                else (
                    ReasonCode.FLOOR_BREACH
                    if (not joint_floors or not endpoint_feasible)
                    else ReasonCode.NONE
                )
            )
            decisions[command.command_id] = self._hold_or_reject(
                command,
                component,
                graph,
                decisions,
                tick,
                now_ms,
                reason,
                joint_du,
                evaluations_used,
            )

        return [decisions[command.command_id] for command in ordered]

    # -- helpers -----------------------------------------------------------
    def _priority_order(
        self,
        commands: Sequence[CommandProposal],
        twin: TwinClient,
        weights: UtilityWeights,
        baseline_kpis: Mapping[str, float],
        budget: EvaluationBudget,
        now_ms: int,
    ) -> list[CommandProposal]:
        ordered = sorted(commands, key=batch_order)
        fallback = [
            command for command in ordered
            if not _is_binary_command(command)
        ] + [
            command for command in ordered
            if _is_binary_command(command)
        ]
        searches = sum(
            1 for command in ordered if not _is_binary_command(command)
        )
        if len(ordered) < 2 or searches == 0:
            return ordered
        if budget.remaining < len(ordered) + 4 * searches:
            return fallback
        scores: dict[str, float] = {}
        for command in ordered:
            kpis, _ = self._ask(
                twin,
                [build_candidate_delta(command, command.requested_value)],
                budget,
                now_ms,
            )
            if kpis is None:
                return fallback
            scores[command.command_id] = self._calculator.delta_utility(
                kpis, baseline_kpis, weights
            )
        return sorted(
            ordered, key=lambda command: (-scores[command.command_id], batch_order(command))
        )

    def _hold_or_reject(
        self,
        command: CommandProposal,
        component: Sequence[CommandProposal],
        graph: nx.Graph[Any, Any] | None,
        decisions: Mapping[str, ArbitrationDecision],
        tick: int,
        now_ms: int,
        reason: ReasonCode,
        delta: float,
        evaluations: int,
    ) -> ArbitrationDecision:
        if reason is ReasonCode.UNCERTAINTY_GUARD:
            if self._can_delay(command):
                return self._delay(command, tick, now_ms, reason, evaluations)
            return self._reject(command, tick, now_ms, reason, delta, evaluations)
        if self._can_delay(command) and self._is_transient(
            command, component, graph, decisions
        ):
            return self._delay(command, tick, now_ms, ReasonCode.NONE, evaluations)
        return self._reject(command, tick, now_ms, reason, delta, evaluations)

    def _delay(
        self,
        command: CommandProposal,
        tick: int,
        now_ms: int,
        reason: ReasonCode,
        evaluations: int,
    ) -> ArbitrationDecision:
        return self._decision(
            command,
            tick,
            now_ms,
            ActionType.DELAY,
            None,
            0.0,
            reason,
            command.hold_count + 1,
            evaluations,
        )

    def _reject(
        self,
        command: CommandProposal,
        tick: int,
        now_ms: int,
        reason: ReasonCode,
        delta: float,
        evaluations: int,
    ) -> ArbitrationDecision:
        return self._decision(
            command,
            tick,
            now_ms,
            ActionType.REJECT,
            None,
            delta,
            reason,
            command.hold_count,
            evaluations,
        )

    def _can_delay(self, command: CommandProposal) -> bool:
        """Hold budget available and the must-decide latch not set (SRS R3)."""
        return (
            not command.must_decide
            and command.hold_count < self._max_hold_ticks
        )

    def _is_transient(
        self,
        command: CommandProposal,
        component: Sequence[CommandProposal],
        graph: nx.Graph[Any, Any] | None,
        decisions: Mapping[str, ArbitrationDecision],
    ) -> bool:
        """A clash is transient while the blocking commands are still moving."""
        if command.hold_count > 0:
            return True
        if self._active_ho_seen:
            return True
        peers: Sequence[CommandProposal] = component
        if graph is not None and command.command_id in graph:
            neighbour_ids = set(graph.neighbors(command.command_id))
            peers = [p for p in component if p.command_id in neighbour_ids]
        for peer in peers:
            if peer.command_id == command.command_id:
                continue
            decided = decisions.get(peer.command_id)
            if decided is not None and decided.action in (
                ActionType.MODIFY,
                ActionType.DELAY,
            ):
                return True
        return False

    def _failure_reason(self, joint_floors: bool) -> ReasonCode:
        return ReasonCode.FLOOR_BREACH if not joint_floors else ReasonCode.NONE

    def _reject_undecided(
        self,
        ordered: Sequence[CommandProposal],
        decisions: dict[str, ArbitrationDecision],
        tick: int,
        now_ms: int,
        reason: ReasonCode,
    ) -> list[ArbitrationDecision]:
        for command in ordered:
            if command.command_id in decisions:
                continue
            decisions[command.command_id] = self._decision(
                command,
                tick,
                now_ms,
                ActionType.REJECT,
                None,
                0.0,
                reason,
                command.hold_count,
                0,
            )
        return [decisions[command.command_id] for command in ordered]

    def _ask(
        self,
        twin: TwinClient,
        deltas: list[dict[str, Any]],
        budget: EvaluationBudget,
        now_ms: int,
    ) -> tuple[Mapping[str, float] | None, dict[str, Any] | None]:
        """One twin evaluation with timeout and fault containment."""
        budget.spend()
        started = time.perf_counter()
        try:
            response = twin.evaluate([dict(delta) for delta in deltas])
        except TimeoutError:
            return None, None
        except Exception:
            return None, None
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > self._twin_timeout_ms:
            return None, None
        reported = response.get("computation_time_ms")
        if reported is not None:
            try:
                if float(reported) > self._twin_timeout_ms:
                    return None, None
            except (TypeError, ValueError):
                pass
        kpis = self._kpis(response)
        if not kpis:
            return None, None
        resp_dict = dict(response)
        pred_v2 = self._extract_prediction_v2(resp_dict, twin, deltas)
        if pred_v2 is not None:
            resp_dict["prediction_v2"] = pred_v2
        return kpis, resp_dict

    @staticmethod
    def _extract_prediction_v2(
        response: Mapping[str, Any],
        twin: Any,
        deltas: Sequence[Mapping[str, Any]],
    ) -> TwinPredictionV2 | None:
        raw = response.get("prediction_v2") or response.get("twin_prediction_v2")
        if isinstance(raw, TwinPredictionV2):
            return raw
        if isinstance(raw, Mapping):
            try:
                return TwinPredictionV2.model_validate(raw)
            except Exception:
                pass
        if "intervals" in response and isinstance(response.get("intervals"), list):
            try:
                cand_id = str(
                    response.get("candidate_id")
                    or response.get("evaluation_id")
                    or "cand-auto"
                )
                return TwinPredictionV2.model_validate({
                    "candidate_id": cand_id,
                    "intervals": response["intervals"],
                    "calibrated": bool(response.get("calibrated", False)),
                    "n_calibration": int(response.get("n_calibration", 0)),
                })
            except Exception:
                pass
        if hasattr(twin, "predict_v2") and callable(getattr(twin, "predict_v2")):
            try:
                res = twin.predict_v2(deltas)
                if isinstance(res, TwinPredictionV2):
                    return res
                if isinstance(res, Mapping):
                    return TwinPredictionV2.model_validate(res)
            except Exception:
                pass
        if hasattr(twin, "prediction_v2"):
            attr = getattr(twin, "prediction_v2")
            if isinstance(attr, TwinPredictionV2):
                return attr
            if isinstance(attr, Mapping):
                try:
                    return TwinPredictionV2.model_validate(attr)
                except Exception:
                    pass
        return None

    @staticmethod
    def _kpis(response: Mapping[str, Any]) -> dict[str, float]:
        raw = response.get("predicted_kpis")
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, float] = {}
        for key, value in raw.items():
            try:
                result[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _twin_flag(response: Mapping[str, Any] | None) -> bool | None:
        if not response:
            return None
        value = response.get("safety_floors_satisfied")
        return None if value is None else bool(value)

    @staticmethod
    def _response_has_active_ho(response: Mapping[str, Any] | None) -> bool:
        if not response:
            return False
        factor = str(response.get("limiting_factor", "")).strip().lower()
        if factor == "active_ho":
            return True
        users = response.get("active_handover_users")
        if users is None:
            return False
        try:
            return float(users) > 0.0
        except (TypeError, ValueError):
            return False

    def _decision(
        self,
        command: CommandProposal,
        tick: int,
        now_ms: int,
        action: ActionType,
        approved_value: float | None,
        delta_utility: float,
        reason: ReasonCode,
        hold_count: int,
        twin_eval_count: int,
    ) -> ArbitrationDecision:
        return ArbitrationDecision(
            decision_id=self._id_factory(),
            command_id=command.command_id,
            batch_tick=int(tick),
            action=action,
            approved_value=approved_value,
            delta_utility=float(delta_utility),
            reason_code=reason,
            hold_count=int(hold_count),
            twin_eval_count=int(twin_eval_count),
            decided_at_epoch_ms=int(now_ms) if now_ms is not None else self._clock(),
        )


def _default_id() -> str:
    return uuid.uuid4().hex


def _now_ms() -> int:
    return int(time.time() * 1000)
