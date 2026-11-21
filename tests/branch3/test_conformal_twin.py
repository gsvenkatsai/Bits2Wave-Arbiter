"""Unit and empirical validation tests for Digital Twin Conformal Intervals.

ARBITER v2 Feature #2:
- Cold start handling (< 30 calibration points -> calibrated=False)
- Exact finite-sample quantile calculation
- Empirical coverage test (>= 0.88 on held-out synthetic stream)
- Drift injection and interval widening
- predict_v2 latency (< 10 ms)
- Mock twin v2 compatibility for Pranav
- v2_calibration persistence in SQLite
"""
from __future__ import annotations

import json
import random
import time
import numpy as np

from arbiter.shared.v2_contracts import TwinPredictionV2
from arbiter.twin.conformal import (
    ConformalPredictor,
    DEFAULT_CONSERVATIVE_INTERVALS,
    record_v2_calibration_event,
)
from arbiter.twin.evaluator import DigitalTwinEvaluator
from arbiter.twin.mocks.mock_twin_v2 import MockDigitalTwinV2
from arbiter.twin.schemas import CandidateDelta


def test_conformal_cold_start_under_30_points():
    """Verify n < 30 returns calibrated=False and wide default intervals."""
    cp = ConformalPredictor(
        window_size=200, min_calibration_points=30, alpha=0.1
    )

    # Populate only 15 points
    for _ in range(15):
        cp.add_observation("mean_sinr_db", predicted=14.0, actual=14.2)

    pred = cp.predict_intervals(
        {"mean_sinr_db": 14.0}, candidate_id="cand-cold-01"
    )

    assert pred.calibrated is False
    assert pred.n_calibration == 15
    interval = pred.intervals[0]
    assert interval.kpi == "mean_sinr_db"
    assert interval.mean == 14.0
    expected_q = DEFAULT_CONSERVATIVE_INTERVALS["mean_sinr_db"]
    assert np.isclose(interval.upper - interval.mean, expected_q)
    assert np.isclose(interval.mean - interval.lower, expected_q)


def test_conformal_finite_sample_quantile_exact():
    """Verify exact quantile formula: k = ceil((n+1)(1-alpha))."""
    cp = ConformalPredictor(
        window_size=100, min_calibration_points=30, alpha=0.1
    )

    # 49 known residuals: 1.0, 2.0, ..., 49.0
    # For n=49, alpha=0.1: k = ceil((49 + 1) * 0.90) = ceil(45.0) = 45.
    # The 45th smallest residual is 45.0.
    for r in range(1, 50):
        cp.add_residual("dropped_users", float(r))

    q, is_calibrated, n_cal = cp.compute_quantile(
        "dropped_users", alpha=0.1
    )
    assert is_calibrated is True
    assert n_cal == 49
    assert q == 45.0


def test_conformal_empirical_coverage_above_88_pct():
    """Verify empirical coverage >= 0.88 on held-out synthetic test stream."""
    random.seed(100)
    np.random.seed(100)

    cp = ConformalPredictor(
        window_size=200, min_calibration_points=30, alpha=0.1
    )

    # Calibration phase: 200 points with Gaussian error ~ N(0, 1.5^2)
    true_sinr_baseline = 15.0
    n_cal = 200
    for _ in range(n_cal):
        actual = true_sinr_baseline + random.gauss(0.0, 1.5)
        predicted = true_sinr_baseline
        cp.add_observation("mean_sinr_db", predicted=predicted, actual=actual)

    # Held-out testing phase: 200 points
    n_test = 200
    covered_count = 0
    test_actuals = []
    test_lowers = []
    test_uppers = []

    for _ in range(n_test):
        actual = true_sinr_baseline + random.gauss(0.0, 1.5)
        predicted = true_sinr_baseline
        pred_v2 = cp.predict_intervals({"mean_sinr_db": predicted}, alpha=0.1)
        interv = pred_v2.intervals[0]

        test_actuals.append(actual)
        test_lowers.append(interv.lower)
        test_uppers.append(interv.upper)

        if interv.lower <= actual <= interv.upper:
            covered_count += 1

    empirical_coverage = covered_count / float(n_test)
    mean_width = float(np.mean(np.array(test_uppers) - np.array(test_lowers)))
    print("\n[CONFORMAL COVERAGE REPORT]")
    print("Target Coverage: 90.00% (alpha=0.10)")
    print(f"Empirical Coverage: {empirical_coverage * 100:.2f}%")
    print(f"Average Interval Width: {mean_width:.3f} dB")

    # Specification threshold: >= 0.88
    err_msg = f"Empirical coverage {empirical_coverage:.4f} < 0.88"
    assert empirical_coverage >= 0.88, err_msg


