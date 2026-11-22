"""Abstract base class for uncoordinated RAN software controllers."""
from abc import ABC, abstractmethod
import time
from typing import Any, Dict, List, Optional
import uuid

from arbiter.shared.models import (
    CommandProposal,
    ControllerType,
    ParameterType,
)


class BaseController(ABC):
    """Abstract base class enforcing schema boundaries on proposals.

    Autonomous controllers operate independently and never coordinate.
    """

    def __init__(
        self,
        controller_id: str,
        controller_type: ControllerType,
        poll_interval_ticks: int = 1,
        instance_id: Optional[str] = None,
        cell_scope: Optional[List[str]] = None,
    ) -> None:
        self.controller_id = controller_id
        self.controller_type = controller_type
        self.poll_interval_ticks = poll_interval_ticks
        self.instance_id = instance_id or controller_id
        self.cell_scope = list(cell_scope) if cell_scope is not None else None

    @abstractmethod
    def evaluate_telemetry(self, metrics: Any) -> List[CommandProposal]:
        """Evaluate network metrics and emit command proposals."""
        pass

    def propose(
        self,
        target_cell_id: str,
        parameter: ParameterType,
        requested_value: float,
        emergency_flag: bool = False,
        timestamp_epoch_ms: Optional[int] = None,
    ) -> CommandProposal:
        """Construct and validate a CommandProposal against boundaries."""
        val = float(requested_value)

        # Enforce validation invariants
        if parameter == ParameterType.TX_POWER_DBM:
            if not (30.0 <= val <= 46.0):
                raise ValueError(
                    f"TX_POWER_DBM {val} dBm out of valid range [30.0, 46.0]"
                )
        elif parameter == ParameterType.TILT_DEG:
            if not (0.0 <= val <= 15.0):
                raise ValueError(
                    f"TILT_DEG {val} deg out of valid range [0.0, 15.0]"
                )
        elif parameter == ParameterType.SLEEP_STATE:
            if val not in (0.0, 1.0):
                raise ValueError(
                    f"SLEEP_STATE {val} must be strictly 0.0 or 1.0"
                )
        elif parameter == ParameterType.OFFLOAD_RATIO:
            if not (0.0 <= val <= 1.0):
                raise ValueError(
                    f"OFFLOAD_RATIO {val} must be within [0.0, 1.0]"
                )

        epoch_ms = (
            int(timestamp_epoch_ms)
            if timestamp_epoch_ms is not None
            else int(time.time() * 1000)
        )

        return CommandProposal(
            command_id=str(uuid.uuid4()),
            controller_id=self.instance_id,
            controller_type=self.controller_type,
            target_cell_id=target_cell_id,
            parameter=parameter,
            requested_value=val,
            emergency_flag=emergency_flag,
            timestamp_epoch_ms=epoch_ms,
            hold_count=0,
        )

    @staticmethod
    def submit(proposal: CommandProposal, sink: Any) -> Dict[str, Any]:
        """Dispatch proposal to an ingress queue sink stub."""
        res = sink.submit_proposal(proposal)
        return dict(res)
