"""Conflict graph construction: DIRECT, INDIRECT and IMPLICIT_PINGPONG clashes.

Nodes are command ids, edges are clashes, and connected components are solved
independently by the engine (in parallel).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from arbiter.engine.constants import PINGPONG_WINDOW_TICKS
from arbiter.engine.contracts import CommandProposal, ConflictType

__all__ = [
    "ConflictEdge",
    "ConflictGraph",
    "ConflictDetector",
    "PingPongTracker",
    "MAX_INDIRECT_HOPS",
]

#: Indirect clashes propagate through the neighbour graph up to two hops.
MAX_INDIRECT_HOPS = 2

#: Values closer than this are treated as the same request (not "opposing").
VALUE_EPSILON = 1e-9


def parameter_key(parameter: object) -> str:
    """Normalise an enum or string parameter to its canonical string value."""
    value = getattr(parameter, "value", parameter)
    return str(value)


@dataclass(frozen=True)
class ConflictEdge:
    """A symmetric clash between two commands."""

    command_a: str
    command_b: str
    conflict_type: ConflictType

    def involves(self, command_id: str) -> bool:
        """True when ``command_id`` is one of the edge endpoints."""
        return command_id in (self.command_a, self.command_b)

    def other(self, command_id: str) -> str:
        """Return the opposite endpoint of this edge."""
        return self.command_b if command_id == self.command_a else self.command_a


class ConflictGraph:
    """Graph of commands (nodes) and clashes (edges)."""

    def __init__(
        self,
        command_ids: Iterable[str],
        edges: Sequence[ConflictEdge] = (),
        pingpong_command_ids: Iterable[str] = (),
    ) -> None:
        self._nodes: tuple[str, ...] = tuple(dict.fromkeys(command_ids))
        self._edges: tuple[ConflictEdge, ...] = tuple(edges)
        self.pingpong_command_ids: frozenset[str] = frozenset(pingpong_command_ids)

    @property
    def nodes(self) -> tuple[str, ...]:
        """All command ids, de-duplicated and in insertion order."""
        return self._nodes

    @property
    def edges(self) -> tuple[ConflictEdge, ...]:
        """All clash edges."""
        return self._edges

    def neighbours(self, command_id: str) -> tuple[str, ...]:
        """Commands directly connected to ``command_id``."""
        adjacent = {
            edge.other(command_id) for edge in self._edges if edge.involves(command_id)
        }
        return tuple(sorted(adjacent))

    def conflict_between(self, command_a: str, command_b: str) -> ConflictType | None:
        """Conflict type of the edge joining two commands, if any."""
        for edge in self._edges:
            if edge.command_a == command_a and edge.command_b == command_b:
                return edge.conflict_type
            if edge.command_a == command_b and edge.command_b == command_a:
                return edge.conflict_type
        return None

    def components(self) -> tuple[tuple[str, ...], ...]:
        """Connected components, deterministic and sorted, singletons included."""
        parent: dict[str, str] = {node: node for node in self._nodes}

        def find(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(left: str, right: str) -> None:
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[root_right] = root_left

        for edge in self._edges:
            if edge.command_a in parent and edge.command_b in parent:
                union(edge.command_a, edge.command_b)

        grouped: dict[str, list[str]] = {}
        for node in self._nodes:
            grouped.setdefault(find(node), []).append(node)

        return tuple(sorted((tuple(sorted(group)) for group in grouped.values())))


class PingPongTracker:
    """Sliding-window detector for A->B then B->A handover reversals."""

    def __init__(self, window_ticks: int = PINGPONG_WINDOW_TICKS) -> None:
        if window_ticks < 1:
            raise ValueError("window_ticks must be >= 1")
        self.window_ticks = window_ticks
        self._history: dict[tuple[str, str], list[tuple[int, str]]] = {}

    def record(
        self,
        tick: int,
        controller_id: str,
        parameter: object,
        target_cell_id: str,
    ) -> None:
        """Remember an enforced move so a reversal can be spotted later."""
        key = (controller_id, parameter_key(parameter))
        self._history.setdefault(key, []).append((tick, target_cell_id))

    def is_pingpong(
        self,
        tick: int,
        controller_id: str,
        parameter: object,
        target_cell_id: str,
    ) -> bool:
        """True when this proposal returns to a cell the controller just left.

        The pattern is: the last two recorded moves for this controller and
        parameter targeted different cells ``A`` then ``B``, both inside the
        window, and this proposal targets ``A`` again (A->B->A).
        """
        key = (controller_id, parameter_key(parameter))
        history = self._history.get(key, [])
        if len(history) < 2:
            return False
        previous_tick, previous_cell = history[-1]
        earlier_tick, earlier_cell = history[-2]
        if tick - previous_tick > self.window_ticks:
            return False
        if tick - earlier_tick > self.window_ticks:
            return False
        if previous_cell == earlier_cell:
            return False
        if previous_cell == target_cell_id:
            return False
        return earlier_cell == target_cell_id

    def reset(self) -> None:
        """Drop all recorded history."""
        self._history.clear()


class ConflictDetector:
    """Builds a :class:`ConflictGraph` for one tick batch."""

    def __init__(
        self,
        cell_neighbors: Mapping[str, Sequence[str]] | None = None,
        pingpong_window_ticks: int = PINGPONG_WINDOW_TICKS,
    ) -> None:
        self._neighbors: dict[str, set[str]] = {}
        for cell, neighbors in (cell_neighbors or {}).items():
            self._neighbors.setdefault(cell, set()).update(neighbors)
            for neighbor in neighbors:
                self._neighbors.setdefault(neighbor, set()).add(cell)
        self._hops_cache: dict[tuple[str, str], int | None] = {}
        self.pingpong = PingPongTracker(pingpong_window_ticks)

    # -- topology ----------------------------------------------------------
    def hops(self, cell_a: str, cell_b: str) -> int | None:
        """Graph distance between cells, capped at :data:`MAX_INDIRECT_HOPS`."""
        if cell_a == cell_b:
            return 0
        key = (cell_a, cell_b) if cell_a < cell_b else (cell_b, cell_a)
        if key in self._hops_cache:
            return self._hops_cache[key]
        distance = self._bfs(cell_a, cell_b)
        self._hops_cache[key] = distance
        return distance

    def _bfs(self, start: str, goal: str) -> int | None:
        frontier = [start]
        visited = {start}
        for depth in range(1, MAX_INDIRECT_HOPS + 1):
            next_frontier: list[str] = []
            for cell in frontier:
                for neighbor in self._neighbors.get(cell, ()):
                    if neighbor == goal:
                        return depth
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
        return None

    # -- ping-pong bookkeeping --------------------------------------------
    def record_decision(
        self,
        tick: int,
        controller_id: str,
        parameter: object,
        target_cell_id: str,
    ) -> None:
        """Record an applied move for later reversal detection."""
        self.pingpong.record(tick, controller_id, parameter, target_cell_id)

    def reset_history(self) -> None:
        """Clear ping-pong history (e.g. between simulation scenarios)."""
        self.pingpong.reset()

    # -- detection ---------------------------------------------------------
    def detect(self, proposals: Sequence[CommandProposal], tick: int) -> ConflictGraph:
        """Build the conflict graph for this batch."""
        proposals = list(proposals)
        pingpong_ids = {
            proposal.command_id
            for proposal in proposals
            if self.pingpong.is_pingpong(
                tick,
                proposal.controller_id,
                proposal.parameter,
                proposal.target_cell_id,
            )
        }

        edges: list[ConflictEdge] = []
        for index, left in enumerate(proposals):
            for right in proposals[index + 1:]:
                if left.command_id in pingpong_ids or right.command_id in pingpong_ids:
                    continue
                pair = (left.command_id, right.command_id)
                edge = self._classify(left, right, pair)
                if edge is not None:
                    edges.append(edge)

        return ConflictGraph(
            (proposal.command_id for proposal in proposals),
            tuple(edges),
            pingpong_ids,
        )

    def _classify(
        self,
        left: CommandProposal,
        right: CommandProposal,
        pair: tuple[str, str],
    ) -> ConflictEdge | None:
        same_parameter = parameter_key(left.parameter) == parameter_key(right.parameter)
        if left.target_cell_id == right.target_cell_id and same_parameter:
            opposing = abs(left.requested_value - right.requested_value) > VALUE_EPSILON
            if opposing:
                return ConflictEdge(pair[0], pair[1], ConflictType.DIRECT)
            return None

        if left.target_cell_id == right.target_cell_id:
            return None

        distance = self.hops(left.target_cell_id, right.target_cell_id)
        if distance is not None and 1 <= distance <= MAX_INDIRECT_HOPS:
            return ConflictEdge(pair[0], pair[1], ConflictType.INDIRECT)
        return None
