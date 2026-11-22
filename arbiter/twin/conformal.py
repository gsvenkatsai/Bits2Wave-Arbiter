"""Conformal Safety Intervals for Project ARBITER v2 Digital Twin.

Implements split conformal prediction per KPI:
  - dropped_users
  - mean_sinr_db
  - energy_w
  - interference_db
  - coverage_ratio
  - drop_rate_ratio
  - normalized_energy
  - normalized_interference

Maintains a rolling calibration set of residuals |twin_pred - ground_truth|
(default window 200). For miscoverage rate alpha=0.1, computes finite-sample
quantile q = ceil((n+1)(1-alpha))-th smallest residual.

Guarantees finite-sample marginal coverage under exchangeability.
With n < 30 calibration points, returns calibrated=False with conservative
default intervals.
"""
from __future__ import annotations

import collections
import json
import math
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from arbiter.shared.v2_contracts import PredictionInterval, TwinPredictionV2

# Core KPI keys specified in Project ARBITER v2
CORE_KPIS: Tuple[str, ...] = (
    "dropped_users",
    "mean_sinr_db",
    "energy_w",
    "interference_db",
    "coverage_ratio",
    "drop_rate_ratio",
    "normalized_energy",
    "normalized_interference",
)

# Wide conservative default interval half-widths when n < 30
DEFAULT_CONSERVATIVE_INTERVALS: Dict[str, float] = {
    "dropped_users": 10.0,
    "mean_sinr_db": 6.0,
    "energy_w": 15.0,
    "interference_db": 6.0,
    "coverage_ratio": 0.15,
    "drop_rate_ratio": 0.05,
    "normalized_energy": 0.25,
    "normalized_interference": 0.25,
}

# Physical bounds for clipping interval endpoints
KPI_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "dropped_users": (0.0, None),
    "mean_sinr_db": (None, None),
    "energy_w": (0.0, None),
    "interference_db": (None, None),
    "coverage_ratio": (0.0, 1.0),
    "drop_rate_ratio": (0.0, 1.0),
    "normalized_energy": (0.0, 1.0),
    "normalized_interference": (0.0, 1.0),
}


