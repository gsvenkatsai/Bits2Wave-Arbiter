"""Decision-ladder integration tests for the arbitrator."""
from __future__ import annotations

from typing import Any

import pytest

from arbiter.engine.arbitrator import ArbitrationConfig, Arbitrator, BatchOutcome
from arbiter.engine.contracts import ActionType, ParameterType, ReasonCode
from arbiter.engine.mocks.mock_twin_client import MockTwinClient
from arbiter.queue import ProposalQueue

from support import (
    BASE_KPIS,
    BREACH_KPIS,
    GOOD_KPIS,
    TINY_KPIS,
    WORSE_KPIS,
    twin_response,
)


def test_approve_when_joint_utility_improves(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)])
    arbitrator = Arbitrator(twin)
    proposal = make_proposal()

    outcome = arbitrator.decide_batch([proposal], tick=1, baseline_kpis=BASE_KPIS)

    decision = outcome.decisions[0]
    assert decision.action is ActionType.APPROVE
    assert decision.reason_code is ReasonCode.NONE
    assert decision.approved_value == pytest.approx(proposal.requested_value)
    assert decision.delta_utility == pytest.approx(0.0266, abs=1e-4)
    assert decision.twin_eval_count == 1
    assert decision.batch_tick == 1
    assert outcome.held == ()
    assert outcome.twin_evaluations == 1
    assert outcome.latency_ms >= 0.0


def test_conflicting_pair_is_one_component_one_evaluation(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)])
    arbitrator = Arbitrator(twin)
    left = make_proposal(command_id="a", requested_value=40.0)
    right = make_proposal(
        command_id="b",
        controller_id="ctrl-lb",
        requested_value=30.0,
    )

    outcome = arbitrator.decide_batch([left, right], tick=2, baseline_kpis=BASE_KPIS)

    assert twin.call_count == 1
    assert len(twin.last_deltas) == 2
    assert [decision.action for decision in outcome.decisions] == [
        ActionType.APPROVE,
        ActionType.APPROVE,
    ]
    assert outcome.action_counts == {"APPROVE": 2}


def test_independent_components_solve_in_parallel_and_keep_order(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[twin_response(GOOD_KPIS)] * 3
    )
    arbitrator = Arbitrator(twin, ArbitrationConfig(max_workers=3))
    proposals = [
        make_proposal(command_id="x", target_cell_id="cell-x"),
        make_proposal(command_id="y", target_cell_id="cell-y", controller_id="ctrl-lb"),
        make_proposal(command_id="z", target_cell_id="cell-z", controller_id="ctrl-cc"),
    ]

    outcome = arbitrator.decide_batch(proposals, tick=4, baseline_kpis=BASE_KPIS)

    assert twin.call_count == 3
    assert [decision.command_id for decision in outcome.decisions] == ["x", "y", "z"]
    assert all(decision.action is ActionType.APPROVE for decision in outcome.decisions)


def test_floor_breach_delays_then_rejects(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[twin_response(BREACH_KPIS, floors=False)] * 2
    )
    arbitrator = Arbitrator(twin)
    proposal = make_proposal()

    delayed = arbitrator.decide_batch([proposal], tick=1, baseline_kpis=BASE_KPIS)
    decision = delayed.decisions[0]
    assert decision.action is ActionType.DELAY
    assert decision.reason_code is ReasonCode.FLOOR_BREACH
    assert decision.hold_count == 1
    assert len(delayed.held) == 1
    assert delayed.held[0].hold_count == 1

    exhausted = make_proposal(command_id="late", hold_count=3)
    rejected = arbitrator.decide_batch([exhausted], tick=4, baseline_kpis=BASE_KPIS)
    final = rejected.decisions[0]
    assert final.action is ActionType.REJECT
    assert final.reason_code is ReasonCode.FLOOR_BREACH
    assert final.approved_value is None


def test_modify_finds_bounded_compromise(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[twin_response(WORSE_KPIS), twin_response(GOOD_KPIS)]
    )
    arbitrator = Arbitrator(
        twin,
        current_values={("cell-a", "TX_POWER_DBM"): 30.0},
    )
    proposal = make_proposal(requested_value=40.0)

    outcome = arbitrator.decide_batch([proposal], tick=1, baseline_kpis=BASE_KPIS)

    decision = outcome.decisions[0]
    assert decision.action is ActionType.MODIFY
    assert decision.approved_value == pytest.approx(35.0)
    assert decision.delta_utility == pytest.approx(0.0266, abs=1e-4)
    # The bounded search evaluates the whole 6-point ladder (1 joint + 5 search
    # probes) and keeps the best feasible point, so the NFR-3 cap is exactly hit.
    assert decision.twin_eval_count == 6
    assert twin.call_count == 6
    assert twin.last_deltas[0]["from_value"] == pytest.approx(30.0)


