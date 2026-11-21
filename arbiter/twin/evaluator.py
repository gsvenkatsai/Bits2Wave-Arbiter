"""Digital Twin Evaluator engine and API callable.

SRS FR-5.1, FR-5.3, NFR-2.
Evaluates candidate parameter deltas and predicts joint multi-cell KPIs
with an ultra-fast target latency (< 10 ms, P95 < 8 ms).
Supports v2 conformal safety intervals via predict_v2().
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

import numpy as np

from arbiter.shared.constants import (
    MIN_COVERAGE_RATIO,
    MAX_DROP_RATE_RATIO,
)
from arbiter.shared.v2_contracts import TwinPredictionV2
from arbiter.twin.analytical_model import (
    AnalyticalPropagationChain,
    DEFAULT_MAX_TX_POWER_WATTS,
    DEFAULT_INTERFERENCE_SCALE_WATTS,
)
from arbiter.twin.calibration import OnlineTwinCalibrator
from arbiter.twin.conformal import ConformalPredictor
from arbiter.twin.fail_safe import LKGReversionManager
from arbiter.twin.schemas import (
    CandidateDelta,
    ClusterSnapshot,
    NetworkStateProvider,
    PredictedKPIs,
    TwinEvaluationRequest,
    TwinEvaluationResponse,
)


class DigitalTwinEvaluator:
    """Predictive evaluation engine using closed-form analytical models."""

    def __init__(
        self,
        state_provider: NetworkStateProvider,
        calibrator: Optional[OnlineTwinCalibrator] = None,
        fail_safe_manager: Optional[LKGReversionManager] = None,
        propagation_chain: Optional[AnalyticalPropagationChain] = None,
        conformal_predictor: Optional[ConformalPredictor] = None,
    ) -> None:
        self.state_provider: NetworkStateProvider = state_provider
        self.calibrator: OnlineTwinCalibrator = (
            calibrator or OnlineTwinCalibrator()
        )
        self.fail_safe_manager: LKGReversionManager = (
            fail_safe_manager or LKGReversionManager()
        )
        self.propagation_chain: AnalyticalPropagationChain = (
            propagation_chain or AnalyticalPropagationChain()
        )
        self.conformal_predictor: ConformalPredictor = (
            conformal_predictor or ConformalPredictor()
        )

    def predict(
        self,
        candidate_deltas: List[CandidateDelta],
        timeout_budget_ms: Optional[float] = None,
        evaluation_id: Optional[str] = None,
    ) -> TwinEvaluationResponse:
        """Predict joint multi-cell KPIs for candidate deltas (v1 interface).

        Executes within timeout budget; reverts to LKG fail-safe if exceeded.
        """
        eval_id = evaluation_id or f"eval-{uuid.uuid4().hex[:8]}"
        snapshot = self.state_provider.get_snapshot()

        commanded_cells = [d.cell_id for d in candidate_deltas]
        affected_cells = self.propagation_chain.get_affected_neighborhood(
            commanded_cells, snapshot.neighbor_map, max_hops=2
        )

        confidence_margin = self.calibrator.get_confidence_margin()

        # Fast-path: execute analytical chain directly if no delay hook
        can_fast_path = (
            self.fail_safe_manager.injected_delay_ms == 0.0 and
            (timeout_budget_ms is None or timeout_budget_ms >= 50.0)
        )
        if can_fast_path:
            start = time.perf_counter()
            predicted_kpis = self.propagation_chain.evaluate_deltas(
                snapshot, candidate_deltas
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            safety_floors_satisfied = (
                predicted_kpis.coverage_ratio >= MIN_COVERAGE_RATIO and
                predicted_kpis.drop_rate_ratio <= MAX_DROP_RATE_RATIO
            )

            return TwinEvaluationResponse(
                evaluation_id=eval_id,
                computation_time_ms=elapsed_ms,
                predicted_kpis=predicted_kpis,
                safety_floors_satisfied=safety_floors_satisfied,
                confidence_margin=confidence_margin,
            )

        # Threaded guard path for timeout enforcement and delay injection
        def eval_task() -> PredictedKPIs:
            return self.propagation_chain.evaluate_deltas(
                snapshot, candidate_deltas
            )

        return self.fail_safe_manager.execute_with_guard(
            evaluation_id=eval_id,
            eval_fn=eval_task,
            affected_cells=affected_cells,
            timeout_budget_ms=timeout_budget_ms,
            confidence_margin=confidence_margin,
        )

    def predict_v2(
        self,
        candidate_deltas: List[CandidateDelta],
        timeout_budget_ms: Optional[float] = None,
        candidate_id: Optional[str] = None,
        alpha: float = 0.1,
        apply_drift: bool = True,
    ) -> TwinPredictionV2:
        """Predict calibrated conformal uncertainty intervals (v2 interface).

        Returns TwinPredictionV2 carrying intervals with mean, lower, upper.
        """
        cand_id = candidate_id or f"cand-{uuid.uuid4().hex[:8]}"
        snapshot = self.state_provider.get_snapshot()

        # Run analytical propagation
        predicted_kpis = self.propagation_chain.evaluate_deltas(
            snapshot, candidate_deltas
        )

        # Compute multi-metric dictionary (core KPIs + normalized ratios)
        total_ues = sum(
            snapshot.ue_counts.get(c, 50)
            for c in predicted_kpis.affected_cell_ids
        )
        dropped_users_val = float(predicted_kpis.drop_rate_ratio * total_ues)
        n_affected = max(len(predicted_kpis.affected_cell_ids), 1)
        max_power = n_affected * DEFAULT_MAX_TX_POWER_WATTS
        energy_watts_val = float(
            predicted_kpis.normalized_energy * max_power
        )
        linear_interf = (
            predicted_kpis.normalized_interference *
            DEFAULT_INTERFERENCE_SCALE_WATTS
        )
        safe_interf = max(linear_interf, 1e-15)
        interf_dbm_val = float(10.0 * np.log10(safe_interf) + 30.0)

        kpis_for_conformal: Dict[str, float] = {
            "dropped_users": dropped_users_val,
            "mean_sinr_db": predicted_kpis.predicted_sinr_avg_db,
            "energy_w": energy_watts_val,
            "interference_db": interf_dbm_val,
            "coverage_ratio": predicted_kpis.coverage_ratio,
            "drop_rate_ratio": predicted_kpis.drop_rate_ratio,
            "normalized_energy": predicted_kpis.normalized_energy,
            "normalized_interference": predicted_kpis.normalized_interference,
        }

        return self.conformal_predictor.predict_intervals(
            predicted_kpis=kpis_for_conformal,
            alpha=alpha,
            candidate_id=cand_id,
            apply_drift_flag=apply_drift,
        )


# Global default evaluator instance for standalone API callable
_global_evaluator: Optional[DigitalTwinEvaluator] = None


def set_global_evaluator(evaluator: DigitalTwinEvaluator) -> None:
    """Register the active global DigitalTwinEvaluator instance."""
    global _global_evaluator
    _global_evaluator = evaluator


def get_global_evaluator() -> Optional[DigitalTwinEvaluator]:
    """Retrieve active global DigitalTwinEvaluator instance."""
    return _global_evaluator


def handle_twin_evaluate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Plain Python callable for POST /api/v1/twin/evaluate (Section 3.7).

    Dev 4 mounts this directly into FastAPI routing without schema changes.
    """
    request = TwinEvaluationRequest.model_validate(payload)
    evaluator = get_global_evaluator()

    if evaluator is None:
        # Create minimal fallback state if no instance registered
        class FallbackProvider:
            def get_snapshot(self) -> ClusterSnapshot:
                cell_ids = ["cell_A", "cell_B", "cell_C"]
                return ClusterSnapshot(
                    version=1,
                    cell_ids=cell_ids,
                    coupling_matrix=np.array([
                        [0.0, 0.05, 0.01],
                        [0.05, 0.0, 0.04],
                        [0.01, 0.04, 0.0],
                    ]),
                    tx_power_dbm={
                        "cell_A": 43.0,
                        "cell_B": 43.0,
                        "cell_C": 43.0,
                    },
                    is_sleeping={
                        "cell_A": False,
                        "cell_B": False,
                        "cell_C": False,
                    },
                    signal_powers_w=np.array([1e-7, 1e-7, 1e-7]),
                    current_interference_w=np.array([2e-9, 2e-9, 2e-9]),
                    ue_counts={"cell_A": 50, "cell_B": 50, "cell_C": 50},
                    noise_floor_w=1e-13,
                    neighbor_map={
                        "cell_A": ["cell_B", "cell_C"],
                        "cell_B": ["cell_A", "cell_C"],
                        "cell_C": ["cell_A", "cell_B"],
                    },
                )

        evaluator = DigitalTwinEvaluator(state_provider=FallbackProvider())
        set_global_evaluator(evaluator)

    response = evaluator.predict(
        candidate_deltas=request.candidate_deltas,
        timeout_budget_ms=request.timeout_budget_ms,
        evaluation_id=request.evaluation_id,
    )

    return response.model_dump()
