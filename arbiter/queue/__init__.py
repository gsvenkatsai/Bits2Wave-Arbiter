"""Branch 2 100 ms arbitration proposal queue (FR-3) and tick orchestration."""
from __future__ import annotations

from arbiter.engine.contracts import MAX_HOLD_TICKS, TICK_MS
from arbiter.queue.emergency_lane import EmergencyAuditRecord, EmergencyBypassLane
from arbiter.queue.orchestrator import QueueOrchestrator, build_default_orchestrator
from arbiter.queue.proposal_queue import ProposalQueue, QueueInvariantError

__all__ = [
    "TICK_MS",
    "MAX_HOLD_TICKS",
    "ProposalQueue",
    "QueueInvariantError",
    "EmergencyBypassLane",
    "EmergencyAuditRecord",
    "QueueOrchestrator",
    "build_default_orchestrator",
]
