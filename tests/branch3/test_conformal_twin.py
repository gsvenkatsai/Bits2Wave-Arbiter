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
from typing import Any
import numpy as np

from arbiter.shared.v2_contracts import TwinPredictionV2
from arbiter.twin.conformal import (
    ConformalPredictor,
    DEFAULT_CONSERVATIVE_INTERVALS,
    record_v2_calibration_event,
)
from arbiter.twin.evaluator import (
    DigitalTwinEvaluator,
    SimulatorStateProvider,
    ensure_global_evaluator,
)
from arbiter.twin.mocks.mock_twin_v2 import MockDigitalTwinV2
from arbiter.twin.schemas import CandidateDelta


def test_conformal_cold_start_under_30_points() -> None:
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


def test_conformal_finite_sample_quantile_exact() -> None:
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


def test_conformal_empirical_coverage_above_88_pct() -> None:
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


def test_drift_injection_widens_intervals() -> None:
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


def test_predict_v2_backward_compatibility(
    mock_state_provider: Any,
) -> None:
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


def test_predict_v2_latency_sub_10ms(
    mock_state_provider: Any,
) -> None:
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


def test_mock_twin_v2_matches_contracts() -> None:
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


def test_v2_calibration_db_persistence(tmp_db_factory: Any) -> None:
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


def test_conformal_rolling_window_pruning() -> None:
    """Verify residuals buffer prunes oldest observations past maxlen."""
    cp = ConformalPredictor(window_size=50, min_calibration_points=10)

    # Add 70 observations
    for i in range(70):
        cp.add_residual("dropped_users", float(i))

    assert cp.get_calibration_count("dropped_users") == 50
    # Oldest retained residual should be 20.0 (0 to 19 pruned)
    assert cp._residuals["dropped_users"][0] == 20.0


def test_calibration_status_contract_c3_uncalibrated_and_calibrated(
    mock_state_provider: Any,
) -> None:
    """Verify Contract C3 calibration_status() before and after warm-up."""
    cp = ConformalPredictor(
        window_size=200, min_calibration_points=30, alpha=0.1
    )
    evaluator = DigitalTwinEvaluator(
        state_provider=mock_state_provider, conformal_predictor=cp
    )

    # 1. Uncalibrated state
    status_uncal = evaluator.calibration_status()
    assert status_uncal["calibrated"] is False
    assert status_uncal["n_calibration"] == 0
    assert status_uncal["interval_halfwidth_db"] is None

    # Predictor returns calibrated=False and wide conservative intervals
    pred_uncal = evaluator.predict_v2(
        [
            CandidateDelta(
                cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=43.0
            )
        ]
    )
    assert pred_uncal.calibrated is False
    sinr_uncal = next(
        i for i in pred_uncal.intervals if i.kpi == "mean_sinr_db"
    )
    assert sinr_uncal.upper - sinr_uncal.lower >= 12.0

    # 2. Add 35 calibration points
    for i in range(35):
        cp.add_observation(
            "mean_sinr_db", predicted=10.0, actual=10.2 + (i % 3) * 0.1
        )

    status_cal = evaluator.calibration_status()
    assert status_cal["calibrated"] is True
    assert status_cal["n_calibration"] == 35
    assert isinstance(status_cal["interval_halfwidth_db"], float)
    assert status_cal["interval_halfwidth_db"] > 0.0


def test_ensure_global_evaluator_real_simulator_warmup() -> None:
    """Verify ensure_global_evaluator runs real simulator warm-up under 3 s."""
    t0 = time.perf_counter()
    evaluator = ensure_global_evaluator(
        topology="5_CELL_CORE", warm_up=True, n_samples=60, seed=42
    )
    elapsed = time.perf_counter() - t0

    assert elapsed < 3.0, f"Warm-up took {elapsed:.3f} s, exceeding 3 s budget"
    assert isinstance(evaluator.state_provider, SimulatorStateProvider)

    status = evaluator.calibration_status()
    assert status["calibrated"] is True
    assert status["n_calibration"] == 60
    assert status["interval_halfwidth_db"] is not None
    assert status["interval_halfwidth_db"] > 0.0

    # Test predict_v2 returns calibrated prediction
    pred = evaluator.predict_v2(
        [
            CandidateDelta(
                cell_id="cell_B", parameter="TX_POWER_DBM", proposed_value=44.0
            )
        ]
    )
    assert pred.calibrated is True
    assert pred.n_calibration == 60


