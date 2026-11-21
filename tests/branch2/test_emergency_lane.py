"""Emergency bypass lane tests: immediate approval and audit trail."""
from __future__ import annotations

from typing import Any

import pytest

from arbiter.engine.contracts import ActionType, ReasonCode
from arbiter.queue import EmergencyBypassLane


def test_accept_returns_emergency_approval(make_proposal: Any) -> None:
    lane = EmergencyBypassLane(id_factory=lambda: "decision-1", clock=lambda: 900)
    command = make_proposal(
        command_id="urgent",
        controller="ctrl-ps",
        target_cell="C",
        parameter="TX_POWER_DBM",
        requested_value=46.0,
        hold_count=2,
    )

    decision = lane.accept(command, tick=4, timestamp_epoch_ms=1234)

    assert decision.decision_id == "decision-1"
    assert decision.command_id == "urgent"
    assert decision.batch_tick == 4
    assert decision.action is ActionType.APPROVE
    assert decision.approved_value == pytest.approx(46.0)
    assert decision.delta_utility == pytest.approx(0.0)
    assert decision.reason_code is ReasonCode.EMERGENCY_OVERRIDE
    assert decision.hold_count == 2
    assert decision.twin_eval_count == 0
    assert decision.decided_at_epoch_ms == 1234


def test_accept_uses_clock_when_no_timestamp_is_given(make_proposal: Any) -> None:
    lane = EmergencyBypassLane(clock=lambda: 555)
    decision = lane.accept(make_proposal())
    assert decision.decided_at_epoch_ms == 555


def test_accept_appends_audit_record(make_proposal: Any) -> None:
    lane = EmergencyBypassLane()
    command = make_proposal(command_id="a1", controller="ctrl-ps", target_cell="B")

    decision = lane.accept(command, tick=2)

    records = lane.peek_audit()
    assert len(records) == 1
    record = records[0]
    assert record.command_id == "a1"
    assert record.controller == "ctrl-ps"
    assert record.target_cell == "B"
    assert record.parameter == command.parameter
    assert record.requested_value == pytest.approx(command.requested_value)
    assert record.batch_tick == 2
    assert record.decision is decision


def test_drain_audit_returns_and_clears(make_proposal: Any) -> None:
    lane = EmergencyBypassLane()
    lane.accept(make_proposal(command_id="a1"))
    lane.accept(make_proposal(command_id="a2"))

    assert lane.audit_count == 2
    drained = lane.drain_audit()

    assert [record.command_id for record in drained] == ["a1", "a2"]
    assert lane.audit_count == 0
    assert lane.drain_audit() == []


def test_decision_ids_are_unique_by_default(make_proposal: Any) -> None:
    lane = EmergencyBypassLane()
    first = lane.accept(make_proposal(command_id="a1"))
    second = lane.accept(make_proposal(command_id="a2"))
    assert first.decision_id != second.decision_id
