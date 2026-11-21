"""Energy Saving Controller: detects low load and proposes sleep mode."""
from collections import defaultdict
from typing import Any, Dict, List, Optional

from arbiter.controllers.base import BaseController
from arbiter.shared.models import (
    CommandProposal,
    ControllerType,
    ParameterType,
    TelemetrySample,
)


class EnergySavingController(BaseController):
    """Energy Saving Controller (ES) managing cell sleep states."""

    def __init__(
        self,
        controller_id: str = "ctrl-es-01",
        poll_interval_ticks: int = 1,
        consecutive_low_ticks: int = 5,
        low_load_threshold: float = 0.15,
        cooldown_ticks: int = 10,
    ) -> None:
        super().__init__(
            controller_id=controller_id,
            controller_type=ControllerType.ENERGY_SAVING,
            poll_interval_ticks=poll_interval_ticks,
        )
        self.consecutive_low_ticks = consecutive_low_ticks
        self.low_load_threshold = low_load_threshold
        self.cooldown_ticks = cooldown_ticks
        self.load_history: Dict[str, List[float]] = defaultdict(list)
        self.last_proposed_tick: Dict[str, int] = {}

    def propose_sleep(
        self,
        cell_id: str,
        timestamp_epoch_ms: Optional[int] = None,
    ) -> CommandProposal:
        """Emit command proposal to transition cell into sleep state."""
        return self.propose(
            target_cell_id=cell_id,
            parameter=ParameterType.SLEEP_STATE,
            requested_value=1.0,
            emergency_flag=False,
            timestamp_epoch_ms=timestamp_epoch_ms,
        )

    def evaluate_telemetry(
        self,
        metrics: Any,
        current_tick: int = 0,
        cell_sleep_states: Optional[Dict[str, bool]] = None,
    ) -> List[CommandProposal]:
        """Examine telemetry samples or load mapping.

        Proposes sleep if load < 15% for 5 consecutive ticks.
        """
        proposals: List[CommandProposal] = []
        cell_loads: Dict[str, float] = {}

        if isinstance(metrics, list):
            for item in metrics:
                if isinstance(item, TelemetrySample):
                    # Estimate cell load: 30.0 Mbps nominal capacity
                    load = (
                        float(item.throughput_mbps / 30.0)
                        if item.throughput_mbps > 0
                        else 0.0
                    )
                    cell_loads[item.cell_id] = load
        elif isinstance(metrics, dict):
            cell_loads = {str(k): float(v) for k, v in metrics.items()}

        for cell_id, load in cell_loads.items():
            self.load_history[cell_id].append(load)
            n_ticks = self.consecutive_low_ticks
            recent_history = self.load_history[cell_id][-n_ticks:]

            # Check if sleeping already
            is_sleeping = cell_sleep_states.get(
                cell_id, False) if cell_sleep_states else False

            # Check cooldown
            last_tick = self.last_proposed_tick.get(cell_id, -999)
            in_cooldown = (current_tick - last_tick) < self.cooldown_ticks

            if (
                len(recent_history) >= self.consecutive_low_ticks
                and all(ld < self.low_load_threshold for ld in recent_history)
                and not is_sleeping
                and not in_cooldown
            ):
                prop = self.propose_sleep(cell_id=cell_id)
                self.last_proposed_tick[cell_id] = current_tick
                proposals.append(prop)

        return proposals
