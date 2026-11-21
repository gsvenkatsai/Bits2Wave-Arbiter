"""100 ms proposal queue with a bypass emergency lane.

The Referee consumes one batch per 100 ms tick.  Emergency proposals never enter
arbitration: they are separated into their own lane and audited post-hoc with
``ReasonCode.EMERGENCY_OVERRIDE``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from arbiter.engine.constants import MAX_HOLD_TICKS, TICK_DURATION_MS
from arbiter.engine.contracts import CommandProposal

__all__ = [
    "QueueBatch",
    "ProposalQueue",
    "HoldBudgetExceeded",
    "TICK_DURATION_MS",
]


class HoldBudgetExceeded(RuntimeError):
    """A command that already used its hold budget can never be held again."""


@dataclass(frozen=True)
class QueueBatch:
    """One tick's worth of work split into arbitrated and bypass lanes."""

    tick: int
    proposals: tuple[CommandProposal, ...] = ()
    emergency: tuple[CommandProposal, ...] = ()

    @property
    def batch_size(self) -> int:
        """Number of proposals handed to arbitration (excludes the bypass lane)."""
        return len(self.proposals) + len(self.emergency)

    @property
    def is_empty(self) -> bool:
        """True when neither lane produced work."""
        return not self.proposals and not self.emergency


class ProposalQueue:
    """FIFO queue that batches proposals on the 100 ms arbitration cadence."""

    def __init__(self, max_hold_ticks: int = MAX_HOLD_TICKS) -> None:
        if max_hold_ticks < 1:
            raise ValueError("max_hold_ticks must be >= 1")
        self.max_hold_ticks = max_hold_ticks
        self._pending: list[CommandProposal] = []
        self._emergency: list[CommandProposal] = []
        self._seen: set[str] = set()

    # -- ingestion ---------------------------------------------------------
    def submit(self, proposal: CommandProposal) -> bool:
        """Accept a proposal; returns ``False`` for a duplicate command id."""
        if proposal.command_id in self._seen:
            return False
        self._seen.add(proposal.command_id)
        self._enqueue(proposal)
        return True

    def _enqueue(self, proposal: CommandProposal) -> None:
        if proposal.emergency_flag:
            self._emergency.append(proposal)
        else:
            self._pending.append(proposal)

    def submit_many(self, proposals: Iterable[CommandProposal]) -> int:
        """Accept several proposals; returns how many were new."""
        accepted = 0
        for proposal in proposals:
            if self.submit(proposal):
                accepted += 1
        return accepted

    # -- hold bookkeeping --------------------------------------------------
    def can_hold(self, proposal: CommandProposal) -> bool:
        """True while the command still has hold ticks left before it must decide."""
        return proposal.hold_count < self.max_hold_ticks

    def hold(self, proposal: CommandProposal) -> CommandProposal:
        """Return the re-queued copy with an incremented hold counter.

        A command at its hold limit must be decided instead; asking again raises.
        """
        if not self.can_hold(proposal):
            raise HoldBudgetExceeded(
                f"command {proposal.command_id} reached max hold "
                f"({self.max_hold_ticks}) and must be decided"
            )
        held = proposal.model_copy(update={"hold_count": proposal.hold_count + 1})
        # The hold is the same command continuing across ticks, so it bypasses
        # duplicate suppression while keeping the original command id.
        self._enqueue(held)
        return held

    # -- consumption -------------------------------------------------------
    def drain(self, tick: int) -> QueueBatch:
        """Take everything queued for this tick, clearing both lanes."""
        proposals = tuple(self._pending)
        emergency = tuple(self._emergency)
        self._pending.clear()
        self._emergency.clear()
        return QueueBatch(tick=tick, proposals=proposals, emergency=emergency)

    # -- inspection --------------------------------------------------------
    @property
    def pending_count(self) -> int:
        """Proposals waiting for arbitration."""
        return len(self._pending)

    @property
    def emergency_count(self) -> int:
        """Proposals waiting on the bypass lane."""
        return len(self._emergency)

    @property
    def is_empty(self) -> bool:
        """True when both lanes are empty."""
        return not self._pending and not self._emergency

    def pending_snapshot(self) -> tuple[CommandProposal, ...]:
        """Read-only view of the arbitration lane (does not drain)."""
        return tuple(self._pending)

    def emergency_snapshot(self) -> tuple[CommandProposal, ...]:
        """Read-only view of the bypass lane (does not drain)."""
        return tuple(self._emergency)
