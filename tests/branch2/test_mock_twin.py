"""Mock twin tests: determinism, scripting and fault injection."""
from __future__ import annotations

import time

import pytest

from arbiter.engine.mocks.mock_twin_client import (
    DEFAULT_KPIS,
    MockTwinClient,
    TwinClient,
    TwinEvaluationError,
)


def test_mock_is_a_twin_client() -> None:
    assert isinstance(MockTwinClient(), TwinClient)


def test_default_forecast_is_healthy() -> None:
    response = MockTwinClient().evaluate([])
    assert response["evaluation_id"] == "mock-eval-0001"
    assert response["predicted_kpis"] == pytest.approx(DEFAULT_KPIS)
    assert response["safety_floors_satisfied"] is True
    assert response["confidence_margin"] == pytest.approx(0.0)


def test_mock_is_deterministic() -> None:
    deltas = [
        {
            "command_id": "c1",
            "cell_id": "cell-a",
            "parameter": "TX_POWER_DBM",
            "from_value": 30.0,
            "to_value": 34.0,
        }
    ]
    first = MockTwinClient().evaluate(deltas)
    second = MockTwinClient().evaluate(deltas)
    assert first == second


def test_power_increase_raises_coverage_and_energy() -> None:
    deltas = [
        {
            "command_id": "c1",
            "cell_id": "cell-a",
            "parameter": "TX_POWER_DBM",
            "from_value": 30.0,
            "to_value": 40.0,
        }
    ]
    kpis = MockTwinClient().evaluate(deltas)["predicted_kpis"]
    assert kpis["coverage_ratio"] > DEFAULT_KPIS["coverage_ratio"]
    assert kpis["normalized_energy"] > DEFAULT_KPIS["normalized_energy"]


def test_cell_off_breaches_coverage_floor() -> None:
    deltas = [
        {
            "command_id": "c1",
            "cell_id": "cell-a",
            "parameter": "SLEEP_STATE",
            "from_value": 0.0,
            "to_value": 1.0,
        }
    ]
    response = MockTwinClient().evaluate(deltas)
    assert response["safety_floors_satisfied"] is False
    assert response["predicted_kpis"]["coverage_ratio"] < 0.90


def test_scripted_responses_are_consumed_in_order() -> None:
    twin = MockTwinClient(
        scripted_responses=[
            {"predicted_kpis": {"coverage_ratio": 0.99}},
            {"predicted_kpis": {"coverage_ratio": 0.91}, "confidence_margin": 0.2},
        ]
    )
    first = twin.evaluate([])
    second = twin.evaluate([])
    assert first["evaluation_id"] == "mock-eval-0001"
    assert first["confidence_margin"] == pytest.approx(0.0)
    assert second["predicted_kpis"]["coverage_ratio"] == pytest.approx(0.91)
    assert second["confidence_margin"] == pytest.approx(0.2)


def test_floor_breach_injection() -> None:
    twin = MockTwinClient(floor_breach_at_calls=[2])
    assert twin.evaluate([])["safety_floors_satisfied"] is True
    breached = twin.evaluate([])
    assert breached["safety_floors_satisfied"] is False
    assert breached["predicted_kpis"]["drop_rate_ratio"] == pytest.approx(0.05)


def test_raise_injection() -> None:
    twin = MockTwinClient(raise_at_calls=[1])
    with pytest.raises(TwinEvaluationError):
        twin.evaluate([])


def test_timeout_injection_sleeps_past_budget() -> None:
    twin = MockTwinClient(timeout_at_calls=[1], timeout_overshoot_s=0.01)
    started = time.perf_counter()
    twin.evaluate([], timeout_budget_ms=10.0)
    assert (time.perf_counter() - started) >= 0.02


def test_response_factory_overrides_forecast() -> None:
    twin = MockTwinClient(
        response_factory=lambda deltas: {
            "predicted_kpis": {"coverage_ratio": 0.5, "drop_rate_ratio": 0.5},
            "safety_floors_satisfied": False,
        }
    )
    response = twin.evaluate([])
    assert response["safety_floors_satisfied"] is False
    assert response["predicted_kpis"]["coverage_ratio"] == pytest.approx(0.5)
    assert response["evaluation_id"] == "mock-eval-0001"


def test_call_history_and_reset() -> None:
    twin = MockTwinClient()
    assert twin.last_deltas == []
    twin.evaluate([{"command_id": "c1", "parameter": "TILT_DEG"}])
    assert twin.call_count == 1
    assert len(twin.calls) == 1
    assert twin.last_deltas[0]["command_id"] == "c1"
    twin.reset()
    assert twin.call_count == 0
    assert twin.calls == []


def test_latency_knob_sleeps() -> None:
    twin = MockTwinClient(latency_s=0.01)
    started = time.perf_counter()
    twin.evaluate([])
    assert (time.perf_counter() - started) >= 0.01


def test_clamps_kpis_into_unit_interval() -> None:
    deltas = [
        {
            "command_id": "c1",
            "cell_id": "cell-a",
            "parameter": "TX_POWER_DBM",
            "from_value": 0.0,
            "to_value": 1000.0,
        }
    ]
    kpis = MockTwinClient().evaluate(deltas)["predicted_kpis"]
    assert 0.0 <= kpis["coverage_ratio"] <= 1.0
    assert 0.0 <= kpis["normalized_energy"] <= 1.0


def test_unknown_parameter_has_no_effect() -> None:
    deltas = [{"command_id": "c1", "parameter": "UNKNOWN", "to_value": 99.0}]
    response = MockTwinClient().evaluate(deltas)
    assert response["predicted_kpis"] == pytest.approx(DEFAULT_KPIS)
