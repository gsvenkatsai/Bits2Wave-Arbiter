"""Classifier tests: DIRECT vs same-direction, INDIRECT rules and hop radius."""
from __future__ import annotations

from typing import Any

import networkx as nx
import pytest

from arbiter.detector import ConflictClassifier, direction, normalize_parameter
from arbiter.engine.contracts import ConflictType

HEX = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}


def _power(make_proposal: Any, **overrides: Any) -> Any:
    payload = {
        "target_cell": "A",
        "parameter": "TX_POWER_DBM",
        "current_value": 30.0,
        "requested_value": 33.0,
    }
    payload.update(overrides)
    return make_proposal(**payload)


def _sleep(make_proposal: Any, **overrides: Any) -> Any:
    payload = {
        "target_cell": "B",
        "parameter": "SLEEP_STATE",
        "current_value": 0.0,
        "requested_value": 1.0,
    }
    payload.update(overrides)
    return make_proposal(**payload)


def test_direct_conflict_for_opposing_directions(make_proposal: Any, hex_adjacency: dict) -> None:
    up = _power(make_proposal, command_id="up", requested_value=35.0)
    down = _power(make_proposal, command_id="down", requested_value=25.0)

    assert ConflictClassifier().classify(up, down, hex_adjacency) is ConflictType.DIRECT


def test_same_direction_is_not_a_conflict(make_proposal: Any, hex_adjacency: dict) -> None:
    first = _power(make_proposal, command_id="a", requested_value=35.0)
    second = _power(make_proposal, command_id="b", requested_value=33.0)

    assert ConflictClassifier().classify(first, second, hex_adjacency) is None


def test_identical_requests_are_not_a_conflict(make_proposal: Any, hex_adjacency: dict) -> None:
    first = _power(make_proposal, command_id="a")
    second = _power(make_proposal, command_id="b")

    assert ConflictClassifier().classify(first, second, hex_adjacency) is None


def test_no_change_is_not_a_conflict(make_proposal: Any, hex_adjacency: dict) -> None:
    first = _power(make_proposal, command_id="a", requested_value=30.0)
    second = _power(make_proposal, command_id="b", requested_value=20.0)

    assert ConflictClassifier().classify(first, second, hex_adjacency) is None


def test_same_cell_different_parameter_is_not_a_conflict(
    make_proposal: Any, hex_adjacency: dict
) -> None:
    power = _power(make_proposal, command_id="power")
    tilt = make_proposal(
        command_id="tilt",
        target_cell="A",
        parameter="TILT_DEG",
        current_value=4.0,
        requested_value=2.0,
    )

    assert ConflictClassifier().classify(power, tilt, hex_adjacency) is None


def test_indirect_power_up_vs_neighbour(make_proposal: Any, hex_adjacency: dict) -> None:
    power = _power(make_proposal, command_id="power", target_cell="A")
    victim = _sleep(make_proposal, command_id="sleep", target_cell="B")

    assert ConflictClassifier().classify(power, victim, hex_adjacency) is ConflictType.INDIRECT


def test_indirect_within_two_hops(make_proposal: Any, hex_adjacency: dict) -> None:
    power = _power(make_proposal, command_id="power", target_cell="A")
    victim = _sleep(make_proposal, command_id="sleep", target_cell="C")

    assert ConflictClassifier().classify(power, victim, hex_adjacency) is ConflictType.INDIRECT


def test_no_indirect_conflict_outside_hop_radius(
    make_proposal: Any, hex_adjacency: dict
) -> None:
    power = _power(make_proposal, command_id="power", target_cell="A")
    victim = _sleep(make_proposal, command_id="sleep", target_cell="D")

    assert ConflictClassifier().classify(power, victim, hex_adjacency) is None


def test_hop_radius_is_configurable(make_proposal: Any, hex_adjacency: dict) -> None:
    power = _power(make_proposal, command_id="power", target_cell="A")
    victim = _sleep(make_proposal, command_id="sleep", target_cell="C")
    classifier = ConflictClassifier(hop_radius=1)

    assert classifier.classify(power, victim, hex_adjacency) is None


