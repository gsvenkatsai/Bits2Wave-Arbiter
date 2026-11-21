"""Online Digital Twin Calibration and MAPE Computation.

SRS FR-5.4, FR-5.6, NFR-4.
OnlineTwinCalibrator ingests telemetry with a 1-TICK LAG:
Predictions made at tick t are compared against telemetry from tick t+1.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from arbiter.shared.constants import (
    TWIN_MAPE_TARGET_PCT,
)
from arbiter.twin.analytical_model import (
    DEFAULT_COLD_START_CONFIDENCE_MARGIN,
)


class MAPECalculator:
    """Calculates Mean Absolute Percentage Error (MAPE) with robust zero/negative guarding.

    Spec formula:
      mape = mean(abs((telemetry_sinr - predicted_sinr) / telemetry_sinr)) * 100.0

    Guard notes:
      SINR values expressed in decibels (dB) can be zero or negative (e.g. -3 dB).
      Direct division by near-zero telemetry values produces division-by-zero or extreme spikes.
      This calculator guards the denominator using an absolute floor epsilon (default 1.0 dB
      or linear translation) to ensure numerical stability.
    """

    @staticmethod
    def calculate_mape(
        telemetry_sinr: np.ndarray,
        predicted_sinr: np.ndarray,
        epsilon: float = 1.0,
    ) -> float:
        """Compute guarded MAPE percentage between telemetry and twin predictions."""
        t_arr = np.asarray(telemetry_sinr, dtype=np.float64)
        p_arr = np.asarray(predicted_sinr, dtype=np.float64)

        if t_arr.size == 0 or p_arr.size == 0:
            return 0.0

        if t_arr.shape != p_arr.shape:
            # Flatten or match lengths
            min_len = min(t_arr.size, p_arr.size)
            t_arr = t_arr.ravel()[:min_len]
            p_arr = p_arr.ravel()[:min_len]

        # Guard denominator: avoid division by zero or numbers near zero
        guarded_denominator = np.where(np.abs(t_arr) < epsilon, epsilon, np.abs(t_arr))
        abs_relative_errors = np.abs((t_arr - p_arr) / guarded_denominator)
        mape = float(np.mean(abs_relative_errors) * 100.0)
        return mape


@dataclass
class StoredPrediction:
    """Historical prediction recorded at tick t for 1-tick lag calibration."""
    tick: int
    cell_id: str
    predicted_sinr_db: float
    timestamp_epoch_ms: int


class OnlineTwinCalibrator:
    """Recalibrates twin online from telemetry stream with a strict 1-TICK LAG (FR-5.4).

    Predictions emitted at tick t are stored in a rolling buffer.
    When telemetry from tick t+1 arrives, the calibrator compares the actual measured
    telemetry against tick t predictions to calculate MAPE.

    If MAPE > 10.0%, the uncertainty confidence margin is dynamically widened.
    """

    def __init__(
        self,
        base_confidence_margin: float = DEFAULT_COLD_START_CONFIDENCE_MARGIN,
        mape_target_pct: float = TWIN_MAPE_TARGET_PCT,
        history_window_size: int = 50,
    ) -> None:
        self.base_confidence_margin: float = base_confidence_margin
        self.mape_target_pct: float = mape_target_pct
        self.current_confidence_margin: float = base_confidence_margin
        self.latest_mape: float = 0.0
        self.history_window_size: int = history_window_size

        # Buffer: key = (tick, cell_id) -> predicted_sinr_db
        self._predictions_by_tick: Dict[Tuple[int, str], float] = {}
        self._recent_errors: deque[float] = deque(maxlen=history_window_size)

    def record_prediction(self, tick: int, cell_id: str, predicted_sinr_db: float) -> None:
        """Buffer a twin prediction generated at simulation tick t."""
        self._predictions_by_tick[(tick, cell_id)] = predicted_sinr_db

    def ingest_telemetry_tick(
        self,
        current_tick: int,
        telemetry_samples: List[Dict[str, Any]],
    ) -> float:
        """Ingest telemetry arriving at tick t+1 and compare with predictions from tick t.

        telemetry_samples: list of dicts with keys {'cell_id': str, 'sinr_db': float}
        """
        # TODO(INTEGRATION): Connect calibration loop to real SQLite event stream from Branch 4
        lagged_tick = current_tick - 1
        matched_actual: List[float] = []
        matched_predicted: List[float] = []

        for sample in telemetry_samples:
            cid = str(sample.get("cell_id", ""))
            sinr = float(sample.get("sinr_db", 0.0))
            pred_key = (lagged_tick, cid)

            if pred_key in self._predictions_by_tick:
                matched_predicted.append(self._predictions_by_tick[pred_key])
                matched_actual.append(sinr)
                # Clean up consumed prediction
                del self._predictions_by_tick[pred_key]

        if matched_actual and matched_predicted:
            batch_mape = MAPECalculator.calculate_mape(
                np.array(matched_actual),
                np.array(matched_predicted),
            )
            self._recent_errors.append(batch_mape)
            self.latest_mape = float(np.mean(list(self._recent_errors)))
            self._update_uncertainty_margin()
        else:
            batch_mape = self.latest_mape

        # Prune old predictions older than 10 ticks
        stale_ticks = [k for k in self._predictions_by_tick if k[0] < current_tick - 10]
        for k in stale_ticks:
            self._predictions_by_tick.pop(k, None)

        return self.latest_mape

    def _update_uncertainty_margin(self) -> None:
        """Widen confidence margin when MAPE exceeds target threshold (FR-5.6)."""
        if self.latest_mape > self.mape_target_pct:
            excess = self.latest_mape - self.mape_target_pct
            # Proportional widening beyond 10% target
            widening_factor = 1.0 + (excess / 10.0)
            self.current_confidence_margin = float(self.base_confidence_margin * widening_factor)
        else:
            self.current_confidence_margin = self.base_confidence_margin

    def get_confidence_margin(self) -> float:
        """Return the current calibrated confidence guard margin."""
        return self.current_confidence_margin
