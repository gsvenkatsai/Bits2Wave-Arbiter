"""Task 5: the detector's cell pre-filter must never change the edge set.

``ConflictGraphBuilder`` only compares proposal pairs whose target cells are
the same cell or inside the classifier's hop radius, then classifies them.
This module proves that pre-filter is equivalent to comparing every pair by
generating random proposal sets and comparing against a naive all-pairs
builder.

It also pins the reason the pre-filter cannot be narrowed to *adjacent* cells:
the power-up rule reaches two hops, so distance-2 pairs legitimately conflict.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Sequence

import networkx as nx

from arbiter.detector.classifier import ConflictClassifier
from arbiter.detector.graph import ConflictGraphBuilder, batch_order

#: 7 cells: a 5-cell chain 0-1-2-3-4, cell 5 hanging off cell 2, cell 6 isolated.
TOPOLOGY: Dict[str, List[str]] = {
    "c0": ["c1"],
    "c1": ["c0", "c2"],
    "c2": ["c1", "c3", "c5"],
    "c3": ["c2", "c4"],
    "c4": ["c3"],
    "c5": ["c2"],
    "c6": [],
}

CELLS: Sequence[str] = tuple(sorted(TOPOLOGY))
PARAMETERS: Sequence[str] = (
    "TX_POWER_DBM",
    "TILT_DEG",
    "SLEEP_STATE",
    "OFFLOAD_RATIO",
)


def _naive_build(
    batch: Sequence[Any],
    adjacency: Dict[str, List[str]],
) -> nx.Graph[Any, Any]:
    """Reference builder: classify every pair, no pre-filter at all."""
    graph: nx.Graph[Any, Any] = nx.Graph()
    classifier = ConflictClassifier()
    ordered = sorted(batch, key=batch_order)
    for command in ordered:
        graph.add_node(command.command_id, command=command)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            conflict, rule = classifier.explain(left, right, adjacency)
            if conflict is not None:
                graph.add_edge(
                    left.command_id,
                    right.command_id,
                    conflict_type=conflict,
                    rule=rule,
                )
    return graph


def _edge_signature(graph: nx.Graph[Any, Any]) -> set[tuple[str, str, str, str]]:
    signature: set[tuple[str, str, str, str]] = set()
    for left, right, data in graph.edges(data=True):
        a, b = sorted((str(left), str(right)))
        conflict = data.get("conflict_type")
        signature.add(
            (
                a,
                b,
                str(getattr(conflict, "value", conflict)),
                str(data.get("rule")),
            )
        )
    return signature


def _random_batch(
    rng: random.Random,
    make_proposal: Any,
    size: int,
    suffix: str,
) -> List[Any]:
    batch: List[Any] = []
    for index in range(size):
        cell = rng.choice(CELLS)
        parameter = rng.choice(PARAMETERS)
        if parameter == "TX_POWER_DBM":
            current = rng.choice([30.0, 40.0, 43.0])
            requested = round(current + rng.choice([-6.0, -1.0, 1.0, 3.0]), 3)
        elif parameter == "TILT_DEG":
            current = rng.choice([2.0, 6.0, 10.0])
            requested = round(current + rng.choice([-2.0, 2.0]), 3)
        elif parameter == "SLEEP_STATE":
            current = rng.choice([0.0, 1.0])
            requested = 1.0 - current
        else:
            current = rng.choice([0.0, 0.1, 0.4])
            requested = round(rng.choice([0.0, 0.2, 0.4, 0.8]), 3)
        handover_to = None
        if parameter == "OFFLOAD_RATIO" and rng.random() < 0.4:
            handover_to = rng.choice(CELLS)
        batch.append(
            make_proposal(
                command_id=f"{suffix}-{index:03d}",
                controller=f"ctrl-{rng.choice(['es', 'lb', 'cc', 'ps'])}",
                target_cell=cell,
                parameter=parameter,
                current_value=current,
                requested_value=requested,
                handover_to=handover_to,
                created_tick=rng.choice([0, 1, 2]),
            )
        )
    return batch


def test_pre_filtered_builder_matches_the_naive_builder(
    make_proposal: Any,
) -> None:
    builder = ConflictGraphBuilder()
    rng = random.Random(20240117)
    non_empty = 0

    for case in range(80):
        size = rng.choice([1, 2, 3, 5, 8, 12])
        batch = _random_batch(rng, make_proposal, size, f"case{case}")

        filtered = builder.build(batch, TOPOLOGY)
        naive = _naive_build(batch, TOPOLOGY)

        assert set(filtered.nodes) == set(naive.nodes)
        assert _edge_signature(filtered) == _edge_signature(naive), (
            f"case {case} diverged with batch size {size}"
        )
        non_empty += 1 if naive.number_of_edges() else 0

    assert non_empty > 20, "random cases must actually exercise conflicts"


def test_pre_filter_keeps_two_hop_conflicts(make_proposal: Any) -> None:
    """Narrowing the filter to adjacent cells only would drop a real conflict."""
    power_up = make_proposal(
        command_id="power-c0",
        target_cell="c0",
        parameter="TX_POWER_DBM",
        current_value=40.0,
        requested_value=43.0,
    )
    idle_neighbour = make_proposal(
        command_id="tilt-c2",
        target_cell="c2",
        parameter="TILT_DEG",
        current_value=6.0,
        requested_value=4.0,
    )

    classifier = ConflictClassifier()
    conflict, _ = classifier.explain(power_up, idle_neighbour, TOPOLOGY)
    graph = ConflictGraphBuilder().build([power_up, idle_neighbour], TOPOLOGY)

    # c0 and c2 are two hops apart: not adjacent, but inside the hop radius.
    assert classifier.hop_distance(TOPOLOGY, "c0", "c2") == 2
    assert conflict is not None
    assert graph.has_edge("power-c0", "tilt-c2")


def test_unrelated_cells_produce_no_edges(make_proposal: Any) -> None:
    left = make_proposal(
        command_id="far-left",
        target_cell="c0",
        parameter="TX_POWER_DBM",
        current_value=40.0,
        requested_value=43.0,
    )
    right = make_proposal(
        command_id="far-right",
        target_cell="c6",
        parameter="TX_POWER_DBM",
        current_value=40.0,
        requested_value=43.0,
    )

    graph = ConflictGraphBuilder().build([left, right], TOPOLOGY)

    assert set(graph.nodes) == {"far-left", "far-right"}
    assert graph.number_of_edges() == 0
