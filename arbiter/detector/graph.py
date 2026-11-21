"""FR-4 conflict graph construction on top of networkx.

Nodes are commands (``command`` node attribute) and edges carry the
``conflict_type`` plus the matched classifier rule. Pair checks are restricted
to cells inside the hop radius, so cost scales with neighbourhood size instead
of the whole batch.
"""
from __future__ import annotations

from typing import Any, Sequence

import networkx as nx

from arbiter.detector.classifier import AdjacencyLike, ConflictClassifier
from arbiter.engine.contracts import CommandProposal

__all__ = ["ConflictGraphBuilder", "batch_order"]


def batch_order(command: CommandProposal) -> tuple[int, str]:
    """Deterministic ordering: creation tick, then command id."""
    return (command.created_tick, command.command_id)


class ConflictGraphBuilder:
    """Builds the per-tick conflict graph."""

    def __init__(self, classifier: ConflictClassifier | None = None) -> None:
        self._classifier = classifier or ConflictClassifier()

    @property
    def classifier(self) -> ConflictClassifier:
        """Classifier used for pairwise checks."""
        return self._classifier

    def build(
        self,
        batch: Sequence[CommandProposal],
        adjacency: AdjacencyLike,
    ) -> nx.Graph[Any, Any]:
        """Return a conflict graph for ``batch``."""
        graph: nx.Graph[Any, Any] = nx.Graph()
        ordered = sorted(batch, key=batch_order)
        by_cell: dict[str, list[CommandProposal]] = {}
        for command in ordered:
            graph.add_node(command.command_id, command=command)
            by_cell.setdefault(command.target_cell, []).append(command)

        for cell in sorted(by_cell):
            for other_cell in sorted(
                self._classifier.cells_within(cell, adjacency)
            ):
                if other_cell == cell or other_cell not in by_cell:
                    continue
                for left in by_cell[cell]:
                    for right in by_cell[other_cell]:
                        # Each undirected pair is examined once, in a stable order.
                        if left.command_id > right.command_id:
                            continue
                        self._add_edge_if_conflicting(graph, left, right, adjacency)
            for index, left in enumerate(by_cell[cell]):
                for right in by_cell[cell][index + 1:]:
                    self._add_edge_if_conflicting(graph, left, right, adjacency)
        return graph

    def _add_edge_if_conflicting(
        self,
        graph: nx.Graph[Any, Any],
        left: CommandProposal,
        right: CommandProposal,
        adjacency: AdjacencyLike,
    ) -> None:
        conflict, rule = self._classifier.explain(left, right, adjacency)
        if conflict is None:
            return
        graph.add_edge(
            left.command_id,
            right.command_id,
            conflict_type=conflict,
            rule=rule,
        )
