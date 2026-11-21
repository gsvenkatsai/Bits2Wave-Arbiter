"""Unit and performance tests for Digital Twin Evaluator.

SRS FR-5.1, FR-5.2, FR-5.3, NFR-2, C-2.
Verifies latency benchmark (< 10 ms, P95 < 8 ms), KPI bounds,
directional impact of power/sleep/offload commands, and API payload compatibility.
"""
import time
import numpy as np
import pytest

from arbiter.twin.analytical_model import (
    AnalyticalPropagationChain,
    dbm_to_watts,
    watts_to_dbm,
)
from arbiter.twin.evaluator import (
    DigitalTwinEvaluator,
    handle_twin_evaluate,
    set_global_evaluator,
)
from arbiter.twin.schemas import CandidateDelta


def test_dbm_watts_conversions():
    """Verify standard decibel-milliwatt and linear Watt conversions."""
    assert np.isclose(dbm_to_watts(30.0), 1.0)
    assert np.isclose(dbm_to_watts(40.0), 10.0)
    assert np.isclose(dbm_to_watts(43.0), 19.952623, atol=1e-3)
    assert np.isclose(watts_to_dbm(1.0), 30.0)
    assert np.isclose(watts_to_dbm(10.0), 40.0)


def test_twin_evaluation_clipping_and_safety_floors(mock_state_provider):
    """Verify all normalized KPI fields are strictly bounded within [0.0, 1.0]."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    # Standard nominal evaluation
    deltas = [
        CandidateDelta(cell_id="cell_B", parameter="TX_POWER_DBM", proposed_value=43.0),
    ]
    resp = evaluator.predict(deltas)

    assert 0.0 <= resp.predicted_kpis.coverage_ratio <= 1.0
    assert 0.0 <= resp.predicted_kpis.normalized_energy <= 1.0
    assert 0.0 <= resp.predicted_kpis.normalized_interference <= 1.0
    assert 0.0 <= resp.predicted_kpis.drop_rate_ratio <= 1.0
    assert isinstance(resp.predicted_kpis.predicted_sinr_avg_db, float)
    assert resp.safety_floors_satisfied is True
    assert resp.confidence_margin > 0.0


def test_twin_tx_power_increase_directionality(mock_state_provider):
    """Verify increasing TX power increases energy consumption and interference on neighbors."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    baseline_resp = evaluator.predict([])
    high_power_resp = evaluator.predict([
        CandidateDelta(cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=46.0),
    ])

    # Higher power -> higher normalized energy
    assert high_power_resp.predicted_kpis.normalized_energy > baseline_resp.predicted_kpis.normalized_energy
    # Higher power -> increased interference into neighbors
    assert high_power_resp.predicted_kpis.normalized_interference >= baseline_resp.predicted_kpis.normalized_interference


def test_twin_sleep_state_directionality(mock_state_provider):
    """Verify putting a cell to sleep reduces energy and reduces interference on neighbor cluster."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    baseline_resp = evaluator.predict([])
    sleep_resp = evaluator.predict([
        CandidateDelta(cell_id="cell_B", parameter="SLEEP_STATE", proposed_value=0.0),
    ])

    # Sleeping cell reduces energy
    assert sleep_resp.predicted_kpis.normalized_energy < baseline_resp.predicted_kpis.normalized_energy
    # Cell B was turned off, reducing interference transmitted into Cell A and C
    assert sleep_resp.predicted_kpis.normalized_interference <= baseline_resp.predicted_kpis.normalized_interference


def test_twin_offload_ratio_impact(mock_state_provider):
    """Verify offload ratio candidate deltas execute cleanly over cluster."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    resp = evaluator.predict([
        CandidateDelta(cell_id="cell_A", parameter="OFFLOAD_RATIO", proposed_value=0.40),
    ])
    assert 0.0 <= resp.predicted_kpis.coverage_ratio <= 1.0
    assert "cell_A" in resp.predicted_kpis.affected_cell_ids


