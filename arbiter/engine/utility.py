"""Multi-objective utility and safety-floor evaluation.

``U = w1*Coverage - w2*Energy - w3*Interference - w4*DropRate`` with every
KPI term clipped into ``[0, 1]``.
"""
from __future__ import annotations

from typing import Mapping, cast

from arbiter.engine.constants import (
    DEFAULT_WEIGHT_COVERAGE,
    DEFAULT_WEIGHT_DROP_RATE,
    DEFAULT_WEIGHT_ENERGY,
    DEFAULT_WEIGHT_INTERFERENCE,
    MAX_DROP_RATE_RATIO,
    MIN_COVERAGE_RATIO,
)
from arbiter.engine.contracts import UtilityWeights

__all__ = [
    "KPI_KEYS",
    "clamp01",
    "weight_tuple",
    "compute_utility",
    "utility_delta",
    "floors_satisfied",
    "better",
]

KPI_KEYS: tuple[str, str, str, str] = (
    "coverage_ratio",
    "normalized_energy",
    "normalized_interference",
    "drop_rate_ratio",
)

DEFAULT_WEIGHTS: tuple[float, float, float, float] = (
    DEFAULT_WEIGHT_COVERAGE,
    DEFAULT_WEIGHT_ENERGY,
    DEFAULT_WEIGHT_INTERFERENCE,
    DEFAULT_WEIGHT_DROP_RATE,
)


def clamp01(value: float) -> float:
    """Clip ``value`` into ``[0, 1]`` (NaN becomes 0)."""
    if value != value:  # NaN guard
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def weight_tuple(weights: UtilityWeights | None) -> tuple[float, float, float, float]:
    """Return ``(w1, w2, w3, w4)`` for either the local or a shared weights type."""
    if weights is None:
        return DEFAULT_WEIGHTS
    as_tuple = getattr(weights, "as_tuple", None)
    if callable(as_tuple):
        return cast(tuple[float, float, float, float], as_tuple())
    return (
        float(getattr(weights, "w1", DEFAULT_WEIGHTS[0])),
        float(getattr(weights, "w2", DEFAULT_WEIGHTS[1])),
        float(getattr(weights, "w3", DEFAULT_WEIGHTS[2])),
        float(getattr(weights, "w4", DEFAULT_WEIGHTS[3])),
    )


def compute_utility(
    kpis: Mapping[str, float],
    weights: UtilityWeights | None = None,
) -> float:
    """Score a KPI snapshot. Missing KPIs are treated as their ideal value 0."""
    w1, w2, w3, w4 = weight_tuple(weights)
    coverage = clamp01(float(kpis.get("coverage_ratio", 0.0)))
    energy = clamp01(float(kpis.get("normalized_energy", 0.0)))
    interference = clamp01(float(kpis.get("normalized_interference", 0.0)))
    drop_rate = clamp01(float(kpis.get("drop_rate_ratio", 0.0)))
    return w1 * coverage - w2 * energy - w3 * interference - w4 * drop_rate


def utility_delta(
    candidate_kpis: Mapping[str, float],
    baseline_kpis: Mapping[str, float],
    weights: UtilityWeights | None = None,
) -> float:
    """Predicted joint utility improvement of a candidate over the baseline."""
    return compute_utility(candidate_kpis, weights) - compute_utility(baseline_kpis, weights)


def floors_satisfied(
    kpis: Mapping[str, float],
    min_coverage: float = MIN_COVERAGE_RATIO,
    max_drop_rate: float = MAX_DROP_RATE_RATIO,
) -> bool:
    """True when the coverage and drop-rate safety floors both hold."""
    coverage = float(kpis.get("coverage_ratio", 0.0))
    drop_rate = float(kpis.get("drop_rate_ratio", 1.0))
    return coverage >= min_coverage and drop_rate <= max_drop_rate


def better(candidate: float, incumbent: float | None) -> bool:
    """Strictly-better comparison used to keep the best feasible point."""
    return incumbent is None or candidate > incumbent


def satisfies_margin(delta: float, margin: float) -> bool:
    """Uncertainty guard: ``|dU|`` must exceed the twin's confidence margin."""
    return abs(delta) >= margin
