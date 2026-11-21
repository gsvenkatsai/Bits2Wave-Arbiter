"""Adapter connecting Branch 1 simulator to Branch 2 ProposalQueue.

Enables SimulationEngine and SON controllers to feed the real referee queue
while preserving MockProposalSink for standalone unit tests.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from arbiter.engine.contracts import CommandProposal as EngineCommandProposal
from arbiter.queue.proposal_queue import ProposalQueue

__all__ = ["ProposalQueueSink"]


class ProposalQueueSink:
    """Proposal sink wrapping the real Branch 2 ProposalQueue."""

    def __init__(self, queue: Optional[ProposalQueue] = None) -> None:
        self.queue: ProposalQueue = queue or ProposalQueue()

    def submit_proposal(self, proposal: Any) -> Dict[str, Any]:
        """Submit proposal to real ProposalQueue (translates to submit)."""
        if not hasattr(proposal, "is_emergency"):
            if hasattr(proposal, "model_dump"):
                engine_prop = EngineCommandProposal.model_validate(
                    proposal.model_dump()
                )
            else:
                raw_dict = dict(proposal)
                engine_prop = EngineCommandProposal.model_validate(raw_dict)
        else:
            engine_prop = proposal

        accepted = self.queue.submit(engine_prop)
        return {
            "status": "QUEUED" if accepted else "DUPLICATE_REJECTED",
            "command_id": str(getattr(proposal, "command_id", "")),
            "accepted": accepted,
        }

    def submit(self, proposal: Any) -> bool:
        """Direct pass-through for ProposalQueue.submit."""
        res = self.submit_proposal(proposal)
        return bool(res.get("accepted", False))
