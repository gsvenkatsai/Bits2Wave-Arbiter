"""
arbiter/benchmarks/metrics.py
Statistical evaluation and empirical hypothesis validation — Branch 4.

SRS traceability:
  FR-9.5  Report primary KPIs: dropped users, night energy, oscillations, coverage, twin MAPE, latency p95.
  NFR-1   Decision latency p95 SHALL be < 100 ms (SRS §5 page 5).
  NFR-5   Dropped Users: >= 30% reduction under 2 AM collision scenario vs static-priority (Spec §5.2 page 7).
  NFR-6   Night Energy: >= 10% reduction in nocturnal energy consumption (Spec §5.2 page 7).
  NFR-7   Oscillation Suppression: >= 50% reduction in ping-pong parameter oscillations (Spec §5.2 page 7).
  NFR-8   Coverage Floor: Guarantee coverage does not drop > 1.0 percentage point below baseline (Spec §5.2 page 7).
  NFR-14  Results SHALL be reproducible across 10 seeds with 95% CI (Spec §5.2 page 7).

Exact Confidence Interval Formula:
  CI_95 = mean +- 1.96 * (std_dev / sqrt(N))
  where N = 10 seeds (Branch 4 Spec §5.2 page 7).
"""
from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Statistical constants
# ---------------------------------------------------------------------------

# NOTE: 1.96 is explicitly specified in Branch 4 Spec §5.2 Page 7:
#   "CI_95 = mean +- 1.96 * (std_dev / sqrt(N)) where N = 10 seeds"
# Standard normal z_0.025 = 1.96 was chosen by the specification author
# rather than Student's t critical value (t = 2.262 for N=10, df=9).
# We strictly adhere to the specification formula.
CI_95_MULTIPLIER: float = 1.96


def calculate_ci95(values: list[float]) -> tuple[float, float, float]:
    """
    Calculate mean and 95% Confidence Interval using the exact spec formula:
      CI_95 = mean +- 1.96 * (std_dev / sqrt(N))
    Source: Branch 4 Spec §5.2 page 7.

    Parameters
    ----------
    values : list[float]
        Sample values across seeds (e.g. N = 10).

    Returns
    -------
    tuple[float, float, float]
        (mean, ci_lower, ci_upper)
    """
    n = len(values)
    if n == 0:
        return (0.0, 0.0, 0.0)
    mean_val = float(sum(values) / n)
    if n == 1:
        return (mean_val, mean_val, mean_val)

    # Sample standard deviation (ddof=1)
    variance = sum((x - mean_val) ** 2 for x in values) / (n - 1)
    std_dev = math.sqrt(variance)

    margin = CI_95_MULTIPLIER * (std_dev / math.sqrt(n))
    return (mean_val, mean_val - margin, mean_val + margin)


def calculate_reduction_pct(baseline: float, test: float) -> float:
    """
    Calculate percentage reduction from baseline to test:
      reduction = ((baseline - test) / baseline) * 100.0

    Divide-by-zero guard
    --------------------
    If baseline == 0.0, returns 0.0 to prevent ZeroDivisionError.
    """
    if baseline == 0.0:
        return 0.0
    return ((baseline - test) / baseline) * 100.0


class PerformanceEvaluator:
    """
    Evaluates empirical hypothesis validation assertions against NFR thresholds.
    Source: Branch 4 Spec §5.2 page 7 and SRS §5 (NFR-1, NFR-5..9).
    """

    @staticmethod
    def evaluate_nfr_hypotheses(
        arbiter_metrics: dict[str, Any],
        baseline_metrics: dict[str, Any] | None = None,
        scenario: str = "2_AM_COLLISION",
    ) -> dict[str, bool]:
        """
        Evaluate empirical hypotheses:
          - NFR-1: decision latency p95 < 100 ms
          - NFR-5: dropped users >= 30% reduction (in 2_AM_COLLISION vs baseline)
          - NFR-6: nocturnal energy >= 10% reduction
          - NFR-7: ping-pong oscillations >= 50% reduction
          - NFR-8: coverage drop <= 1.0 percentage points below baseline

        Parameters
        ----------
        arbiter_metrics : dict
            Dict mapping metric name to mean float value (or MetricSummary).
        baseline_metrics : dict | None
            Dict mapping metric name to mean float value for baseline.
        scenario : str
            Scenario name. NFR-5 specifically targets '2_AM_COLLISION' (Spec §5.2).

        Returns
        -------
        dict[str, bool]
            Dict of pass/fail results for each applicable hypothesis.
        """
        results: dict[str, bool] = {}

        def _get_val(m: dict[str, Any], key: str) -> float | None:
            v = m.get(key)
            if v is None:
                return None
            if hasattr(v, "mean"):
                return float(v.mean)
            if isinstance(v, dict) and "mean" in v:
                return float(v["mean"])
            return float(v)

        # NFR-1: Decision latency p95 < 100 ms (SRS §5 NFR-1, evaluated on ARBITER)
        arb_lat = _get_val(arbiter_metrics, "decision_latency_p95_ms")
        if arb_lat is not None:
            results["nfr_1_latency_p95"] = arb_lat < 100.0

        if not baseline_metrics:
            return results

        arb_drop = _get_val(arbiter_metrics, "dropped_users")
        base_drop = _get_val(baseline_metrics, "dropped_users")
        if arb_drop is not None and base_drop is not None and scenario == "2_AM_COLLISION":
            # NFR-5: >= 30% reduction in dropped users under 2 AM collision vs static priority
            drop_red = calculate_reduction_pct(base_drop, arb_drop)
            results["nfr_5_drop_reduction"] = drop_red >= 30.0

        arb_energy = _get_val(arbiter_metrics, "energy_consumption_w")
        base_energy = _get_val(baseline_metrics, "energy_consumption_w")
        if arb_energy is not None and base_energy is not None:
            # NFR-6: >= 10% reduction in nocturnal energy
            energy_red = calculate_reduction_pct(base_energy, arb_energy)
            results["nfr_6_night_energy_savings"] = energy_red >= 10.0

        arb_osc = _get_val(arbiter_metrics, "oscillations_per_10min")
        base_osc = _get_val(baseline_metrics, "oscillations_per_10min")
        if arb_osc is not None and base_osc is not None:
            # NFR-7: >= 50% reduction in ping-pong parameter oscillations
            osc_red = calculate_reduction_pct(base_osc, arb_osc)
            results["nfr_7_oscillation_suppression"] = osc_red >= 50.0

        arb_cov = _get_val(arbiter_metrics, "coverage_ratio")
        base_cov = _get_val(baseline_metrics, "coverage_ratio")
        if arb_cov is not None and base_cov is not None:
            # NFR-8: Coverage drop <= 1.0 percentage point (0.01 in ratio) below baseline
            # (coverage does not drop > 1.0 percentage point below baseline)
            diff_pp = (base_cov - arb_cov) * 100.0
            results["nfr_8_coverage_floor"] = diff_pp <= 1.0

        return results
