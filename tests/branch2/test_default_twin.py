"""Contract C1: ``make_default_twin`` builds the real twin, scripted on request."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from arbiter.engine.contracts import ActionType, ReasonCode
from arbiter.engine.default_twin import (
    KINDS,
    DefaultTwinClient,
    TwinBuildError,
    make_default_twin,
    topology_adjacency,
)
from arbiter.engine.mocks.mock_twin_client import ScriptedTwin
from arbiter.engine.twin_adapter import KPI_KEYS
from arbiter.queue import QueueOrchestrator


def test_topology_adjacency_uses_the_real_simulator_grid() -> None:
    from arbiter.simulator.grid import HexagonalGrid

    configs = HexagonalGrid().generate_5_cell_cluster(isd=500.0)
    expected = {
        cell_id: list(config.neighbor_cell_ids)
        for cell_id, config in configs.items()
    }
    assert topology_adjacency("5_CELL_CORE") == expected
    assert set(topology_adjacency()["cell_A"]) == {
        "cell_B",
        "cell_C",
        "cell_D",
        "cell_E",
    }


def test_real_twin_satisfies_the_twin_client_protocol() -> None:
    twin = make_default_twin()

    assert isinstance(twin, DefaultTwinClient)
    assert not isinstance(twin, ScriptedTwin)

    response = twin.evaluate([])
    kpis = response["predicted_kpis"]
    assert set(KPI_KEYS) <= set(kpis)
    for key in KPI_KEYS:
        assert 0.0 <= kpis[key] <= 1.0
    assert isinstance(response["safety_floors_satisfied"], bool)
    assert response["computation_time_ms"] >= 0.0


def test_real_twin_reports_calibration_status() -> None:
    twin = make_default_twin("5_CELL_CORE", "real")

    status = twin.calibration_status()

    assert set(status) == {
        "calibrated",
        "n_calibration",
        "interval_halfwidth_db",
    }
    # A fresh twin has no calibration residuals at all: say so honestly.
    assert status["calibrated"] is False
    assert status["n_calibration"] == 0
    assert status["interval_halfwidth_db"] is None


def test_real_twin_predicts_from_the_analytical_model() -> None:
    twin = make_default_twin()

    baseline = twin.evaluate([])["predicted_kpis"]
    sleeping = twin.evaluate([
        {"cell_id": "cell_B", "parameter": "SLEEP_STATE", "to_value": 1.0}
    ])["predicted_kpis"]

    assert sleeping["normalized_energy"] < baseline["normalized_energy"]


def test_scripted_twin_is_opt_in_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="arbiter.engine.scripted_twin"):
        twin = make_default_twin("5_CELL_CORE", "scripted")

    assert isinstance(twin, ScriptedTwin)
    assert any(
        "ScriptedTwin requested" in record.getMessage()
        for record in caplog.records
    )


def test_unknown_kind_is_rejected() -> None:
    assert KINDS == ("real", "scripted")
    with pytest.raises(ValueError):
        make_default_twin("5_CELL_CORE", "mock")


def test_real_twin_build_failure_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    import arbiter.twin.evaluator as evaluator_module

    class _Boom:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("propagation chain unavailable")

    monkeypatch.setattr(evaluator_module, "DigitalTwinEvaluator", _Boom)
    monkeypatch.setattr(evaluator_module, "get_global_evaluator", lambda: None)

    with pytest.raises(TwinBuildError, match="cannot build the real twin"):
        make_default_twin("5_CELL_CORE", "real")


def test_orchestrator_accepts_the_real_twin(make_proposal: Any) -> None:
    twin = make_default_twin()
    orchestrator = QueueOrchestrator(twin=twin, adjacency=topology_adjacency())

    orchestrator.submit(
        make_proposal(
            command_id="real-1",
            target_cell="cell_A",
            parameter="TX_POWER_DBM",
            current_value=43.0,
            requested_value=46.0,
        )
    )
    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)

    decisions = result["decisions"]
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.action in set(ActionType)
    assert decision.twin_eval_count > 0
    assert decision.reason_code is not ReasonCode.TWIN_TIMEOUT