def test_drift_injection_widens_intervals():
    """Verify injecting drift widens intervals after recalibration."""
    random.seed(99)
    cp = ConformalPredictor(
        window_size=100, min_calibration_points=30, alpha=0.1
    )

    # Baseline calibration: small error (sigma=0.5)
    for _ in range(50):
        actual = 20.0 + random.gauss(0.0, 0.5)
        cp.add_observation("energy_w", predicted=20.0, actual=actual)

    baseline_pred = cp.predict_intervals({"energy_w": 20.0}, alpha=0.1)
    base_interv = baseline_pred.intervals[0]
    baseline_width = base_interv.upper - base_interv.lower

    # Inject significant drift: +5.0 W bias
    cp.set_drift("energy_w", bias=5.0, noise_std=1.0)

    # Ingest new observations generated under drift
    for _ in range(50):
        actual = 20.0 + random.gauss(0.0, 0.5)
        pred_val = cp.apply_drift("energy_w", 20.0)
        cp.add_observation("energy_w", predicted=pred_val, actual=actual)

    recalibrated_pred = cp.predict_intervals({"energy_w": 20.0}, alpha=0.1)
    drift_interv = recalibrated_pred.intervals[0]
    drifted_width = drift_interv.upper - drift_interv.lower

    print(
        f"\n[DRIFT EXPANSION] Base: {baseline_width:.3f} W, "
        f"Drifted: {drifted_width:.3f} W"
    )
    assert drifted_width > baseline_width * 1.5, "Failed to widen under drift"


def test_predict_v2_backward_compatibility(mock_state_provider):
    """Verify v1 predict() and v2 predict_v2() operate concurrently."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    deltas = [
        CandidateDelta(
            cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=43.0
        ),
    ]

    # v1 call
    resp_v1 = evaluator.predict(deltas)
    assert resp_v1.safety_floors_satisfied is True
    assert hasattr(resp_v1, "predicted_kpis")

    # v2 call
    resp_v2 = evaluator.predict_v2(deltas, candidate_id="cand-v2-compat")
    assert isinstance(resp_v2, TwinPredictionV2)
    assert resp_v2.candidate_id == "cand-v2-compat"
    assert len(resp_v2.intervals) > 0

    kpi_names = [i.kpi for i in resp_v2.intervals]
    assert "dropped_users" in kpi_names
    assert "mean_sinr_db" in kpi_names
    assert "energy_w" in kpi_names
    assert "coverage_ratio" in kpi_names


def test_predict_v2_latency_sub_10ms(mock_state_provider):
    """Verify predict_v2 latency remains well under the 10 ms SLA."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)

    # Pre-calibrate with 50 points
    for _ in range(50):
        evaluator.conformal_predictor.add_residual("mean_sinr_db", 0.5)
        evaluator.conformal_predictor.add_residual("dropped_users", 1.0)
        evaluator.conformal_predictor.add_residual("energy_w", 2.0)

    deltas = [
        CandidateDelta(
            cell_id="cell_B", parameter="SLEEP_STATE", proposed_value=0.0
        ),
        CandidateDelta(
            cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=43.0
        ),
    ]

    # Measure 50 iterations
    times_ms = []
    for _ in range(50):
        t0 = time.perf_counter()
        _ = evaluator.predict_v2(deltas)
        times_ms.append((time.perf_counter() - t0) * 1000.0)

    mean_ms = float(np.mean(times_ms))
    p95_ms = float(np.percentile(times_ms, 95))
    max_ms = float(np.max(times_ms))

    print(
        f"\n[PREDICT_V2 TIMING] Mean: {mean_ms:.3f} ms, "
        f"P95: {p95_ms:.3f} ms, Max: {max_ms:.3f} ms"
    )
    assert max_ms < 10.0, f"Latency SLA violated: max was {max_ms:.3f} ms"
    assert p95_ms < 8.0, f"P95 latency {p95_ms:.3f} ms exceeded 8 ms target"


def test_mock_twin_v2_matches_contracts():
    """Verify MockDigitalTwinV2 fulfills Pranav's contract requirements."""
    mock = MockDigitalTwinV2()
    result = mock.predict_v2(candidate_id="cand-pranav-01")

    assert result.candidate_id == "cand-pranav-01"
    assert result.calibrated is True
    assert result.n_calibration == 50
    assert len(result.intervals) >= 4

    for interv in result.intervals:
        assert interv.lower < interv.mean < interv.upper
        assert interv.coverage == 0.90


def test_v2_calibration_db_persistence(tmp_db_factory):
    """Verify record_v2_calibration_event persists into v2_calibration."""
    conn = tmp_db_factory.get_connection()

    predicted = {"dropped_users": 2.0, "mean_sinr_db": 14.5}
    actual = {"dropped_users": 3.0, "mean_sinr_db": 14.1}

    cid = record_v2_calibration_event(
        conn=conn,
        candidate_id="cand-cal-01",
        tick=42,
        predicted=predicted,
        actual=actual,
    )

    cursor = conn.cursor()
    cursor.execute(
        "SELECT calibration_id, candidate_id, tick, payload "
        "FROM v2_calibration WHERE calibration_id = ?",
        (cid,),
    )
    row = cursor.fetchone()

    assert row is not None
    assert row["candidate_id"] == "cand-cal-01"
    assert row["tick"] == 42
    payload = json.loads(row["payload"])
    assert payload["residuals"]["dropped_users"] == 1.0


def test_conformal_rolling_window_pruning():
    """Verify residuals buffer prunes oldest observations past maxlen."""
    cp = ConformalPredictor(window_size=50, min_calibration_points=10)

    # Add 70 observations
    for i in range(70):
        cp.add_residual("dropped_users", float(i))

    assert cp.get_calibration_count("dropped_users") == 50
    # Oldest retained residual should be 20.0 (0 to 19 pruned)
    assert cp._residuals["dropped_users"][0] == 20.0