def test_safety_floor_breach_detection(mock_state_provider):
    """Verify safety_floors_satisfied triggers False when conditions breach floors."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    # Force severe interference from neighbor while reducing serving power, breaching safety floors
    severe_deltas = [
        CandidateDelta(cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=-30.0),
        CandidateDelta(cell_id="cell_B", parameter="TX_POWER_DBM", proposed_value=55.0),
    ]
    resp = evaluator.predict(severe_deltas)
    assert resp.safety_floors_satisfied is False
    assert resp.predicted_kpis.drop_rate_ratio > 0.02 or resp.predicted_kpis.coverage_ratio < 0.90


def test_twin_tilt_delta_propagation(mock_state_provider):
    """Verify antenna tilt adjustments modulate propagation and interference footprint."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    baseline_resp = evaluator.predict([])
    tilted_resp = evaluator.predict([
        CandidateDelta(cell_id="cell_B", parameter="TILT_DEG", proposed_value=8.0),
    ])

    # Tilt reduces inter-cell interference
    assert tilted_resp.predicted_kpis.normalized_interference <= baseline_resp.predicted_kpis.normalized_interference


def test_twin_unrecognized_cell_fallback(mock_state_provider):
    """Verify evaluator gracefully handles deltas containing unknown cell IDs."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    resp = evaluator.predict([
        CandidateDelta(cell_id="unknown_cell_99", parameter="TX_POWER_DBM", proposed_value=43.0),
    ])
    assert resp.safety_floors_satisfied is True
    assert len(resp.predicted_kpis.affected_cell_ids) > 0


def test_handle_twin_evaluate_fallback_creation():
    """Verify handle_twin_evaluate initializes default fallback provider when none registered."""
    import arbiter.twin.evaluator as eval_module
    eval_module._global_evaluator = None

    spec_request = {
        "evaluation_id": "eval-fallback-01",
        "candidate_deltas": [
            {"cell_id": "cell_A", "parameter": "TX_POWER_DBM", "proposed_value": 43.0},
        ],
        "timeout_budget_ms": 100.0,
    }
    result = eval_module.handle_twin_evaluate(spec_request)
    assert result["evaluation_id"] == "eval-fallback-01"
    assert result["safety_floors_satisfied"] is True


def test_latency_benchmark_p95_sub_8ms(mock_state_provider):
    """Benchmark: 100% of evaluations < 10 ms, P95 < 8 ms (NFR-2, FR-5.3)."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)
    candidate_deltas = [
        CandidateDelta(cell_id="cell_B", parameter="SLEEP_STATE", proposed_value=0.0),
        CandidateDelta(cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=44.0),
    ]

    # Warm-up run to prime caches
    evaluator.predict(candidate_deltas)

    n_runs = 60
    latencies = []
    for _ in range(n_runs):
        resp = evaluator.predict(candidate_deltas)
        latencies.append(resp.computation_time_ms)

    latencies_arr = np.array(latencies)
    mean_lat = float(np.mean(latencies_arr))
    p95_lat = float(np.percentile(latencies_arr, 95))
    max_lat = float(np.max(latencies_arr))

    print(f"\n[BENCHMARK] Mean: {mean_lat:.3f} ms, P95: {p95_lat:.3f} ms, Max: {max_lat:.3f} ms")

    # Hard NFR-2 criteria
    assert (latencies_arr < 10.0).all(), f"Failed 10 ms SLA: max was {max_lat:.3f} ms"
    assert p95_lat < 8.0, f"Failed P95 < 8 ms target: P95 was {p95_lat:.3f} ms"


def test_handle_twin_evaluate_api_payload(mock_state_provider):
    """Verify handle_twin_evaluate callable processes exact spec example payload."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)
    set_global_evaluator(evaluator)

    spec_request = {
        "evaluation_id": "eval-9982",
        "candidate_deltas": [
            {"cell_id": "cell_B", "parameter": "SLEEP_STATE", "proposed_value": 0.0},
            {"cell_id": "cell_A", "parameter": "TX_POWER_DBM", "proposed_value": 44.0},
        ],
        "timeout_budget_ms": 15.0,
    }

    result = handle_twin_evaluate(spec_request)

    assert result["evaluation_id"] == "eval-9982"
    assert "computation_time_ms" in result
    assert result["computation_time_ms"] < 15.0
    assert "predicted_kpis" in result
    assert "coverage_ratio" in result["predicted_kpis"]
    assert "normalized_energy" in result["predicted_kpis"]
    assert "normalized_interference" in result["predicted_kpis"]
    assert "drop_rate_ratio" in result["predicted_kpis"]
    assert "predicted_sinr_avg_db" in result["predicted_kpis"]
    assert "affected_cell_ids" in result["predicted_kpis"]
    assert "safety_floors_satisfied" in result
    assert "confidence_margin" in result