def test_binary_command_skips_modify(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(WORSE_KPIS)])
    arbitrator = Arbitrator(twin, current_values={("cell-a", "SLEEP_STATE"): 1.0})
    proposal = make_proposal(
        parameter=ParameterType.SLEEP_STATE,
        requested_value=0.0,
    )

    outcome = arbitrator.decide_batch([proposal], tick=1, baseline_kpis=BASE_KPIS)

    assert outcome.decisions[0].action is ActionType.DELAY
    assert twin.call_count == 1


def test_component_never_exceeds_twin_budget(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(WORSE_KPIS)] * 10)
    arbitrator = Arbitrator(
        twin,
        ArbitrationConfig(max_twin_evaluations=6),
        current_values={("cell-a", "TX_POWER_DBM"): 30.0},
    )
    proposal = make_proposal(requested_value=40.0)

    outcome = arbitrator.decide_batch([proposal], tick=1, baseline_kpis=BASE_KPIS)

    assert twin.call_count <= 6
    assert outcome.decisions[0].action in (ActionType.DELAY, ActionType.REJECT)


def test_budget_of_one_allows_only_joint_evaluation(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(WORSE_KPIS)] * 3)
    arbitrator = Arbitrator(
        twin,
        ArbitrationConfig(max_twin_evaluations=1),
        current_values={("cell-a", "TX_POWER_DBM"): 30.0},
    )

    arbitrator.decide_batch([make_proposal()], tick=1, baseline_kpis=BASE_KPIS)

    assert twin.call_count == 1


def test_uncertainty_guard_delays_then_rejects(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[twin_response(TINY_KPIS, margin=0.5)] * 2
    )
    arbitrator = Arbitrator(twin)
    proposal = make_proposal()

    guarded = arbitrator.decide_batch([proposal], tick=1, baseline_kpis=BASE_KPIS)
    first = guarded.decisions[0]
    assert first.action is ActionType.DELAY
    assert first.reason_code is ReasonCode.UNCERTAINTY_GUARD
    assert first.hold_count == 1

    forced = arbitrator.decide_batch(
        [make_proposal(command_id="forced", hold_count=3)],
        tick=4,
        baseline_kpis=BASE_KPIS,
    )
    final = forced.decisions[0]
    assert final.action is ActionType.REJECT
    assert final.reason_code is ReasonCode.UNCERTAINTY_GUARD


def test_twin_exception_rejects_with_timeout(make_proposal: Any) -> None:
    twin = MockTwinClient(raise_at_calls=[1])
    arbitrator = Arbitrator(twin)

    outcome = arbitrator.decide_batch([make_proposal()], tick=1, baseline_kpis=BASE_KPIS)

    decision = outcome.decisions[0]
    assert decision.action is ActionType.REJECT
    assert decision.reason_code is ReasonCode.TWIN_TIMEOUT
    assert decision.approved_value is None
    assert decision.delta_utility == pytest.approx(0.0)


def test_slow_twin_rejects_with_timeout(make_proposal: Any) -> None:
    twin = MockTwinClient(timeout_at_calls=[1], timeout_overshoot_s=0.02)
    arbitrator = Arbitrator(twin, ArbitrationConfig(twin_timeout_ms=1.0))

    outcome = arbitrator.decide_batch([make_proposal()], tick=1, baseline_kpis=BASE_KPIS)

    assert outcome.decisions[0].reason_code is ReasonCode.TWIN_TIMEOUT


