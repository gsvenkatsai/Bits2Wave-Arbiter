"""Digital Twin Evaluator engine for Project ARBITER.

SRS FR-5.1, FR-5.3, NFR-2, Contract C3.
Evaluates candidate parameter deltas and predicts joint multi-cell KPIs
with an ultra-fast target latency (< 10 ms, P95 < 8 ms).
Supports v2 conformal safety intervals via predict_v2() and Contract C3
calibration_status(). Provides ensure_global_evaluator() for simulator-grounded
warm-up.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import numpy as np

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

__all__ = [
    "DigitalTwinEvaluator",
    "SimulatorStateProvider",
    "ensure_global_evaluator",
    "get_global_evaluator",
    "handle_twin_evaluate",
    "set_global_evaluator",
]


class SimulatorStateProvider:
    """NetworkStateProvider dynamically reading state from a SimulationEngine.

    Extracts real cell configurations, live user distributions, path loss
    couplings, and interference baselines without hardcoded fakes.
    """

    def __init__(self, engine: Any) -> None:
        self.engine: Any = engine

    def get_snapshot(self) -> ClusterSnapshot:
        """Capture live cluster radio parameters from SimulationEngine."""
        cell_ids = list(self.engine.cells.keys())
        n = len(cell_ids)
        coords = {
            cid: (c.config.x_coord, c.config.y_coord)
            for cid, c in self.engine.cells.items()
        }

        # Distance-decay path loss coupling between base stations
        coupling = np.zeros((n, n), dtype=np.float64)
        for i, ci in enumerate(cell_ids):
            for j, cj in enumerate(cell_ids):
                if i != j:
                    d = np.hypot(
                        coords[ci][0] - coords[cj][0],
                        coords[ci][1] - coords[cj][1],
                    )
                    pl = self.engine.propagation.compute_path_loss(d)
                    coupling[i, j] = 10.0 ** ((-pl + 15.0) / 10.0)

        active_ues = self.engine.traffic_model.get_active_users(
            self.engine.tick
        )
        sig_powers: List[float] = []
        int_powers: List[float] = []
        for cid in cell_ids:
            c_ues = [u for u in active_ues if u.attached_cell_id == cid]
            if c_ues:
                sig_powers.append(
                    float(
                        np.mean([
                            10.0 ** ((u.rx_power_dbm - 30.0) / 10.0)
                            for u in c_ues
                        ])
                    )
                )
                int_powers.append(
                    float(
                        np.mean([
                            10.0 ** ((u.interference_power_dbm - 30.0) / 10.0)
                            for u in c_ues
                        ])
                    )
                )
            else:
                sig_powers.append(1e-9)
                int_powers.append(1e-10)

        loads = self.engine.get_cell_loads()
        cell_loads = {cid: float(loads.get(cid, 0.0)) for cid in cell_ids}

        return ClusterSnapshot(
            version=int(self.engine.tick),
            cell_ids=cell_ids,
            coupling_matrix=coupling,
            tx_power_dbm={
                cid: float(c.config.current_tx_power_dbm)
                for cid, c in self.engine.cells.items()
            },
            is_sleeping={
                cid: bool(c.config.is_sleeping)
                for cid, c in self.engine.cells.items()
            },
            signal_powers_w=np.asarray(sig_powers, dtype=np.float64),
            current_interference_w=np.asarray(int_powers, dtype=np.float64),
            ue_counts={
                cid: sum(1 for u in active_ues if u.attached_cell_id == cid)
                for cid in cell_ids
            },
            noise_floor_w=float(self.engine.propagation.N0_WATTS),
            neighbor_map={
                cid: list(c.neighbor_cell_ids)
                for cid, c in self.engine.cells.items()
            },
            offload_ratios={
                cid: float(self.engine.offload_overrides.get(cid, 0.0))
                for cid in cell_ids
            },
            cell_loads=cell_loads,
            antenna_tilt_deg={
                cid: float(c.config.antenna_tilt_deg)
                for cid, c in self.engine.cells.items()
            },
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
        """Evaluate candidate parameter deltas and return predicted KPIs.

        Guaranteed latency budget SLA (< 10 ms).
        """
        eval_id = evaluation_id or f"eval-{uuid.uuid4().hex[:8]}"
        confidence_margin = self.calibrator.get_confidence_margin()
        affected_cells = [d.cell_id for d in candidate_deltas]

        def _evaluate_action() -> PredictedKPIs:
            snapshot = self.state_provider.get_snapshot()
            return self.propagation_chain.evaluate_deltas(
                snapshot, candidate_deltas
            )

        return self.fail_safe_manager.execute_with_guard(
            evaluation_id=eval_id,
            eval_fn=_evaluate_action,
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

    def calibration_status(
        self, kpi: str = "mean_sinr_db", alpha: Optional[float] = None
    ) -> Dict[str, Any]:
        """Contract C3: Report conformal calibration status.

        Returns:
            calibrated flag, n_calibration, and interval_halfwidth_db.
        """
        return self.conformal_predictor.calibration_status(
            kpi=kpi, alpha=alpha
        )


# Global default evaluator instance
_global_evaluator: Optional[DigitalTwinEvaluator] = None


def set_global_evaluator(evaluator: DigitalTwinEvaluator) -> None:
    """Register the active global DigitalTwinEvaluator instance."""
    global _global_evaluator
    _global_evaluator = evaluator


def get_global_evaluator() -> Optional[DigitalTwinEvaluator]:
    """Retrieve active global DigitalTwinEvaluator instance."""
    return _global_evaluator


def ensure_global_evaluator(
    topology: str = "5_CELL_CORE",
    warm_up: bool = True,
    n_samples: int = 60,
    seed: int = 42,
    horizon_ticks: int = 1,
) -> DigitalTwinEvaluator:
    """Ensure the global DigitalTwinEvaluator is created and calibrated.

    SRS FR-5.1, Contract C3.
    Instantiates a real DigitalTwinEvaluator backed by the SimulationEngine.
    If warm_up=True, runs a seeded calibration scenario using the simulator's
    public snapshot/fork API: samples N candidate deltas, compares twin
    predictions against forked simulation measured KPIs, and feeds residuals
    into ConformalPredictor.

    Fails loudly if the simulator is missing; never falls back to fakes.
    """
    # Python 3.13 networkx generic subscription compatibility guard
    try:
        import networkx as _nx
        if not hasattr(_nx.Graph, "__class_getitem__"):
            setattr(
                _nx.Graph,
                "__class_getitem__",
                classmethod(lambda cls, item: cls),
            )
    except ImportError:
        pass

    try:
        from arbiter.simulator.engine import SimulationEngine
    except ImportError as exc:
        raise RuntimeError(
            "ensure_global_evaluator requires real arbiter.simulator.engine: "
            f"{exc}"
        ) from exc

    engine = SimulationEngine(topology=topology, seed=seed)
    engine.step()  # Advance to initial steady state

    state_provider = SimulatorStateProvider(engine)
    conformal_predictor = ConformalPredictor(
        window_size=200,
        min_calibration_points=30,
        alpha=0.1,
    )
    evaluator = DigitalTwinEvaluator(
        state_provider=state_provider,
        conformal_predictor=conformal_predictor,
    )

    if warm_up:
        rng = np.random.default_rng(seed)
        cell_ids = list(engine.cells.keys())

        for i in range(max(1, n_samples)):
            cid = str(rng.choice(cell_ids))
            param = str(
                rng.choice([
                    "TX_POWER_DBM",
                    "TILT_DEG",
                    "SLEEP_STATE",
                    "OFFLOAD_RATIO",
                ])
            )

            if param == "TX_POWER_DBM":
                val = float(rng.uniform(33.0, 46.0))
            elif param == "TILT_DEG":
                val = float(rng.uniform(2.0, 8.0))
            elif param == "SLEEP_STATE":
                non_anchor = [c for c in cell_ids if c != "cell_A"]
                target_cell = (
                    str(rng.choice(non_anchor)) if non_anchor else cid
                )
                cid = target_cell
                val = 1.0 if rng.random() < 0.25 else 0.0
            else:  # OFFLOAD_RATIO
                val = float(rng.uniform(0.0, 0.4))

            deltas = [
                CandidateDelta(
                    cell_id=cid,
                    parameter=param,
                    proposed_value=val,
                )
            ]

            # Twin prediction (without synthetic drift)
            twin_pred = evaluator.predict_v2(deltas, apply_drift=False)
            twin_intervals = {
                item.kpi: item for item in twin_pred.intervals
            }

            # Fork simulation ground truth
            forked = engine.fork(seed=seed + 1000 + i)
            forked.apply_actuation_deltas([
                {
                    "target_cell_id": d.cell_id,
                    "parameter": d.parameter,
                    "requested_value": d.proposed_value,
                }
                for d in deltas
            ])

            samples = []
            for _ in range(max(1, horizon_ticks)):
                samples = forked.step()

            meas_ues = forked.traffic_model.get_active_users(forked.tick)
            if meas_ues:
                meas_sinr = float(np.mean([u.sinr_db for u in meas_ues]))
                meas_interf = float(
                    np.mean([u.interference_power_dbm for u in meas_ues])
                )
            else:
                meas_sinr = float(np.mean([s.sinr_db for s in samples]))
                meas_interf = -90.0

            meas_dropped = float(sum(s.dropped_users for s in samples))
            meas_energy = float(sum(s.energy_consumption_w for s in samples))
            total_active = max(sum(s.active_users for s in samples), 1)
            meas_drop_ratio = float(meas_dropped / total_active)
            meas_cov_ratio = float(1.0 - meas_drop_ratio)
            meas_norm_energy = float(
                meas_energy /
                (len(cell_ids) * DEFAULT_MAX_TX_POWER_WATTS * 10.0)
            )
            interf_w = 10.0 ** ((meas_interf - 30.0) / 10.0)
            meas_norm_interf = float(
                max(0.0, min(1.0, interf_w / DEFAULT_INTERFERENCE_SCALE_WATTS))
            )

            # Record residuals in conformal calibrator
            if "mean_sinr_db" in twin_intervals:
                conformal_predictor.add_observation(
                    "mean_sinr_db",
                    predicted=twin_intervals["mean_sinr_db"].mean,
                    actual=meas_sinr,
                )
            if "energy_w" in twin_intervals:
                conformal_predictor.add_observation(
                    "energy_w",
                    predicted=twin_intervals["energy_w"].mean,
                    actual=meas_energy,
                )
            if "dropped_users" in twin_intervals:
                conformal_predictor.add_observation(
                    "dropped_users",
                    predicted=twin_intervals["dropped_users"].mean,
                    actual=meas_dropped,
                )
            if "coverage_ratio" in twin_intervals:
                conformal_predictor.add_observation(
                    "coverage_ratio",
                    predicted=twin_intervals["coverage_ratio"].mean,
                    actual=meas_cov_ratio,
                )
            if "drop_rate_ratio" in twin_intervals:
                conformal_predictor.add_observation(
                    "drop_rate_ratio",
                    predicted=twin_intervals["drop_rate_ratio"].mean,
                    actual=meas_drop_ratio,
                )
            if "normalized_energy" in twin_intervals:
                conformal_predictor.add_observation(
                    "normalized_energy",
                    predicted=twin_intervals["normalized_energy"].mean,
                    actual=meas_norm_energy,
                )
            if "normalized_interference" in twin_intervals:
                conformal_predictor.add_observation(
                    "normalized_interference",
                    predicted=twin_intervals["normalized_interference"].mean,
                    actual=meas_norm_interf,
                )
            if "interference_db" in twin_intervals:
                conformal_predictor.add_observation(
                    "interference_db",
                    predicted=twin_intervals["interference_db"].mean,
                    actual=meas_interf,
                )

    set_global_evaluator(evaluator)
    return evaluator


def handle_twin_evaluate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Plain Python callable for POST /api/v1/twin/evaluate (Section 3.7).

    Mounts directly into FastAPI routing without schema changes.
    """
    request = TwinEvaluationRequest.model_validate(payload)
    evaluator = get_global_evaluator()

    if evaluator is None:
        evaluator = ensure_global_evaluator(warm_up=False)

    response = evaluator.predict(
        candidate_deltas=request.candidate_deltas,
        timeout_budget_ms=request.timeout_budget_ms,
        evaluation_id=request.evaluation_id,
    )

    return response.model_dump()