def test_ensure_global_evaluator_uncalibrated_when_warmup_false() -> None:
    """Verify ensure_global_evaluator with warm_up=False reports honestly."""
    evaluator = ensure_global_evaluator(
        topology="5_CELL_CORE", warm_up=False, seed=99
    )
    status = evaluator.calibration_status()

    assert status["calibrated"] is False
    assert status["n_calibration"] == 0
    assert status["interval_halfwidth_db"] is None

    pred = evaluator.predict_v2(
        [
            CandidateDelta(
                cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=43.0
            )
        ]
    )
    assert pred.calibrated is False


def test_drift_injection_widens_status_and_intervals() -> None:
    """Verify drift injection hook widens status interval_halfwidth_db."""
    evaluator = ensure_global_evaluator(
        topology="5_CELL_CORE", warm_up=True, n_samples=60, seed=42
    )
    status_baseline = evaluator.calibration_status()
    baseline_hw = status_baseline["interval_halfwidth_db"]
    assert baseline_hw is not None

    # Inject substantial drift bias on mean_sinr_db
    evaluator.conformal_predictor.set_drift(
        "mean_sinr_db", bias=10.0, noise_std=2.0
    )

    # Ingest new drift-impacted observations
    for i in range(40):
        drifted_pred = evaluator.conformal_predictor.apply_drift(
            "mean_sinr_db", 10.0, seed=i
        )
        evaluator.conformal_predictor.add_observation(
            "mean_sinr_db", predicted=drifted_pred, actual=10.0
        )

    status_drifted = evaluator.calibration_status()
    assert status_drifted["interval_halfwidth_db"] is not None
    assert status_drifted["interval_halfwidth_db"] > baseline_hw * 1.5


def test_held_out_simulator_coverage_above_88_pct() -> None:
    """Verify empirical coverage on held-out simulator stream exceeds 88%."""
    evaluator = ensure_global_evaluator(
        topology="5_CELL_CORE", warm_up=True, n_samples=60, seed=42
    )

    try:
        from arbiter.simulator.engine import SimulationEngine
    except ImportError:
        import pytest
        pytest.skip("arbiter.simulator not available")

    heldout_engine = SimulationEngine(topology="5_CELL_CORE", seed=777)
    heldout_engine.step()

    rng = np.random.default_rng(777)
    cell_ids = list(heldout_engine.cells.keys())
    n_test = 50
    covered = 0

    for i in range(n_test):
        cid = str(rng.choice(cell_ids))
        param = str(
            rng.choice([
                "TX_POWER_DBM",
                "TILT_DEG",
                "SLEEP_STATE",
                "OFFLOAD_RATIO",
            ])
        )
        if param == "TX_POWER_DBM":
            val = float(rng.uniform(33.0, 46.0))
        elif param == "TILT_DEG":
            val = float(rng.uniform(2.0, 8.0))
        elif param == "SLEEP_STATE":
            non_anchor = [c for c in cell_ids if c != "cell_A"]
            cid = str(rng.choice(non_anchor)) if non_anchor else cid
            val = 1.0 if rng.random() < 0.25 else 0.0
        else:
            val = float(rng.uniform(0.0, 0.4))

        delta = [
            CandidateDelta(cell_id=cid, parameter=param, proposed_value=val)
        ]
        pred = evaluator.predict_v2(delta, apply_drift=False)
        sinr_intv = next(
            item for item in pred.intervals if item.kpi == "mean_sinr_db"
        )

        forked = heldout_engine.fork(seed=8000 + i)
        forked.apply_actuation_deltas([
            {
                "target_cell_id": d.cell_id,
                "parameter": d.parameter,
                "requested_value": d.proposed_value,
            }
            for d in delta
        ])
        samples = forked.step()
        meas_ues = forked.traffic_model.get_active_users(forked.tick)
        meas_sinr = (
            float(np.mean([u.sinr_db for u in meas_ues]))
            if meas_ues
            else float(np.mean([s.sinr_db for s in samples]))
        )

        if sinr_intv.lower <= meas_sinr <= sinr_intv.upper:
            covered += 1

    coverage = covered / float(n_test)
    assert coverage >= 0.88, (
        f"Empirical coverage {coverage:.4f} fell below 0.88"
    )
