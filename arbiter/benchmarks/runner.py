"""
arbiter/benchmarks/runner.py
BenchmarkRunner implementation — Branch 4.

SRS traceability:
  FR-9.2  Compare ARBITER against uncoordinated and static-priority baselines (Spec §2.2 page 3).
  FR-9.4  Peak, quiet night (2 AM), and surge benchmarks over 10 random seeds with 95% CI.
  §4.1    POST /api/v1/benchmarks/run endpoint contract.
  §5.2    10 seeds (seed=101..110), 95% CI formula, hypothesis validation.
  §7.2    Monte Carlo harness executes 10 seeds across 3 scenarios in < 60 seconds.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from arbiter.benchmarks.metrics import (
    PerformanceEvaluator,
    calculate_ci95,
)
from arbiter.benchmarks.scenarios import (
    DEFAULT_SEEDS,
    MODE_ARBITER,
    MODE_STATIC_PRIORITY,
    MODE_UNCOORDINATED,
    SCENARIO_2_AM_COLLISION,
    SYNTHETIC_DATA_SOURCE,
    VALID_MODES,
    VALID_SCENARIOS,
    MonteCarloScenarioHarness,
)

# ---------------------------------------------------------------------------
# ASSUMPTIONS block — one place to change if team requirements evolve
# ---------------------------------------------------------------------------
_ASSUMPTIONS: dict[str, Any] = {
    # Default PRNG seeds: exact seed=101..110 from Spec §5.2 page 7
    "DEFAULT_SEEDS": DEFAULT_SEEDS,
    # Execution mode default
    "DEFAULT_MODES": [MODE_UNCOORDINATED, MODE_STATIC_PRIORITY, MODE_ARBITER],
    # Primary baseline against which NFR-5 is evaluated (Spec §5.2)
    "PRIMARY_BASELINE_MODE": MODE_STATIC_PRIORITY,
}


class MetricSummary(BaseModel):
    """Statistical summary for a single KPI across seeds (Spec §5.2)."""
    mean: float = Field(..., description="Sample mean across seeds")
    std_dev: float = Field(..., description="Sample standard deviation (ddof=1)")
    ci_lower: float = Field(..., description="Lower bound of 95% Confidence Interval")
    ci_upper: float = Field(..., description="Upper bound of 95% Confidence Interval")
    samples: list[float] = Field(..., description="Raw metric values across all seeds")


class BenchmarkResult(BaseModel):
    """
    Standardized benchmark outcome schema for ARBITER and baselines.
    Used uniformly across BenchmarkRunner and AblationSuite.
    """
    scenario: str = Field(..., description="Benchmark scenario name (e.g. '2_AM_COLLISION')")
    mode: str = Field(..., description="Evaluation mode (e.g. 'ARBITER', 'STATIC_PRIORITY')")
    seeds: list[int] = Field(..., description="PRNG seeds evaluated (default 101..110)")
    data_source: str = Field(
        default=SYNTHETIC_DATA_SOURCE,
        description="Data generation source: explicitly labeled 'synthetic' (Spec §6)",
    )
    metrics: dict[str, MetricSummary] = Field(
        ...,
        description="Keyed KPI summaries reporting mean, CI95, and raw samples",
    )
    nfr_evaluations: dict[str, bool] = Field(
        default_factory=dict,
        description="Empirical hypothesis validation checks (NFR-1, NFR-5..9)",
    )


class BenchmarkRunner:
    """
    Executes Monte Carlo benchmark experiments across scenarios and modes (FR-9.2, FR-9.4).
    """

    def __init__(self, harness: MonteCarloScenarioHarness | None = None) -> None:
        self._harness = harness or MonteCarloScenarioHarness()

    def run(
        self,
        scenario: str = SCENARIO_2_AM_COLLISION,
        modes: list[str] | None = None,
        seeds: list[int] | None = None,
    ) -> dict[str, BenchmarkResult]:
        """
        Run the Monte Carlo benchmark suite for the specified scenario across seeds.

        Parameters
        ----------
        scenario : str
            One of '2_AM_COLLISION', 'PEAK', 'SURGE'.
        modes : list[str] | None
            Modes to compare (defaults to UNCOORDINATED, STATIC_PRIORITY, ARBITER).
        seeds : list[int] | None
            Seeds list (defaults to 101..110 per Spec §5.2).

        Returns
        -------
        dict[str, BenchmarkResult]
            Keyed by mode name.
        """
        if scenario not in VALID_SCENARIOS:
            # Allow custom scenarios if requested, but validate known ones
            pass

        eval_modes = modes or _ASSUMPTIONS["DEFAULT_MODES"]
        eval_seeds = seeds or _ASSUMPTIONS["DEFAULT_SEEDS"]

        results: dict[str, BenchmarkResult] = {}
        mode_metric_means: dict[str, dict[str, float]] = {}

        for mode in eval_modes:
            raw_metric_samples: dict[str, list[float]] = {}

            for seed in eval_seeds:
                run_kpis = self._harness.run_seed(scenario=scenario, mode=mode, seed=seed)
                for kpi_name, val in run_kpis.items():
                    raw_metric_samples.setdefault(kpi_name, []).append(float(val))

            metric_summaries: dict[str, MetricSummary] = {}
            means_for_eval: dict[str, float] = {}

            for kpi_name, sample_list in raw_metric_samples.items():
                mean_val, ci_low, ci_high = calculate_ci95(sample_list)
                n = len(sample_list)
                if n > 1:
                    variance = sum((x - mean_val) ** 2 for x in sample_list) / (n - 1)
                    std_dev = variance ** 0.5
                else:
                    std_dev = 0.0

                metric_summaries[kpi_name] = MetricSummary(
                    mean=round(mean_val, 4),
                    std_dev=round(std_dev, 4),
                    ci_lower=round(ci_low, 4),
                    ci_upper=round(ci_high, 4),
                    samples=[round(s, 4) for s in sample_list],
                )
                means_for_eval[kpi_name] = mean_val

            mode_metric_means[mode] = means_for_eval

            results[mode] = BenchmarkResult(
                scenario=scenario,
                mode=mode,
                seeds=eval_seeds,
                data_source=SYNTHETIC_DATA_SOURCE,
                metrics=metric_summaries,
                nfr_evaluations={},
            )

        # Evaluate NFR hypotheses for ARBITER against primary baseline if available
        if MODE_ARBITER in results:
            baseline_key = (
                MODE_STATIC_PRIORITY
                if MODE_STATIC_PRIORITY in mode_metric_means
                else (MODE_UNCOORDINATED if MODE_UNCOORDINATED in mode_metric_means else None)
            )
            base_means = mode_metric_means.get(baseline_key) if baseline_key else None
            nfr_checks = PerformanceEvaluator.evaluate_nfr_hypotheses(
                arbiter_metrics=mode_metric_means[MODE_ARBITER],
                baseline_metrics=base_means,
                scenario=scenario,
            )
            results[MODE_ARBITER].nfr_evaluations = nfr_checks

        return results
