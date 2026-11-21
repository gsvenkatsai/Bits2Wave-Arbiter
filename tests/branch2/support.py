"""Deterministic KPI fixtures shared by Branch 2 tests."""
from __future__ import annotations

from typing import Any, Mapping

from arbiter.engine.mocks.mock_twin_client import DEFAULT_KPIS

#: Healthy baseline: U ~= 0.1220 with default weights.
BASE_KPIS: dict[str, float] = dict(DEFAULT_KPIS)

#: Floors hold and U improves (U ~= 0.1486, dU ~= +0.0266).
GOOD_KPIS: dict[str, float] = {
    "coverage_ratio": 0.97,
    "normalized_energy": 0.45,
    "normalized_interference": 0.25,
    "drop_rate_ratio": 0.008,
}

#: Floors hold and U improves more than GOOD (U ~= 0.1645).
GREAT_KPIS: dict[str, float] = {
    "coverage_ratio": 0.98,
    "normalized_energy": 0.42,
    "normalized_interference": 0.22,
    "drop_rate_ratio": 0.005,
}

#: Floors hold but U regresses (U ~= 0.0755, dU ~= -0.0465).
WORSE_KPIS: dict[str, float] = {
    "coverage_ratio": 0.90,
    "normalized_energy": 0.60,
    "normalized_interference": 0.35,
    "drop_rate_ratio": 0.015,
}

#: Coverage floor breached.
BREACH_KPIS: dict[str, float] = {
    "coverage_ratio": 0.85,
    "normalized_energy": 0.40,
    "normalized_interference": 0.20,
    "drop_rate_ratio": 0.010,
}

#: Barely better than the baseline (dU ~= +0.00003), used for the margin guard.
TINY_KPIS: dict[str, float] = {
    "coverage_ratio": 0.9501,
    "normalized_energy": 0.50,
    "normalized_interference": 0.30,
    "drop_rate_ratio": 0.010,
}


def twin_response(
    kpis: Mapping[str, float],
    *,
    floors: bool = True,
    margin: float = 0.0,
    evaluation_id: str = "eval-1",
) -> dict[str, Any]:
    """Build a twin response payload shaped like the Branch 3 contract."""
    return {
        "evaluation_id": evaluation_id,
        "predicted_kpis": dict(kpis),
        "safety_floors_satisfied": floors,
        "confidence_margin": margin,
    }
