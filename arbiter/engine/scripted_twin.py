"""Scripted (analytical toy) twin for the 2 AM collision - explicit opt-in only.

This module exists because the *production* tree must never import from
``tests/``. The toy KPI curve that used to live in
``tests/branch2/test_decision_ladder.py::_scenario_kpis`` now lives here, and
``tests/branch2/test_decision_ladder.py`` re-imports it from this module.

It is **not** a digital twin: it is a hand-written linear KPI curve that models
the 2 AM collision shape:

* ``SLEEP_STATE``  : coverage/energy/interference fall, drop rate rises
* ``OFFLOAD_RATIO``: energy falls, drop rate grows quadratically
* ``TX_POWER_DBM`` : coverage/energy/interference rise per dB

It is only produced when a caller asks for it explicitly
(``make_default_twin(kind="scripted")`` / :func:`make_scripted_twin`), and
building it always logs a warning so it can never silently stand in for the
real twin (contract C1).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from arbiter.engine.mocks.mock_twin_client import ScriptedTwin

__all__ = [
    "SCENARIO_KPIS_SOURCE",
    "SCRIPTED_TWIN_WARNING",
    "make_scripted_twin",
    "scenario_kpis",
]

logger = logging.getLogger(__name__)

#: Which component a caller is really talking to. Surfaced in logs/metrics.
SCENARIO_KPIS_SOURCE = "arbiter.engine.scripted_twin (analytical toy curve)"

#: Logged every time the scripted twin is constructed.
SCRIPTED_TWIN_WARNING = (
    "ScriptedTwin requested explicitly: KPI predictions come from the "
    "hand-written 2 AM collision curve, NOT from the digital twin. "
    "Use make_default_twin(kind='real') for arbitration decisions."
)

#: Baseline KPIs of the toy curve before any candidate delta is applied.
BASELINE_KPIS: Dict[str, float] = {
    "coverage_ratio": 0.95,
    "normalized_energy": 0.50,
    "normalized_interference": 0.30,
    "drop_rate_ratio": 0.01,
}

#: Per-parameter linear coefficients of the toy curve.
SLEEP_COEFFICIENTS: Dict[str, float] = {
    "coverage_ratio": -0.20,
    "normalized_energy": -0.20,
    "normalized_interference": -0.10,
    "drop_rate_ratio": 0.02,
}
OFFLOAD_COEFFICIENTS: Dict[str, float] = {
    "coverage_ratio": -0.02,
    "normalized_energy": -0.30,
    "normalized_interference": 0.05,
}
#: ``drop_rate_ratio`` grows with ``value**2`` for OFFLOAD_RATIO.
OFFLOAD_DROP_QUADRATIC = 0.15
TX_POWER_COEFFICIENTS: Dict[str, float] = {
    "coverage_ratio": 0.05,
    "normalized_energy": 0.02,
    "normalized_interference": 0.02,
}
#: Reference power used when a TX_POWER delta omits ``from_value``.
TX_POWER_REFERENCE_DBM = 30.0


def scenario_kpis(candidate: List[dict[str, Any]]) -> Dict[str, float]:
    """Analytical toy twin modelling the 2 AM collision curve."""
    values: Dict[str, float] = dict(BASELINE_KPIS)
    for delta in candidate:
        parameter = str(delta.get("parameter", "")).upper()
        to_value = float(delta.get("to_value", 0.0))
        if parameter == "SLEEP_STATE":
            for key, coefficient in SLEEP_COEFFICIENTS.items():
                values[key] += coefficient * to_value
        elif parameter == "OFFLOAD_RATIO":
            for key, coefficient in OFFLOAD_COEFFICIENTS.items():
                values[key] += coefficient * to_value
            values["drop_rate_ratio"] += (
                OFFLOAD_DROP_QUADRATIC * to_value * to_value
            )
        elif parameter == "TX_POWER_DBM":
            from_value = float(delta.get("from_value", 0.0))
            step = to_value - (
                from_value if from_value > 0.0 else TX_POWER_REFERENCE_DBM
            )
            for key, coefficient in TX_POWER_COEFFICIENTS.items():
                values[key] += coefficient * step
    return {
        "coverage_ratio": max(0.0, min(1.0, values["coverage_ratio"])),
        "normalized_energy": max(0.0, min(1.0, values["normalized_energy"])),
        "normalized_interference": max(
            0.0, min(1.0, values["normalized_interference"])
        ),
        "drop_rate_ratio": max(0.0, min(1.0, values["drop_rate_ratio"])),
    }


def make_scripted_twin(*, warn: bool = True) -> ScriptedTwin:
    """Build the analytical toy twin, logging why it should not be used."""
    if warn:
        logger.warning(SCRIPTED_TWIN_WARNING)
    return ScriptedTwin(scenario_kpis)
