"""Mock actuator for Branch 2 tests: records applications and simulates rollback.

Branch 3 owns the real actuator/commit-log path; this double lets the referee
tests assert exactly what was applied and roll it back deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from arbiter.engine.contracts import CommandProposal

__all__ = ["AppliedChange", "MockActuatorClient"]


@dataclass(frozen=True)
class AppliedChange:
    """One parameter write performed by the actuator."""

    command_id: str
    target_cell: str
    parameter: str
    previous_value: float
    applied_value: float
    tick: int = 0


class MockActuatorClient:
    """In-memory actuator that keeps an ordered application log."""

    def __init__(
        self,
        initial_values: Mapping[tuple[str, str], float] | None = None,
    ) -> None:
        self._values: dict[tuple[str, str], float] = dict(initial_values or {})
        self.applied: list[AppliedChange] = []
        self.rollbacks: list[AppliedChange] = []

    # -- reads -------------------------------------------------------------
    def value_of(self, target_cell: str, parameter: str) -> float | None:
        """Current value of one parameter, if it has ever been set."""
        return self._values.get((target_cell, parameter))

    def snapshot(self) -> dict[tuple[str, str], float]:
        """Copy of the current parameter state."""
        return dict(self._values)

    @property
    def applied_count(self) -> int:
        """Number of changes currently applied (i.e. not rolled back)."""
        return len(self.applied)

    # -- writes ------------------------------------------------------------
    def apply(
        self,
        command: CommandProposal,
        value: float | None = None,
        tick: int = 0,
    ) -> AppliedChange:
        """Apply a command (or an overridden value) and record the change."""
        applied_value = command.requested_value if value is None else float(value)
        key = (command.target_cell, command.parameter)
        previous = self._values.get(key, command.current_value)
        change = AppliedChange(
            command_id=command.command_id,
            target_cell=command.target_cell,
            parameter=command.parameter,
            previous_value=previous,
            applied_value=applied_value,
            tick=tick,
        )
        self._values[key] = applied_value
        self.applied.append(change)
        return change

    def rollback(self, count: int = 1) -> list[AppliedChange]:
        """Revert the last ``count`` applications, newest first."""
        if count <= 0:
            raise ValueError("rollback count must be >= 1")
        reverted: list[AppliedChange] = []
        for _ in range(min(count, len(self.applied))):
            change = self.applied.pop()
            self._values[(change.target_cell, change.parameter)] = change.previous_value
            self.rollbacks.append(change)
            reverted.append(change)
        return reverted

    def reset(self) -> None:
        """Clear the parameter state and both logs."""
        self._values.clear()
        self.applied.clear()
        self.rollbacks.clear()
