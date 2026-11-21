"""Mock actuator tests: recording, state tracking and rollback."""
from __future__ import annotations

from typing import Any

import pytest

from arbiter.engine.mocks import AppliedChange, MockActuatorClient


def test_apply_records_change(make_proposal: Any) -> None:
    actuator = MockActuatorClient()
    proposal = make_proposal(command_id="c1", target_cell="A", current_value=30.0)

    change = actuator.apply(proposal, tick=4)

    assert isinstance(change, AppliedChange)
    assert change.command_id == "c1"
    assert change.target_cell == "A"
    assert change.previous_value == pytest.approx(30.0)
    assert change.applied_value == pytest.approx(40.0)
    assert change.tick == 4
    assert actuator.applied == [change]
    assert actuator.applied_count == 1
    assert actuator.value_of("A", "TX_POWER_DBM") == pytest.approx(40.0)


def test_apply_with_override_and_existing_state(make_proposal: Any) -> None:
    actuator = MockActuatorClient({("A", "TX_POWER_DBM"): 32.0})
    proposal = make_proposal(command_id="c1", target_cell="A", current_value=30.0)

    change = actuator.apply(proposal, value=35.0)

    assert change.previous_value == pytest.approx(32.0)
    assert change.applied_value == pytest.approx(35.0)
    assert actuator.snapshot() == {("A", "TX_POWER_DBM"): pytest.approx(35.0)}


def test_apply_accumulates_history(make_proposal: Any) -> None:
    actuator = MockActuatorClient()
    first = make_proposal(command_id="c1", target_cell="A", requested_value=35.0)
    second = make_proposal(command_id="c2", target_cell="B", requested_value=10.0)

    actuator.apply(first)
    actuator.apply(second)

    assert actuator.applied_count == 2
    assert [change.command_id for change in actuator.applied] == ["c1", "c2"]


def test_rollback_reverts_last_change(make_proposal: Any) -> None:
    actuator = MockActuatorClient()
    actuator.apply(make_proposal(command_id="c1", target_cell="A", current_value=30.0))

    reverted = actuator.rollback()

    assert len(reverted) == 1
    assert reverted[0].command_id == "c1"
    assert actuator.applied_count == 0
    assert actuator.rollbacks == reverted
    assert actuator.value_of("A", "TX_POWER_DBM") == pytest.approx(30.0)


def test_rollback_multiple_changes_restores_pre_state(make_proposal: Any) -> None:
    actuator = MockActuatorClient()
    actuator.apply(make_proposal(command_id="c1", target_cell="A", requested_value=35.0))
    actuator.apply(make_proposal(command_id="c2", target_cell="A", requested_value=38.0))

    reverted = actuator.rollback(2)

    assert [change.command_id for change in reverted] == ["c2", "c1"]
    assert actuator.value_of("A", "TX_POWER_DBM") == pytest.approx(30.0)
    assert actuator.applied_count == 0


def test_rollback_more_than_applied_reverts_everything(make_proposal: Any) -> None:
    actuator = MockActuatorClient()
    actuator.apply(make_proposal(command_id="c1", target_cell="A"))

    reverted = actuator.rollback(5)

    assert len(reverted) == 1
    assert actuator.applied_count == 0


def test_rollback_requires_positive_count() -> None:
    with pytest.raises(ValueError):
        MockActuatorClient().rollback(0)


def test_rollback_on_empty_actuator_is_a_noop() -> None:
    actuator = MockActuatorClient()
    assert actuator.rollback() == []
    assert actuator.rollbacks == []


def test_reset_clears_state_and_logs(make_proposal: Any) -> None:
    actuator = MockActuatorClient()
    actuator.apply(make_proposal(command_id="c1", target_cell="A"))
    actuator.rollback()

    actuator.reset()

    assert actuator.snapshot() == {}
    assert actuator.applied == []
    assert actuator.rollbacks == []
    assert actuator.value_of("A", "TX_POWER_DBM") is None


def test_unknown_parameter_has_no_value() -> None:
    assert MockActuatorClient().value_of("A", "TILT_DEG") is None
