"""Utility function and safety-floor tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from arbiter.engine.utility import (
    DEFAULT_WEIGHTS,
    better,
    clamp01,
    compute_utility,
    floors_satisfied,
    satisfies_margin,
    utility_delta,
    weight_tuple,
)

from support import BASE_KPIS, BREACH_KPIS, GOOD_KPIS, WORSE_KPIS


def test_clamp01_bounds() -> None:
    assert clamp01(-0.5) == 0.0
    assert clamp01(0.42) == pytest.approx(0.42)
    assert clamp01(1.5) == 1.0
    assert clamp01(float("nan")) == 0.0


def test_weight_tuple_defaults() -> None:
    assert weight_tuple(None) == DEFAULT_WEIGHTS
    assert DEFAULT_WEIGHTS == pytest.approx((0.3, 0.2, 0.2, 0.3))


def test_weight_tuple_falls_back_to_attributes() -> None:
    legacy = SimpleNamespace(w1=0.5, w2=0.1, w3=0.1, w4=0.3)
    assert weight_tuple(legacy) == pytest.approx((0.5, 0.1, 0.1, 0.3))


def test_compute_utility_matches_formula() -> None:
    # 0.3*0.95 - 0.2*0.5 - 0.2*0.3 - 0.3*0.01 = 0.122
    assert compute_utility(BASE_KPIS) == pytest.approx(0.122)


def test_compute_utility_improves_for_better_kpis() -> None:
    assert compute_utility(GOOD_KPIS) > compute_utility(BASE_KPIS)
    assert compute_utility(WORSE_KPIS) < compute_utility(BASE_KPIS)


def test_compute_utility_clips_terms() -> None:
    clipped = compute_utility(
        {
            "coverage_ratio": 1.8,
            "normalized_energy": -1.0,
            "normalized_interference": 2.0,
            "drop_rate_ratio": -0.5,
        }
    )
    assert clipped == pytest.approx(0.3 * 1.0 - 0.0 - 0.2 * 1.0 - 0.0)


def test_compute_utility_missing_kpis_default_to_zero() -> None:
    assert compute_utility({"coverage_ratio": 1.0}) == pytest.approx(0.3)


def test_compute_utility_custom_weights() -> None:
    from arbiter.engine.contracts import UtilityWeights

    weights = UtilityWeights(coverage=1.0, energy=0.0, interference=0.0, drop_rate=0.0)
    assert compute_utility(BASE_KPIS, weights) == pytest.approx(0.95)


def test_utility_delta_sign() -> None:
    assert utility_delta(GOOD_KPIS, BASE_KPIS) == pytest.approx(0.0266, abs=1e-4)
    assert utility_delta(WORSE_KPIS, BASE_KPIS) < 0.0


def test_floors_satisfied_boundaries() -> None:
    assert floors_satisfied({"coverage_ratio": 0.90, "drop_rate_ratio": 0.02}) is True
    assert floors_satisfied({"coverage_ratio": 0.8999, "drop_rate_ratio": 0.0}) is False
    assert floors_satisfied({"coverage_ratio": 1.0, "drop_rate_ratio": 0.0201}) is False
    assert floors_satisfied(BREACH_KPIS) is False


def test_floors_satisfied_missing_kpis_are_pessimistic() -> None:
    assert floors_satisfied({}) is False


def test_better_and_margin_helpers() -> None:
    assert better(1.0, None) is True
    assert better(1.0, 0.5) is True
    assert better(0.5, 0.5) is False
    assert satisfies_margin(0.5, 0.25) is True
    assert satisfies_margin(-0.5, 0.25) is True
    assert satisfies_margin(0.1, 0.25) is False
