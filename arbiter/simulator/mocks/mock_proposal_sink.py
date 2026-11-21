"""Mock Proposal Queue sink for isolated simulation testing."""
from typing import Any, Dict, List

from arbiter.shared.models import CommandProposal


class MockProposalSink:
    """In-memory stub collecting proposals emitted by controllers."""

    def __init__(self) -> None:
        self.received_proposals: List[CommandProposal] = []

    def submit_proposal(self, proposal: CommandProposal) -> Dict[str, Any]:
        self.received_proposals.append(proposal)
        return {
            "status": "QUEUED_MOCK",
            "command_id": proposal.command_id,
            "mock_tick": len(self.received_proposals),
        }


# TODO(INTEGRATION): Replace MockProposalSink with HTTP/gRPC client targeting
# Branch 2 Queue endpoint. Real implementation in Branch 2:
# POST http://localhost:8000/api/v1/commands/propose
