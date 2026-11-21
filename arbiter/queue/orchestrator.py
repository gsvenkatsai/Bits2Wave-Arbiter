"""Per-tick orchestration: queue -> detector -> engine.

This is a plain Python entry point for Dev 4's HTTP route (which is not created
here). The detector, engine, twin and cell adjacency are injected so the real
implementations can be swapped without touching the queue.
"""
from __future__ import annotations

import math
import time
import uuid
from collections import deque
from typing import Any, Callable, Deque, Dict, Protocol, Sequence

import networkx as nx

from arbiter.detector.classifier import AdjacencyLike
from arbiter.detector.graph import ConflictGraphBuilder
from arbiter.detector.partitioner import GraphPartitioner
from arbiter.engine.contracts import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    UtilityWeights,
)
from arbiter.engine.ladder import DecisionLadderEngine
from arbiter.engine.mocks.mock_twin_client import MockDigitalTwinClient, TwinClient
from arbiter.engine.plan_executor import PlanExecutor
from arbiter.engine.planner import ArbitrationPlanner
from arbiter.queue.proposal_queue import ProposalQueue
from arbiter.shared.v2_contracts import RiskDial

__all__ = [
    "DetectorProtocol",
    "EngineProtocol",
    "QueueOrchestrator",
    "build_default_orchestrator",
]

_APPLIED_ACTIONS = frozenset({ActionType.APPROVE, ActionType.MODIFY})


class DetectorProtocol(Protocol):
    """Conflict detector boundary."""

    def build(
        self, batch: Sequence[CommandProposal], adjacency: AdjacencyLike
    ) -> nx.Graph[Any, Any]:
        """Build the conflict graph for one batch."""
        ...


class EngineProtocol(Protocol):
    """Arbitration engine boundary."""

    def evaluate(
        self,
        component: Sequence[CommandProposal],
        graph: nx.Graph[Any, Any] | None,
        twin: TwinClient,
        weights: UtilityWeights,
        tick: int,
        now_ms: int,
    ) -> list[ArbitrationDecision]:
        """Decide one conflict-graph component."""
        ...

    def record_handover(self, command: CommandProposal, tick: int) -> None:
        """Record an applied handover for ping-pong detection."""
        ...


def _now_ms() -> int:
    return int(time.time() * 1000)


