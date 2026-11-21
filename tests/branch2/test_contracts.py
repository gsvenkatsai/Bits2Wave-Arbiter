"""Contract, constants and weight-preset tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from arbiter.engine import contracts
from arbiter.shared import models as shared_models
from arbiter.engine.contracts import (
    FLOOR_COVERAGE,
    FLOOR_DROP_RATE,
    MAX_HOLD_TICKS,
    MAX_TWIN_EVALS,
    PINGPONG_WINDOW,
    TICK_MS,
    TWIN_TIMEOUT_MS,
    WEIGHT_PRESETS,
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ConflictType,
    ReasonCode,
    UtilityWeights,
)


def test_spec_constants() -> None:
    assert FLOOR_COVERAGE == pytest.approx(0.90)
    assert FLOOR_DROP_RATE == pytest.approx(0.02)
    assert MAX_HOLD_TICKS == 3
    assert PINGPONG_WINDOW == 5
    assert TWIN_TIMEOUT_MS == pytest.approx(100.0)
    assert TICK_MS == 100
    assert MAX_TWIN_EVALS == 6


def test_action_type_members() -> None:
    assert [action.value for action in ActionType] == [
        "APPROVE",
        "MODIFY",
        "DELAY",
        "REJECT",
    ]


def test_conflict_type_members() -> None:
    assert {conflict.value for conflict in ConflictType} == {
        "DIRECT",
        "INDIRECT",
        "IMPLICIT_PINGPONG",
    }


def test_reason_code_members() -> None:
    assert {reason.value for reason in ReasonCode} == {
        "NONE",
        "ACTIVE_HO",
        "HEADROOM",
        "CYCLIC_PINGPONG",
        "TWIN_TIMEOUT",
        "UNCERTAINTY_GUARD",
        "FLOOR_BREACH",
        "EMERGENCY_OVERRIDE",
    }


def test_command_proposal_shape() -> None:
    proposal = CommandProposal(
        command_id="c1",
        controller="ctrl-energy",
        controller_type="ENERGY_SAVING",
        target_cell="A",
        timestamp_epoch_ms=0,
        parameter="SLEEP_STATE",
        requested_value=0.0,
        current_value=1.0,
        created_tick=7,
    )
    assert proposal.is_emergency is False
    assert proposal.is_binary is False
    assert proposal.hold_count == 0
    assert proposal.handover_from is None
    assert proposal.handover_to is None


def test_command_proposal_accepts_handover_and_binary_flags() -> None:
    proposal = CommandProposal(
        command_id="c2",
        controller="ctrl-lb",
        controller_type="LOAD_BALANCING",
        target_cell="B",
        timestamp_epoch_ms=0,
        parameter="OFFLOAD_RATIO",
        requested_value=0.4,
        current_value=0.2,
        created_tick=3,
        is_binary=True,
        is_emergency=True,
        hold_count=2,
        handover_from="A",
        handover_to="B",
    )
    assert proposal.is_binary is True
    assert proposal.is_emergency is True
    assert proposal.hold_count == 2
    assert proposal.handover_from == "A"
    assert proposal.handover_to == "B"


def test_command_proposal_requires_core_fields() -> None:
    with pytest.raises(ValidationError):
        CommandProposal(  # type: ignore[call-arg]
            command_id="c3", controller="ctrl", target_cell="A"
        )


def test_arbitration_decision_shape() -> None:
    decision = ArbitrationDecision(
        decision_id="d1",
        command_id="c1",
        batch_tick=11,
        action=ActionType.MODIFY,
        approved_value=37.5,
        delta_utility=0.021,
        reason_code=ReasonCode.NONE,
        decided_at_epoch_ms=1700,
    )
    assert decision.action is ActionType.MODIFY
    assert decision.approved_value == pytest.approx(37.5)
    assert decision.hold_count == 0
    assert decision.twin_eval_count == 0


def test_arbitration_decision_approved_value_is_optional() -> None:
    decision = ArbitrationDecision(
        decision_id="d2",
        command_id="c1",
        batch_tick=1,
        action=ActionType.REJECT,
        delta_utility=-0.01,
        reason_code=ReasonCode.FLOOR_BREACH,
        decided_at_epoch_ms=5,
    )
    assert decision.approved_value is None


def test_utility_weights_defaults() -> None:
    weights = UtilityWeights()
    assert weights.as_tuple() == pytest.approx((0.3, 0.2, 0.2, 0.3))
    assert weights.total == pytest.approx(1.0)


def test_utility_weights_reject_negative_values() -> None:
    with pytest.raises(ValidationError):
        UtilityWeights(w1=-0.1)


def test_utility_weights_reject_zero_sum() -> None:
    with pytest.raises(ValidationError):
        UtilityWeights(w1=0.0, w2=0.0, w3=0.0, w4=0.0)


def test_utility_weights_from_preset_default() -> None:
    assert UtilityWeights.from_preset("default").as_tuple() == pytest.approx(
        (0.3, 0.2, 0.2, 0.3)
    )


def test_utility_weights_from_preset_energy_saver() -> None:
    weights = UtilityWeights.from_preset("night_energy_saver")
    assert weights.w2 > weights.w1
    assert weights.as_tuple() == pytest.approx(WEIGHT_PRESETS["night_energy_saver"])


def test_utility_weights_from_preset_coverage_first() -> None:
    weights = UtilityWeights.from_preset("coverage_first")
    assert weights.w1 == max(weights.as_tuple())
    assert weights.as_tuple() == pytest.approx(WEIGHT_PRESETS["coverage_first"])


def test_utility_weights_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="unknown weight preset"):
        UtilityWeights.from_preset("does_not_exist")


def test_contract_summary_reports_source() -> None:
    summary = contracts.contract_summary()
    assert summary["actions"] == ["APPROVE", "MODIFY", "DELAY", "REJECT"]
    assert set(summary["presets"]) == {
        "default",
        "night_energy_saver",
        "coverage_first",
    }
    assert summary["shared_available"] == ["True"]
    assert summary["contracts_source"] == ["arbiter.shared.models"]


def test_contracts_are_the_shared_models() -> None:
    assert contracts.SHARED_CONTRACTS_AVAILABLE is True
    assert contracts.ActionType is shared_models.ActionType
    assert contracts.ConflictType is shared_models.ConflictType
    assert contracts.ReasonCode is shared_models.ReasonCode
    assert contracts.ArbitrationDecision is shared_models.ArbitrationDecision
    assert issubclass(contracts.CommandProposal, shared_models.CommandProposal)


def test_proposal_accepts_shared_and_branch2_vocabulary() -> None:
    shared_style = CommandProposal(
        command_id="c4",
        controller_id="ctrl-energy",
        controller_type="ENERGY_SAVING",
        target_cell_id="A",
        parameter="TX_POWER_DBM",
        requested_value=33.0,
        timestamp_epoch_ms=250,
        emergency_flag=True,
    )
    branch2_style = CommandProposal(
        command_id="c5",
        controller="ctrl-energy",
        controller_type="ENERGY_SAVING",
        target_cell="A",
        parameter="TX_POWER_DBM",
        requested_value=33.0,
        timestamp_epoch_ms=0,
        is_emergency=True,
    )

    assert shared_style.target_cell == "A"
    assert shared_style.controller == "ctrl-energy"
    assert shared_style.is_emergency is True
    assert shared_style.created_tick == 2  # 250 ms // TICK_MS
    assert branch2_style.target_cell_id == "A"
    assert branch2_style.controller_id == "ctrl-energy"
    assert branch2_style.emergency_flag is True
    assert branch2_style.created_tick == 0


def test_proposal_serialises_as_the_shared_model() -> None:
    proposal = CommandProposal(
        command_id="c6",
        controller="ctrl-energy",
        controller_type="ENERGY_SAVING",
        target_cell="B",
        parameter="SLEEP_STATE",
        requested_value=1.0,
        current_value=0.0,
        timestamp_epoch_ms=0,
        is_binary=True,
    )

    dumped = proposal.model_dump()
    assert dumped["target_cell_id"] == "B"
    assert dumped["emergency_flag"] is False
    assert shared_models.CommandProposal.model_validate(dumped).target_cell_id == "B"
