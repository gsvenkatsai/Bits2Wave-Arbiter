"""Task 4.1: per-controller flood guard (20 proposals / 100 ms by default)."""
from __future__ import annotations

from typing import Any, List

import pytest

from arbiter.engine.contracts import ActionType, ReasonCode
from arbiter.engine.mocks.mock_twin_client import MockDigitalTwinClient
from arbiter.queue import ProposalQueue, QueueOrchestrator
from arbiter.queue.rate_limiter import (
    DEFAULT_MAX_PROPOSALS_PER_WINDOW,
    DEFAULT_RATE_WINDOW_MS,
    RATE_LIMIT_REASON,
    ProposalRateLimiter,
)


class _Clock:
    """Deterministic millisecond clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, milliseconds: float) -> None:
        self.now += milliseconds


def _submit_many(
    queue: ProposalQueue,
    make_proposal: Any,
    count: int,
    controller: str = "ctrl-flood",
    cell: str = "A",
    offset: int = 0,
) -> List[bool]:
    return [
        queue.submit(
            make_proposal(
                command_id=f"flood-{controller}-{offset + index}",
                controller=controller,
                target_cell=cell,
            )
        )
        for index in range(count)
    ]


def test_rate_limiter_defaults() -> None:
    limiter = ProposalRateLimiter()
    assert limiter.max_proposals == DEFAULT_MAX_PROPOSALS_PER_WINDOW == 20
    assert limiter.window_ms == DEFAULT_RATE_WINDOW_MS == 100
    assert RATE_LIMIT_REASON is ReasonCode.NONE


@pytest.mark.parametrize(
    "kwargs",
    [{"max_proposals": 0}, {"window_ms": 0}],
)
def test_rate_limiter_rejects_invalid_configuration(kwargs: Any) -> None:
    with pytest.raises(ValueError):
        ProposalRateLimiter(**kwargs)


def test_queue_caps_a_controller_per_window(make_proposal: Any) -> None:
    clock = _Clock()
    queue = ProposalQueue(rate_clock=clock)

    accepted = _submit_many(queue, make_proposal, 25)

    assert accepted == [True] * 20 + [False] * 5
    assert queue.rate_limited_count == 5
    drained = queue.drain_rate_limited()
    assert [command.command_id for command in drained] == [
        f"flood-ctrl-flood-{index}" for index in range(20, 25)
    ]
    # Draining is destructive: the second call is empty.
    assert queue.drain_rate_limited() == []


def test_window_rollover_restores_the_budget(make_proposal: Any) -> None:
    clock = _Clock()
    queue = ProposalQueue(rate_clock=clock)

    _submit_many(queue, make_proposal, 20)
    assert queue.submit(
        make_proposal(command_id="late", controller="ctrl-flood")
    ) is False

    clock.advance(DEFAULT_RATE_WINDOW_MS)

    assert queue.submit(
        make_proposal(command_id="next-window", controller="ctrl-flood")
    ) is True
    assert queue.rate_limited_count == 1


def test_cap_is_per_controller(make_proposal: Any) -> None:
    clock = _Clock()
    queue = ProposalQueue(rate_clock=clock, max_proposals_per_window=2)

    assert _submit_many(queue, make_proposal, 2, controller="ctrl-a") == [True, True]
    assert _submit_many(
        queue, make_proposal, 3, controller="ctrl-a", offset=2
    ) == [False, False, False]
    assert _submit_many(queue, make_proposal, 2, controller="ctrl-b") == [True, True]
    assert queue.rate_limited_count == 3
    assert queue.rate_limiter.rejected_by_controller == {"ctrl-a": 3}


def test_emergency_bypass_lane_is_unaffected(make_proposal: Any) -> None:
    clock = _Clock()
    queue = ProposalQueue(rate_clock=clock, max_proposals_per_window=1)

    assert queue.submit(
        make_proposal(command_id="normal", controller="ctrl-es")
    ) is True
    assert queue.submit(
        make_proposal(command_id="normal-2", controller="ctrl-es")
    ) is False
    assert queue.submit(
        make_proposal(
            command_id="urgent",
            controller="ctrl-es",
            is_emergency=True,
        )
    ) is True

    assert queue.emergency_count == 1
    assert queue.rate_limited_count == 1
    assert [record.command_id for record in queue.emergency_lane.peek_audit()] == [
        "urgent"
    ]


def test_orchestrator_exposes_the_flood_metric(make_proposal: Any) -> None:
    clock = _Clock()
    queue = ProposalQueue(rate_clock=clock, max_proposals_per_window=2)
    orchestrator = QueueOrchestrator(
        queue=queue,
        twin=MockDigitalTwinClient(),
        adjacency={},
    )

    accepted = [
        orchestrator.submit(
            make_proposal(command_id=f"flood-{index}", target_cell="A")
        )
        for index in range(5)
    ]
    assert accepted == [True, True, False, False, False]

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)

    assert result["rate_limited_count"] == 3
    assert orchestrator.rate_limited_count == 3
    flood_ids = {"flood-2", "flood-3", "flood-4"}
    flood_decisions = [
        decision
        for decision in result["decisions"]
        if decision.command_id in flood_ids
    ]
    assert len(flood_decisions) == 3
    for decision in flood_decisions:
        assert decision.action is ActionType.REJECT
        assert decision.reason_code is ReasonCode.NONE
        assert decision.approved_value is None
        assert decision.twin_eval_count == 0

    # Rejections are reported exactly once.
    follow_up = orchestrator.on_tick(current_tick=2, timestamp_epoch_ms=200)
    assert follow_up["rate_limited_count"] == 0