class QueueOrchestrator:
    """Drives one arbitration tick end to end."""

    def __init__(
        self,
        queue: ProposalQueue | None = None,
        detector: DetectorProtocol | None = None,
        engine: EngineProtocol | None = None,
        twin: TwinClient | None = None,
        adjacency: AdjacencyLike | None = None,
        weights: UtilityWeights | None = None,
        max_workers: int = 4,
        clock: Callable[[], int] | None = None,
        latency_window: int = 512,
        actuator: Any | None = None,
        enable_plan_synthesis: bool = False,
        planner: ArbitrationPlanner | None = None,
        plan_executor: PlanExecutor | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._queue = queue or ProposalQueue()
        self._detector: DetectorProtocol = detector or ConflictGraphBuilder()
        self._engine: EngineProtocol = engine or DecisionLadderEngine()
        self._twin: TwinClient = twin or MockDigitalTwinClient()
        self._actuator: Any | None = actuator
        self._adjacency: AdjacencyLike = adjacency if adjacency is not None else {}
        self._weights = weights or UtilityWeights()
        self._partitioner = GraphPartitioner()
        self._max_workers = int(max_workers)
        self._clock: Callable[[], int] = clock or _now_ms
        self._latencies: Deque[float] = deque(maxlen=max(latency_window, 1))
        self._enable_plan_synthesis = bool(enable_plan_synthesis)
        self._planner = planner or ArbitrationPlanner()
        self._plan_executor = plan_executor or PlanExecutor(
            actuator=self._actuator, twin=self._twin
        )

    # -- WeightSettable protocol -------------------------------------------
    def set_weights(self, dial: RiskDial) -> None:
        """Apply dynamic weights from a user-configured RiskDial."""
        self._weights = UtilityWeights(
            w1=dial.weight_coverage,
            w2=dial.weight_energy,
            w3=dial.weight_interference,
            w4=dial.weight_drop_rate,
        )
        if hasattr(self._engine, "set_weights"):
            self._engine.set_weights(dial)

    def get_weights(self) -> RiskDial:
        """Retrieve the currently active RiskDial weights."""
        if hasattr(self._engine, "get_weights"):
            gw = getattr(self._engine, "get_weights")
            if callable(gw):
                res: Any = gw()
                if isinstance(res, RiskDial):
                    return res
        return RiskDial(
            weight_coverage=self._weights.w1,
            weight_energy=self._weights.w2,
            weight_interference=self._weights.w3,
            weight_drop_rate=self._weights.w4,
            label="active",
        )

    # -- accessors ---------------------------------------------------------
    @property
    def queue(self) -> ProposalQueue:
        """Queue backing this orchestrator."""
        return self._queue

    @property
    def engine(self) -> EngineProtocol:
        """Injected arbitration engine."""
        return self._engine

    @property
    def detector(self) -> DetectorProtocol:
        """Injected conflict detector."""
        return self._detector

    @property
    def actuator(self) -> Any | None:
        """Injected actuator enforcer or adapter."""
        return self._actuator

    @property
    def planner(self) -> ArbitrationPlanner:
        """Injected plan synthesizer."""
        return self._planner

    @property
    def plan_executor(self) -> PlanExecutor:
        """Injected plan execution engine."""
        return self._plan_executor

    @property
    def enable_plan_synthesis(self) -> bool:
        """Whether plan synthesis is active."""
        return self._enable_plan_synthesis

    @enable_plan_synthesis.setter
    def enable_plan_synthesis(self, value: bool) -> None:
        self._enable_plan_synthesis = bool(value)

    def submit(self, cmd: CommandProposal) -> bool:
        """Accept a proposal (emergency ones bypass the queue)."""
        return self._queue.submit(cmd)

    # -- tick --------------------------------------------------------------
    def on_tick(self, current_tick: int, timestamp_epoch_ms: int) -> Dict[str, Any]:
        """Run one arbitration tick and return the batch summary."""
        batch = self._queue.flush_batch(current_tick)
        decisions: list[ArbitrationDecision] = []
        if batch:
            graph = self._detector.build(batch, self._adjacency)
            components = self._partitioner.components(graph)

            def solve(component: Sequence[CommandProposal]) -> list[ArbitrationDecision]:
                started = time.perf_counter()
                component_decisions = self._engine.evaluate(
                    component,
                    graph,
                    self._twin,
                    self._weights,
                    current_tick,
                    timestamp_epoch_ms,
                )
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                self._record_latency(elapsed_ms, len(component_decisions))
                return component_decisions

            results = self._partitioner.solve_parallel(
                components, solve, self._max_workers, tick=current_tick
            )
            for component_decisions in results:
                decisions.extend(component_decisions)

            by_id = {command.command_id: command for command in batch}
            for decision in decisions:
                command = by_id.get(decision.command_id)
                if command is None:
                    continue
                if decision.action is ActionType.DELAY:
                    # Re-queue the held command for its next re-evaluation.
                    self._queue.hold(command, current_tick)
                elif decision.action in _APPLIED_ACTIONS:
                    self._engine.record_handover(command, current_tick)
                    if self._actuator is not None:
                        if hasattr(self._actuator, "apply"):
                            self._actuator.apply(
                                command, decision.approved_value, tick=current_tick
                            )
                        elif hasattr(self._actuator, "commit"):
                            self._actuator.commit({
                                "commit_id": str(uuid.uuid4()),
                                "decision_id": decision.decision_id,
                                "command_id": command.command_id,
                                "target_cell_id": command.target_cell,
                                "parameter": command.parameter,
                                "old_value": float(command.current_value),
                                "new_value": float(
                                    decision.approved_value
                                    if decision.approved_value is not None
                                    else command.requested_value
                                ),
                                "timestamp_epoch_ms": timestamp_epoch_ms,
                            })

            if self._enable_plan_synthesis and decisions:
                if self._planner.should_synthesize(decisions):
                    synthesized_plan = self._planner.build_plan(
                        batch, decisions, current_tick, twin=self._twin
                    )
                    if synthesized_plan is not None:
                        self._plan_executor.submit_plan(synthesized_plan)
                self._plan_executor.advance(current_tick)

        return {
            "batch_tick": current_tick,
            "decisions_count": len(decisions),
            "decisions": decisions,
        }

    # -- latency -----------------------------------------------------------
    def _record_latency(self, elapsed_ms: float, decisions: int) -> None:
        if decisions <= 0:
            return
        per_decision = elapsed_ms / decisions
        for _ in range(decisions):
            self._latencies.append(per_decision)

    def latency_p95_ms(self) -> float:
        """95th percentile per-decision decision latency in milliseconds."""
        samples = sorted(self._latencies)
        if not samples:
            return 0.0
        index = math.ceil(0.95 * len(samples)) - 1
        bounded = min(max(index, 0), len(samples) - 1)
        return float(samples[bounded])

    @property
    def latency_samples(self) -> tuple[float, ...]:
        """Recorded per-decision latencies in milliseconds."""
        return tuple(self._latencies)

    def reset_latency(self) -> None:
        """Clear the latency window."""
        self._latencies.clear()


def build_default_orchestrator(
    adjacency: AdjacencyLike | None = None,
    twin: TwinClient | None = None,
    actuator: Any | None = None,
    **kwargs: Any,
) -> QueueOrchestrator:
    """Convenience factory with the production detector and ladder wired in."""
    return QueueOrchestrator(adjacency=adjacency, twin=twin, actuator=actuator, **kwargs)
