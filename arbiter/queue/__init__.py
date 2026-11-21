"""Branch 2 100 ms arbitration proposal queue (FR-3)."""
from __future__ import annotations

from arbiter.engine.contracts import MAX_HOLD_TICKS, TICK_MS
from arbiter.queue.emergency_lane import EmergencyAuditRecord, EmergencyBypassLane
from arbiter.queue.orchestrator import (
    ConflictGraphStub,
    QueueOrchestrator,
    StubDetector,
    StubEngine,
)
from arbiter.queue.proposal_queue import ProposalQueue, QueueInvariantError

__all__ = [
    "TICK_MS",
    "MAX_HOLD_TICKS",
    "ProposalQueue",
    "QueueInvariantError",
    "EmergencyBypassLane",
    "EmergencyAuditRecord",
    "QueueOrchestrator",
    "ConflictGraphStub",
    "StubDetector",
    "StubEngine",
]
