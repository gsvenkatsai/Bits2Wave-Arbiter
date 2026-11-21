"""
arbiter/benchmarks/ablations.py
Ablation study suite — Branch 4.

SRS traceability:
  FR-9.3  Support ablations: no-twin and no-modify configurations (Spec §2.2 page 3).
  Output contract: Conforms to identical BenchmarkResult schema as BenchmarkRunner (Spec §FR-9.3).
"""
from __future__ import annotations

from typing import Any
from arbiter.benchmarks.runner import BenchmarkResult, BenchmarkRunner
from arbiter.benchmarks.scenarios import (
    DEFAULT_SEEDS,
    MODE_ARBITER,
    MODE_NO_MODIFY,
    MODE_NO_TWIN,
    SCENARIO_2_AM_COLLISION,
)

# Canonical ablation modes from spec §2.2 FR-9.3
ABLATION_MODES: list[str] = [
    MODE_NO_TWIN,
    MODE_NO_MODIFY,
    MODE_ARBITER,
]


class AblationSuite:
    """
    Executes ablation experiments comparing full ARBITER against no-twin and no-modify configurations.
    Returns identical dict[str, BenchmarkResult] shape as BenchmarkRunner.run().
    """

    def __init__(self, runner: BenchmarkRunner | None = None) -> None:
        self._runner = runner or BenchmarkRunner()

    def run(
        self,
        scenario: str = SCENARIO_2_AM_COLLISION,
        seeds: list[int] | None = None,
    ) -> dict[str, BenchmarkResult]:
        """
        Run ablation suite for a scenario across seeds.

        Parameters
        ----------
        scenario : str
            Scenario name (default: '2_AM_COLLISION').
        seeds : list[int] | None
            Random seeds (default: 101..110 from Spec §5.2).

        Returns
        -------
        dict[str, BenchmarkResult]
            Keyed by ablation mode ('NO_TWIN', 'NO_MODIFY', 'ARBITER').
        """
        eval_seeds = seeds or DEFAULT_SEEDS
        return self._runner.run(
            scenario=scenario,
            modes=ABLATION_MODES,
            seeds=eval_seeds,
        )
