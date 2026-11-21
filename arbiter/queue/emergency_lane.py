"""Emergency bypass lane (spec: emergency commands skip arbitration).

An emergency proposal is approved immediately with
``ReasonCode.EMERGENCY_OVERRIDE`` and audited post-hoc, so Dev 3/4 can persist
the override later. No twin call is ever made here.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, List

from arbiter.engine.contracts import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ReasonCode,
)

__all__ = ["EmergencyAuditRecord", "EmergencyBypassLane"]


def _default_id() -> str:
    return uuid.uuid4().hex


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class EmergencyAuditRecord:
    """One post-hoc audit entry for an emergency override."""

    command_id: str
    controller: str
    target_cell: str
    parameter: str
    requested_value: float
    batch_tick: int
    decided_at_epoch_ms: int
    decision: ArbitrationDecision


class EmergencyBypassLane:
    """Immediate-approval lane for emergency proposals, with an audit buffer."""

    def __init__(
        self,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._audit: List[EmergencyAuditRecord] = []
        self._id_factory: Callable[[], str] = id_factory or _default_id
        self._clock: Callable[[], int] = clock or _now_ms

    # -- writes ------------------------------------------------------------
    def accept(
        self,
        cmd: CommandProposal,
        tick: int = 0,
        timestamp_epoch_ms: int | None = None,
    ) -> ArbitrationDecision:
        """Approve ``cmd`` immediately and append its audit record."""
        decided_at = self._clock() if timestamp_epoch_ms is None else int(timestamp_epoch_ms)
        decision = ArbitrationDecision(
            decision_id=self._id_factory(),
            command_id=cmd.command_id,
            batch_tick=int(tick),
            action=ActionType.APPROVE,
            approved_value=cmd.requested_value,
            delta_utility=0.0,
            reason_code=ReasonCode.EMERGENCY_OVERRIDE,
            hold_count=cmd.hold_count,
            twin_eval_count=0,
            decided_at_epoch_ms=decided_at,
        )
        record = EmergencyAuditRecord(
            command_id=cmd.command_id,
            controller=cmd.controller,
            target_cell=cmd.target_cell,
            parameter=cmd.parameter,
            requested_value=cmd.requested_value,
            batch_tick=int(tick),
            decided_at_epoch_ms=decided_at,
            decision=decision,
        )
        with self._lock:
            self._audit.append(record)
        return decision

    # -- reads -------------------------------------------------------------
    def drain_audit(self) -> List[EmergencyAuditRecord]:
        """Return the buffered audit records and clear the buffer."""
        with self._lock:
            drained = list(self._audit)
            self._audit.clear()
            return drained

    def peek_audit(self) -> List[EmergencyAuditRecord]:
        """Copy of the buffered audit records without clearing them."""
        with self._lock:
            return list(self._audit)

    @property
    def audit_count(self) -> int:
        """Number of audit records waiting to be drained."""
        with self._lock:
            return len(self._audit)
