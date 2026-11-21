"""Partitioner tests: deterministic components, parallel solving, isolation."""
from __future__ import annotations

import threading
import time
from typing import Any, List, Sequence

import pytest

from arbiter.detector import ConflictGraphBuilder, GraphPartitioner
from arbiter.engine.contracts import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ReasonCode,
)

#: Two disconnected islands: {A, B} and {X, Y}.
ISLANDS = {"A": ["B"], "B": ["A"], "X": ["Y"], "Y": ["X"]}


def _two_cluster_graph(make_proposal: Any) -> Any:
    batch = [
        make_proposal(
            command_id="es",
            target_cell="B",
            parameter="SLEEP_STATE",
            current_value=0.0,
            requested_value=1.0,
        ),
        make_proposal(
            command_id="lb",
            target_cell="A",
            parameter="OFFLOAD_RATIO",
            current_value=0.0,
            requested_value=0.4,
            handover_from="A",
            handover_to="B",
        ),
        make_proposal(
            command_id="cc",
            target_cell="A",
            parameter="TX_POWER_DBM",
            current_value=30.0,
            requested_value=33.0,
        ),
        make_proposal(
            command_id="x-power",
            target_cell="X",
            parameter="TX_POWER_DBM",
            current_value=30.0,
            requested_value=33.0,
        ),
        make_proposal(
            command_id="y-sleep",
            target_cell="Y",
            parameter="SLEEP_STATE",
            current_value=0.0,
            requested_value=1.0,
        ),
    ]
    return ConflictGraphBuilder().build(batch, ISLANDS)


def _approve_solver(component: List[CommandProposal]) -> List[ArbitrationDecision]:
    return [
        ArbitrationDecision(
            decision_id=f"d-{command.command_id}",
            command_id=command.command_id,
            batch_tick=1,
            action=ActionType.APPROVE,
            approved_value=command.requested_value,
            delta_utility=0.01,
            reason_code=ReasonCode.NONE,
            decided_at_epoch_ms=0,
        )
        for command in component
    ]


def _summarise(results: Sequence[Sequence[ArbitrationDecision]]) -> List[List[tuple]]:
    return [
        [
            (decision.command_id, decision.action, decision.delta_utility)
            for decision in component
        ]
        for component in results
    ]


def test_components_are_deterministic_and_sorted(make_proposal: Any) -> None:
    graph = _two_cluster_graph(make_proposal)

    components = GraphPartitioner().components(graph)

    assert [[command.command_id for command in component] for component in components] == [
        ["cc", "es", "lb"],
        ["x-power", "y-sleep"],
    ]


def test_components_cover_every_node_once(make_proposal: Any) -> None:
    graph = _two_cluster_graph(make_proposal)

    components = GraphPartitioner().components(graph)
    flattened = [command.command_id for component in components for command in component]

    assert sorted(flattened) == sorted(graph.nodes)
    assert len(flattened) == len(set(flattened))


def test_parallel_matches_serial(make_proposal: Any) -> None:
    graph = _two_cluster_graph(make_proposal)
    partitioner = GraphPartitioner()
    components = partitioner.components(graph)

    serial = partitioner.solve_serial(components, _approve_solver, tick=1)
    parallel = partitioner.solve_parallel(components, _approve_solver, max_workers=4, tick=1)

    assert _summarise(parallel) == _summarise(serial)
    assert [len(component) for component in parallel] == [3, 2]


def test_parallel_preserves_component_order(make_proposal: Any) -> None:
    graph = _two_cluster_graph(make_proposal)
    partitioner = GraphPartitioner()

    results = partitioner.solve_parallel(
        partitioner.components(graph), _approve_solver, max_workers=2
    )

    assert [decision.command_id for decision in results[0]] == ["cc", "es", "lb"]
    assert [decision.command_id for decision in results[1]] == ["x-power", "y-sleep"]


def test_parallel_uses_multiple_workers(make_proposal: Any) -> None:
    graph = _two_cluster_graph(make_proposal)
    partitioner = GraphPartitioner()
    thread_names: List[str] = []
    lock = threading.Lock()

    def solver(component: List[CommandProposal]) -> List[ArbitrationDecision]:
        with lock:
            thread_names.append(threading.current_thread().name)
        time.sleep(0.02)
        return _approve_solver(component)

    partitioner.solve_parallel(partitioner.components(graph), solver, max_workers=2)

    assert len(set(thread_names)) == 2


def test_solver_exception_falls_back_to_reject(make_proposal: Any) -> None:
    graph = _two_cluster_graph(make_proposal)
    partitioner = GraphPartitioner()
    errors: List[tuple[int, BaseException]] = []

    def solver(component: List[CommandProposal]) -> List[ArbitrationDecision]:
        if component[0].command_id == "cc":
            raise RuntimeError("twin exploded")
        return _approve_solver(component)

    results = partitioner.solve_parallel(
        partitioner.components(graph),
        solver,
        max_workers=2,
        tick=9,
        on_error=lambda index, error: errors.append((index, error)),
    )

    failed = results[0]
    assert [decision.command_id for decision in failed] == ["cc", "es", "lb"]
    for decision in failed:
        assert decision.action is ActionType.REJECT
        assert decision.reason_code is ReasonCode.TWIN_TIMEOUT
        assert decision.approved_value is None
        assert decision.delta_utility == pytest.approx(0.0)
        assert decision.batch_tick == 9
    assert [decision.action for decision in results[1]] == [
        ActionType.APPROVE,
        ActionType.APPROVE,
    ]
    assert len(errors) == 1
    assert errors[0][0] == 0
    assert isinstance(errors[0][1], RuntimeError)


def test_empty_components_return_empty(make_proposal: Any) -> None:
    assert GraphPartitioner().solve_parallel([], _approve_solver, max_workers=4) == []


def test_invalid_max_workers(make_proposal: Any) -> None:
    graph = _two_cluster_graph(make_proposal)
    partitioner = GraphPartitioner()

    with pytest.raises(ValueError):
        partitioner.solve_parallel(partitioner.components(graph), _approve_solver, 0)
