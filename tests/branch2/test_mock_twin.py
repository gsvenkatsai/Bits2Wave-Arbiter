"""Mock twin tests: determinism, configuration and the scripted curve."""
from __future__ import annotations

import time

import pytest

from arbiter.engine.mocks import (
    DEFAULT_KPIS,
    MockDigitalTwinClient,
    ScriptedTwin,
    TwinClient,
    TwinTimeoutError,
    floors_satisfied_by,
)


def test_both_mocks_satisfy_the_protocol() -> None:
    assert isinstance(MockDigitalTwinClient(), TwinClient)
    assert isinstance(ScriptedTwin(lambda candidate: DEFAULT_KPIS), TwinClient)


def test_mock_returns_configured_kpis() -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.97})
    response = twin.evaluate([])
    assert response["evaluation_id"] == "mock-eval-0001"
    assert response["predicted_kpis"]["coverage_ratio"] == pytest.approx(0.97)
    # Unspecified KPIs fall back to the healthy defaults.
    assert response["predicted_kpis"]["normalized_energy"] == pytest.approx(
        DEFAULT_KPIS["normalized_energy"]
    )
    assert response["safety_floors_satisfied"] is True
    assert response["confidence_margin"] == pytest.approx(0.0)


def test_mock_is_deterministic() -> None:
    candidate = [
        {
            "command_id": "c1",
            "cell_id": "A",
            "parameter": "TX_POWER_DBM",
            "from_value": 30.0,
            "to_value": 34.0,
        }
    ]
    first = MockDigitalTwinClient().evaluate(candidate)
    second = MockDigitalTwinClient().evaluate(candidate)
    assert first == second


def test_mock_derives_floor_breach_from_kpis() -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.85})
    assert twin.evaluate([])["safety_floors_satisfied"] is False


def test_mock_floors_flag_can_be_overridden() -> None:
    twin = MockDigitalTwinClient({"coverage_ratio": 0.85}, floors_satisfied=True)
    assert twin.evaluate([])["safety_floors_satisfied"] is True


def test_mock_reports_configured_margin() -> None:
    twin = MockDigitalTwinClient(confidence_margin=0.05)
    assert twin.evaluate([])["confidence_margin"] == pytest.approx(0.05)


def test_mock_counts_calls_and_candidates() -> None:
    twin = MockDigitalTwinClient()
    assert twin.last_candidate == []
    twin.evaluate([{"command_id": "c1"}])
    twin.evaluate([{"command_id": "c2"}])
    assert twin.call_count == 2
    assert len(twin.candidates) == 2
    assert twin.last_candidate == [{"command_id": "c2"}]
    twin.reset()
    assert twin.call_count == 0
    assert twin.candidates == []


def test_mock_records_candidate_copy() -> None:
    twin = MockDigitalTwinClient()
    candidate = [{"command_id": "c1", "to_value": 33.0}]
    twin.evaluate(candidate)
    candidate[0]["to_value"] = 99.0
    assert twin.last_candidate[0]["to_value"] == pytest.approx(33.0)


def test_mock_latency_is_simulated() -> None:
    twin = MockDigitalTwinClient(latency_ms=10.0)
    started = time.perf_counter()
    twin.evaluate([], timeout_budget_ms=50.0)
    assert (time.perf_counter() - started) >= 0.01


def test_mock_raises_on_timeout_when_configured() -> None:
    twin = MockDigitalTwinClient(latency_ms=50.0, raise_on_timeout=True)
    with pytest.raises(TwinTimeoutError, match="overran"):
        twin.evaluate([], timeout_budget_ms=10.0)


def test_mock_does_not_raise_when_within_budget() -> None:
    twin = MockDigitalTwinClient(latency_ms=1.0, raise_on_timeout=True)
    assert twin.evaluate([], timeout_budget_ms=10.0)["evaluation_id"] == "mock-eval-0001"


def test_scripted_twin_models_a_utility_curve() -> None:
    def curve(candidate: list[dict[str, object]]) -> dict[str, float]:
        # Coverage peaks at a requested value of 36 dBm, then degrades.
        value = float(candidate[0]["to_value"])  # type: ignore[arg-type]
        distance = abs(value - 36.0)
        return {
            "coverage_ratio": 0.98 - distance * 0.01,
            "drop_rate_ratio": 0.01,
        }

    twin = ScriptedTwin(curve)
    peak = twin.evaluate([{"to_value": 36.0}])
    off_peak = twin.evaluate([{"to_value": 46.0}])

    assert peak["predicted_kpis"]["coverage_ratio"] == pytest.approx(0.98)
    assert off_peak["predicted_kpis"]["coverage_ratio"] == pytest.approx(0.88)
    assert peak["safety_floors_satisfied"] is True
    assert off_peak["safety_floors_satisfied"] is False
    assert twin.call_count == 2
    assert twin.candidates[1] == [{"to_value": 46.0}]


def test_scripted_twin_floors_override_and_margin() -> None:
    twin = ScriptedTwin(
        lambda candidate: {"coverage_ratio": 0.99},
        floors_satisfied=False,
        confidence_margin=0.1,
    )
    response = twin.evaluate([])
    assert response["safety_floors_satisfied"] is False
    assert response["confidence_margin"] == pytest.approx(0.1)


def test_floors_helper_boundaries() -> None:
    assert floors_satisfied_by({"coverage_ratio": 0.90, "drop_rate_ratio": 0.02}) is True
    assert floors_satisfied_by({"coverage_ratio": 0.89, "drop_rate_ratio": 0.0}) is False
    assert floors_satisfied_by({"coverage_ratio": 1.0, "drop_rate_ratio": 0.03}) is False
    assert floors_satisfied_by({}) is False
