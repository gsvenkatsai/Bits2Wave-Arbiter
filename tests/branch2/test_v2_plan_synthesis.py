"""Unit and scenario tests for ARBITER v2 Plan Synthesis, Uncertainty Guard, and Dynamic Weights.

Covers:
1. WEIGHTSETTABLE: set_weights(RiskDial) / get_weights() thread-safe, changing
   weights changes decisions in borderline cases.
2. UNCERTAINTY GUARD: TwinPredictionV2 interval worst-case floor screening,
   handling of wide intervals, uncalibrated conservative mode.
3. PLAN SYNTHESIS: 2 AM collision multi-step arbitration plan structure,
   preconditions, deferred execution, plan expiration, and persistence.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from arbiter.detector.graph import ConflictGraphBuilder
from arbiter.engine.contracts import (
    MAX_HOLD_TICKS,
    ActionType,
    ReasonCode,
)
from arbiter.engine.ladder import DecisionLadderEngine, UncertaintyGuard
from arbiter.engine.mocks.mock_actuator_client import MockActuatorClient
from arbiter.engine.mocks.mock_twin_client import MockDigitalTwinClient, ScriptedTwin
from arbiter.engine.plan_executor import (
    PlanExecutor,
    get_plan,
    list_plans,
    set_default_executor,
)
from arbiter.engine.planner import ArbitrationPlanner, evaluate_precondition
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.shared.models import ParameterType
from arbiter.shared.v2_contracts import (
    ArbitrationPlan,
    PlanStep,
    PredictionInterval,
    RiskDial,
    TwinPredictionV2,
    WeightSettable,
)
from tests.branch2.test_decision_ladder import _scenario_twin, _two_am_commands

HEX = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}


# ---------------------------------------------------------------------------
# Test 1: WeightSettable Protocol and Thread-Safety
# ---------------------------------------------------------------------------
def test_weight_settable_implements_protocol_and_thread_safety() -> None:
    """Verify DecisionLadderEngine and QueueOrchestrator implement WeightSettable."""
    engine = DecisionLadderEngine()
    assert isinstance(engine, WeightSettable)

    orchestrator = QueueOrchestrator(engine=engine)
    assert isinstance(orchestrator, WeightSettable)

    # Initial defaults match shared constants
    default_dial = engine.get_weights()
    assert default_dial.weight_coverage == pytest.approx(0.3)
    assert default_dial.weight_energy == pytest.approx(0.2)
    assert default_dial.weight_interference == pytest.approx(0.2)
    assert default_dial.weight_drop_rate == pytest.approx(0.3)

    # Set new weights via RiskDial
    energy_dial = RiskDial(
        weight_coverage=0.2,
        weight_energy=0.4,
        weight_interference=0.2,
        weight_drop_rate=0.2,
        label="energy_saver",
    )
    orchestrator.set_weights(energy_dial)

    active_dial = orchestrator.get_weights()
    assert active_dial.weight_energy == pytest.approx(0.4)
    assert active_dial.label == "energy_saver"
    assert engine.get_weights().weight_energy == pytest.approx(0.4)

    # Thread-safety under concurrent set/get
    errors: list[Exception] = []

    def mutate_weights(val: float) -> None:
        try:
            for _ in range(50):
                d = RiskDial(
                    weight_coverage=val,
                    weight_energy=1.0 - val - 0.4,
                    weight_interference=0.2,
                    weight_drop_rate=0.2,
                    label="concurrent",
                )
                engine.set_weights(d)
                curr = engine.get_weights()
                assert curr.total == pytest.approx(1.0)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=mutate_weights, args=(0.1 + i * 0.05,))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


# ---------------------------------------------------------------------------
# Test 2: Weight change flips decision in known borderline case
# ---------------------------------------------------------------------------
def test_changing_weights_changes_decision_in_borderline_case(make_proposal: Any) -> None:
    """Verify changing weights alters decision in a borderline trade-off case."""
    # Action trades a slight drop in coverage (0.95 -> 0.915, floor=0.90)
    # for high energy savings (0.50 -> 0.20)
    def borderline_kpis(candidate: list[dict[str, Any]]) -> dict[str, float]:
        if not candidate:
            return {
                "coverage_ratio": 0.95,
                "normalized_energy": 0.50,
                "normalized_interference": 0.30,
                "drop_rate_ratio": 0.005,
            }
        return {
            "coverage_ratio": 0.915,
            "normalized_energy": 0.20,
            "normalized_interference": 0.30,
            "drop_rate_ratio": 0.005,
        }

    twin = ScriptedTwin(borderline_kpis)
    engine = DecisionLadderEngine()

    cmd = make_proposal(
        command_id="cmd-borderline",
        controller="ctrl-es",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.2,
        is_binary=False,
    )

    # Dial 1: Energy Priority -> dU is positive -> MODIFY or APPROVE
    energy_dial = RiskDial(
        weight_coverage=0.1,
        weight_energy=0.7,
        weight_interference=0.1,
        weight_drop_rate=0.1,
        label="energy_heavy",
    )
    engine.set_weights(energy_dial)
    decisions_energy = engine.evaluate(
        component=[cmd],
        graph=None,
        twin=twin,
        tick=1,
        now_ms=1000,
    )
    assert decisions_energy[0].action in (ActionType.APPROVE, ActionType.MODIFY)

    # Dial 2: Coverage Strict Priority -> dU is negative -> REJECT/DELAY
    coverage_dial = RiskDial(
        weight_coverage=0.85,
        weight_energy=0.05,
        weight_interference=0.05,
        weight_drop_rate=0.05,
        label="coverage_heavy",
    )
    engine.set_weights(coverage_dial)
    decisions_coverage = engine.evaluate(
        component=[cmd],
        graph=None,
        twin=twin,
        tick=2,
        now_ms=2000,
    )
    assert decisions_coverage[0].action in (ActionType.DELAY, ActionType.REJECT)


# ---------------------------------------------------------------------------
# Test 3: Uncertainty Guard with wide interval triggers UNCERTAINTY_GUARD
# ---------------------------------------------------------------------------
def test_uncertainty_guard_wide_interval_returns_uncertainty_guard(make_proposal: Any) -> None:
    """When interval worst-case breaches floor while mean passes,
    guard returns UNCERTAINTY_GUARD."""
    # Mean drop rate is 0.012 (passing <= 0.02), but upper bound is 0.028 (breaching floor > 0.02)
    pred_v2 = TwinPredictionV2(
        candidate_id="cand-wide",
        intervals=[
            PredictionInterval(
                kpi="coverage_ratio",
                mean=0.94,
                lower=0.91,
                upper=0.96,
                coverage=0.90,
            ),
            PredictionInterval(
                kpi="drop_rate_ratio",
                mean=0.012,
                lower=0.005,
                upper=0.028,
                coverage=0.90,
            ),
            PredictionInterval(
                kpi="normalized_energy",
                mean=0.45,
                lower=0.40,
                upper=0.50,
                coverage=0.90,
            ),
            PredictionInterval(
                kpi="normalized_interference",
                mean=0.28,
                lower=0.25,
                upper=0.32,
                coverage=0.90,
            ),
        ],
        calibrated=True,
        n_calibration=50,
    )

    class TwinWithIntervals:
        def evaluate(
            self,
            candidate_deltas: list[dict[str, Any]],
            timeout_budget_ms: float = 15.0,
        ) -> dict[str, Any]:
            return {
                "evaluation_id": "eval-interval-1",
                "predicted_kpis": {
                    "coverage_ratio": 0.94,
                    "normalized_energy": 0.45,
                    "normalized_interference": 0.28,
                    "drop_rate_ratio": 0.012,
                },
                "safety_floors_satisfied": True,
                "prediction_v2": pred_v2,
            }

    twin = TwinWithIntervals()
    engine = DecisionLadderEngine()

    cmd = make_proposal(
        command_id="cmd-guard",
        controller="ctrl-es",
        target_cell="B",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
        is_binary=True,
    )

    decisions = engine.evaluate([cmd], graph=None, twin=twin, tick=1, now_ms=1000)
    assert len(decisions) == 1
    decision = decisions[0]

    # Binary sleep cannot modify; hold budget allows DELAY with UNCERTAINTY_GUARD
    assert decision.action is ActionType.DELAY
    assert decision.reason_code is ReasonCode.UNCERTAINTY_GUARD


# ---------------------------------------------------------------------------
# Test 4: Uncertainty Guard conservative on uncalibrated twin
# ---------------------------------------------------------------------------
def test_uncertainty_guard_uncalibrated_twin_is_conservative() -> None:
    """An uncalibrated twin causes intervals to be widened conservatively."""
    guard = UncertaintyGuard()

    # Borderline interval: upper drop rate is 0.018 (< 0.02 floor) when calibrated
    interval = PredictionInterval(
        kpi="drop_rate_ratio",
        mean=0.014,
        lower=0.010,
        upper=0.018,
        coverage=0.90,
    )

    calibrated_pred = TwinPredictionV2(
        candidate_id="cand-calibrated",
        intervals=[interval],
        calibrated=True,
        n_calibration=100,
    )
    mean_ok_cal, worst_ok_cal = guard.check_intervals(calibrated_pred)
    assert mean_ok_cal is True
    assert worst_ok_cal is True  # 0.018 <= 0.02 passes

    # Same interval but uncalibrated -> widened upper bound breaches 0.02
    uncalibrated_pred = TwinPredictionV2(
        candidate_id="cand-uncalibrated",
        intervals=[interval],
        calibrated=False,
        n_calibration=0,
    )
    mean_ok_uncal, worst_ok_uncal = guard.check_intervals(uncalibrated_pred)
    assert mean_ok_uncal is True
    assert worst_ok_uncal is False  # Widened > 0.02 breaches floor!


# ---------------------------------------------------------------------------
# Test 5: Plan Synthesis for 2 AM collision produces expected steps
# ---------------------------------------------------------------------------
def test_plan_synthesis_2am_collision_structure(make_proposal: Any) -> None:
    """Verify ArbitrationPlan for 2 AM collision contains LB, CC, and sleep steps."""
    planner = ArbitrationPlanner(window_ticks=MAX_HOLD_TICKS)
    commands = _two_am_commands(make_proposal)
    twin = _scenario_twin()

    engine = DecisionLadderEngine()
    builder = ConflictGraphBuilder()
    graph = builder.build(commands, HEX)

    decisions = engine.evaluate(commands, graph=graph, twin=twin, tick=1, now_ms=1000)

    plan = planner.build_plan(commands, decisions, current_tick=1, twin=twin)
    assert plan is not None
    assert plan.source_command_id == "es"
    assert plan.created_tick == 1
    assert plan.expires_tick == 1 + MAX_HOLD_TICKS

    assert len(plan.steps) == 3

    # Step 0: LB offload
    step0 = plan.steps[0]
    assert step0.step_index == 0
    assert step0.tick_offset == 0
    assert step0.target_cell_id in ("A", "cell_A")
    assert step0.parameter == ParameterType.OFFLOAD_RATIO
    assert step0.value == pytest.approx(0.1528, abs=0.02)
    assert step0.precondition == ""

    # Step 1: CC boost
    step1 = plan.steps[1]
    assert step1.step_index == 1
    assert step1.tick_offset == 0
    assert step1.target_cell_id in ("A", "cell_A")
    assert step1.parameter == ParameterType.TX_POWER_DBM
    assert step1.value == pytest.approx(31.146, abs=0.5)

    # Step 2: ES sleep with precondition
    step2 = plan.steps[2]
    assert step2.step_index == 2
    assert step2.target_cell_id in ("B", "cell_B")
    assert step2.parameter == ParameterType.SLEEP_STATE
    assert step2.value == 1.0
    assert "load(" in step2.precondition
    assert "no_active_handover(" in step2.precondition


# ---------------------------------------------------------------------------
# Test 6: Precondition evaluator logic
# ---------------------------------------------------------------------------
def test_evaluate_precondition_various_scenarios() -> None:
    """Test safe parsing and evaluation of multi-condition guard clauses."""
    # Context with Cell A load high and Cell B active handovers
    ctx1 = {
        "cell_A": {"load": 0.75, "sinr": 12.0},
        "cell_B": {"load": 0.20, "active_handover_users": 2},
    }
    expr = "load(cell_A)<0.60 and load(cell_B)<0.15 and no_active_handover(cell_B)"
    assert evaluate_precondition(expr, ctx1) is False

    # Load on Cell A falls, but Cell B still has active handovers
    ctx2 = {
        "cell_A": {"load": 0.50, "sinr": 12.0},
        "cell_B": {"load": 0.10, "active_handover_users": 1},
    }
    assert evaluate_precondition(expr, ctx2) is False

    # Preconditions fully met: load low, zero handovers
    ctx3 = {
        "cell_A": {"load": 0.55, "sinr": 12.0},
        "cell_B": {"load": 0.12, "active_handover_users": 0},
    }
    assert evaluate_precondition(expr, ctx3) is True


# ---------------------------------------------------------------------------
# Test 7: Sleep executes only after preconditions hold
# ---------------------------------------------------------------------------
def test_sleep_executes_only_after_preconditions_hold(tmp_path: Path) -> None:
    """Verify deferred step is held until precondition passes, then executes."""
    db_file = str(tmp_path / "test_plan_exec.db")
    actuator = MockActuatorClient()
    twin = MockDigitalTwinClient()

    executor = PlanExecutor(actuator=actuator, twin=twin, db_path=db_file)

    plan = ArbitrationPlan(
        plan_id="plan-test-staged",
        source_command_id="cmd-es",
        created_tick=1,
        expires_tick=5,
        steps=[
            PlanStep(
                step_index=0,
                tick_offset=0,
                target_cell_id="B",
                parameter=ParameterType.OFFLOAD_RATIO,
                value=0.15,
                precondition="",
                status="PENDING",
            ),
            PlanStep(
                step_index=1,
                tick_offset=1,
                target_cell_id="B",
                parameter=ParameterType.SLEEP_STATE,
                value=1.0,
                precondition="load(A)<0.60 and no_active_handover(B)",
                status="PENDING",
            ),
        ],
        rationale="Staged test plan",
    )
    executor.submit_plan(plan)

    # Tick 1: step 0 executes; step 1 is tick_offset 1 so not evaluated
    res1 = executor.advance(
        current_tick=1,
        state_context={"A": {"load": 0.80}, "B": {"active_handover_users": 2}},
    )
    assert any(r.step_index == 0 and r.action == "EXECUTED" for r in res1)
    assert not any(r.step_index == 1 and r.action == "EXECUTED" for r in res1)

    # Tick 2: tick_offset reached, but precondition fails (load=0.70 > 0.60) -> DEFERRED
    res2 = executor.advance(
        current_tick=2,
        state_context={"A": {"load": 0.70}, "B": {"active_handover_users": 0}},
    )
    assert any(r.step_index == 1 and r.action == "DEFERRED" for r in res2)

    # Tick 3: precondition met (load=0.55 < 0.60, no handovers) -> EXECUTED!
    res3 = executor.advance(
        current_tick=3,
        state_context={"A": {"load": 0.55}, "B": {"active_handover_users": 0}},
    )
    assert any(r.step_index == 1 and r.action == "EXECUTED" for r in res3)

    stored_plan = executor.get_plan("plan-test-staged")
    assert stored_plan is not None
    assert all(s.status == "COMPLETED" for s in stored_plan.steps)


# ---------------------------------------------------------------------------
# Test 8: Expired plans are dropped when preconditions never hold
# ---------------------------------------------------------------------------
def test_expired_plans_are_dropped(tmp_path: Path) -> None:
    """When expires_tick is reached with precondition unsatisfied, step is ABORTED."""
    db_file = str(tmp_path / "test_plan_drop.db")
    actuator = MockActuatorClient()
    executor = PlanExecutor(actuator=actuator, db_path=db_file)

    plan = ArbitrationPlan(
        plan_id="plan-expire-me",
        source_command_id="cmd-es",
        created_tick=1,
        expires_tick=3,
        steps=[
            PlanStep(
                step_index=0,
                tick_offset=1,
                target_cell_id="B",
                parameter=ParameterType.SLEEP_STATE,
                value=1.0,
                precondition="load(A)<0.60",
                status="PENDING",
            )
        ],
        rationale="Expiring plan",
    )
    executor.submit_plan(plan)

    # Tick 2: load high -> DEFERRED
    res2 = executor.advance(current_tick=2, state_context={"A": {"load": 0.90}})
    assert any(r.action == "DEFERRED" for r in res2)

    # Tick 3: expires_tick reached -> ABORTED / dropped
    res3 = executor.advance(current_tick=3, state_context={"A": {"load": 0.90}})
    assert any(r.action == "ABORTED" for r in res3)

    stored = executor.get_plan("plan-expire-me")
    assert stored is not None
    assert stored.steps[0].status == "ABORTED"


# ---------------------------------------------------------------------------
# Test 9: Venkat's API list_plans and get_plan over v2_plans SQLite table
# ---------------------------------------------------------------------------
def test_plan_persistence_and_venkat_api(tmp_path: Path) -> None:
    """Verify plans persist to SQLite v2_plans and list_plans/get_plan retrieve them."""
    db_file = str(tmp_path / "test_venkat_api.db")
    executor = PlanExecutor(db_path=db_file)
    set_default_executor(executor)

    plan = ArbitrationPlan(
        plan_id="plan-venkat-001",
        source_command_id="cmd-source-42",
        created_tick=10,
        expires_tick=15,
        steps=[
            PlanStep(
                step_index=0,
                tick_offset=0,
                target_cell_id="cell_X",
                parameter=ParameterType.TX_POWER_DBM,
                value=44.0,
                precondition="",
                status="COMPLETED",
            )
        ],
        rationale="Plan for Venkat's API test",
    )
    executor.submit_plan(plan)

    # Direct query of SQLite database v2_plans table
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        "SELECT plan_id, source_command_id, payload FROM v2_plans WHERE plan_id = ?",
        ("plan-venkat-001",),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "plan-venkat-001"
    assert row[1] == "cmd-source-42"
    payload = json.loads(row[2])
    assert payload["plan_id"] == "plan-venkat-001"
    conn.close()

    # Public API query functions
    retrieved = get_plan("plan-venkat-001")
    assert retrieved is not None
    assert retrieved.source_command_id == "cmd-source-42"

    all_plans = list_plans()
    assert any(p.plan_id == "plan-venkat-001" for p in all_plans)


# ---------------------------------------------------------------------------
# Test 10: Zero regressions in existing 2 AM collision decisions
# ---------------------------------------------------------------------------
def test_no_regressions_in_existing_2am_decisions(make_proposal: Any) -> None:
    """Verify existing baseline decisions hold: ES DELAY, LB MODIFY ~15%, CC MODIFY +1 dB."""
    commands = _two_am_commands(make_proposal)
    twin = _scenario_twin()
    orchestrator = QueueOrchestrator(twin=twin, adjacency=HEX, max_workers=2)

    for cmd in commands:
        assert orchestrator.submit(cmd) is True

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=1000)
    by_id = {d.command_id: d for d in result["decisions"]}

    # ES gets DELAY (hold count 1)
    assert by_id["es"].action is ActionType.DELAY
    assert by_id["es"].hold_count == 1

    # LB offload bisected to approx 15% (0.1528)
    assert by_id["lb"].action is ActionType.MODIFY
    assert by_id["lb"].reason_code is ReasonCode.HEADROOM
    assert by_id["lb"].approved_value == pytest.approx(0.1528, abs=0.01)

    # CC power boost bisected to approx +1 dB (31.146 vs 30.0)
    assert by_id["cc"].action is ActionType.MODIFY
    assert by_id["cc"].approved_value == pytest.approx(31.146, abs=0.2)
