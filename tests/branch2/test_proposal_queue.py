"""FR-3 queue tests: batching order, emergency bypass, hold budget, threading."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, List

import pytest

from arbiter.engine.contracts import ActionType, ReasonCode
from arbiter.queue import ProposalQueue, QueueInvariantError


def test_submit_buffers_and_rejects_duplicates(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="c1")

    assert queue.submit(command) is True
    assert queue.submit(command) is False
    assert queue.pending_count == 1
    assert queue.delayed_count == 0
    assert queue.is_empty is False


def test_submit_many_counts_accepted(make_proposal: Any) -> None:
    queue = ProposalQueue()
    first = make_proposal(command_id="a")
    second = make_proposal(command_id="b")

    assert queue.submit_many([first, second, first]) == 2
    assert queue.pending_count == 2


def test_flush_returns_batch_and_clears(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal()
    queue.submit(command)

    batch = queue.flush_batch(0)

    assert batch == [command]
    assert queue.is_empty is True
    assert queue.flush_batch(1) == []


def test_flush_orders_by_created_tick_then_command_id(make_proposal: Any) -> None:
    queue = ProposalQueue()
    late = make_proposal(command_id="a-late", created_tick=5)
    early_b = make_proposal(command_id="b-early", created_tick=1)
    early_a = make_proposal(command_id="a-early", created_tick=1)
    for command in (late, early_b, early_a):
        queue.submit(command)

    batch = queue.flush_batch(6)

    assert [command.command_id for command in batch] == [
        "a-early",
        "b-early",
        "a-late",
    ]


def test_emergency_bypass_is_never_buffered(make_proposal: Any) -> None:
    queue = ProposalQueue()
    urgent = make_proposal(
        command_id="urgent",
        is_emergency=True,
        controller="ctrl-ps",
        requested_value=46.0,
    )

    assert queue.submit(urgent) is True

    assert queue.pending_count == 0
    assert queue.delayed_count == 0
    assert queue.is_empty is True
    assert queue.flush_batch(0) == []
    assert queue.emergency_lane.audit_count == 1

    records = queue.emergency_lane.drain_audit()
    decision = records[0].decision
    assert decision.action is ActionType.APPROVE
    assert decision.reason_code is ReasonCode.EMERGENCY_OVERRIDE
    assert decision.delta_utility == pytest.approx(0.0)
    assert decision.approved_value == pytest.approx(46.0)
    assert decision.twin_eval_count == 0


def test_emergency_decisions_use_the_queue_tick(make_proposal: Any) -> None:
    queue = ProposalQueue()
    queue.set_tick(7)
    queue.submit(make_proposal(command_id="e1", is_emergency=True))

    record = queue.emergency_lane.peek_audit()[0]
    assert record.batch_tick == 7
    assert record.decision.batch_tick == 7


def test_duplicate_emergency_is_rejected(make_proposal: Any) -> None:
    queue = ProposalQueue()
    urgent = make_proposal(command_id="dup", is_emergency=True)
    assert queue.submit(urgent) is True
    assert queue.submit(urgent) is False
    assert queue.emergency_lane.audit_count == 1


def test_hold_parks_until_next_tick(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="h1")

    held = queue.hold(command, tick=2)

    assert held is not None
    assert held.hold_count == 1
    assert held.must_decide is False
    assert queue.delayed_count == 1
    assert queue.flush_batch(2) == []
    assert queue.flush_batch(3) == [held]


def test_hold_limit_is_three_ticks(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="h2")

    first = queue.hold(command, tick=0)
    assert first is not None and first.hold_count == 1
    queue.flush_batch(1)

    second = queue.hold(first, tick=1)
    assert second is not None and second.hold_count == 2
    queue.flush_batch(2)

    third = queue.hold(second, tick=2)
    assert third is not None
    assert third.hold_count == 3
    assert third.must_decide is True
    assert queue.is_holdable(third) is False


def test_never_held_after_must_decide(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="h3")

    held = command
    for tick in range(3):
        parked = queue.hold(held, tick)
        assert parked is not None
        held = parked
        released = queue.flush_batch(tick + 1)
        assert released == [held]

    assert held.must_decide is True
    assert queue.must_decide(held) is True
    assert queue.hold(held, tick=4) is None
    assert queue.is_holdable(held) is False
    assert queue.flush_batch(5) == []
    queue.check_invariants()


def test_emergency_is_never_holdable(make_proposal: Any) -> None:
    queue = ProposalQueue()
    urgent = make_proposal(command_id="e2", is_emergency=True)

    assert queue.is_holdable(urgent) is False
    assert queue.hold(urgent, tick=0) is None


def test_parked_command_cannot_be_held_twice(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="h4")

    assert queue.hold(command, tick=0) is not None
    assert queue.hold(command, tick=0) is None
    assert queue.delayed_count == 1


def test_increment_holds_advances_and_clamps(make_proposal: Any) -> None:
    queue = ProposalQueue()
    queue.hold(make_proposal(command_id="h5"), tick=0)

    assert [c.hold_count for c in queue.increment_holds()] == [2]
    assert [c.hold_count for c in queue.increment_holds()] == [3]
    third = queue.increment_holds()
    assert [c.hold_count for c in third] == [3]
    assert third[0].must_decide is True
    queue.check_invariants()


def test_increment_holds_with_nothing_parked() -> None:
    assert ProposalQueue().increment_holds() == []


def test_invariants_hold_after_a_tick_sequence(make_proposal: Any) -> None:
    queue = ProposalQueue()
    queue.submit(make_proposal(command_id="n1"))
    queue.submit(make_proposal(command_id="n2"))
    queue.hold(make_proposal(command_id="p1"), tick=0)
    queue.flush_batch(1)
    queue.check_invariants()


def test_invariant_guard_detects_excessive_hold(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="bad")
    queue.submit(command)
    queue._hold_counts["bad"] = 4  # type: ignore[attr-defined]

    with pytest.raises(QueueInvariantError):
        queue.check_invariants()


def test_invariant_guard_detects_duplicate_queue_entry(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="dup2")
    queue.submit(command)
    queue._buffer.append(command)  # type: ignore[attr-defined]

    with pytest.raises(QueueInvariantError):
        queue.check_invariants()


def test_invariant_guard_detects_second_hold_cycle(make_proposal: Any) -> None:
    queue = ProposalQueue()
    command = make_proposal(command_id="cycle")

    held = command
    for tick in range(3):
        parked = queue.hold(held, tick)
        assert parked is not None
        held = parked
        queue.flush_batch(tick + 1)

    # Simulate a caller bypassing is_holdable and parking it again.
    queue._delayed.append(  # type: ignore[attr-defined]
        SimpleNamespace(command=command.model_copy(update={"must_decide": False}), due_tick=99)
    )

    with pytest.raises(QueueInvariantError):
        queue.check_invariants()


def test_invalid_hold_budget() -> None:
    with pytest.raises(ValueError):
        ProposalQueue(max_hold_ticks=0)


def test_snapshot_is_ordered_and_read_only(make_proposal: Any) -> None:
    queue = ProposalQueue()
    queue.hold(make_proposal(command_id="z", created_tick=2), tick=0)
    queue.submit(make_proposal(command_id="a", created_tick=0))

    snapshot = queue.snapshot()

    assert [command.command_id for command in snapshot] == ["a", "z"]
    assert queue.pending_count == 1
    assert queue.delayed_count == 1


def test_concurrent_submissions_are_safe(make_proposal: Any) -> None:
    queue = ProposalQueue()
    accepted: List[bool] = []
    accepted_lock = threading.Lock()
    threads = 8
    per_thread = 50

    def worker(worker_id: int) -> None:
        for index in range(per_thread):
            command = make_proposal(
                command_id=f"w{worker_id}-{index}",
                created_tick=index,
            )
            result = queue.submit(command)
            with accepted_lock:
                accepted.append(result)

    workers = [threading.Thread(target=worker, args=(worker_id,)) for worker_id in range(threads)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()

    assert sum(1 for ok in accepted if ok) == threads * per_thread
    assert queue.pending_count == threads * per_thread
    batch = queue.flush_batch(0)
    assert len(batch) == threads * per_thread
    assert len({command.command_id for command in batch}) == threads * per_thread
    queue.check_invariants()


def test_concurrent_emergency_submissions_stay_out_of_the_buffer(make_proposal: Any) -> None:
    queue = ProposalQueue()
    threads = 8
    per_thread = 20

    def worker(worker_id: int) -> None:
        for index in range(per_thread):
            queue.submit(
                make_proposal(
                    command_id=f"n{worker_id}-{index}",
                )
            )
            queue.submit(
                make_proposal(
                    command_id=f"e{worker_id}-{index}",
                    is_emergency=True,
                )
            )

    workers = [threading.Thread(target=worker, args=(worker_id,)) for worker_id in range(threads)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()

    assert queue.pending_count == threads * per_thread
    assert queue.emergency_lane.audit_count == threads * per_thread
    assert len(queue.flush_batch(0)) == threads * per_thread
