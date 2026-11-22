"""Decision-ladder tests: 2 AM scenario, guards, budget and starvation."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from arbiter.engine.contracts import (
    ActionType,
    CommandProposal,
    ReasonCode,
    UtilityWeights,
)
from arbiter.engine.ladder import (
    DecisionLadderEngine,
    SafetyFloorEnforcer,
    UncertaintyGuard,
    build_candidate_delta,
)
from arbiter.engine.mocks import MockDigitalTwinClient, ScriptedTwin
from arbiter.engine.scripted_twin import make_scripted_twin
from arbiter.engine.scripted_twin import scenario_kpis as _scenario_kpis
from arbiter.queue import QueueOrchestrator

HEX = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}

WEIGHTS = UtilityWeights()


def _scenario_twin() -> ScriptedTwin:
    """The analytical toy twin (owned by arbiter/engine/scripted_twin.py)."""
    return make_scripted_twin(warn=False)


def _two_am_commands(make_proposal: Any) -> List[Any]:
    energy_saver = make_proposal(
        command_id="es",
        controller="ctrl-es",
        target_cell="B",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
        is_binary=True,
        created_tick=0,
    )
    load_balancer = make_proposal(
        command_id="lb",
        controller="ctrl-lb",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from="A",
        handover_to="B",
        created_tick=1,
    )
    coverage = make_proposal(
        command_id="cc",
        controller="ctrl-cc",
        target_cell="A",
        parameter="TX_POWER_DBM",
        current_value=30.0,
        requested_value=33.0,
        created_tick=2,
    )
    return [energy_saver, load_balancer, coverage]


def _by_id(decisions: Any) -> Dict[str, Any]:
    return {decision.command_id: decision for decision in decisions}


def test_two_am_collision_end_to_end(make_proposal: Any) -> None:
    twin = _scenario_twin()
    orchestrator = QueueOrchestrator(twin=twin, adjacency=HEX, max_workers=2)
    for command in _two_am_commands(make_proposal):
        assert orchestrator.submit(command) is True

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=1000)

    decisions = _by_id(result["decisions"])
    assert set(decisions) == {"es", "lb", "cc"}

    assert decisions["es"].action is ActionType.DELAY
    assert decisions["es"].hold_count == 1

    assert decisions["lb"].action is ActionType.MODIFY
    assert decisions["lb"].reason_code is ReasonCode.HEADROOM
    assert decisions["lb"].approved_value == pytest.approx(0.1528, abs=0.01)

    assert decisions["cc"].action is ActionType.MODIFY
    assert decisions["cc"].approved_value == pytest.approx(31.146, abs=0.2)

    assert twin.call_count <= 6
    assert orchestrator.latency_p95_ms() < 100.0


def test_switch_off_is_never_delayed_past_the_budget(make_proposal: Any) -> None:
    twin = _scenario_twin()
    orchestrator = QueueOrchestrator(twin=twin, adjacency=HEX)
    for command in _two_am_commands(make_proposal):
        orchestrator.submit(command)

    actions: List[ActionType] = []
    holds: List[int] = []
    for tick in range(1, 5):
        result = orchestrator.on_tick(current_tick=tick, timestamp_epoch_ms=tick * 100)
        decisions = _by_id(result["decisions"])
        assert "es" in decisions
        actions.append(decisions["es"].action)
        holds.append(decisions["es"].hold_count)

    assert actions[:3] == [ActionType.DELAY, ActionType.DELAY, ActionType.DELAY]
    assert actions[3] in (ActionType.APPROVE, ActionType.REJECT)
    assert holds[3] == 3


def test_uncertainty_guard_delays_then_rejects(make_proposal: Any) -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.9501}, confidence_margin=0.5)
    engine = DecisionLadderEngine()
    command = make_proposal(command_id="u1")

    guarded = engine.evaluate([command], None, twin, WEIGHTS, 1, 0)[0]
    assert guarded.action is ActionType.DELAY
    assert guarded.reason_code is ReasonCode.UNCERTAINTY_GUARD
    assert guarded.hold_count == 1

    forced = make_proposal(command_id="u2", hold_count=3, must_decide=True)
    rejected = engine.evaluate([forced], None, twin, WEIGHTS, 2, 0)[0]
    assert rejected.action is ActionType.REJECT
    assert rejected.reason_code is ReasonCode.UNCERTAINTY_GUARD


def test_floor_breach_rejects_when_not_transient(make_proposal: Any) -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.80})
    engine = DecisionLadderEngine()

    decision = engine.evaluate([make_proposal()], None, twin, WEIGHTS, 1, 0)[0]

    assert decision.action is ActionType.REJECT
    assert decision.reason_code is ReasonCode.FLOOR_BREACH
    assert decision.approved_value is None


def test_twin_failure_rejects_with_timeout(make_proposal: Any) -> None:
    twin = MockDigitalTwinClient(latency_ms=500.0, raise_on_timeout=True)
    engine = DecisionLadderEngine()

    decisions = engine.evaluate([make_proposal()], None, twin, WEIGHTS, 1, 0)

    assert len(decisions) == 1
    assert decisions[0].action is ActionType.REJECT
    assert decisions[0].reason_code is ReasonCode.TWIN_TIMEOUT
    assert decisions[0].approved_value is None


def test_binary_command_skips_modify(make_proposal: Any) -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.85})
    engine = DecisionLadderEngine()
    command = make_proposal(
        command_id="off",
        target_cell="B",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
        is_binary=True,
    )

    decision = engine.evaluate([command], None, twin, WEIGHTS, 1, 0)[0]

    assert decision.action is not ActionType.MODIFY
    assert twin.call_count == 2  # baseline + joint probe only


def test_component_budget_is_never_exceeded(make_proposal: Any) -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.93})
    engine = DecisionLadderEngine()
    component = [
        make_proposal(command_id="p1", target_cell="A", requested_value=33.0),
        make_proposal(
            command_id="p2",
            target_cell="B",
            parameter="TILT_DEG",
            current_value=4.0,
            requested_value=2.0,
            created_tick=1,
        ),
    ]

    decisions = engine.evaluate(component, None, twin, WEIGHTS, 1, 0)

    assert twin.call_count <= 6
    assert all(decision.twin_eval_count <= 6 for decision in decisions)
    assert {decision.command_id for decision in decisions} == {"p1", "p2"}


def test_pingpong_is_rejected_without_twin_call(make_proposal: Any) -> None:
    twin = _scenario_twin()
    orchestrator = QueueOrchestrator(twin=twin, adjacency=HEX)

    outbound = make_proposal(
        command_id="h-ab",
        controller="ctrl-lb",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.2,
        handover_from="A",
        handover_to="B",
    )
    orchestrator.submit(outbound)
    first = _by_id(orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)["decisions"])
    assert first["h-ab"].action is ActionType.APPROVE
    calls_after_first = twin.call_count

    inbound = make_proposal(
        command_id="h-ba",
        controller="ctrl-lb",
        target_cell="B",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.2,
        handover_from="B",
        handover_to="A",
        created_tick=1,
    )
    orchestrator.submit(inbound)
    second = _by_id(orchestrator.on_tick(current_tick=2, timestamp_epoch_ms=200)["decisions"])

    assert second["h-ba"].action is ActionType.REJECT
    assert second["h-ba"].reason_code is ReasonCode.CYCLIC_PINGPONG
    assert second["h-ba"].twin_eval_count == 0
    assert twin.call_count == calls_after_first


def test_emergency_never_reaches_the_ladder(make_proposal: Any) -> None:
    twin = MockDigitalTwinClient()
    engine = DecisionLadderEngine()
    urgent = make_proposal(command_id="urgent", is_emergency=True)

    decision = engine.evaluate([urgent], None, twin, WEIGHTS, 1, 0)[0]

    assert decision.action is ActionType.APPROVE
    assert decision.reason_code is ReasonCode.EMERGENCY_OVERRIDE
    assert decision.delta_utility == pytest.approx(0.0)
    assert twin.call_count == 0


def test_emergency_bypasses_the_orchestrator(make_proposal: Any) -> None:
    orchestrator = QueueOrchestrator(twin=MockDigitalTwinClient(), adjacency=HEX)
    orchestrator.submit(make_proposal(command_id="urgent", is_emergency=True))

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)

    assert result["decisions_count"] == 0
    assert orchestrator.queue.emergency_lane.audit_count == 1
    record = orchestrator.queue.emergency_lane.peek_audit()[0]
    assert record.decision.reason_code is ReasonCode.EMERGENCY_OVERRIDE


def test_empty_orchestrator_reports_zero_latency() -> None:
    orchestrator = QueueOrchestrator(twin=MockDigitalTwinClient())
    assert orchestrator.latency_p95_ms() == pytest.approx(0.0)


def test_decisions_carry_required_metadata(make_proposal: Any) -> None:
    twin = _scenario_twin()
    engine = DecisionLadderEngine(id_factory=lambda: "fixed-id", clock=lambda: 7)
    decision = engine.evaluate([make_proposal()], None, twin, WEIGHTS, 3, 1234)[0]

    assert decision.decision_id == "fixed-id"
    assert decision.batch_tick == 3
    assert decision.decided_at_epoch_ms == 1234
    assert decision.hold_count == 0
    assert isinstance(decision.delta_utility, float)


def test_active_ho_limits_produce_active_ho_reason(make_proposal: Any) -> None:
    class HoTwin(ScriptedTwin):
        """Scripted twin that reports active handovers as the limiting factor."""

        def evaluate(self, candidate: Any, timeout_budget_ms: float = 15.0) -> Any:
            response = super().evaluate(candidate, timeout_budget_ms)
            response["limiting_factor"] = "active_ho"
            return response

    engine = DecisionLadderEngine()
    ho_twin = HoTwin(_scenario_kpis)
    command = make_proposal(
        command_id="ho",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from="A",
        handover_to="B",
    )

    decision = engine.evaluate([command], None, ho_twin, WEIGHTS, 1, 0)[0]

    assert decision.action is ActionType.MODIFY
    assert decision.reason_code is ReasonCode.ACTIVE_HO


# -- guard units --------------------------------------------------------------


def test_safety_floor_enforcer_reports_breaches() -> None:
    enforcer = SafetyFloorEnforcer()
    healthy = {"coverage_ratio": 0.95, "drop_rate_ratio": 0.01}
    starved = {"coverage_ratio": 0.80, "drop_rate_ratio": 0.01}

    assert enforcer.floors_ok(healthy) is True
    assert enforcer.breaches(healthy) is False
    assert enforcer.breaches(starved) is True
    assert enforcer.floors_ok(healthy, twin_floors_satisfied=False) is False


def test_uncertainty_guard_configuration() -> None:
    with pytest.raises(ValueError):
        UncertaintyGuard(default_margin=-0.1)

    guard = UncertaintyGuard(default_margin=0.05)
    assert guard.margin_for(None) == pytest.approx(0.05)
    assert guard.margin_for({}) == pytest.approx(0.05)
    assert guard.margin_for({"confidence_margin": 0.2}) == pytest.approx(0.2)
    assert guard.margin_for({"confidence_margin": "junk"}) == pytest.approx(0.05)
    assert guard.is_uncertain(0.01, 0.05) is True
    assert guard.is_uncertain(0.5, 0.05) is False


def test_engine_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        DecisionLadderEngine(max_twin_evals=0)
    with pytest.raises(ValueError):
        DecisionLadderEngine(max_hold_ticks=0)


def test_engine_exposes_configuration() -> None:
    engine = DecisionLadderEngine(max_twin_evals=4)

    assert engine.max_twin_evals == 4
    assert engine.cycle_detector is not None


def test_candidate_delta_payload() -> None:
    command = CommandProposal.model_validate({
        "command_id": "c",
        "controller": "ctrl",
        "controller_type": "COVERAGE_CAPACITY",
        "target_cell": "A",
        "timestamp_epoch_ms": 0,
        "parameter": "TX_POWER_DBM",
        "current_value": 30.0,
        "requested_value": 33.0,
        "created_tick": 0,
    })

    assert build_candidate_delta(command, 31.0) == {
        "command_id": "c",
        "cell_id": "A",
        "parameter": "TX_POWER_DBM",
        "from_value": 30.0,
        "to_value": 31.0,
    }


def test_empty_component_returns_no_decisions() -> None:
    engine = DecisionLadderEngine()
    twin = MockDigitalTwinClient()

    assert engine.evaluate([], None, twin, WEIGHTS, 0, 0) == []
    assert twin.call_count == 0


def test_fully_cyclic_component_skips_the_twin(make_proposal: Any) -> None:
    engine = DecisionLadderEngine()
    twin = MockDigitalTwinClient()
    outbound = make_proposal(
        command_id="a",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        requested_value=0.2,
        handover_from="A",
        handover_to="B",
    )
    engine.record_handover(outbound, 1)

    reversal = make_proposal(
        command_id="b",
        target_cell="B",
        parameter="OFFLOAD_RATIO",
        requested_value=0.2,
        handover_from="B",
        handover_to="A",
        created_tick=1,
    )
    decision = engine.evaluate([reversal], None, twin, WEIGHTS, 2, 0)[0]

    assert decision.action is ActionType.REJECT
    assert decision.reason_code is ReasonCode.CYCLIC_PINGPONG
    assert twin.call_count == 0


def test_engine_never_raises_on_internal_failure(make_proposal: Any) -> None:
    class ExplodingWeights:
        @property
        def w1(self) -> float:
            raise RuntimeError("boom")

        @property
        def w2(self) -> float:
            raise RuntimeError("boom")

        @property
        def w3(self) -> float:
            raise RuntimeError("boom")

        @property
        def w4(self) -> float:
            raise RuntimeError("boom")

    engine = DecisionLadderEngine()
    decisions = engine.evaluate(
        [make_proposal()], None, MockDigitalTwinClient(), ExplodingWeights(), 1, 0
    )

    assert len(decisions) == 1
    assert decisions[0].action is ActionType.REJECT
    assert decisions[0].reason_code is ReasonCode.TWIN_TIMEOUT


# -- twin failure modes -------------------------------------------------------


def test_explicit_twin_timeout_is_contained(make_proposal: Any) -> None:
    class RaisingTwin(MockDigitalTwinClient):
        def evaluate(self, candidate_deltas: Any, timeout_budget_ms: float = 15.0) -> Any:
            raise TimeoutError("too slow")

    decision = DecisionLadderEngine().evaluate(
        [make_proposal()], None, RaisingTwin(), WEIGHTS, 1, 0
    )[0]

    assert decision.action is ActionType.REJECT
    assert decision.reason_code is ReasonCode.TWIN_TIMEOUT


def test_reported_computation_time_over_budget_is_rejected(make_proposal: Any) -> None:
    class SlowReportingTwin(MockDigitalTwinClient):
        def evaluate(self, candidate_deltas: Any, timeout_budget_ms: float = 15.0) -> Any:
            response = super().evaluate(candidate_deltas, timeout_budget_ms)
            response["computation_time_ms"] = 5000
            return response

    decision = DecisionLadderEngine().evaluate(
        [make_proposal()], None, SlowReportingTwin(), WEIGHTS, 1, 0
    )[0]

    assert decision.reason_code is ReasonCode.TWIN_TIMEOUT


def test_unparsable_computation_time_is_ignored(make_proposal: Any) -> None:
    class OddReportingTwin(ScriptedTwin):
        def evaluate(self, candidate_deltas: Any, timeout_budget_ms: float = 15.0) -> Any:
            response = super().evaluate(candidate_deltas, timeout_budget_ms)
            response["computation_time_ms"] = "n/a"
            return response

    command = make_proposal(
        command_id="ho",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.2,
        handover_from="A",
        handover_to="B",
    )
    decision = DecisionLadderEngine().evaluate(
        [command], None, OddReportingTwin(_scenario_kpis), WEIGHTS, 1, 0
    )[0]

    assert decision.action is ActionType.APPROVE


def test_malformed_payload_is_rejected(make_proposal: Any) -> None:
    class JunkTwin:
        def evaluate(self, candidate_deltas: Any, timeout_budget_ms: float = 15.0) -> Any:
            return {"predicted_kpis": "not-a-mapping"}

    decision = DecisionLadderEngine().evaluate(
        [make_proposal()], None, JunkTwin(), WEIGHTS, 1, 0
    )[0]

    assert decision.reason_code is ReasonCode.TWIN_TIMEOUT


def test_missing_floor_flag_falls_back_to_kpis(make_proposal: Any) -> None:
    class FlaglessTwin(ScriptedTwin):
        def evaluate(self, candidate_deltas: Any, timeout_budget_ms: float = 15.0) -> Any:
            response = super().evaluate(candidate_deltas, timeout_budget_ms)
            response.pop("safety_floors_satisfied", None)
            return response

    command = make_proposal(
        command_id="ho",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.2,
        handover_from="A",
        handover_to="B",
    )
    decision = DecisionLadderEngine().evaluate(
        [command], None, FlaglessTwin(_scenario_kpis), WEIGHTS, 1, 0
    )[0]

    assert decision.action is ActionType.APPROVE


def test_active_handover_users_count_as_active_ho(make_proposal: Any) -> None:
    class UsersTwin(ScriptedTwin):
        def evaluate(self, candidate_deltas: Any, timeout_budget_ms: float = 15.0) -> Any:
            response = super().evaluate(candidate_deltas, timeout_budget_ms)
            response["active_handover_users"] = 2
            return response

    command = make_proposal(
        command_id="ho",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from="A",
        handover_to="B",
    )
    decision = DecisionLadderEngine().evaluate(
        [command], None, UsersTwin(_scenario_kpis), WEIGHTS, 1, 0
    )[0]

    assert decision.action is ActionType.MODIFY
    assert decision.reason_code is ReasonCode.ACTIVE_HO


# -- budget and ordering ------------------------------------------------------


def test_exhausted_budget_never_overspends(make_proposal: Any) -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.93})
    engine = DecisionLadderEngine(max_twin_evals=2)

    decision = engine.evaluate([make_proposal()], None, twin, WEIGHTS, 1, 0)[0]

    assert twin.call_count == 2
    assert decision.action in (ActionType.REJECT, ActionType.DELAY)
    assert decision.twin_eval_count == 0