def test_pingpong_is_rejected_without_twin_call(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[twin_response(GOOD_KPIS), twin_response(GOOD_KPIS)]
    )
    arbitrator = Arbitrator(twin)
    controller = "ctrl-es"

    arbitrator.decide_batch(
        [make_proposal(command_id="p1", controller_id=controller, target_cell_id="cell-a")],
        tick=1,
        baseline_kpis=BASE_KPIS,
    )
    arbitrator.decide_batch(
        [make_proposal(command_id="p2", controller_id=controller, target_cell_id="cell-b")],
        tick=2,
        baseline_kpis=BASE_KPIS,
    )
    reversing = make_proposal(
        command_id="p3", controller_id=controller, target_cell_id="cell-a"
    )
    outcome = arbitrator.decide_batch([reversing], tick=3, baseline_kpis=BASE_KPIS)

    decision = outcome.decisions[0]
    assert decision.action is ActionType.REJECT
    assert decision.reason_code is ReasonCode.CYCLIC_PINGPONG
    assert decision.twin_eval_count == 0
    assert twin.call_count == 2


def test_emergency_lane_bypasses_arbitration(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)])
    arbitrator = Arbitrator(twin)
    normal = make_proposal(command_id="normal")
    urgent = make_proposal(
        command_id="urgent",
        emergency_flag=True,
        controller_id="ctrl-ps",
        requested_value=46.0,
    )

    outcome = arbitrator.decide_batch([normal, urgent], tick=1, baseline_kpis=BASE_KPIS)

    assert twin.call_count == 1
    assert len(outcome.emergency_decisions) == 1
    emergency = outcome.emergency_decisions[0]
    assert emergency.action is ActionType.APPROVE
    assert emergency.reason_code is ReasonCode.EMERGENCY_OVERRIDE
    assert emergency.approved_value == pytest.approx(46.0)
    assert emergency.twin_eval_count == 0
    assert [decision.command_id for decision in outcome.decisions] == ["normal", "urgent"]


def test_decide_consumes_queue_batch(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)])
    arbitrator = Arbitrator(twin)
    queue = ProposalQueue()
    queue.submit(make_proposal())

    outcome = arbitrator.decide(queue.drain(5), baseline_kpis=BASE_KPIS)

    assert isinstance(outcome, BatchOutcome)
    assert outcome.decisions[0].batch_tick == 5
    assert outcome.decision_for(outcome.decisions[0].command_id) is outcome.decisions[0]


def test_decision_metadata_from_injected_clock_and_ids(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)])
    arbitrator = Arbitrator(
        twin,
        id_factory=lambda: "decision-fixed",
        clock=lambda: 4242,
    )

    decision = arbitrator.decide_batch(
        [make_proposal()], tick=1, baseline_kpis=BASE_KPIS
    ).decisions[0]

    assert decision.decision_id == "decision-fixed"
    assert decision.decided_at_epoch_ms == 4242


def test_current_value_setters_anchor_the_search(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(WORSE_KPIS)])
    arbitrator = Arbitrator(twin)
    proposal = make_proposal(requested_value=40.0)
    arbitrator.set_current_value("cell-a", ParameterType.TX_POWER_DBM, 32.0)

    assert arbitrator.current_value(proposal) == pytest.approx(32.0)
    arbitrator.decide_batch([proposal], tick=1, baseline_kpis=BASE_KPIS)
    assert twin.last_deltas[0]["from_value"] == pytest.approx(32.0)

    arbitrator.set_current_values({})
    assert arbitrator.current_value(proposal) is None


def test_all_proposals_receive_exactly_one_decision(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[
            twin_response(GOOD_KPIS),
            twin_response(BREACH_KPIS, floors=False),
            twin_response(GOOD_KPIS),
        ]
    )
    arbitrator = Arbitrator(twin)
    proposals = [
        make_proposal(command_id="c1", target_cell_id="cell-x"),
        make_proposal(command_id="c2", target_cell_id="cell-y", controller_id="ctrl-lb"),
        make_proposal(command_id="c3", target_cell_id="cell-z", controller_id="ctrl-cc"),
        make_proposal(command_id="c4", target_cell_id="cell-w", emergency_flag=True),
    ]

    outcome = arbitrator.decide_batch(proposals, tick=1, baseline_kpis=BASE_KPIS)

    assert [decision.command_id for decision in outcome.decisions] == [
        "c1",
        "c2",
        "c3",
        "c4",
    ]
    assert len({decision.decision_id for decision in outcome.decisions}) == 4


def test_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        ArbitrationConfig(max_twin_evaluations=0)
    with pytest.raises(ValueError):
        ArbitrationConfig(max_hold_ticks=0)
    with pytest.raises(ValueError):
        ArbitrationConfig(max_workers=0)
