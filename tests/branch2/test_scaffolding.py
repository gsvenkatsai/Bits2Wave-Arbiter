"""Scaffolding tests: hex topology fixture, proposal factory, package skeletons."""
from __future__ import annotations

import importlib
from typing import Any

import pytest

from arbiter.engine.contracts import CommandProposal


def test_hex_adjacency_has_five_cells(hex_adjacency: dict[str, list[str]]) -> None:
    assert sorted(hex_adjacency) == ["A", "B", "C", "D", "E"]


def test_a_is_adjacent_to_b(hex_adjacency: dict[str, list[str]]) -> None:
    assert "B" in hex_adjacency["A"]
    assert "A" in hex_adjacency["B"]


def test_adjacency_is_symmetric(hex_adjacency: dict[str, list[str]]) -> None:
    for cell, neighbours in hex_adjacency.items():
        for neighbour in neighbours:
            assert cell in hex_adjacency[neighbour]


def test_cell_ids_fixture(cell_ids: tuple[str, ...]) -> None:
    assert cell_ids == ("A", "B", "C", "D", "E")


def test_hex_adjacency_fixture_is_a_copy(hex_adjacency: dict[str, list[str]]) -> None:
    hex_adjacency["A"].append("E")
    from tests.branch2.conftest import HEX_ADJACENCY

    assert "E" not in HEX_ADJACENCY["A"]


def test_make_proposal_defaults(make_proposal: Any) -> None:
    proposal = make_proposal()
    assert isinstance(proposal, CommandProposal)
    assert proposal.command_id == "cmd-001"
    assert proposal.controller == "ctrl-energy"
    assert proposal.target_cell == "A"
    assert proposal.parameter == "TX_POWER_DBM"
    assert proposal.requested_value == pytest.approx(40.0)
    assert proposal.current_value == pytest.approx(30.0)
    assert proposal.created_tick == 0
    assert proposal.hold_count == 0
    assert proposal.is_emergency is False
    assert proposal.is_binary is False


def test_make_proposal_overrides_and_unique_ids(make_proposal: Any) -> None:
    first = make_proposal(command_id="custom", target_cell="C", is_binary=True)
    second = make_proposal()
    assert first.command_id == "custom"
    assert first.target_cell == "C"
    assert first.is_binary is True
    assert second.command_id != first.command_id


@pytest.mark.parametrize("module_name", ["arbiter.queue", "arbiter.detector"])
def test_skeleton_packages_import(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.__all__


def test_package_skeletons_share_spec_constants() -> None:
    queue = importlib.import_module("arbiter.queue")
    detector = importlib.import_module("arbiter.detector")
    assert queue.TICK_MS == 100
    assert queue.MAX_HOLD_TICKS == 3
    assert detector.PINGPONG_WINDOW == 5
