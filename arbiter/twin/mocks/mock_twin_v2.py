"""Mock Digital Twin v2 for early development and testing.

Provides deterministic calibrated prediction intervals (mean +/- fixed margin)
matching arbiter.shared.v2_contracts.TwinPredictionV2.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from arbiter.shared.v2_contracts import PredictionInterval, TwinPredictionV2
from arbiter.twin.schemas import CandidateDelta


DEFAULT_MOCK_INTERVALS: Dict[str, Dict[str, float]] = {
    "coverage_ratio": {
        "mean": 0.942,
        "margin": 0.025,
    },
    "drop_rate_ratio": {
        "mean": 0.004,
        "margin": 0.003,
    },
    "normalized_energy": {
        "mean": 0.448,
        "margin": 0.038,
    },
    "normalized_interference": {
        "mean": 0.285,
        "margin": 0.035,
    },
    "dropped_users": {
        "mean": 2.0,
        "margin": 1.5,
    },
    "mean_sinr_db": {
        "mean": 14.5,
        "margin": 1.5,
    },
    "energy_w": {
        "mean": 45.0,
        "margin": 5.0,
    },
    "interference_db": {
        "mean": -95.0,
        "margin": 2.0,
    },
}


class MockDigitalTwinV2:
    """Mock twin implementation returning fixed-margin calibrated intervals."""

    def __init__(
        self,
        interval_configs: Optional[Dict[str, Dict[str, float]]] = None,
        calibrated: bool = True,
        n_calibration: int = 50,
    ) -> None:
        self.interval_configs = interval_configs or DEFAULT_MOCK_INTERVALS
        self.calibrated = calibrated
        self.n_calibration = n_calibration

    def predict_v2(
        self,
        candidate_deltas: Optional[List[CandidateDelta]] = None,
        candidate_id: str = "cand-mock-001",
        alpha: float = 0.1,
    ) -> TwinPredictionV2:
        """Return fixed-margin intervals matching TwinPredictionV2."""
        intervals: List[PredictionInterval] = []
        coverage = round(1.0 - alpha, 4)

        for kpi, conf in self.interval_configs.items():
            mean = conf["mean"]
            margin = conf["margin"]
            intervals.append(
                PredictionInterval(
                    kpi=kpi,
                    mean=round(float(mean), 4),
                    lower=round(float(mean - margin), 4),
                    upper=round(float(mean + margin), 4),
                    coverage=coverage,
                )
            )

        return TwinPredictionV2(
            candidate_id=candidate_id,
            intervals=intervals,
            calibrated=self.calibrated,
            n_calibration=self.n_calibration,
        )
