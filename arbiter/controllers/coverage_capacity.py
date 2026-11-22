"""Coverage Capacity Controller: detects weak coverage and boosts power."""
from typing import Any, Dict, List, Optional

from arbiter.controllers.base import BaseController
from arbiter.shared.models import (
    CommandProposal,
    ControllerType,
    ParameterType,
    TelemetrySample,
)


class CoverageCapacityController(BaseController):
    """Coverage & Capacity Controller optimizing Tx power and down-tilts."""

    def __init__(
        self,
        controller_id: str = "ctrl-cc-01",
        poll_interval_ticks: int = 1,
        edge_sinr_threshold_db: float = 0.0,
        default_power_boost_db: float = 3.0,
        instance_id: Optional[str] = None,
        cell_scope: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            controller_id=controller_id,
            controller_type=ControllerType.COVERAGE_CAPACITY,
            poll_interval_ticks=poll_interval_ticks,
            instance_id=instance_id,
            cell_scope=cell_scope,
        )
        self.edge_sinr_threshold_db = edge_sinr_threshold_db
        self.default_power_boost_db = default_power_boost_db

    def propose_power_boost(
        self,
        cell_id: str,
        delta_db: float = 3.0,
        current_power_dbm: float = 43.0,
        timestamp_epoch_ms: Optional[int] = None,
    ) -> CommandProposal:
        """Construct power boost proposal clamped to maximum 46.0 dBm."""
        boosted = min(46.0, current_power_dbm + delta_db)
        return self.propose(
            target_cell_id=cell_id,
            parameter=ParameterType.TX_POWER_DBM,
            requested_value=boosted,
            emergency_flag=False,
            timestamp_epoch_ms=timestamp_epoch_ms,
        )

    def evaluate_telemetry(
        self,
        metrics: Any,
        edge_sinr_override: Optional[Dict[str, float]] = None,
        cell_powers: Optional[Dict[str, float]] = None,
    ) -> List[CommandProposal]:
        """Detect edge coverage drops (<0 dB) and propose power boosts."""
        proposals: List[CommandProposal] = []
        edge_sinr_map: Dict[str, float] = {}

        if edge_sinr_override is not None:
            edge_sinr_map = edge_sinr_override
        elif isinstance(metrics, list):
            for item in metrics:
                if isinstance(item, TelemetrySample):
                    # Use sample mean SINR as fallback
                    edge_sinr_map[item.cell_id] = float(item.sinr_db)
        elif isinstance(metrics, dict):
            edge_sinr_map = {str(k): float(v) for k, v in metrics.items()}

        powers = cell_powers or {}

        for cell_id, sinr in edge_sinr_map.items():
            if self.cell_scope is not None and cell_id not in self.cell_scope:
                continue
            if sinr < self.edge_sinr_threshold_db:
                curr_pwr = powers.get(cell_id, 43.0)
                if curr_pwr < 46.0:
                    prop = self.propose_power_boost(
                        cell_id=cell_id,
                        delta_db=self.default_power_boost_db,
                        current_power_dbm=curr_pwr,
                    )
                    proposals.append(prop)

        return proposals
