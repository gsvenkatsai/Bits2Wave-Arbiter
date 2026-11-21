"""Contract shim tests: shapes must match the frozen spec exactly."""
from __future__ import annotations

import pytest

from arbiter.engine import contracts
from arbiter.engine.contracts import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ConflictType,
    ControllerType,
    ParameterType,
    ReasonCode,
    UtilityWeights,
)


def test_shared_contracts_are_used_when_available() -> None:
    assert contracts.SHARED_CONTRACTS_AVAILABLE is True


@pytest.mark.parametrize(
    ("member", "value"),
    [
        (ActionType.APPROVE, "APPROVE"),
        (ActionType.MODIFY, "MODIFY"),
        (ActionType.DELAY, "DELAY"),
        (ActionType.REJECT, "REJECT"),
    ],
)
def test_action_type_values(member: ActionType, value: str) -> None:
    assert member.value == value


def test_conflict_and_reason_enums_cover_spec() -> None:
    assert {member.value for member in ConflictType} == {
        "DIRECT",
        "INDIRECT",
        "IMPLICIT_PINGPONG",
    }
    assert {member.value for member in ReasonCode} == {
        "NONE",
        "ACTIVE_HO",
        "HEADROOM",
        "CYCLIC_PINGPONG",
        "TWIN_TIMEOUT",
        "UNCERTAINTY_GUARD",
        "FLOOR_BREACH",
        "EMERGENCY_OVERRIDE",
    }


def test_utility_weights_defaults_and_aliases() -> None:
    weights = UtilityWeights()
    assert weights.as_tuple() == pytest.approx((0.3, 0.2, 0.2, 0.3))
    aliased = UtilityWeights(w1=0.4, w2=0.1, w3=0.1, w4=0.4)
    assert aliased.as_tuple() == pytest.approx((0.4, 0.1, 0.1, 0.4))
    assert aliased.coverage == pytest.approx(0.4)


def test_utility_weights_reject_out_of_range() -> None:
    with pytest.raises(ValueError):
        UtilityWeights(coverage=1.5)


def test_command_proposal_defaults() -> None:
    proposal = CommandProposal(
        command_id="c1",
        controller_id="ctrl",
        controller_type=ControllerType.LOAD_BALANCING,
        target_cell_id="cell-a",
        parameter=ParameterType.TILT_DEG,
        requested_value=4.0,
        timestamp_epoch_ms=1,
    )
    assert proposal.emergency_flag is False
    assert proposal.hold_count == 0


def test_arbitration_decision_defaults() -> None:
    decision = ArbitrationDecision(
        decision_id="d1",
        command_id="c1",
        batch_tick=7,
        action=ActionType.DELAY,
        delta_utility=0.0,
        reason_code=ReasonCode.NONE,
        decided_at_epoch_ms=12,
    )
    assert decision.approved_value is None
    assert decision.hold_count == 0
    assert decision.twin_eval_count == 0


def test_contract_summary_lists_enums() -> None:
    summary = contracts.contract_summary()
    assert summary["shared_available"] == ["True"]
    assert summary["actions"] == ["APPROVE", "MODIFY", "DELAY", "REJECT"]
    assert "EMERGENCY_OVERRIDE" in summary["reasons"]
