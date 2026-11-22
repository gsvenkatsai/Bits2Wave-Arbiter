"""Load Balancing Controller: offloads traffic to neighbors."""
import logging
from typing import Any, Dict, List, Optional

from arbiter.controllers.base import BaseController
from arbiter.shared.models import (
    CommandProposal,
    ControllerType,
    ParameterType,
    TelemetrySample,
)
from arbiter.simulator.traffic import CELL_CAPACITY_MBPS

logger = logging.getLogger(__name__)


class LoadBalancingController(BaseController):
    """Load Balancing Controller managing user handover and offloads."""

    def __init__(
        self,
        controller_id: str = "ctrl-lb-01",
        poll_interval_ticks: int = 1,
        high_load_threshold: float = 0.75,
        default_offload_ratio: float = 0.40,
        instance_id: Optional[str] = None,
        cell_scope: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            controller_id=controller_id,
            controller_type=ControllerType.LOAD_BALANCING,
            poll_interval_ticks=poll_interval_ticks,
            instance_id=instance_id,
            cell_scope=cell_scope,
        )
        self.high_load_threshold = high_load_threshold
        self.default_offload_ratio = default_offload_ratio

    def propose_offload(
        self,
        source_cell: str,
        target_cell: str,
        ratio: float = 0.40,
        timestamp_epoch_ms: Optional[int] = None,
    ) -> CommandProposal:
        """Propose traffic offload from source_cell to target_cell.

        Note: target_cell is the receiver specified in target_cell_id.
        The source cell is implied by topology and network load state.
        """
        logger.info(
            "LB Controller proposing offload of %.1f%% from %s to %s",
            ratio * 100.0,
            source_cell,
            target_cell,
        )
        return self.propose(
            target_cell_id=target_cell,
            parameter=ParameterType.OFFLOAD_RATIO,
            requested_value=ratio,
            emergency_flag=False,
            timestamp_epoch_ms=timestamp_epoch_ms,
        )

    def evaluate_telemetry(
        self,
        metrics: Any,
        adjacency_map: Optional[Dict[str, List[str]]] = None,
        cell_loads_override: Optional[Dict[str, float]] = None,
    ) -> List[CommandProposal]:
        """Detect overloaded cells (>75%) and propose offload to neighbor."""
        proposals: List[CommandProposal] = []
        cell_loads: Dict[str, float] = {}

        if cell_loads_override is not None:
            cell_loads = cell_loads_override
        elif isinstance(metrics, list):
            for item in metrics:
                if isinstance(item, TelemetrySample):
                    cell_loads[item.cell_id] = float(
                        item.throughput_mbps / CELL_CAPACITY_MBPS
                    )
        elif isinstance(metrics, dict):
            cell_loads = {str(k): float(v) for k, v in metrics.items()}

        if not adjacency_map:
            return proposals

        for cell_id, load in cell_loads.items():
            if self.cell_scope is not None and cell_id not in self.cell_scope:
                continue
            if load > self.high_load_threshold:
                neighbors = adjacency_map.get(cell_id, [])
                if neighbors:
                    # Identify least-loaded neighbor as target
                    target_neighbor = min(
                        neighbors, key=lambda nid: cell_loads.get(nid, 0.0))
                    prop = self.propose_offload(
                        source_cell=cell_id,
                        target_cell=target_neighbor,
                        ratio=self.default_offload_ratio,
                    )
                    proposals.append(prop)

        return proposals
