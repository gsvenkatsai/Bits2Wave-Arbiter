"""Comprehensive tests for ARBITER v2 simulator features.

Covers:
- Counterfactual engine (compute_counterfactual, KPI summary, explanation).
- Combinatorial optimality gap (grid evaluation, multi-seed aggregation).
- Automated collision discovery search (seeded search, worst gallery).
"""
from __future__ import annotations

import pytest

from arbiter.controllers.scenarios import trigger_2am_collision
from arbiter.engine.contracts import (
    CommandProposal as EngineProposal,
    UtilityWeights,
)
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.shared.models import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ControllerType,
    ParameterType,
    ReasonCode,
)
from arbiter.shared.v2_contracts import (
    CounterfactualResult,
    OptimalityResult,
    RiskDial,
)
from arbiter.simulator.collision_search import (
    _random_proposal,
    search_collisions,
)
from arbiter.simulator.counterfactual import (
    compute_counterfactual,
    compute_kpi_summary,
)
from arbiter.simulator.engine import SimulationEngine
from arbiter.simulator.optimality import (
    compute_optimality,
    run_multi_seed_optimality,
)
from tests.branch2.test_decision_ladder import _scenario_twin


def test_compute_counterfactual_2am_collision() -> None:
    """Counterfactual rollout shows ARBITER prevents blackout and drops."""
    engine = SimulationEngine(seed=42)
    for _ in range(6):
        engine.step()

    proposals = trigger_2am_collision(
        engine, base_ts_ms=engine.simulated_epoch_ms
    )
    adj = {
        cid: cell.config.neighbor_cell_ids
        for cid, cell in engine.cells.items()
    }
    orchestrator = QueueOrchestrator(
        twin=_scenario_twin(), adjacency=adj, max_workers=2
    )
    for p in proposals:
        orchestrator.submit(EngineProposal.model_validate(p.model_dump()))

    res = orchestrator.on_tick(
        current_tick=6, timestamp_epoch_ms=engine.simulated_epoch_ms
    )

    cf = compute_counterfactual(
        snapshot=engine,
        proposals=proposals,
        decisions=res["decisions"],
        horizon_ticks=10,
    )

    assert isinstance(cf, CounterfactualResult)
    assert cf.batch_tick == 6
    assert cf.horizon_ticks == 10
    # Coordinated ARBITER maintains 0 drops
    assert cf.with_arbiter.dropped_users == 0
    # Uncoordinated blackout drops users
    assert cf.without_arbiter.dropped_users > 0
    assert cf.diff["dropped_users"] < 0.0
    assert "dropped" in cf.explanation
    assert "mean SINR" in cf.explanation


def test_compute_counterfactual_from_simulation_snapshot() -> None:
    """compute_counterfactual accepts an opaque SimulationSnapshot."""
    engine = SimulationEngine(seed=42)
    for _ in range(6):
        engine.step()
    snap = engine.snapshot()

    proposals = trigger_2am_collision(
        engine, base_ts_ms=engine.simulated_epoch_ms
    )
    decs = [
        ArbitrationDecision(
            decision_id="d1",
            command_id=proposals[0].command_id,
            batch_tick=6,
            action=ActionType.DELAY,
            approved_value=0.0,
            delta_utility=0.2,
            reason_code=ReasonCode.FLOOR_BREACH,
            hold_count=1,
            decided_at_epoch_ms=engine.simulated_epoch_ms,
        )
    ]

    cf = compute_counterfactual(
        snapshot=snap,
        proposals=proposals,
        decisions=decs,
        horizon_ticks=5,
    )
    assert cf.batch_tick == 6
    assert isinstance(cf, CounterfactualResult)


def test_compute_counterfactual_with_risk_dial_weights() -> None:
    """RiskDial weights are respected during counterfactual utility scoring."""
    engine = SimulationEngine(seed=42)
    for _ in range(6):
        engine.step()
    proposals = trigger_2am_collision(
        engine, base_ts_ms=engine.simulated_epoch_ms
    )

    dial = RiskDial(
        weight_coverage=0.40,
        weight_energy=0.10,
        weight_interference=0.10,
        weight_drop_rate=0.40,
        label="strict_safety",
    )

    cf = compute_counterfactual(
        snapshot=engine,
        proposals=proposals,
        decisions=[],
        horizon_ticks=5,
        weights=dial,
    )
    assert cf.with_arbiter.utility is not None
    assert cf.without_arbiter.utility is not None