class ConformalPredictor:
    """Computes split conformal prediction intervals per cellular KPI."""

    def __init__(
        self,
        window_size: int = 200,
        min_calibration_points: int = 30,
        alpha: float = 0.1,
    ) -> None:
        self.window_size: int = window_size
        self.min_calibration_points: int = min_calibration_points
        self.default_alpha: float = alpha

        # Rolling buffers of absolute residuals per KPI
        self._residuals: Dict[str, collections.deque[float]] = {
            kpi: collections.deque(maxlen=window_size) for kpi in CORE_KPIS
        }

        # Controllable drift injection hook: kpi -> (bias, noise_std)
        self._drift_hooks: Dict[str, Tuple[float, float]] = {}

    def get_calibration_count(self, kpi: str) -> int:
        """Return count of stored residuals for a given KPI."""
        if kpi not in self._residuals:
            return 0
        return len(self._residuals[kpi])

    def add_residual(self, kpi: str, residual: float) -> None:
        """Add an absolute error residual for a KPI."""
        if kpi not in self._residuals:
            self._residuals[kpi] = collections.deque(maxlen=self.window_size)
        self._residuals[kpi].append(float(abs(residual)))

    def add_observation(
        self, kpi: str, predicted: float, actual: float
    ) -> None:
        """Record twin prediction and actual ground truth observation."""
        res = abs(predicted - actual)
        self.add_residual(kpi, res)

    def add_batch_observations(
        self,
        predicted: Dict[str, float],
        actual: Dict[str, float],
    ) -> None:
        """Record a batch of predicted and ground-truth KPI observations."""
        for kpi, act_val in actual.items():
            if kpi in predicted:
                self.add_observation(kpi, predicted[kpi], act_val)

    def set_drift(
        self, kpi: str, bias: float, noise_std: float = 0.0
    ) -> None:
        """Inject synthetic drift (bias + noise) on a KPI output."""
        self._drift_hooks[kpi] = (float(bias), float(noise_std))

    def clear_drift(self, kpi: Optional[str] = None) -> None:
        """Clear drift injection hooks for one or all KPIs."""
        if kpi is None:
            self._drift_hooks.clear()
        else:
            self._drift_hooks.pop(kpi, None)

    def apply_drift(
        self, kpi: str, value: float, seed: Optional[int] = None
    ) -> float:
        """Apply active drift bias and noise to a raw predicted value."""
        if kpi not in self._drift_hooks:
            return value
        bias, noise_std = self._drift_hooks[kpi]
        noise = 0.0
        if noise_std > 0.0:
            import random
            rng = random.Random(seed) if seed is not None else random
            noise = rng.gauss(0.0, noise_std)
        return float(value + bias + noise)

    def compute_quantile(
        self, kpi: str, alpha: float
    ) -> Tuple[float, bool, int]:
        """Compute conformal quantile q for target miscoverage rate alpha.

        Formula:
          n = len(residuals)
          k = ceil((n + 1) * (1 - alpha))
          q = sorted_residuals[k - 1]

        Returns (q, calibrated_flag, n_calibration).
        """
        residuals = self._residuals.get(kpi, collections.deque())
        n = len(residuals)

        if n < self.min_calibration_points:
            # Cold start: uncalibrated conservative fallback
            q_default = DEFAULT_CONSERVATIVE_INTERVALS.get(kpi, 1.0)
            return q_default, False, n

        # Finite-sample conformal quantile: ceil((n + 1) * (1 - alpha))
        sorted_res = sorted(residuals)
        k = math.ceil((n + 1) * (1.0 - alpha))
        idx = min(max(k - 1, 0), n - 1)
        q = sorted_res[idx]

        return float(q), True, n

    def calibration_status(
        self, kpi: str = "mean_sinr_db", alpha: Optional[float] = None
    ) -> Dict[str, Any]:
        """Contract C3: Report conformal calibration status.

        Returns:
            calibrated flag, n_calibration, and interval_halfwidth_db.
        """
        effective_alpha = self.default_alpha if alpha is None else alpha
        q, is_calibrated, n_cal = self.compute_quantile(kpi, effective_alpha)
        return {
            "calibrated": is_calibrated,
            "n_calibration": n_cal,
            "interval_halfwidth_db": (
                float(round(q, 4)) if is_calibrated else None
            ),
        }

    def predict_intervals(
        self,
        predicted_kpis: Dict[str, float],
        alpha: Optional[float] = None,
        candidate_id: str = "cand-0",
        apply_drift_flag: bool = True,
    ) -> TwinPredictionV2:
        """Generate conformal PredictionIntervals and TwinPredictionV2."""
        effective_alpha = self.default_alpha if alpha is None else alpha
        nominal_coverage = round(1.0 - effective_alpha, 4)

        intervals: List[PredictionInterval] = []
        is_all_calibrated = True
        total_cal = 0

        for kpi, raw_mean in predicted_kpis.items():
            if apply_drift_flag:
                mean_val = self.apply_drift(kpi, raw_mean)
            else:
                mean_val = raw_mean
            q, is_calibrated, n_cal = self.compute_quantile(
                kpi, effective_alpha
            )

            if not is_calibrated:
                is_all_calibrated = False
            total_cal = max(total_cal, n_cal)

            lower_raw = mean_val - q
            upper_raw = mean_val + q

            # Optional physical bounds clipping
            bounds = KPI_BOUNDS.get(kpi, (None, None))
            lower_bound, upper_bound = bounds
            if lower_bound is not None:
                lower = max(lower_bound, lower_raw)
            else:
                lower = lower_raw

            if upper_bound is not None:
                upper = min(upper_bound, upper_raw)
            else:
                upper = upper_raw

            intervals.append(
                PredictionInterval(
                    kpi=kpi,
                    mean=round(float(mean_val), 4),
                    lower=round(float(lower), 4),
                    upper=round(float(upper), 4),
                    coverage=nominal_coverage,
                )
            )

        return TwinPredictionV2(
            candidate_id=candidate_id,
            intervals=intervals,
            calibrated=is_all_calibrated,
            n_calibration=total_cal,
        )


def record_v2_calibration_event(
    conn: sqlite3.Connection,
    candidate_id: str,
    tick: int,
    predicted: Dict[str, float],
    actual: Dict[str, float],
    calibration_id: Optional[str] = None,
) -> str:
    """Persist calibration prediction and actual observation into table."""
    cid = calibration_id or f"cal-{uuid.uuid4().hex[:12]}"
    residuals = {
        k: abs(predicted[k] - actual[k])
        for k in actual
        if k in predicted
    }

    payload = {
        "candidate_id": candidate_id,
        "tick": tick,
        "predicted": predicted,
        "actual": actual,
        "residuals": residuals,
        "recorded_at_epoch_ms": int(time.time() * 1000),
    }

    query = """
    INSERT OR REPLACE INTO v2_calibration (
        calibration_id, candidate_id, tick, payload
    ) VALUES (?, ?, ?, ?)
    """
    conn.execute(query, (cid, candidate_id, tick, json.dumps(payload)))
    conn.commit()
    return cid
