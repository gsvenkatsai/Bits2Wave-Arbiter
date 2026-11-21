"""Unit tests for LKGReversionManager and fail-safe timeout guard.

SRS FR-5.5, NFR-10.
Verifies automatic fallback to LKG response when evaluation exceeds 100 ms,
ensures no unhandled exceptions raise into caller, and validates deterministic delay injection.
"""
import pytest
import time

from arbiter.twin.fail_safe import LKGReversionManager
from arbiter.twin.evaluator import DigitalTwinEvaluator
from arbiter.twin.schemas import CandidateDelta, PredictedKPIs


def test_failsafe_normal_fast_execution(mock_state_provider):
    """Verify normal evaluation under 100 ms completes with safety_floors_satisfied=True."""
    evaluator = DigitalTwinEvaluator(state_provider=mock_state_provider)
    deltas = [
        CandidateDelta(cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=43.0),
    ]
    resp = evaluator.predict(deltas)

    assert resp.safety_floors_satisfied is True
    assert resp.computation_time_ms < 100.0


def test_failsafe_forced_120ms_delay_triggers_fallback(mock_state_provider):
    """Verify forced 120 ms delay deterministically triggers LKG fallback without raising exceptions (NFR-10)."""
    failsafe_manager = LKGReversionManager(timeout_ms=100.0)
    # Inject deterministic 120 ms delay to exceed 100 ms limit
    failsafe_manager.injected_delay_ms = 120.0

    evaluator = DigitalTwinEvaluator(
        state_provider=mock_state_provider,
        fail_safe_manager=failsafe_manager,
    )

    deltas = [
        CandidateDelta(cell_id="cell_B", parameter="SLEEP_STATE", proposed_value=0.0),
    ]

    # Must NOT raise any TimeoutError or unhandled exception
    resp = evaluator.predict(deltas, timeout_budget_ms=100.0)

    # Must signal fail-safe conservative result
    assert resp.safety_floors_satisfied is False
    assert resp.predicted_kpis.drop_rate_ratio > 0.02
    assert resp.predicted_kpis.coverage_ratio < 0.90
    assert resp.confidence_margin >= 0.10

    failsafe_manager.shutdown()


def test_failsafe_tight_timeout_budget_enforcement(mock_state_provider):
    """Verify tighter per-request timeout budget (e.g. 15 ms) triggers fallback cleanly if exceeded."""
    failsafe_manager = LKGReversionManager(timeout_ms=100.0)
    failsafe_manager.injected_delay_ms = 35.0  # Exceeds 15 ms budget, but below 100 ms default

    evaluator = DigitalTwinEvaluator(
        state_provider=mock_state_provider,
        fail_safe_manager=failsafe_manager,
    )

    deltas = [
        CandidateDelta(cell_id="cell_A", parameter="TX_POWER_DBM", proposed_value=44.0),
    ]

    resp = evaluator.predict(deltas, timeout_budget_ms=15.0)

    # Budget was 15 ms, task took 35 ms -> fallback triggered
    assert resp.safety_floors_satisfied is False

    failsafe_manager.shutdown()


def test_failsafe_exception_resilience():
    """Verify that any unexpected exception inside eval_fn is safely trapped into LKG fallback."""
    failsafe_manager = LKGReversionManager(timeout_ms=100.0)

    def crashing_eval() -> PredictedKPIs:
        raise RuntimeError("Simulated crash inside physics engine")

    resp = failsafe_manager.execute_with_guard(
        evaluation_id="eval-crash-test",
        eval_fn=crashing_eval,
        affected_cells=["cell_A"],
    )

    assert resp.safety_floors_satisfied is False
    assert resp.evaluation_id == "eval-crash-test"

    failsafe_manager.shutdown()
