"""Conflict detection tests: DIRECT, INDIRECT, IMPLICIT_PINGPONG and graph."""
from __future__ import annotations

from typing import Any

import pytest

from arbiter.detector import ConflictDetector, ConflictEdge, ConflictGraph, PingPongTracker
from arbiter.engine.contracts import ConflictType, ParameterType

NEIGHBORS = {"cell-a": ["cell-b"], "cell-b": ["cell-c"], "cell-c": ["cell-d"]}


def test_direct_conflict_on_same_cell_and_parameter(make_proposal: Any) -> None:
    detector = ConflictDetector()
    left = make_proposal(command_id="a", requested_value=40.0)
    right = make_proposal(command_id="b", requested_value=30.0)

    graph = detector.detect([left, right], tick=1)

    assert graph.nodes == ("a", "b")
    assert len(graph.edges) == 1
    assert graph.edges[0].conflict_type is ConflictType.DIRECT
    assert graph.conflict_between("a", "b") is ConflictType.DIRECT
    assert graph.conflict_between("b", "a") is ConflictType.DIRECT
    assert graph.components() == (("a", "b"),)


def test_identical_requests_do_not_conflict(make_proposal: Any) -> None:
    detector = ConflictDetector()
    left = make_proposal(command_id="a", requested_value=40.0)
    right = make_proposal(command_id="b", requested_value=40.0)

    assert detector.detect([left, right], tick=1).edges == ()


def test_same_cell_different_parameter_does_not_conflict(make_proposal: Any) -> None:
    detector = ConflictDetector()
    left = make_proposal(command_id="a", parameter=ParameterType.TX_POWER_DBM)
    right = make_proposal(command_id="b", parameter=ParameterType.TILT_DEG, requested_value=3.0)

    assert detector.detect([left, right], tick=1).edges == ()


def test_indirect_conflict_at_one_and_two_hops(make_proposal: Any) -> None:
    detector = ConflictDetector(NEIGHBORS)
    near = make_proposal(command_id="near", target_cell_id="cell-b")
    far = make_proposal(command_id="far", target_cell_id="cell-c", controller_id="ctrl-lb")

    graph = detector.detect([near, far], tick=1)

    assert len(graph.edges) == 1
    assert graph.edges[0].conflict_type is ConflictType.INDIRECT


def test_no_indirect_conflict_beyond_two_hops(make_proposal: Any) -> None:
    detector = ConflictDetector(NEIGHBORS)
    left = make_proposal(command_id="a", target_cell_id="cell-a")
    right = make_proposal(command_id="d", target_cell_id="cell-d", controller_id="ctrl-lb")

    assert detector.detect([left, right], tick=1).edges == ()


def test_hops_distance_and_cache() -> None:
    detector = ConflictDetector(NEIGHBORS)
    assert detector.hops("cell-a", "cell-a") == 0
    assert detector.hops("cell-a", "cell-c") == 2
    assert detector.hops("cell-c", "cell-a") == 2
    assert detector.hops("cell-a", "cell-d") is None
    assert detector.hops("cell-x", "cell-a") is None


def test_conflict_edges_expose_endpoints() -> None:
    edge = ConflictEdge("a", "b", ConflictType.DIRECT)
    assert edge.involves("a") is True
    assert edge.involves("c") is False
    assert edge.other("a") == "b"
    assert edge.other("b") == "a"


def test_neighbours_and_empty_graph() -> None:
    graph = ConflictGraph(["a", "b", "c"], [ConflictEdge("a", "c", ConflictType.INDIRECT)])
    assert graph.neighbours("a") == ("c",)
    assert graph.neighbours("b") == ()
    assert graph.components() == (("a", "c"), ("b",))
    assert graph.conflict_between("a", "b") is None


def test_graph_deduplicates_nodes() -> None:
    graph = ConflictGraph(["a", "a", "b"])
    assert graph.nodes == ("a", "b")


def test_pingpong_tracker_detects_reversal() -> None:
    tracker = PingPongTracker(window_ticks=5)
    tracker.record(1, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a")
    tracker.record(2, "ctrl-es", ParameterType.SLEEP_STATE, "cell-b")

    assert tracker.is_pingpong(3, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a") is True
    assert tracker.is_pingpong(3, "ctrl-es", ParameterType.SLEEP_STATE, "cell-c") is False
    assert tracker.is_pingpong(3, "ctrl-lb", ParameterType.SLEEP_STATE, "cell-a") is False


def test_pingpong_tracker_respects_sliding_window() -> None:
    tracker = PingPongTracker(window_ticks=5)
    tracker.record(1, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a")
    tracker.record(2, "ctrl-es", ParameterType.SLEEP_STATE, "cell-b")

    assert tracker.is_pingpong(8, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a") is False


def test_pingpong_tracker_needs_two_distinct_moves() -> None:
    tracker = PingPongTracker()
    tracker.record(1, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a")
    assert tracker.is_pingpong(2, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a") is False

    tracker.record(2, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a")
    assert tracker.is_pingpong(3, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a") is False


def test_pingpong_tracker_reset_and_validation() -> None:
    tracker = PingPongTracker()
    tracker.record(1, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a")
    tracker.reset()
    assert tracker.is_pingpong(2, "ctrl-es", ParameterType.SLEEP_STATE, "cell-a") is False
    with pytest.raises(ValueError):
        PingPongTracker(window_ticks=0)


def test_detector_flags_pingpong_and_skips_its_edges(make_proposal: Any) -> None:
    detector = ConflictDetector()
    detector.record_decision(1, "ctrl-es", ParameterType.TX_POWER_DBM, "cell-a")
    detector.record_decision(2, "ctrl-es", ParameterType.TX_POWER_DBM, "cell-b")

    reversing = make_proposal(
        command_id="reversing", controller_id="ctrl-es", target_cell_id="cell-a"
    )
    competing = make_proposal(
        command_id="competing", target_cell_id="cell-a", requested_value=20.0
    )

    graph = detector.detect([reversing, competing], tick=3)

    assert graph.pingpong_command_ids == frozenset({"reversing"})
    assert all(edge.involves("reversing") is False for edge in graph.edges)


def test_detector_reset_history(make_proposal: Any) -> None:
    detector = ConflictDetector()
    detector.record_decision(1, "ctrl-es", ParameterType.TX_POWER_DBM, "cell-a")
    detector.record_decision(2, "ctrl-es", ParameterType.TX_POWER_DBM, "cell-b")
    detector.reset_history()

    graph = detector.detect(
        [make_proposal(command_id="x", target_cell_id="cell-a")], tick=3
    )
    assert graph.pingpong_command_ids == frozenset()
