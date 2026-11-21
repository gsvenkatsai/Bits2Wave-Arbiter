"""Orchestrator tests: queue -> detector -> engine wiring with injected doubles."""
from __future__ import annotations

from typing import Any, List, Sequence

from arbiter.engine.contracts import ActionType, ArbitrationDecision, CommandProposal, ReasonCode
from arbiter.queue import ProposalQueue, QueueOrchestrator
from arbiter.queue.orchestrator import ConflictGraphStub


class RecordingDetector:
    def __init__(self) -> None:
        self.calls: List[tuple[list[str], int]] = []

    def detect(
        self, proposals: Sequence[CommandProposal], tick: int
    ) -> ConflictGraphStub:
        self.calls.append(([proposal.command_id for proposal in proposals], tick))
        return ConflictGraphStub(nodes=tuple(p.command_id for p in proposals))


class ScriptedEngine:
    def __init__(self, actions: Sequence[ActionType]) -> None:
        self.actions = list(actions)
        self.calls: List[tuple[list[str], int, int]] = []

    def decide(
        self,
        proposals: Sequence[CommandProposal],
        graph: ConflictGraphStub,
        tick: int,
        timestamp_epoch_ms: int,
    ) -> Sequence[ArbitrationDecision]:
        self.calls.append(
            ([proposal.command_id for proposal in proposals], tick, timestamp_epoch_ms)
        )
        decisions = []
        for index, proposal in enumerate(proposals):
            action = self.actions[index] if index < len(self.actions) else ActionType.APPROVE
            decisions.append(
                ArbitrationDecision(
                    decision_id=f"d-{proposal.command_id}",
                    command_id=proposal.command_id,
                    batch_tick=tick,
                    action=action,
                    approved_value=None,
                    delta_utility=0.0,
                    reason_code=ReasonCode.NONE,
                    decided_at_epoch_ms=timestamp_epoch_ms,
                )
            )
        return decisions


def test_on_tick_returns_required_keys(make_proposal: Any) -> None:
    detector = RecordingDetector()
    engine = ScriptedEngine([ActionType.APPROVE])
    orchestrator = QueueOrchestrator(detector=detector, engine=engine)
    orchestrator.submit(make_proposal(command_id="c1"))

    result = orchestrator.on_tick(current_tick=3, timestamp_epoch_ms=300)

    assert set(result) == {"batch_tick", "decisions_count", "decisions"}
    assert result["batch_tick"] == 3
    assert result["decisions_count"] == 1
    assert result["decisions"][0].command_id == "c1"
    assert detector.calls == [(["c1"], 3)]
    assert engine.calls == [(["c1"], 3, 300)]


def test_on_tick_with_empty_queue_skips_detector_and_engine() -> None:
    detector = RecordingDetector()
    engine = ScriptedEngine([])
    orchestrator = QueueOrchestrator(detector=detector, engine=engine)

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)

    assert result == {"batch_tick": 1, "decisions_count": 0, "decisions": []}
    assert detector.calls == []
    assert engine.calls == []


def test_default_stubs_run_without_decisions(make_proposal: Any) -> None:
    orchestrator = QueueOrchestrator()
    orchestrator.submit(make_proposal())

    result = orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)

    assert result["decisions_count"] == 0
    assert result["decisions"] == []
    assert orchestrator.queue.is_empty is True


def test_delay_decision_requeues_the_command(make_proposal: Any) -> None:
    engine = ScriptedEngine([ActionType.DELAY])
    orchestrator = QueueOrchestrator(engine=engine)
    orchestrator.submit(make_proposal(command_id="slow"))

    first = orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)
    assert first["decisions"][0].action is ActionType.DELAY
    assert orchestrator.queue.delayed_count == 1

    second = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)
    assert [decision.command_id for decision in second["decisions"]] == ["slow"]
    assert engine.calls[-1][0] == ["slow"]


def test_emergency_commands_bypass_the_orchestrator_engine(make_proposal: Any) -> None:
    engine = ScriptedEngine([ActionType.APPROVE])
    orchestrator = QueueOrchestrator(engine=engine)

    assert orchestrator.submit(make_proposal(command_id="urgent", is_emergency=True)) is True
    result = orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)

    assert result["decisions_count"] == 0
    assert engine.calls == []
    assert orchestrator.queue.emergency_lane.audit_count == 1


def test_orchestrator_accepts_injected_queue(make_proposal: Any) -> None:
    queue = ProposalQueue()
    queue.submit(make_proposal(command_id="pre"))
    orchestrator = QueueOrchestrator(queue=queue)

    result = orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)

    assert result["batch_tick"] == 0
    assert queue.is_empty is True
    assert orchestrator.queue is queue
