"""Per-tick orchestration: queue -> detector -> engine.

This is a plain Python entry point for Dev 4's HTTP route (which is not created
here). The detector and engine are injected behind protocols so the real
conflict graph (Task 3) and decision ladder (Tasks 4-5) can be plugged in
without touching the queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol, Sequence

from arbiter.engine.contracts import ActionType, ArbitrationDecision, CommandProposal
from arbiter.queue.proposal_queue import ProposalQueue

__all__ = [
    "ConflictGraphLike",
    "DetectorProtocol",
    "EngineProtocol",
    "ConflictGraphStub",
    "StubDetector",
    "StubEngine",
    "QueueOrchestrator",
]


class ConflictGraphLike(Protocol):
    """Minimal conflict-graph view the engine consumes."""

    @property
    def nodes(self) -> Sequence[str]:
        """Command ids present in this batch's conflict graph."""
        ...


class DetectorProtocol(Protocol):
    """Conflict detector boundary (Task 3 fills the real implementation)."""

    def detect(
        self, proposals: Sequence[CommandProposal], tick: int
    ) -> ConflictGraphLike:
        """Build the conflict graph for one batch."""
        ...


class EngineProtocol(Protocol):
    """Arbitration engine boundary (Tasks 4-5 fill the real implementation)."""

    def decide(
        self,
        proposals: Sequence[CommandProposal],
        graph: ConflictGraphLike,
        tick: int,
        timestamp_epoch_ms: int,
    ) -> Sequence[ArbitrationDecision]:
        """Arbitrate one batch and return one decision per proposal."""
        ...


@dataclass(frozen=True)
class ConflictGraphStub:
    """Placeholder graph until the detector lands.

    # TODO(INTEGRATION): replace with arbiter.detector.ConflictGraph (Task 3).
    """

    nodes: tuple[str, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()


class StubDetector:
    """No-conflict detector stub.

    # TODO(INTEGRATION): delete once arbiter.detector.ConflictDetector exists.
    """

    def detect(
        self, proposals: Sequence[CommandProposal], tick: int
    ) -> ConflictGraphStub:
        return ConflictGraphStub(nodes=tuple(p.command_id for p in proposals))


class StubEngine:
    """No-op engine stub.

    # TODO(INTEGRATION): replace with the arbitration ladder (Tasks 4-5).
    """

    def decide(
        self,
        proposals: Sequence[CommandProposal],
        graph: ConflictGraphLike,
        tick: int,
        timestamp_epoch_ms: int,
    ) -> Sequence[ArbitrationDecision]:
        return ()


class QueueOrchestrator:
    """Drives one arbitration tick from the queue through detector and engine."""

    def __init__(
        self,
        queue: ProposalQueue | None = None,
        detector: DetectorProtocol | None = None,
        engine: EngineProtocol | None = None,
    ) -> None:
        self._queue = queue or ProposalQueue()
        self._detector: DetectorProtocol = detector or StubDetector()
        self._engine: EngineProtocol = engine or StubEngine()

    @property
    def queue(self) -> ProposalQueue:
        """The queue backing this orchestrator."""
        return self._queue

    def submit(self, cmd: CommandProposal) -> bool:
        """Accept a proposal into the queue (emergency ones bypass it)."""
        return self._queue.submit(cmd)

    def on_tick(
        self, current_tick: int, timestamp_epoch_ms: int
    ) -> Dict[str, Any]:
        """Run one arbitration tick and return the batch summary."""
        batch = self._queue.flush_batch(current_tick)
        decisions: list[ArbitrationDecision] = []
        if batch:
            graph = self._detector.detect(batch, current_tick)
            decisions = list(
                self._engine.decide(batch, graph, current_tick, timestamp_epoch_ms)
            )
            self._apply_holds(batch, decisions, current_tick)
        return {
            "batch_tick": current_tick,
            "decisions_count": len(decisions),
            "decisions": decisions,
        }

    def _apply_holds(
        self,
        batch: Sequence[CommandProposal],
        decisions: Sequence[ArbitrationDecision],
        tick: int,
    ) -> None:
        """Re-queue DELAYed commands so they are re-evaluated when due."""
        by_id = {command.command_id: command for command in batch}
        for decision in decisions:
            if decision.action is not ActionType.DELAY:
                continue
            command = by_id.get(decision.command_id)
            if command is not None:
                self._queue.hold(command, tick)
