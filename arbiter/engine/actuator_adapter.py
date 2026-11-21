"""Actuator adapter: Branch 3's ActuatorEnforcer -> Branch 2 actuator protocol.

Bridges the real transactional actuator to the referee engine while keeping
MockActuatorClient available for unit tests.
"""
from __future__ import annotations

import uuid
from typing import Any

from arbiter.actuator.enforcer import ActuatorEnforcer
from arbiter.engine.contracts import CommandProposal
from arbiter.engine.mocks.mock_actuator_client import AppliedChange

__all__ = ["ActuatorAdapter"]


class ActuatorAdapter:
    """Adapts Branch 3's ActuatorEnforcer to Branch 2's actuator interface."""

    def __init__(self, enforcer: ActuatorEnforcer) -> None:
        self._enforcer = enforcer
        self.applied: list[AppliedChange] = []
        self._values: dict[tuple[str, str], float] = {}

    @property
    def enforcer(self) -> ActuatorEnforcer:
        """Underlying Branch 3 ActuatorEnforcer instance."""
        return self._enforcer

    def value_of(self, target_cell: str, parameter: str) -> float | None:
        """Current value of one parameter, if recorded."""
        return self._values.get((target_cell, parameter))

    def apply(
        self,
        command: CommandProposal,
        value: float | None = None,
        tick: int = 0,
    ) -> AppliedChange:
        """Commit an approved change via ActuatorEnforcer."""
        applied_value = command.requested_value if value is None else float(value)
        commit_id = str(uuid.uuid4())
        self._enforcer.commit({
            "commit_id": commit_id,
            "decision_id": f"dec-{command.command_id}",
            "command_id": command.command_id,
            "target_cell_id": command.target_cell,
            "parameter": command.parameter,
            "old_value": float(command.current_value),
            "new_value": float(applied_value),
            "timestamp_epoch_ms": tick * 100,
        })
        change = AppliedChange(
            command_id=command.command_id,
            target_cell=command.target_cell,
            parameter=command.parameter,
            previous_value=float(command.current_value),
            applied_value=applied_value,
            tick=tick,
        )
        self._values[(command.target_cell, command.parameter)] = applied_value
        self.applied.append(change)
        return change

    def commit(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Directly delegate commit to ActuatorEnforcer."""
        return self._enforcer.commit(request_data)
