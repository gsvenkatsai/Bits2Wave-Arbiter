"""
arbiter/benchmarks/scenarios.py
Monte Carlo scenario definitions and synthetic execution harness — Branch 4.

SRS traceability:
  FR-9.4  Peak, quiet night (2 AM), and surge benchmarks over 10 random seeds with 95% CI (Spec §2.2 page 3).
  §4.1    Scenario names and modes (page 5).
  §5.2    Seeds seed=101..110 (page 7).
  §6      Parallel Development & Mocking Strategy:
          "Developer 4 uses a synthetic telemetry generator to test the dashboard,
          WebSocket broadcasts, and charts in isolation before Branch 1 simulator integration." (Spec §6 page 7).
"""
from __future__ import annotations

from typing import Any
import numpy as np

# ---------------------------------------------------------------------------
# Exact scenario names from spec:
#   Spec §2.2 FR-9.4 page 3: "Peak, quiet night (2 AM), and surge benchmarks"
#   Spec §4.1 page 5: "scenario": "2_AM_COLLISION"
#   SRS §3.9 FR-9.4 page 5: "peak, quiet-night (2 AM) and surge scenarios"
# ---------------------------------------------------------------------------
SCENARIO_2_AM_COLLISION: str = "2_AM_COLLISION"
SCENARIO_PEAK: str = "PEAK"
SCENARIO_SURGE: str = "SURGE"

VALID_SCENARIOS: set[str] = {
    SCENARIO_2_AM_COLLISION,
    SCENARIO_PEAK,
    SCENARIO_SURGE,
}

# ---------------------------------------------------------------------------
# Modes from spec §4.1 page 5:
#   "modes": ["UNCOORDINATED", "STATIC_PRIORITY", "ARBITER"]
# Ablation modes from spec §2.2 FR-9.3 page 3:
#   "Support ablations: no-twin and no-modify configurations"
# ---------------------------------------------------------------------------
MODE_UNCOORDINATED: str = "UNCOORDINATED"
MODE_STATIC_PRIORITY: str = "STATIC_PRIORITY"
MODE_ARBITER: str = "ARBITER"
MODE_NO_TWIN: str = "NO_TWIN"
MODE_NO_MODIFY: str = "NO_MODIFY"

VALID_MODES: set[str] = {
    MODE_UNCOORDINATED,
    MODE_STATIC_PRIORITY,
    MODE_ARBITER,
    MODE_NO_TWIN,
    MODE_NO_MODIFY,
}

# ---------------------------------------------------------------------------
# Seeds from spec §5.2 page 7:
#   "The benchmarking harness executes 10 distinct simulation runs using fixed PRNG seeds (seed=101..110)"
# ---------------------------------------------------------------------------
DEFAULT_SEEDS: list[int] = list(range(101, 111))

# Data source label explicitly required by user & spec §6:
SYNTHETIC_DATA_SOURCE: str = "synthetic"


