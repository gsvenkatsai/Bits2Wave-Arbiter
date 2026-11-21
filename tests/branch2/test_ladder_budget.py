"""Ladder budget, ordering and transient-delay behaviour."""
from __future__ import annotations

from typing import Any, Dict, List

from arbiter.engine.contracts import ActionType, UtilityWeights
from arbiter.engine.ladder import DecisionLadderEngine
from arbiter.engine.mocks import ScriptedTwin
from arbiter.queue import build_default_orchestrator

WEIGHTS = UtilityWeights()


def _scenario_kpis(candidate: List[dict[str, Any]]) -> Dict[str, float]:
    coverage = 0.95
    energy = 0.50
    interference = 0.30
    drop_rate = 0.01
    for delta in candidate:
        parameter = str(delta.get("parameter", "")).upper()
        to_value = float(delta.get("to_value", 0.0))
        if parameter == "SLEEP_STATE":
            coverage -= 0.20 * to_value
            energy -= 0.20 * to_value
            interference -= 0.10 * to_value
            drop_rate += 0.02 * to_value
        elif parameter == "OFFLOAD_RATIO":
            coverage -= 0.02 * to_value
            energy -= 0.30 * to_value
            interference += 0.05 * to_value
            drop_rate += 0.15 * to_value * to_value
        elif parameter == "TX_POWER_DBM":
            step = to_value - 30.0
            coverage += 0.05 * step
            energy += 0.02 * step
            interference += 0.02 * step
    return {
        "coverage_ratio": max(0.0, min(1.0, coverage)),
        "normalized_energy": max(0.0, min(1.0, energy)),
        "normalized_interference": max(0.0, min(1.0, interference)),
        "drop_rate_ratio": max(0.0, min(1.0, drop_rate)),
    }


def test_priority_ranking_uses_standalone_delta_utility(make_proposal: Any) -> None:
    def kpis(candidate: List[dict[str, Any]]) -> Dict[str, float]:
        coverage, energy = 0.95, 0.50
        for delta in candidate:
            value = float(delta["to_value"])
            if delta["command_id"] == "big":
                coverage -= 0.005 * value
                energy -= 0.30 * value
            else:
                coverage -= 0.20 * value
                energy -= 0.02 * value
        return {
            "coverage_ratio": coverage,
            "normalized_energy": energy,
            "normalized_interference": 0.30,
            "drop_rate_ratio": 0.01,
        }

    twin = ScriptedTwin(kpis)
    engine = DecisionLadderEngine(max_twin_evals=30)
    big = make_proposal(
        command_id="big",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
    )
    small = make_proposal(
        command_id="small",
        target_cell="B",
        parameter="TILT_DEG",
        current_value=4.0,
        requested_value=2.0,
        created_tick=1,
    )

    decisions = {
        decision.command_id: decision
        for decision in engine.evaluate([big, small], None, twin, WEIGHTS, 1, 0)
    }

    assert [delta["command_id"] for delta in twin.candidates[2]] == ["big"]
    assert decisions["big"].action is ActionType.MODIFY
    assert decisions["small"].action is ActionType.DELAY


def test_transient_delay_follows_a_moving_peer(make_proposal: Any) -> None:
    twin = ScriptedTwin(_scenario_kpis)
    engine = DecisionLadderEngine()
    blocked = make_proposal(
        command_id="blocked",
        target_cell="B",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
        is_binary=True,
    )
    moving = make_proposal(
        command_id="moving",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        created_tick=1,
    )

    decisions = {
        decision.command_id: decision
        for decision in engine.evaluate([blocked, moving], None, twin, WEIGHTS, 1, 0)
    }

    assert decisions["moving"].action is ActionType.MODIFY
    assert decisions["blocked"].action is ActionType.DELAY
    assert decisions["blocked"].hold_count == 1


def test_default_orchestrator_factory(make_proposal: Any) -> None:
    orchestrator = build_default_orchestrator()
    orchestrator.submit(make_proposal())

    result = orchestrator.on_tick(current_tick=0, timestamp_epoch_ms=0)

    assert result["decisions_count"] == 1
    assert orchestrator.latency_p95_ms() >= 0.0
    orchestrator.reset_latency()
    assert orchestrator.latency_samples == ()
