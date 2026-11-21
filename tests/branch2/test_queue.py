"""Queue tests: batching, emergency lane and hold bookkeeping."""
from __future__ import annotations

from typing import Any

import pytest

from arbiter.queue import HoldBudgetExceeded, ProposalQueue, QueueBatch
from arbiter.queue.proposal_queue import TICK_DURATION_MS


def test_tick_duration_is_100ms() -> None:
    assert TICK_DURATION_MS == 100


def test_submit_and_drain_batch(make_proposal: Any) -> None:
    queue = ProposalQueue()
    proposal = make_proposal()
    assert queue.submit(proposal) is True
    assert queue.pending_count == 1
    assert queue.is_empty is False

    batch = queue.drain(3)
    assert isinstance(batch, QueueBatch)
    assert batch.tick == 3
    assert batch.proposals == (proposal,)
    assert batch.emergency == ()
    assert batch.batch_size == 1
    assert batch.is_empty is False
    assert queue.is_empty is True


def test_duplicate_command_id_is_rejected(make_proposal) -> None:
    queue = ProposalQueue()
    proposal = make_proposal(command_id="dup")
    assert queue.submit(proposal) is True
    assert queue.submit(proposal) is False
    assert queue.pending_count == 1


def test_emergency_lane_bypasses_arbitration(make_proposal: Any) -> None:
    queue = ProposalQueue()
    normal = make_proposal(command_id="normal")
    emergency = make_proposal(command_id="urgent", emergency_flag=True)
    assert queue.submit(normal) is True
    assert queue.submit(emergency) is True
    assert queue.pending_count == 1
    assert queue.emergency_count == 1

    batch = queue.drain(0)
    assert batch.proposals == (normal,)
    assert batch.emergency == (emergency,)
    assert batch.batch_size == 2


def test_submit_many_counts_new_proposals(make_proposal: Any) -> None:
    queue = ProposalQueue()
    first = make_proposal(command_id="a")
    second = make_proposal(command_id="b")
    assert queue.submit_many([first, second, first]) == 2
    assert queue.pending_count == 2


def test_empty_drain_returns_empty_batch() -> None:
    batch = ProposalQueue().drain(9)
    assert batch.is_empty is True
    assert batch.batch_size == 0


def test_can_hold_until_limit(make_proposal: Any) -> None:
    queue = ProposalQueue(max_hold_ticks=3)
    assert queue.can_hold(make_proposal(hold_count=0)) is True
    assert queue.can_hold(make_proposal(hold_count=2)) is True
    assert queue.can_hold(make_proposal(hold_count=3)) is False


def test_hold_increments_and_requeues(make_proposal: Any) -> None:
    queue = ProposalQueue()
    proposal = make_proposal(command_id="hold-me")
    queue.submit(proposal)
    queue.drain(0)

    held = queue.hold(proposal)
    assert held.command_id == proposal.command_id
    assert held.hold_count == 1
    assert queue.pending_count == 1
    assert queue.pending_snapshot()[0].hold_count == 1


def test_hold_beyond_limit_raises(make_proposal: Any) -> None:
    queue = ProposalQueue(max_hold_ticks=3)
    proposal = make_proposal(hold_count=3)
    with pytest.raises(HoldBudgetExceeded):
        queue.hold(proposal)


def test_hold_keeps_emergency_commands_on_emergency_lane(make_proposal: Any) -> None:
    queue = ProposalQueue()
    urgent = make_proposal(emergency_flag=True)
    queue.hold(urgent)
    assert queue.emergency_count == 1
    assert queue.pending_count == 0


def test_invalid_max_hold_ticks() -> None:
    with pytest.raises(ValueError):
        ProposalQueue(max_hold_ticks=0)
