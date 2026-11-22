"""Orchestrator wiring tests: queue -> detector -> partitioner -> engine."""
from __future__ import annotations

from typing import Any, List, Sequence

import networkx as nx

from arbiter.engine.contracts import ActionType, ArbitrationDecision, CommandProposal, ReasonCode
from arbiter.queue import ProposalQueue, QueueOrchestrator


class RecordingDetector:
    """Detector double that records the batch and returns an edge-free graph."""

    def __init__(self) -> None:
        self.calls: List[tuple[List[str], Any]] = []

    def build(self, batch: Sequence[CommandProposal], adjacency: Any) -> nx.Graph:
        self.calls.append(([command.command_id for command in batch], adjacency))
        graph: nx.Graph = nx.Graph()
        for command in batch:
            graph.add_node(command.command_id, command=command)
        return graph


class ScriptedEngine:
    """Engine double returning one scripted decision per command."""

    def __init__(self, actions: Sequence[ActionType]) -> None:
        self.actions = list(actions)
        self.calls: List[tuple[List[str], int, int]] = []
        self.handovers: List[tuple[str, int]] = []

    def evaluate(
        self,
        component: Sequence[CommandProposal],
        graph: Any,
        twin: Any,
        weights: Any,
        tick: int,
        now_ms: int,
    ) -> List[ArbitrationDecision]:
        self.calls.append(
            ([command.command_id for command in component], tick, now_ms)
        )
        decisions = []
        for index, command in enumerate(component):
            action = (
                self.actions[index]
                if index < len(self.actions)
                else ActionType.APPROVE
            )
            decisions.append(
                ArbitrationDecision(
                    decision_id=f"d-{command.command_id}",
                    command_id=command.command_id,
                    batch_tick=tick,
                    action=action,
                    approved_value=None,
                    delta_utility=0.0,
                    reason_code=ReasonCode.NONE,
                    decided_at_epoch_ms=now_ms,
                )
            )
        return decisions

    def record_handover(self, command: CommandProposal, tick: int) -> None:
        self.handovers.append((command.command_id, tick))


def test_on_tick_returns_required_keys(make_proposal: Any) -> None:
    detector = RecordingDetector()
    engine = ScriptedEngine([ActionType.APPROVE])
    orchestrator = QueueOrchestrator(detector=detector, engine=engine)
    orchestrator.submit(make_proposal(command_id="c1"))

    result = orchestrator.on_tick(current_tick=3, timestamp_epoch_ms=300)

    assert set(result) == {
        "batch_tick",
        "decisions_count",
        "decisions",
        "rate_limited_count",
    }
    assert result["batch_tick"] == 3
    assert result["decisions_count"] == 1
    assert result["decisions"][0].command_id == "c1"
    assert detector.calls == [(["c1"], {})]
    assert engine.calls == [(["c1"], 3, 300)]


def test_on_tick_with_empty_queue_skips_detector_and_engine() -> None:
    detector = RecordingDetector()
    engine = ScriptedEngine([])
    orchestrator = QueueOrchestrator(detector=detector, engine=engine)

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)

    assert result == {
        "batch_tick": 1,
        "decisions_count": 0,
        "decisions": [],
        "rate_limited_count": 0,
    }
    assert detector.calls == []
    assert engine.calls == []


def test_default_wiring_runs_the_real_ladder(make_proposal: Any) -> None:
    orchestrator = QueueOrchestrator()
    orchestrator.submit(make_proposal())

    result = orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)

    assert result["decisions_count"] == 1
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


def test_emergency_commands_bypass_the_engine(make_proposal: Any) -> None:
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


def test_applied_decisions_are_recorded_as_handovers(make_proposal: Any) -> None:
    engine = ScriptedEngine([ActionType.APPROVE])
    orchestrator = QueueOrchestrator(engine=engine)
    orchestrator.submit(make_proposal(command_id="h1"))

    orchestrator.on_tick(current_tick=4, timestamp_epoch_ms=400)

    assert engine.handovers == [("h1", 4)]


def test_latency_is_recorded_per_decision(make_proposal: Any) -> None:
    engine = ScriptedEngine([ActionType.APPROVE, ActionType.REJECT])
    orchestrator = QueueOrchestrator(engine=engine)
    orchestrator.submit(make_proposal(command_id="a"))
    orchestrator.submit(make_proposal(command_id="b"))

    orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)

    assert len(orchestrator.latency_samples) == 2
    assert orchestrator.latency_p95_ms() >= 0.0
    orchestrator.reset_latency()
    assert orchestrator.latency_p95_ms() == 0.0


def test_p95_is_below_the_nfr_budget(make_proposal: Any) -> None:
    from arbiter.engine.mocks import MockDigitalTwinClient

    orchestrator = QueueOrchestrator(twin=MockDigitalTwinClient())
    for index in range(10):
        orchestrator.submit(make_proposal(command_id=f"c{index}", created_tick=index))

    orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)

    assert orchestrator.latency_p95_ms() < 100.0


def test_invalid_worker_count() -> None:
    import pytest

    with pytest.raises(ValueError):
        QueueOrchestrator(max_workers=0)
