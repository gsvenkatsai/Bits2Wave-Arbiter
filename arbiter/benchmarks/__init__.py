"""Benchmarking package — Branch 4 (FR-9.2 to FR-9.5, NFR-5 to NFR-9, NFR-14)."""
from arbiter.benchmarks.ablations import AblationSuite
from arbiter.benchmarks.metrics import PerformanceEvaluator, calculate_ci95, calculate_reduction_pct
from arbiter.benchmarks.runner import BenchmarkResult, BenchmarkRunner, MetricSummary
from arbiter.benchmarks.scenarios import (
    DEFAULT_SEEDS,
    SCENARIO_2_AM_COLLISION,
    SCENARIO_PEAK,
    SCENARIO_SURGE,
    SYNTHETIC_DATA_SOURCE,
    MonteCarloScenarioHarness,
)

__all__ = [
    "AblationSuite",
    "BenchmarkResult",
    "BenchmarkRunner",
    "DEFAULT_SEEDS",
    "MetricSummary",
    "MonteCarloScenarioHarness",
    "PerformanceEvaluator",
    "SCENARIO_2_AM_COLLISION",
    "SCENARIO_PEAK",
    "SCENARIO_SURGE",
    "SYNTHETIC_DATA_SOURCE",
    "calculate_ci95",
    "calculate_reduction_pct",
]
