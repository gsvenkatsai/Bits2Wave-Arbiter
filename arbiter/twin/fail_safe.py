"""Fail-safe timeout and LKG reversion management.

SRS FR-5.5, NFR-10:
If a twin evaluation exceeds 100 ms, the system SHALL fail safe and revert
to the Last-Known-Good (LKG) configuration from lkg_store.
It must never raise into the caller.
"""
from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Callable, Dict, List, Optional

from arbiter.shared.constants import (
    TWIN_TIMEOUT_MS,
    MIN_COVERAGE_RATIO,
    MAX_DROP_RATE_RATIO,
)
from arbiter.twin.schemas import (
    CandidateDelta,
    PredictedKPIs,
    TwinEvaluationResponse,
)


class LKGReversionManager:
    """Guards digital twin evaluation with strict timeout and fallback to LKG (FR-5.5, NFR-10).

    If evaluation exceeds timeout_ms (default 100 ms), returns a conservative fail-safe response
    with safety_floors_satisfied=False so arbitration takes the conservative path.
    Never raises an unhandled exception into the caller.
    """

    def __init__(
        self,
        timeout_ms: float = TWIN_TIMEOUT_MS,
        executor: Optional[concurrent.futures.ThreadPoolExecutor] = None,
    ) -> None:
        self.timeout_ms: float = timeout_ms
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = executor
        self._owns_executor: bool = executor is None
        # Injectable delay hook for deterministic testing
        self.injected_delay_ms: float = 0.0

    def get_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Lazily initialize thread pool executor."""
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        return self._executor

    def execute_with_guard(
        self,
        evaluation_id: str,
        eval_fn: Callable[[], PredictedKPIs],
        affected_cells: List[str],
        timeout_budget_ms: Optional[float] = None,
        confidence_margin: float = 0.05,
    ) -> TwinEvaluationResponse:
        """Execute evaluation function under strict latency budget with automatic LKG fallback."""
        effective_timeout_ms = min(self.timeout_ms, timeout_budget_ms) if timeout_budget_ms else self.timeout_ms
        timeout_seconds = effective_timeout_ms / 1000.0

        start_time = time.perf_counter()

        # If deterministic test delay is injected, simulate it
        if self.injected_delay_ms > 0:
            def delayed_eval() -> PredictedKPIs:
                time.sleep(self.injected_delay_ms / 1000.0)
                return eval_fn()
            target_fn = delayed_eval
        else:
            target_fn = eval_fn

        executor = self.get_executor()
        future = executor.submit(target_fn)

        try:
            predicted_kpis = future.result(timeout=timeout_seconds)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            # Verify safety floors
            floors_ok = (
                predicted_kpis.coverage_ratio >= MIN_COVERAGE_RATIO and
                predicted_kpis.drop_rate_ratio <= MAX_DROP_RATE_RATIO
            )

            return TwinEvaluationResponse(
                evaluation_id=evaluation_id,
                computation_time_ms=elapsed_ms,
                predicted_kpis=predicted_kpis,
                safety_floors_satisfied=floors_ok,
                confidence_margin=confidence_margin,
            )

        except (concurrent.futures.TimeoutError, TimeoutError):
            # Timed out (> 100 ms) -> fail safe, revert to conservative LKG response
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return self._build_failsafe_response(
                evaluation_id=evaluation_id,
                affected_cells=affected_cells,
                elapsed_ms=elapsed_ms,
                confidence_margin=confidence_margin,
            )
        except Exception:
            # Any unexpected error in twin -> fail safe, do not raise
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return self._build_failsafe_response(
                evaluation_id=evaluation_id,
                affected_cells=affected_cells,
                elapsed_ms=elapsed_ms,
                confidence_margin=confidence_margin,
            )

    def _build_failsafe_response(
        self,
        evaluation_id: str,
        affected_cells: List[str],
        elapsed_ms: float,
        confidence_margin: float,
    ) -> TwinEvaluationResponse:
        """Create conservative fallback response signaling safety failure."""
        safe_kpis = PredictedKPIs(
            coverage_ratio=0.80,  # Below MIN_COVERAGE_RATIO (0.90) to force safety floor violation
            normalized_energy=1.0,
            normalized_interference=0.8,
            drop_rate_ratio=0.08,  # Above MAX_DROP_RATE_RATIO (0.02)
            predicted_sinr_avg_db=-5.0,
            affected_cell_ids=affected_cells,
        )
        return TwinEvaluationResponse(
            evaluation_id=evaluation_id,
            computation_time_ms=elapsed_ms,
            predicted_kpis=safe_kpis,
            safety_floors_satisfied=False,  # Explicitly False: conservative path taken
            confidence_margin=max(confidence_margin, 0.15),
        )

    def shutdown(self) -> None:
        """Clean up thread executor if owned."""
        if self._owns_executor and self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