def generate_synthetic_run(
    scenario: str,
    mode: str,
    seed: int,
    ticks: int = 100,
) -> dict[str, float]:
    """
    SYNTHETIC Telemetry and KPI Generator.
    Source: Branch 4 Spec §6 page 7:
      "Developer 4 uses a synthetic telemetry generator to test the dashboard,
      WebSocket broadcasts, and charts in isolation before Branch 1 simulator integration."

    Label: SYNTHETIC
    Deterministic: Parameterized strictly by `seed` via numpy RandomState.

    Outputs all primary KPIs required by FR-9.5 (Spec §2.2 page 3):
      - dropped_users
      - energy_consumption_w
      - oscillations_per_10min
      - coverage_ratio
      - twin_mape_pct
      - decision_latency_p95_ms (NFR-1)
      - sinr_db
      - throughput_mbps
    """
    rng = np.random.RandomState(seed)

    # Base characteristics by scenario
    if scenario == SCENARIO_2_AM_COLLISION:
        base_drops = 15.0
        base_energy = 2200.0  # Nocturnal baseline
        base_oscillations = 12.0
        base_coverage = 0.94
        base_sinr = 12.0
    elif scenario == SCENARIO_PEAK:
        base_drops = 35.0
        base_energy = 4500.0
        base_oscillations = 18.0
        base_coverage = 0.91
        base_sinr = 8.5
    elif scenario == SCENARIO_SURGE:
        base_drops = 50.0
        base_energy = 5200.0
        base_oscillations = 25.0
        base_coverage = 0.88
        base_sinr = 6.0
    else:
        # Default fallback
        base_drops = 20.0
        base_energy = 3000.0
        base_oscillations = 15.0
        base_coverage = 0.92
        base_sinr = 10.0

    # Noise per seed
    noise = rng.normal(0.0, 0.5)

    # Mode performance adjustments (reflecting ARBITER vs baselines)
    if mode == MODE_UNCOORDINATED:
        # High collisions, severe oscillations
        drops = max(0.0, base_drops * 1.5 + noise * 2.0)
        energy = base_energy * 1.1 + noise * 50.0
        oscillations = max(0.0, base_oscillations * 1.8 + noise)
        coverage = min(1.0, max(0.0, base_coverage - 0.02 + noise * 0.005))
        mape = 14.5 + abs(noise)
        latency_p95 = 5.0 + abs(noise)  # no referee, direct execution
    elif mode == MODE_STATIC_PRIORITY:
        # Baseline per NFR-5: static priority resolution
        drops = max(0.0, base_drops * 1.2 + noise * 1.5)
        energy = base_energy * 1.05 + noise * 40.0
        oscillations = max(0.0, base_oscillations * 1.2 + noise)
        coverage = min(1.0, max(0.0, base_coverage - 0.01 + noise * 0.005))
        mape = 12.0 + abs(noise)
        latency_p95 = 8.0 + abs(noise)
    elif mode == MODE_ARBITER:
        # ARBITER full mode: >= 30% drop reduction vs static priority, >= 10% energy reduction,
        # >= 50% fewer oscillations, coverage maintained within 1 pp, p95 latency < 100 ms
        drops = max(0.0, base_drops * 0.7 + noise * 0.8)  # ~40% reduction vs static
        energy = base_energy * 0.88 + noise * 30.0        # ~12% reduction
        oscillations = max(0.0, base_oscillations * 0.4 + noise * 0.5)  # ~60% reduction
        coverage = min(1.0, max(0.0, base_coverage + 0.005 + noise * 0.002))
        mape = 4.8 + abs(noise) * 0.5                     # < 10% MAPE guard
        latency_p95 = 18.0 + abs(noise) * 2.0             # well below 100 ms (NFR-1)
    elif mode == MODE_NO_TWIN:
        # Ablation 1: No digital twin prediction (spec §2.2 FR-9.3)
        drops = max(0.0, base_drops * 0.9 + noise * 1.0)
        energy = base_energy * 0.95 + noise * 35.0
        oscillations = max(0.0, base_oscillations * 0.7 + noise * 0.6)
        coverage = min(1.0, max(0.0, base_coverage + noise * 0.003))
        mape = 0.0  # no twin
        latency_p95 = 10.0 + abs(noise)
    elif mode == MODE_NO_MODIFY:
        # Ablation 2: No continuous modification ladder (spec §2.2 FR-9.3)
        drops = max(0.0, base_drops * 0.85 + noise * 0.9)
        energy = base_energy * 0.92 + noise * 30.0
        oscillations = max(0.0, base_oscillations * 0.6 + noise * 0.6)
        coverage = min(1.0, max(0.0, base_coverage + noise * 0.003))
        mape = 5.2 + abs(noise) * 0.5
        latency_p95 = 12.0 + abs(noise)
    else:
        drops = base_drops
        energy = base_energy
        oscillations = base_oscillations
        coverage = base_coverage
        mape = 10.0
        latency_p95 = 20.0

    return {
        "dropped_users": float(drops),
        "energy_consumption_w": float(energy),
        "oscillations_per_10min": float(oscillations),
        "coverage_ratio": float(coverage),
        "twin_mape_pct": float(mape),
        "decision_latency_p95_ms": float(latency_p95),
        "sinr_db": float(base_sinr + noise),
        "throughput_mbps": float(100.0 - drops * 0.5 + noise),
    }


class MonteCarloScenarioHarness:
    """
    Monte Carlo scenario runner executing simulation runs across 10 random seeds.
    Source: Branch 4 Spec §2.2 FR-9.4 and §5.2.
    """

    def __init__(self, simulator_fn: Any = None) -> None:
        """
        Pluggable simulation provider.
        If simulator_fn is None, defaults to SYNTHETIC generator (Spec §6).
        """
        self._simulator_fn = simulator_fn or generate_synthetic_run

    def run_seed(self, scenario: str, mode: str, seed: int) -> dict[str, float]:
        """Execute a single run for a given seed."""
        return self._simulator_fn(scenario=scenario, mode=mode, seed=seed)
