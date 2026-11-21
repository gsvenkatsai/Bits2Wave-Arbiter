"""Shared fixtures for Branch 2 tests.

Provides the 5-cell hex topology (A-B-C-D-E, A adjacent to B) and a factory for
building valid ``CommandProposal`` objects.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap only
    sys.path.insert(0, str(ROOT))

from arbiter.engine.contracts import CommandProposal  # noqa: E402

#: 5-cell hex layout flattened to a chain: A-B-C-D-E.
#: Neighbour lists are symmetric so either direction can be looked up.
HEX_ADJACENCY: dict[str, list[str]] = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}

ProposalFactory = Callable[..., CommandProposal]


@pytest.fixture
def cell_ids() -> tuple[str, ...]:
    """The five cell ids of the test topology, in order."""
    return ("A", "B", "C", "D", "E")


@pytest.fixture
def hex_adjacency() -> dict[str, list[str]]:
    """A fresh copy of the 5-cell hex adjacency map."""
    return {cell: list(neighbours) for cell, neighbours in HEX_ADJACENCY.items()}


@pytest.fixture
def make_proposal() -> ProposalFactory:
    """Factory building valid proposals with per-test overrides."""
    counter = itertools.count(1)

    def _make(**overrides: Any) -> CommandProposal:
        payload: dict[str, Any] = {
            "command_id": f"cmd-{next(counter):03d}",
            "controller": "ctrl-energy",
            "controller_type": "ENERGY_SAVING",
            "target_cell": "A",
            "timestamp_epoch_ms": 0,
            "parameter": "TX_POWER_DBM",
            "requested_value": 40.0,
            "current_value": 30.0,
            "is_emergency": False,
            "is_binary": False,
            "created_tick": 0,
            "hold_count": 0,
            "handover_from": None,
            "handover_to": None,
        }
        payload.update(overrides)
        return CommandProposal(**payload)

    return _make
