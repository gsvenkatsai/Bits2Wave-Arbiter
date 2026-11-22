"""Analytical toy digital twin stand-in for simulation and benchmarks.

This module provides an in-tree scripted twin implementation so that
production code in arbiter/simulator/ does not import from tests/.
"""
from __future__ import annotations

from typing import Any, Dict, List


def scenario_kpis(candidate: List[Dict[str, Any]]) -> Dict[str, float]:
    """Analytical toy twin modelling the 2 AM collision curve."""
    coverage = 0.95
    energy = 0.50
    interference = 0.30
    drop_rate = 0.01
    for delta in candidate:
        parameter = str(delta.get("parameter", "")).upper()
        to_value = float(delta.get("to_value", 0.0))
        if parameter == "SLEEP_STATE":
            coverage -= 0.20 * to_value
            energy -= 0.20 * to_value
            interference -= 0.10 * to_value
            drop_rate += 0.02 * to_value
        elif parameter == "OFFLOAD_RATIO":
            coverage -= 0.02 * to_value
            energy -= 0.30 * to_value
            interference += 0.05 * to_value
            drop_rate += 0.15 * to_value * to_value
        elif parameter == "TX_POWER_DBM":
            from_value = float(delta.get("from_value", 0.0))
            baseline = from_value if from_value > 0.0 else 30.0
            step = to_value - baseline
            coverage += 0.05 * step
            energy += 0.02 * step
            interference += 0.02 * step
    return {
        "coverage_ratio": max(0.0, min(1.0, coverage)),
        "normalized_energy": max(0.0, min(1.0, energy)),
        "normalized_interference": max(0.0, min(1.0, interference)),
        "drop_rate_ratio": max(0.0, min(1.0, drop_rate)),
    }


def get_scripted_twin(topology: str = "5_CELL_CORE") -> Any:
    """Return a scripted twin satisfying the TwinClient protocol.

    Prefers Contract C1 make_default_twin if available; otherwise uses
    ScriptedTwin from arbiter.engine.mocks.mock_twin_client.
    """
    try:
        from arbiter.engine.default_twin import make_default_twin

        return make_default_twin(topology=topology, kind="scripted")
    except ImportError:
        from arbiter.engine.mocks.mock_twin_client import ScriptedTwin

        return ScriptedTwin(scenario_kpis)
