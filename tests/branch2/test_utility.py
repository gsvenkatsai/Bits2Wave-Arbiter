"""Utility calculator tests: formula, clamping and safety floors."""
from __future__ import annotations

import pytest

from arbiter.engine.contracts import UtilityWeights
from arbiter.engine.utility import (
    BASELINE_EVALUATIONS,
    KPI_KEYS,
    UtilityCalculator,
    clamp01,
)

BASE = {
    "coverage_ratio": 0.95,
    "normalized_energy": 0.50,
    "normalized_interference": 0.30,
    "drop_rate_ratio": 0.010,
}
GOOD = {
    "coverage_ratio": 0.97,
    "normalized_energy": 0.45,
    "normalized_interference": 0.25,
    "drop_rate_ratio": 0.008,
}
WORSE = {
    "coverage_ratio": 0.90,
    "normalized_energy": 0.60,
    "normalized_interference": 0.35,
    "drop_rate_ratio": 0.015,
}


def test_clamp01_bounds_and_nan() -> None:
    assert clamp01(-1.0) == 0.0
    assert clamp01(0.4) == pytest.approx(0.4)
    assert clamp01(2.0) == 1.0
    assert clamp01(float("nan")) == 0.0


def test_kpi_keys_match_the_twin_contract() -> None:
    assert KPI_KEYS == (
        "coverage_ratio",
        "normalized_energy",
        "normalized_interference",
        "drop_rate_ratio",
    )
    assert BASELINE_EVALUATIONS == 1


def test_joint_utility_matches_formula() -> None:
    # 0.3*0.95 - 0.2*0.5 - 0.2*0.3 - 0.3*0.01 = 0.122
    assert UtilityCalculator().joint_utility(BASE) == pytest.approx(0.122)


def test_joint_utility_uses_custom_weights() -> None:
    calculator = UtilityCalculator()
    weights = UtilityWeights(w1=1.0, w2=0.0, w3=0.0, w4=0.0)
    assert calculator.joint_utility(BASE, weights) == pytest.approx(0.95)
    assert calculator.weights.as_tuple() == pytest.approx((0.3, 0.2, 0.2, 0.3))


def test_joint_utility_clamps_terms() -> None:
    clipped = UtilityCalculator().joint_utility(
        {
            "coverage_ratio": 1.8,
            "normalized_energy": -1.0,
            "normalized_interference": 2.0,
            "drop_rate_ratio": -0.5,
        }
    )
    assert clipped == pytest.approx(0.3 - 0.2)


def test_terms_are_clamped() -> None:
    terms = UtilityCalculator().terms(
        {"coverage_ratio": 2.0, "normalized_energy": -3.0}
    )
    assert terms == pytest.approx((1.0, 0.0, 0.0, 0.0))


def test_missing_kpis_are_pessimistic() -> None:
    assert UtilityCalculator().joint_utility({}) == pytest.approx(0.0)


def test_delta_utility_sign() -> None:
    calculator = UtilityCalculator()
    assert calculator.delta_utility(GOOD, BASE) > 0.0
    assert calculator.delta_utility(WORSE, BASE) < 0.0
    assert calculator.delta_utility(BASE, BASE) == pytest.approx(0.0)


def test_delta_utility_matches_difference() -> None:
    calculator = UtilityCalculator()
    expected = calculator.joint_utility(GOOD) - calculator.joint_utility(BASE)
    assert calculator.delta_utility(GOOD, BASE) == pytest.approx(expected)


def test_floors_ok_boundaries() -> None:
    calculator = UtilityCalculator()
    assert calculator.floors_ok({"coverage_ratio": 0.90, "drop_rate_ratio": 0.02}) is True
    assert calculator.floors_ok({"coverage_ratio": 0.8999, "drop_rate_ratio": 0.0}) is False
    assert calculator.floors_ok({"coverage_ratio": 1.0, "drop_rate_ratio": 0.0201}) is False
    assert calculator.floors_ok({}) is False


def test_floors_ok_never_trusts_the_twin_flag_alone() -> None:
    calculator = UtilityCalculator()

    # Twin says fine, but the predicted KPIs breach the coverage floor.
    assert (
        calculator.floors_ok(
            {"coverage_ratio": 0.80, "drop_rate_ratio": 0.01},
            twin_floors_satisfied=True,
        )
        is False
    )
    # KPIs are fine, but the twin reported a breach: be conservative.
    assert (
        calculator.floors_ok(
            {"coverage_ratio": 0.99, "drop_rate_ratio": 0.001},
            twin_floors_satisfied=False,
        )
        is False
    )
    # No twin flag: computed floors decide.
    assert (
        calculator.floors_ok({"coverage_ratio": 0.99, "drop_rate_ratio": 0.001}) is True
    )
