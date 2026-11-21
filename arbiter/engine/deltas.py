"""Canonical candidate-delta payloads exchanged with the digital twin."""
from __future__ import annotations

from typing import Any

from arbiter.engine.contracts import CommandProposal

__all__ = ["CandidateDelta", "parameter_key", "build_delta"]


#: Structural type of one candidate delta (mirrors Branch 3's twin contract).
CandidateDelta = dict[str, Any]


def parameter_key(parameter: object) -> str:
    """Normalise an enum or string parameter to its canonical string value."""
    value = getattr(parameter, "value", parameter)
    return str(value)


def build_delta(
    proposal: CommandProposal,
    value: float,
    current_value: float | None = None,
) -> CandidateDelta:
    """Build the twin payload for applying ``value`` to one proposal's target."""
    return {
        "command_id": proposal.command_id,
        "cell_id": proposal.target_cell_id,
        "parameter": parameter_key(proposal.parameter),
        "from_value": current_value,
        "to_value": float(value),
    }
