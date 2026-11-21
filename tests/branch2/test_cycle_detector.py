"""Cycle detector tests: reciprocal/longer cycles and sliding-window expiry."""
from __future__ import annotations

from typing import Any

import pytest

from arbiter.detector import PingPongCycleDetector


def _handover(make_proposal: Any, command_id: str, source: str, destination: str) -> Any:
    return make_proposal(
        command_id=command_id,
        controller="ctrl-lb",
        target_cell=source,
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from=source,
        handover_to=destination,
    )


def test_reciprocal_handover_is_cyclic(make_proposal: Any) -> None:
    detector = PingPongCycleDetector(window_ticks=5)
    outbound = _handover(make_proposal, "h1", "A", "B")
    inbound = _handover(make_proposal, "h2", "B", "A")

    detector.record(outbound, tick=1)

    assert detector.is_cyclic(inbound, tick=2) is True


def test_longer_cycle_is_detected(make_proposal: Any) -> None:
    detector = PingPongCycleDetector(window_ticks=5)
    detector.record(_handover(make_proposal, "h1", "A", "B"), tick=1)
    detector.record(_handover(make_proposal, "h2", "B", "C"), tick=2)

    closing = _handover(make_proposal, "h3", "C", "A")

    assert detector.is_cyclic(closing, tick=3) is True


def test_unrelated_handover_is_not_cyclic(make_proposal: Any) -> None:
    detector = PingPongCycleDetector(window_ticks=5)
    detector.record(_handover(make_proposal, "h1", "A", "B"), tick=1)

    other = _handover(make_proposal, "h2", "C", "D")

    assert detector.is_cyclic(other, tick=2) is False


def test_window_expiry(make_proposal: Any) -> None:
    detector = PingPongCycleDetector(window_ticks=5)
    detector.record(_handover(make_proposal, "h1", "A", "B"), tick=0)

    inbound = _handover(make_proposal, "h2", "B", "A")

    assert detector.is_cyclic(inbound, tick=5) is True  # boundary is inclusive
    assert detector.is_cyclic(inbound, tick=6) is False  # edge expired


def test_expired_edges_are_pruned(make_proposal: Any) -> None:
    detector = PingPongCycleDetector(window_ticks=2)
    detector.record(_handover(make_proposal, "h1", "A", "B"), tick=0)

    detector.record(_handover(make_proposal, "h2", "C", "D"), tick=3)

    assert [edge.command_id for edge in detector.edges] == ["h2"]


def test_non_handover_command_records_nothing(make_proposal: Any) -> None:
    detector = PingPongCycleDetector()
    plain = make_proposal(command_id="plain", target_cell="A")

    assert detector.record(plain, tick=0) is None
    assert detector.is_cyclic(plain, tick=0) is False
    assert detector.edges == ()


def test_edge_derives_source_from_target_cell(make_proposal: Any) -> None:
    detector = PingPongCycleDetector()
    command = make_proposal(
        command_id="h1",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        handover_to="B",
    )

    edge = detector.record(command, tick=2)

    assert edge is not None
    assert edge.source_cell == "A"
    assert edge.destination_cell == "B"
    assert edge.tick == 2
    assert edge.command_id == "h1"


def test_self_handover_is_ignored(make_proposal: Any) -> None:
    detector = PingPongCycleDetector()
    command = make_proposal(
        command_id="self",
        target_cell="A",
        handover_from="A",
        handover_to="A",
    )

    assert detector.edge_for(command, tick=0) is None
    assert detector.is_cyclic(command, tick=0) is False


def test_reset_clears_history(make_proposal: Any) -> None:
    detector = PingPongCycleDetector()
    detector.record(_handover(make_proposal, "h1", "A", "B"), tick=0)
    detector.reset()

    assert detector.edges == ()
    assert detector.is_cyclic(_handover(make_proposal, "h2", "B", "A"), tick=1) is False


def test_active_edges_respects_window(make_proposal: Any) -> None:
    detector = PingPongCycleDetector(window_ticks=3)
    detector.record(_handover(make_proposal, "h1", "A", "B"), tick=0)
    detector.record(_handover(make_proposal, "h2", "B", "C"), tick=2)

    assert [edge.command_id for edge in detector.active_edges(3)] == ["h1", "h2"]
    assert [edge.command_id for edge in detector.active_edges(6)] == []


def test_invalid_window() -> None:
    with pytest.raises(ValueError):
        PingPongCycleDetector(window_ticks=0)
