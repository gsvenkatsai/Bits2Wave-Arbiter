"""
tests/branch4/test_scenario_statistics.py
Statistical calculation and NFR empirical hypothesis testing — Branch 4.

Sources:
  Branch 4 Spec §5.2 page 7:
    CI_95 = mean +- 1.96 * (std_dev / sqrt(N)) where N = 10 seeds
  SRS §5 (NFR-1, NFR-5..9):
    NFR-1: Decision latency p95 < 100 ms
    NFR-5: Dropped users >= 30% reduction vs static-priority
    NFR-6: Nocturnal energy >= 10% reduction
    NFR-7: Ping-pong oscillations >= 50% reduction
    NFR-8: Coverage floor <= 1.0 percentage point drop
"""
from __future__ import annotations

import math
import pytest

from arbiter.benchmarks.metrics import (
    CI_95_MULTIPLIER,
    PerformanceEvaluator,
    calculate_ci95,
    calculate_reduction_pct,
)


class TestCI95Formula:

    def test_ci95_multiplier_is_pinned_to_exact_spec_value(self):
        """
        Pin multiplier to 1.96 as specified in Branch 4 Spec §5.2 Page 7:
          'CI_95 = mean +- 1.96 * (std_dev / sqrt(N))'
        Note: Standard normal 1.96 is the spec author's explicit choice over Student's t (2.262 at N=10, df=9).
        """
        assert CI_95_MULTIPLIER == pytest.approx(1.96)

    def test_ci95_manual_analytical_vector(self):
        """
        Vector: [10.0, 12.0, 14.0, 16.0, 18.0]
        N = 5
        mean = 14.0
        variance (ddof=1) = ((16 + 4 + 0 + 4 + 16) / 4) = 40 / 4 = 10.0
        std_dev = sqrt(10.0) ~= 3.16227766
        margin = 1.96 * (3.16227766 / sqrt(5)) ~= 1.96 * 1.41421356 ~= 2.77185858
        ci_lower ~= 11.22814
        ci_upper ~= 16.77186
        """
        samples = [10.0, 12.0, 14.0, 16.0, 18.0]
        mean, ci_low, ci_high = calculate_ci95(samples)
        assert mean == pytest.approx(14.0)
        expected_std = math.sqrt(10.0)
        expected_margin = 1.96 * (expected_std / math.sqrt(5))
        assert ci_low == pytest.approx(14.0 - expected_margin)
        assert ci_high == pytest.approx(14.0 + expected_margin)

    def test_ci95_identical_samples_zero_ci_width(self):
        """Zero variance: all samples identical -> std_dev=0, lower=upper=mean."""
        samples = [42.0] * 10
        mean, ci_low, ci_high = calculate_ci95(samples)
        assert mean == pytest.approx(42.0)
        assert ci_low == pytest.approx(42.0)
        assert ci_high == pytest.approx(42.0)

    def test_ci95_empty_list_returns_zeros(self):
        """Empty input list returns (0.0, 0.0, 0.0) without raising."""
        assert calculate_ci95([]) == (0.0, 0.0, 0.0)

    def test_ci95_single_sample_returns_mean_without_crash(self):
        """Single sample (N=1) returns (val, val, val) without division-by-zero on ddof=1."""
        assert calculate_ci95([7.5]) == (7.5, 7.5, 7.5)


class TestReductionPct:

    def test_calculate_reduction_pct_normal(self):
        """Standard reduction: baseline 100, test 70 -> 30% reduction."""
        assert calculate_reduction_pct(100.0, 70.0) == pytest.approx(30.0)

    def test_calculate_reduction_pct_increase_returns_negative(self):
        """Negative reduction when test > baseline."""
        assert calculate_reduction_pct(100.0, 110.0) == pytest.approx(-10.0)

    def test_calculate_reduction_pct_zero_baseline_guarded(self):
        """
        Divide-by-zero guard: baseline == 0.0 must return 0.0 without raising.
        """
        assert calculate_reduction_pct(0.0, 50.0) == pytest.approx(0.0)
        assert calculate_reduction_pct(0.0, 0.0) == pytest.approx(0.0)


class TestNFREvaluation:

    def test_nfr_5_drop_reduction_threshold(self):
        """
        NFR-5: >= 30.0% reduction in dropped users under 2_AM_COLLISION.
        30.0% -> Pass; 29.9% -> Fail.
        """
        base = {"dropped_users": 100.0}
        # 30% reduction -> 70.0 dropped users -> PASS
        eval_pass = PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"dropped_users": 70.0},
            baseline_metrics=base,
            scenario="2_AM_COLLISION",
        )
        assert eval_pass["nfr_5_drop_reduction"] is True

        # 29.9% reduction -> 70.1 dropped users -> FAIL
        eval_fail = PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"dropped_users": 70.1},
            baseline_metrics=base,
            scenario="2_AM_COLLISION",
        )
        assert eval_fail["nfr_5_drop_reduction"] is False

    def test_nfr_6_night_energy_savings_threshold(self):
        """
        NFR-6: >= 10.0% reduction in night energy.
        10.0% -> Pass; 9.9% -> Fail.
        """
        base = {"energy_consumption_w": 1000.0}
        # 10% reduction -> 900.0 -> PASS
        assert PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"energy_consumption_w": 900.0},
            baseline_metrics=base,
        )["nfr_6_night_energy_savings"] is True

        # 9.9% reduction -> 901.0 -> FAIL
        assert PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"energy_consumption_w": 901.0},
            baseline_metrics=base,
        )["nfr_6_night_energy_savings"] is False

    def test_nfr_7_oscillation_suppression_threshold(self):
        """
        NFR-7: >= 50.0% reduction in oscillations.
        50.0% -> Pass; 49.9% -> Fail.
        """
        base = {"oscillations_per_10min": 20.0}
        # 50% reduction -> 10.0 -> PASS
        assert PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"oscillations_per_10min": 10.0},
            baseline_metrics=base,
        )["nfr_7_oscillation_suppression"] is True

        # 49.5% reduction -> 10.1 -> FAIL
        assert PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"oscillations_per_10min": 10.1},
            baseline_metrics=base,
        )["nfr_7_oscillation_suppression"] is False

    def test_nfr_8_coverage_floor_threshold(self):
        """
        NFR-8: Coverage does not drop > 1.0 percentage point (0.01) below baseline.
        Drop of 1.0 pp (0.94 -> 0.93) -> PASS
        Drop of 1.1 pp (0.94 -> 0.929) -> FAIL
        """
        base = {"coverage_ratio": 0.94}
        # 1.0 pp drop -> PASS
        assert PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"coverage_ratio": 0.93},
            baseline_metrics=base,
        )["nfr_8_coverage_floor"] is True

        # 1.1 pp drop -> FAIL
        assert PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"coverage_ratio": 0.929},
            baseline_metrics=base,
        )["nfr_8_coverage_floor"] is False

    def test_nfr_1_latency_p95_evaluation(self):
        """
        NFR-1: Decision latency p95 SHALL be < 100 ms (SRS §5 page 5).
        99.9 ms -> PASS; 100.0 ms -> FAIL.
        """
        res_pass = PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"decision_latency_p95_ms": 99.9},
        )
        assert res_pass["nfr_1_latency_p95"] is True

        res_fail = PerformanceEvaluator.evaluate_nfr_hypotheses(
            arbiter_metrics={"decision_latency_p95_ms": 100.0},
        )
        assert res_fail["nfr_1_latency_p95"] is False
