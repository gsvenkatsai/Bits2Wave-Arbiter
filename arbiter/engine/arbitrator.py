"""Decision-ladder arbitration engine.

Ladder (in order): APPROVE -> MODIFY -> DELAY -> REJECT, per conflict-graph
connected component.  Components are independent and solved in parallel.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING, Mapping, Sequence

from arbiter.detector.conflict_detector import ConflictDetector, ConflictGraph
from arbiter.engine.compromise import CompromiseSearch, TwinBudget
from arbiter.engine.constants import (
    BINARY_PARAMETERS,
    BISECTION_MAX_EVALS,
    MAX_HOLD_TICKS,
    TWIN_TIMEOUT_MS,
)
from arbiter.engine.contracts import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ReasonCode,
    UtilityWeights,
)
from arbiter.engine.deltas import CandidateDelta, build_delta, parameter_key
from arbiter.engine.mocks.mock_twin_client import TwinClient
from arbiter.engine.utility import floors_satisfied, utility_delta

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from arbiter.queue.proposal_queue import QueueBatch

__all__ = ["ArbitrationConfig", "Arbitrator", "BatchOutcome"]

#: Actions that commit a radio change and are therefore logged for ping-pong.
_APPLIED_ACTIONS = frozenset({ActionType.APPROVE, ActionType.MODIFY})


def _default_decision_id() -> str:
    return uuid.uuid4().hex


def _now_ms() -> int:
    return int(time.time() * 1000)


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce a possibly-missing numeric twin field to float."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_kpis(value: object) -> Mapping[str, float]:
    """Coerce a twin KPI mapping, tolerating ``None``/malformed payloads."""
    if isinstance(value, Mapping):
        return {str(key): _as_float(item) for key, item in value.items()}
    return {}


@dataclass(frozen=True)
class ArbitrationConfig:
    """Tunables for the decision ladder; defaults match the frozen spec."""

    weights: UtilityWeights | None = None
    max_twin_evaluations: int = BISECTION_MAX_EVALS
    twin_timeout_ms: float = TWIN_TIMEOUT_MS
    max_hold_ticks: int = MAX_HOLD_TICKS
    confidence_margin: float = 0.0
    max_workers: int = 4
    timeout_budget_ms: float = 15.0

    def __post_init__(self) -> None:
        if self.max_twin_evaluations < 1:
            raise ValueError("max_twin_evaluations must be >= 1")
        if self.max_hold_ticks < 1:
            raise ValueError("max_hold_ticks must be >= 1")
        if self.max_workers < 1:
            raise ValueError("max_workers must be >= 1")


@dataclass(frozen=True)
class BatchOutcome:
    """Everything the referee produced for one tick."""

    decisions: tuple[ArbitrationDecision, ...] = ()
    held: tuple[CommandProposal, ...] = ()
    emergency_decisions: tuple[ArbitrationDecision, ...] = ()
    latency_ms: float = 0.0
    twin_evaluations: int = 0

    def decision_for(self, command_id: str) -> ArbitrationDecision | None:
        """Look up the verdict for one command."""
        for decision in self.decisions:
            if decision.command_id == command_id:
                return decision
        return None

    @property
    def action_counts(self) -> dict[str, int]:
        """Histogram of actions, for dashboards and quick assertions."""
        counts: dict[str, int] = {}
        for decision in self.decisions:
            key = decision.action.value
            counts[key] = counts.get(key, 0) + 1
        return counts


@dataclass(frozen=True)
class _ComponentResult:
    proposals: tuple[CommandProposal, ...]
    decisions: tuple[ArbitrationDecision, ...]
    held: tuple[CommandProposal, ...]
    evaluations: int


class Arbitrator:
    """Conflict-aware pre-actuation referee."""

    def __init__(
        self,
        twin: TwinClient,
        config: ArbitrationConfig | None = None,
        *,
        detector: ConflictDetector | None = None,
        cell_neighbors: Mapping[str, Sequence[str]] | None = None,
        current_values: Mapping[tuple[str, str], float] | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._twin = twin
        self._config = config or ArbitrationConfig()
        self._detector = detector or ConflictDetector(cell_neighbors)
        self._current_values: dict[tuple[str, str], float] = dict(current_values or {})
        self._id_factory: Callable[[], str] = id_factory or _default_decision_id
        self._clock: Callable[[], int] = clock or _now_ms

    # -- configuration -----------------------------------------------------
    @property
    def config(self) -> ArbitrationConfig:
        """Active engine configuration."""
        return self._config

    @property
    def detector(self) -> ConflictDetector:
        """Conflict detector shared across ticks (holds ping-pong history)."""
        return self._detector

    def set_current_values(self, values: Mapping[tuple[str, str], float]) -> None:
        """Replace the known current parameter values used as search anchors."""
        self._current_values = dict(values)

    def set_current_value(self, cell_id: str, parameter: object, value: float) -> None:
        """Update one current parameter value."""
        self._current_values[(cell_id, parameter_key(parameter))] = float(value)

    def current_value(self, proposal: CommandProposal) -> float | None:
        """Current value of the parameter a proposal targets, if known."""
        return self._current_values.get(
            (proposal.target_cell_id, parameter_key(proposal.parameter))
        )

    # -- public API --------------------------------------------------------
    def decide(
        self,
        batch: "QueueBatch",
        baseline_kpis: Mapping[str, float],
        current_values: Mapping[tuple[str, str], float] | None = None,
    ) -> BatchOutcome:
        """Arbitrate a drained :class:`QueueBatch`."""
        return self.decide_batch(batch.proposals, batch.tick, baseline_kpis, current_values)

    def decide_batch(
        self,
        proposals: Sequence[CommandProposal],
        tick: int,
        baseline_kpis: Mapping[str, float],
        current_values: Mapping[tuple[str, str], float] | None = None,
    ) -> BatchOutcome:
        """Arbitrate one tick's proposals and return every verdict."""
        started = time.perf_counter()
        if current_values is not None:
            self.set_current_values(current_values)

        ordered = list(proposals)
        decisions: dict[str, ArbitrationDecision] = {}
        held: list[CommandProposal] = []
        emergency_decisions: list[ArbitrationDecision] = []

        # Emergency lane: bypass arbitration, audit post-hoc.
        for proposal in ordered:
            if not proposal.emergency_flag:
                continue
            decision = self._make_decision(
                proposal,
                tick,
                ActionType.APPROVE,
                proposal.requested_value,
                0.0,
                ReasonCode.EMERGENCY_OVERRIDE,
                proposal.hold_count,
                0,
            )
            decisions[proposal.command_id] = decision
            emergency_decisions.append(decision)

        normal = [proposal for proposal in ordered if not proposal.emergency_flag]
        graph = self._detector.detect(normal, tick)
        for proposal in normal:
            if proposal.command_id in graph.pingpong_command_ids:
                decisions[proposal.command_id] = self._make_decision(
                    proposal,
                    tick,
                    ActionType.REJECT,
                    None,
                    0.0,
                    ReasonCode.CYCLIC_PINGPONG,
                    proposal.hold_count,
                    0,
                )

        arbitrable = [
            proposal
            for proposal in normal
            if proposal.command_id not in graph.pingpong_command_ids
        ]
        components = self._components(arbitrable, graph)
        results = self._solve_components(components, tick, baseline_kpis)

        for result in results:
            for decision in result.decisions:
                decisions[decision.command_id] = decision
            held.extend(result.held)
            for proposal in result.proposals:
                applied = decisions.get(proposal.command_id)
                if applied is not None and applied.action in _APPLIED_ACTIONS:
                    self._detector.record_decision(
                        tick,
                        proposal.controller_id,
                        proposal.parameter,
                        proposal.target_cell_id,
                    )

        latency_ms = (time.perf_counter() - started) * 1000.0
        return BatchOutcome(
            decisions=tuple(decisions[proposal.command_id] for proposal in ordered),
            held=tuple(held),
            emergency_decisions=tuple(emergency_decisions),
            latency_ms=latency_ms,
            twin_evaluations=sum(result.evaluations for result in results),
        )

    # -- component bookkeeping --------------------------------------------
    def _components(
        self,
        proposals: Sequence[CommandProposal],
        graph: ConflictGraph,
    ) -> list[list[CommandProposal]]:
        by_id = {proposal.command_id: proposal for proposal in proposals}
        components: list[list[CommandProposal]] = []
        covered: set[str] = set()
        for component in graph.components():
            members = [by_id[command_id] for command_id in component if command_id in by_id]
            if members:
                components.append(members)
                covered.update(proposal.command_id for proposal in members)
        for proposal in proposals:
            if proposal.command_id not in covered:
                components.append([proposal])
        return components

    def _solve_components(
        self,
        components: Sequence[Sequence[CommandProposal]],
        tick: int,
        baseline_kpis: Mapping[str, float],
    ) -> list[_ComponentResult]:
        if not components:
            return []
        if len(components) == 1 or self._config.max_workers == 1:
            return [
                self._solve_component(component, tick, baseline_kpis)
                for component in components
            ]
        workers = min(self._config.max_workers, len(components))

        def solve(component: Sequence[CommandProposal]) -> _ComponentResult:
            return self._solve_component(component, tick, baseline_kpis)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(solve, components))

    # -- ladder ------------------------------------------------------------
    def _solve_component(
        self,
        component: Sequence[CommandProposal],
        tick: int,
        baseline_kpis: Mapping[str, float],
    ) -> _ComponentResult:
        budget = TwinBudget(self._twin, self._config.max_twin_evaluations)
        joint: list[CandidateDelta] = [
            build_delta(proposal, proposal.requested_value, self.current_value(proposal))
            for proposal in component
        ]

        try:
            response, elapsed_ms = self._evaluate(budget, joint)
        except Exception:
            # Twin fault: keep last-known-good state and refuse the change.
            return self._reject_component(
                component, tick, ReasonCode.TWIN_TIMEOUT, 0.0, budget.evaluations
            )
        if elapsed_ms > self._config.twin_timeout_ms:
            return self._reject_component(
                component, tick, ReasonCode.TWIN_TIMEOUT, 0.0, budget.evaluations
            )

        kpis = _as_kpis(response.get("predicted_kpis"))
        delta = utility_delta(kpis, baseline_kpis, self._config.weights)
        floors_ok = (
            bool(response.get("safety_floors_satisfied", False))
            and floors_satisfied(kpis)
        )
        margin = max(
            self._config.confidence_margin,
            _as_float(response.get("confidence_margin")),
        )

        # 1. APPROVE: joint dU > 0, floors hold, and the gain clears the margin.
        if floors_ok and delta > 0.0 and abs(delta) >= margin:
            return self._approve_component(component, tick, delta, budget.evaluations)

        # Uncertainty guard: too close to call, so wait rather than guess.
        if floors_ok and delta > 0.0:
            return self._delay_or_reject(
                component, tick, ReasonCode.UNCERTAINTY_GUARD, delta, budget.evaluations
            )

        # 2. MODIFY: bounded compromise search (binary commands skip this rung).
        if floors_ok:
            modified = self._attempt_modify(component, tick, baseline_kpis, budget, margin)
            if modified is not None:
                return modified
            return self._delay_or_reject(
                component, tick, ReasonCode.NONE, delta, budget.evaluations
            )

        return self._delay_or_reject(
            component, tick, ReasonCode.FLOOR_BREACH, delta, budget.evaluations
        )

    def _attempt_modify(
        self,
        component: Sequence[CommandProposal],
        tick: int,
        baseline_kpis: Mapping[str, float],
        budget: TwinBudget,
        margin: float,
    ) -> _ComponentResult | None:
        peers: dict[str, CandidateDelta] = {
            proposal.command_id: build_delta(
                proposal, proposal.requested_value, self.current_value(proposal)
            )
            for proposal in component
        }
        for command in component:
            if parameter_key(command.parameter) in BINARY_PARAMETERS:
                continue
            if not budget.can_evaluate():
                break
            peer_deltas = [
                delta for command_id, delta in peers.items()
                if command_id != command.command_id
            ]
            search = CompromiseSearch(
                budget,
                weights=self._config.weights,
                confidence_margin=margin,
                timeout_budget_ms=self._config.timeout_budget_ms,
            )
            try:
                result = search.search(
                    command,
                    peer_deltas,
                    baseline_kpis,
                    self.current_value(command),
                )
            except Exception:
                return self._reject_component(
                    component, tick, ReasonCode.TWIN_TIMEOUT, 0.0, budget.evaluations
                )
            if not result.found or result.value is None:
                continue
            evaluated = budget.evaluations
            decisions = tuple(
                self._make_decision(
                    proposal,
                    tick,
                    ActionType.MODIFY
                    if proposal.command_id == command.command_id
                    else ActionType.APPROVE,
                    result.value
                    if proposal.command_id == command.command_id
                    else proposal.requested_value,
                    result.delta_utility,
                    ReasonCode.NONE,
                    proposal.hold_count,
                    evaluated,
                )
                for proposal in component
            )
            return _ComponentResult(tuple(component), decisions, (), evaluated)
        return None

    def _approve_component(
        self,
        component: Sequence[CommandProposal],
        tick: int,
        delta: float,
        evaluations: int,
    ) -> _ComponentResult:
        decisions = tuple(
            self._make_decision(
                proposal,
                tick,
                ActionType.APPROVE,
                proposal.requested_value,
                delta,
                ReasonCode.NONE,
                proposal.hold_count,
                evaluations,
            )
            for proposal in component
        )
        return _ComponentResult(tuple(component), decisions, (), evaluations)

    def _delay_or_reject(
        self,
        component: Sequence[CommandProposal],
        tick: int,
        reason: ReasonCode,
        delta: float,
        evaluations: int,
    ) -> _ComponentResult:
        can_hold = all(
            proposal.hold_count < self._config.max_hold_ticks for proposal in component
        )
        if can_hold:
            decisions = tuple(
                self._make_decision(
                    proposal,
                    tick,
                    ActionType.DELAY,
                    None,
                    0.0,
                    reason,
                    proposal.hold_count + 1,
                    evaluations,
                )
                for proposal in component
            )
            held = tuple(
                proposal.model_copy(update={"hold_count": proposal.hold_count + 1})
                for proposal in component
            )
            return _ComponentResult(tuple(component), decisions, held, evaluations)

        # Hold budget spent: the command must be decided now.
        decisions = tuple(
            self._make_decision(
                proposal,
                tick,
                ActionType.REJECT,
                None,
                delta,
                reason,
                proposal.hold_count,
                evaluations,
            )
            for proposal in component
        )
        return _ComponentResult(tuple(component), decisions, (), evaluations)

    def _reject_component(
        self,
        component: Sequence[CommandProposal],
        tick: int,
        reason: ReasonCode,
        delta: float,
        evaluations: int,
    ) -> _ComponentResult:
        decisions = tuple(
            self._make_decision(
                proposal,
                tick,
                ActionType.REJECT,
                None,
                delta,
                reason,
                proposal.hold_count,
                evaluations,
            )
            for proposal in component
        )
        return _ComponentResult(tuple(component), decisions, (), evaluations)

    # -- helpers -----------------------------------------------------------
    def _evaluate(
        self,
        budget: TwinBudget,
        deltas: list[CandidateDelta],
    ) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        response = budget.evaluate(deltas, self._config.timeout_budget_ms)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return response, elapsed_ms

    def _make_decision(
        self,
        proposal: CommandProposal,
        tick: int,
        action: ActionType,
        approved_value: float | None,
        delta_utility: float,
        reason: ReasonCode,
        hold_count: int,
        twin_eval_count: int,
    ) -> ArbitrationDecision:
        return ArbitrationDecision(
            decision_id=self._id_factory(),
            command_id=proposal.command_id,
            batch_tick=tick,
            action=action,
            approved_value=approved_value,
            delta_utility=float(delta_utility),
            reason_code=reason,
            hold_count=hold_count,
            twin_eval_count=twin_eval_count,
            decided_at_epoch_ms=self._clock(),
        )
