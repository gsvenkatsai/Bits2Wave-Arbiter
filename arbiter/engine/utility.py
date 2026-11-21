"""Multi-objective utility scoring for the arbitration ladder.

``U = w1*coverage - w2*energy - w3*interference - w4*drop_rate`` with every KPI
term clamped into ``[0, 1]``.

Baseline semantics and twin-budget accounting
---------------------------------------------
The *baseline* KPI snapshot is the twin's prediction for the "do nothing" /
current configuration, so obtaining it costs one twin evaluation.

Documented assumption: that baseline evaluation is obtained **once per
conflict-graph component** and shared by every command in the component, so it
counts once (``BASELINE_EVALUATIONS``) against that component's six-evaluation
budget, not once per command. When a caller cannot share it (e.g. a different
baseline per command), it must count one evaluation **per command**, leaving
five for the bounded search.
"""
from __future__ import annotations

from typing import Mapping

from arbiter.engine.contracts import (
    FLOOR_COVERAGE,
    FLOOR_DROP_RATE,
    UtilityWeights,
)

__all__ = [
    "UtilityCalculator",
    "BASELINE_EVALUATIONS",
    "KPI_KEYS",
    "clamp01",
]

#: Twin evaluations needed to obtain a component baseline prediction.
BASELINE_EVALUATIONS: int = 1

#: KPI names shared with the digital-twin contract.
KPI_KEYS: tuple[str, str, str, str] = (
    "coverage_ratio",
    "normalized_energy",
    "normalized_interference",
    "drop_rate_ratio",
)


def clamp01(value: float) -> float:
    """Clamp ``value`` into ``[0, 1]``; NaN maps to 0."""
    if value != value:  # NaN guard
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


class UtilityCalculator:
    """Scores KPI snapshots and enforces the safety floors."""

    def __init__(self, weights: UtilityWeights | None = None) -> None:
        self._weights = weights or UtilityWeights()

    @property
    def weights(self) -> UtilityWeights:
        """Weights used when a call does not pass its own."""
        return self._weights

    def terms(self, kpis: Mapping[str, float]) -> tuple[float, float, float, float]:
        """Return the clamped ``(coverage, energy, interference, drop_rate)``."""
        return (
            clamp01(float(kpis.get("coverage_ratio", 0.0))),
            clamp01(float(kpis.get("normalized_energy", 0.0))),
            clamp01(float(kpis.get("normalized_interference", 0.0))),
            clamp01(float(kpis.get("drop_rate_ratio", 0.0))),
        )

    def joint_utility(
        self,
        kpis: Mapping[str, float],
        weights: UtilityWeights | None = None,
    ) -> float:
        """Score one KPI snapshot with the linear multi-objective formula."""
        active = weights or self._weights
        coverage, energy, interference, drop_rate = self.terms(kpis)
        return (
            active.w1 * coverage
            - active.w2 * energy
            - active.w3 * interference
            - active.w4 * drop_rate
        )

    def delta_utility(
        self,
        candidate_kpis: Mapping[str, float],
        baseline_kpis: Mapping[str, float],
        weights: UtilityWeights | None = None,
    ) -> float:
        """``U(candidate) - U(baseline)`` under the same weights."""
        return self.joint_utility(candidate_kpis, weights) - self.joint_utility(
            baseline_kpis, weights
        )

    def floors_ok(
        self,
        kpis: Mapping[str, float],
        twin_floors_satisfied: bool | None = None,
    ) -> bool:
        """Both safety floors hold and the twin did not report a breach.

        The twin's ``safety_floors_satisfied`` flag is never trusted on its own:
        the floors are recomputed from the predicted KPIs and the twin flag can
        only make the verdict stricter. ``None`` means "no twin flag available".
        """
        coverage = float(kpis.get("coverage_ratio", 0.0))
        drop_rate = float(kpis.get("drop_rate_ratio", 1.0))
        computed = coverage >= FLOOR_COVERAGE and drop_rate <= FLOOR_DROP_RATE
        if twin_floors_satisfied is None:
            return computed
        return computed and bool(twin_floors_satisfied)
