"""Conflict graph tests: node/edge attributes and the 2 AM collision scenario."""
from __future__ import annotations

from typing import Any

import networkx as nx

from arbiter.detector import ConflictGraphBuilder
from arbiter.engine.contracts import ConflictType

HEX = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}


def _two_am_collision(make_proposal: Any) -> list[Any]:
    """ES puts B to sleep, LB offloads A->B, CC raises A by 3 dB."""
    energy_saver = make_proposal(
        command_id="es",
        controller="ctrl-es",
        target_cell="B",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
    )
    load_balancer = make_proposal(
        command_id="lb",
        controller="ctrl-lb",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from="A",
        handover_to="B",
    )
    coverage = make_proposal(
        command_id="cc",
        controller="ctrl-cc",
        target_cell="A",
        parameter="TX_POWER_DBM",
        current_value=30.0,
        requested_value=33.0,
    )
    return [energy_saver, load_balancer, coverage]


def test_graph_carries_command_and_conflict_attributes(make_proposal: Any) -> None:
    power_up = make_proposal(command_id="p1", requested_value=35.0)
    power_down = make_proposal(command_id="p2", requested_value=25.0)

    graph = ConflictGraphBuilder().build([power_up, power_down], HEX)

    assert isinstance(graph, nx.Graph)
    assert graph.nodes["p1"]["command"] is power_up
    edge = graph.edges["p1", "p2"]
    assert edge["conflict_type"] is ConflictType.DIRECT
    assert edge["rule"] == "opposing_values"


def test_two_am_collision_produces_expected_edges(make_proposal: Any) -> None:
    batch = _two_am_collision(make_proposal)

    graph = ConflictGraphBuilder().build(batch, HEX)

    assert sorted(graph.nodes) == ["cc", "es", "lb"]
    edge_list = {tuple(sorted(edge)) for edge in graph.edges}
    assert edge_list == {("es", "lb"), ("cc", "es")}
    assert graph.edges["es", "lb"]["conflict_type"] is ConflictType.INDIRECT
    assert graph.edges["es", "lb"]["rule"] == "sleep_vs_inbound_offload"
    assert graph.edges["cc", "es"]["conflict_type"] is ConflictType.INDIRECT
    assert graph.edges["cc", "es"]["rule"] == "power_up_vs_sensitive_neighbour"
    # Same cell, different parameters: not a DIRECT clash.
    assert not graph.has_edge("lb", "cc")


def test_two_am_collision_is_a_single_component(make_proposal: Any) -> None:
    graph = ConflictGraphBuilder().build(_two_am_collision(make_proposal), HEX)
    components = list(nx.connected_components(graph))
    assert len(components) == 1
    assert components[0] == {"cc", "es", "lb"}


def test_far_cells_are_not_connected(make_proposal: Any) -> None:
    near = make_proposal(command_id="near", target_cell="A", requested_value=35.0)
    far = make_proposal(
        command_id="far",
        target_cell="E",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
    )

    graph = ConflictGraphBuilder().build([near, far], HEX)

    assert set(graph.nodes) == {"near", "far"}
    assert graph.number_of_edges() == 0


def test_graph_build_is_deterministic(make_proposal: Any) -> None:
    batch = _two_am_collision(make_proposal)
    builder = ConflictGraphBuilder()

    first = builder.build(batch, HEX)
    second = builder.build(batch, HEX)

    assert sorted(first.edges) == sorted(second.edges)
    assert {tuple(sorted(edge)) for edge in first.edges} == {
        tuple(sorted(edge)) for edge in second.edges
    }


def test_isolated_command_still_appears_as_node(make_proposal: Any) -> None:
    lonely = make_proposal(command_id="lonely", target_cell="E")

    graph = ConflictGraphBuilder().build([lonely], HEX)

    assert list(graph.nodes) == ["lonely"]


def test_builder_accepts_networkx_adjacency(make_proposal: Any) -> None:
    adjacency = nx.Graph()
    adjacency.add_edge("A", "B")
    power = make_proposal(command_id="p1", target_cell="A")
    sleeper = make_proposal(
        command_id="p2",
        target_cell="B",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
    )

    graph = ConflictGraphBuilder().build([power, sleeper], adjacency)

    assert graph.has_edge("p1", "p2")


def test_builder_exposes_classifier() -> None:
    builder = ConflictGraphBuilder()
    assert builder.classifier.hop_radius == 2
