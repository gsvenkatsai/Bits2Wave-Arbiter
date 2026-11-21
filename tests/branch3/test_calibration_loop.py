"""Unit tests for Online Twin Calibration and MAPE Calculator.

SRS FR-5.4, FR-5.6, NFR-4.
Verifies MAPE calculation, 1-tick lag alignment, zero/negative SINR guard,
and dynamic uncertainty margin widening above 10% threshold.
"""
import numpy as np
import pytest

from arbiter.twin.calibration import MAPECalculator, OnlineTwinCalibrator
from arbiter.twin.mocks.mock_telemetry_feed import MockTelemetryFeed


def test_mape_calculator_basic():
    """Verify standard MAPE calculation matches expected mathematical percentage."""
    telemetry = np.array([10.0, 20.0, 30.0])
    predicted = np.array([11.0, 19.0, 33.0])
    # Relative errors: |1/10| = 0.10, |-1/20| = 0.05, |3/30| = 0.10 -> mean = 0.08333 -> 8.33%
    mape = MAPECalculator.calculate_mape(telemetry, predicted)
    assert np.isclose(mape, 8.3333, atol=1e-2)


def test_mape_calculator_empty_arrays():
    """Verify MAPE calculation returns 0.0 on empty inputs."""
    assert MAPECalculator.calculate_mape(np.array([]), np.array([])) == 0.0


def test_mape_calculator_zero_and_negative_sinr_guard():
    """Verify denominator guard prevents division by zero or NaN on negative/zero SINR."""
    # SINR in dB can be 0.0, negative (-3 dB), or small
    telemetry = np.array([0.0, -3.0, 1e-6])
    predicted = np.array([1.0, -2.5, 0.5])

    mape = MAPECalculator.calculate_mape(telemetry, predicted, epsilon=1.0)
    assert not np.isnan(mape)
    assert not np.isinf(mape)
    assert mape >= 0.0


def test_calibration_one_tick_lag_alignment():
    """Verify predictions recorded at tick t are calibrated ONLY when tick t+1 telemetry arrives."""
    calibrator = OnlineTwinCalibrator(base_confidence_margin=0.05, mape_target_pct=10.0)

    # Tick 1: Twin makes prediction for cell_A
    calibrator.record_prediction(tick=1, cell_id="cell_A", predicted_sinr_db=15.0)

    # Telemetry for tick 1 arrives: should NOT calibrate tick 1 prediction (1-tick lag requires t+1)
    batch_1_telemetry = [{"cell_id": "cell_A", "sinr_db": 14.5}]
    mape_tick1 = calibrator.ingest_telemetry_tick(current_tick=1, telemetry_samples=batch_1_telemetry)
    assert mape_tick1 == 0.0  # No match yet for lag t-1=0

    # Telemetry for tick 2 arrives: matches tick 1 prediction!
    batch_2_telemetry = [{"cell_id": "cell_A", "sinr_db": 14.0}]
    mape_tick2 = calibrator.ingest_telemetry_tick(current_tick=2, telemetry_samples=batch_2_telemetry)

    # Actual = 14.0, Predicted = 15.0 -> Error = |14 - 15| / 14 = 1/14 ~= 7.14%
    assert mape_tick2 > 0.0
    assert np.isclose(mape_tick2, (1.0 / 14.0) * 100.0, atol=1e-1)


def test_confidence_margin_widening_above_ten_percent():
    """Verify confidence margin widens when MAPE > 10.0% and stays baseline otherwise."""
    calibrator = OnlineTwinCalibrator(base_confidence_margin=0.05, mape_target_pct=10.0)

    # Case 1: Low MAPE (< 10%)
    calibrator.record_prediction(tick=1, cell_id="cell_A", predicted_sinr_db=14.5)
    calibrator.ingest_telemetry_tick(current_tick=2, telemetry_samples=[{"cell_id": "cell_A", "sinr_db": 14.0}])
    # MAPE ~= 3.5% <= 10.0%
    assert calibrator.get_confidence_margin() == 0.05

    # Case 2: High MAPE (> 10%)
    calibrator.record_prediction(tick=2, cell_id="cell_A", predicted_sinr_db=20.0)
    # Telemetry at tick 3 is 10.0 -> error is |10 - 20| / 10 = 100% MAPE!
    calibrator.ingest_telemetry_tick(current_tick=3, telemetry_samples=[{"cell_id": "cell_A", "sinr_db": 10.0}])

    assert calibrator.latest_mape > 10.0
    # Confidence margin must have widened above 0.05
    assert calibrator.get_confidence_margin() > 0.05


def test_mock_telemetry_feed_integration():
    """Verify mock telemetry feeder integrates cleanly with calibration loop."""
    feed = MockTelemetryFeed(cell_ids=["cell_A", "cell_B"], seed=123)
    calibrator = OnlineTwinCalibrator()

    for tick in range(1, 5):
        # Predict at tick
        calibrator.record_prediction(tick=tick, cell_id="cell_A", predicted_sinr_db=14.0)
        calibrator.record_prediction(tick=tick, cell_id="cell_B", predicted_sinr_db=11.5)

        # Ingest feed for current tick
        samples = feed.generate_tick_telemetry()
        calibrator.ingest_telemetry_tick(current_tick=tick, telemetry_samples=samples)

    assert calibrator.latest_mape >= 0.0


def test_stale_prediction_pruning():
    """Verify old predictions (> 10 ticks stale) are pruned to prevent memory growth."""
    calibrator = OnlineTwinCalibrator()
    calibrator.record_prediction(tick=1, cell_id="cell_A", predicted_sinr_db=14.0)
    assert (1, "cell_A") in calibrator._predictions_by_tick

    # Ingest tick 15 -> should prune tick 1
    calibrator.ingest_telemetry_tick(current_tick=15, telemetry_samples=[])
    assert (1, "cell_A") not in calibrator._predictions_by_tick
