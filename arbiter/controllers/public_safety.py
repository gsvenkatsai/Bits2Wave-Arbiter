"""Public Safety Controller: high-priority emergency channel override."""
from typing import Any, List, Optional

from arbiter.controllers.base import BaseController
from arbiter.shared.models import (
    CommandProposal,
    ControllerType,
    ParameterType,
)


class PublicSafetyController(BaseController):
    """Public Safety Controller establishing priority emergency channels."""

    def __init__(
        self,
        controller_id: str = "ctrl-ps-01",
        poll_interval_ticks: int = 1,
        instance_id: Optional[str] = None,
        cell_scope: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            controller_id=controller_id,
            controller_type=ControllerType.PUBLIC_SAFETY,
            poll_interval_ticks=poll_interval_ticks,
            instance_id=instance_id,
            cell_scope=cell_scope,
        )

    def evaluate_telemetry(self, metrics: Any) -> List[CommandProposal]:
        """Public safety actions are triggered externally by dispatches."""
        return []

    def propose_emergency_channel(
        self,
        cell_id: str,
        power_dbm: float = 46.0,
        timestamp_epoch_ms: Optional[int] = None,
    ) -> CommandProposal:
        """Issue emergency power override with emergency_flag = True."""
        return self.propose(
            target_cell_id=cell_id,
            parameter=ParameterType.TX_POWER_DBM,
            requested_value=power_dbm,
            emergency_flag=True,
            timestamp_epoch_ms=timestamp_epoch_ms,
        )