def test_interference_sensitivity_can_be_narrowed(
    make_proposal: Any, hex_adjacency: dict
) -> None:
    power = _power(make_proposal, command_id="power", target_cell="A")
    victim = _sleep(make_proposal, command_id="sleep", target_cell="C")
    classifier = ConflictClassifier(interference_sensitive_cells={"B"})

    assert classifier.classify(power, victim, hex_adjacency) is None
    assert classifier.is_interference_sensitive("B") is True
    assert classifier.is_interference_sensitive("C") is False


def test_sleep_vs_inbound_offload(make_proposal: Any, hex_adjacency: dict) -> None:
    sleeper = _sleep(make_proposal, command_id="sleep", target_cell="B")
    offloader = make_proposal(
        command_id="offload",
        controller="ctrl-lb",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from="A",
        handover_to="B",
    )
    classifier = ConflictClassifier()

    conflict, rule = classifier.explain(sleeper, offloader, hex_adjacency)
    assert conflict is ConflictType.INDIRECT
    assert rule == "sleep_vs_inbound_offload"


def test_inbound_offload_without_destination_uses_neighbour_radius(
    make_proposal: Any, hex_adjacency: dict
) -> None:
    sleeper = _sleep(make_proposal, command_id="sleep", target_cell="B")
    offloader = make_proposal(
        command_id="offload",
        controller="ctrl-lb",
        target_cell="A",
        parameter="HANDOVER_SHARE",
        current_value=0.1,
        requested_value=0.5,
    )

    assert (
        ConflictClassifier().classify(sleeper, offloader, hex_adjacency)
        is ConflictType.INDIRECT
    )


def test_decreasing_offload_is_not_harmful(make_proposal: Any, hex_adjacency: dict) -> None:
    sleeper = _sleep(make_proposal, command_id="sleep", target_cell="B")
    offloader = make_proposal(
        command_id="offload",
        controller="ctrl-lb",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.6,
        requested_value=0.2,
        handover_to="B",
    )

    assert ConflictClassifier().classify(sleeper, offloader, hex_adjacency) is None


def test_offload_toward_a_different_cell_is_not_harmful(
    make_proposal: Any, hex_adjacency: dict
) -> None:
    sleeper = _sleep(make_proposal, command_id="sleep", target_cell="B")
    offloader = make_proposal(
        command_id="offload",
        controller="ctrl-lb",
        target_cell="C",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_to="D",
    )

    assert ConflictClassifier().classify(sleeper, offloader, hex_adjacency) is None


def test_power_decrease_is_not_harmful(make_proposal: Any, hex_adjacency: dict) -> None:
    power_down = _power(
        make_proposal, command_id="power", target_cell="A", requested_value=25.0
    )
    victim = _sleep(make_proposal, command_id="sleep", target_cell="B")

    assert ConflictClassifier().classify(power_down, victim, hex_adjacency) is None


def test_adjacency_accepts_a_networkx_graph(make_proposal: Any) -> None:
    graph = nx.Graph()
    graph.add_edge("A", "B")
    power = _power(make_proposal, command_id="power", target_cell="A")
    victim = _sleep(make_proposal, command_id="sleep", target_cell="B")

    assert (
        ConflictClassifier().classify(power, victim, graph) is ConflictType.INDIRECT
    )


def test_hop_helpers(hex_adjacency: dict) -> None:
    classifier = ConflictClassifier()

    assert classifier.hop_distance(hex_adjacency, "A", "A") == 0
    assert classifier.hop_distance(hex_adjacency, "A", "C") == 2
    assert classifier.hop_distance(hex_adjacency, "A", "D") == 3
    assert classifier.cells_within("B", hex_adjacency) == frozenset({"A", "B", "C", "D"})
    assert classifier.cells_within("B", hex_adjacency, radius=1) == frozenset({"A", "B", "C"})


def test_direction_and_parameter_normalisation(make_proposal: Any) -> None:
    up = _power(make_proposal, requested_value=35.0)
    down = _power(make_proposal, requested_value=25.0)
    flat = _power(make_proposal, requested_value=30.0)

    assert direction(up) == 1
    assert direction(down) == -1
    assert direction(flat) == 0
    assert normalize_parameter("tx_power_dbm") == "TX_POWER_DBM"


def test_invalid_hop_radius() -> None:
    with pytest.raises(ValueError):
        ConflictClassifier(hop_radius=0)