def test_compute_counterfactual_invalid_horizon_raises() -> None:
    """Non-positive horizon_ticks raises ValueError."""
    engine = SimulationEngine(seed=42)
    with pytest.raises(ValueError, match="horizon_ticks must be positive"):
        compute_counterfactual(engine, [], [], horizon_ticks=0)


def test_compute_kpi_summary_empty_history() -> None:
    """Empty samples history returns safe zeroed KpiSummary."""
    engine = SimulationEngine(seed=42)
    summary = compute_kpi_summary(engine, [], UtilityWeights())
    assert summary.dropped_users == 0
    assert summary.mean_sinr_db == 0.0
    assert summary.energy_w == 0.0


def test_compute_optimality_2am_collision() -> None:
    """Combinatorial optimality benchmark returns low gap and validates."""
    res = compute_optimality(
        scenario="2am_collision",
        seed=42,
        k_ticks=5,
    )
    assert isinstance(res, OptimalityResult)
    assert res.scenario == "2am_collision"
    assert res.seed == 42
    assert res.combos_evaluated == 64
    assert res.utility_arbiter >= res.utility_baseline
    assert res.utility_optimum >= res.utility_arbiter
    assert res.gap_pct <= 5.0  # Gap is under 5%
    assert res.arbiter_ms < 50.0  # Sub-50 ms execution
    assert res.optimum_s > 0.0


def test_run_multi_seed_optimality() -> None:
    """Multi-seed optimality aggregates across consecutive seeds."""
    summary = run_multi_seed_optimality(
        scenario="2am_collision",
        seeds=(42, 43, 44),
        k_ticks=3,
    )
    assert summary["total_seeds"] == 3
    assert len(summary["results"]) == 3
    assert summary["mean_gap_pct"] >= 0.0
    assert summary["worst_gap_pct"] >= summary["mean_gap_pct"]


def test_search_collisions_and_worst_gallery() -> None:
    """Collision search generates valid gallery and serializable report."""
    report = search_collisions(n_trials=20, seed=42)
    assert report["n_tested"] == 20
    assert report["seed"] == 42
    assert "floor_breaches" in report
    assert "underperforming_cases" in report
    assert len(report["worst_scenarios"]) <= 5

    # Check scenario structure
    if report["worst_scenarios"]:
        worst = report["worst_scenarios"][0]
        assert "trial_index" in worst
        assert "trial_seed" in worst
        assert "proposals" in worst
        assert "utility_arbiter" in worst
        assert "utility_baseline" in worst


def test_random_proposal_domain_bounds() -> None:
    """_random_proposal emits strictly valid typed command proposals."""
    import numpy as np

    rng = np.random.default_rng(99)
    for i in range(50):
        prop = _random_proposal(rng, trial_idx=i, prop_idx=0, base_ts=1000)
        assert isinstance(prop, CommandProposal)
        assert prop.target_cell_id in (
            "cell_A",
            "cell_B",
            "cell_C",
            "cell_D",
            "cell_E",
        )
        assert prop.parameter in (
            ParameterType.TX_POWER_DBM,
            ParameterType.TILT_DEG,
            ParameterType.SLEEP_STATE,
            ParameterType.OFFLOAD_RATIO,
        )
        if prop.parameter == ParameterType.TX_POWER_DBM:
            assert 30.0 <= prop.requested_value <= 46.0
            assert prop.controller_type == ControllerType.COVERAGE_CAPACITY
        elif prop.parameter == ParameterType.TILT_DEG:
            assert 0.0 <= prop.requested_value <= 15.0
            assert prop.controller_type == ControllerType.COVERAGE_CAPACITY
        elif prop.parameter == ParameterType.SLEEP_STATE:
            assert prop.requested_value in (0.0, 1.0)
            assert prop.controller_type == ControllerType.ENERGY_SAVING
        elif prop.parameter == ParameterType.OFFLOAD_RATIO:
            assert 0.05 <= prop.requested_value <= 0.50
            assert prop.controller_type == ControllerType.LOAD_BALANCING
