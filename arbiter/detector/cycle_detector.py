"""FR-4 implicit ping-pong (cyclic handover) detector.

Keeps a sliding window of directed handover edges ``A -> B`` recorded over the
last ``window_ticks`` ticks (default ``PINGPONG_WINDOW = 5``). A new proposal is
cyclic when adding its edge would close a directed cycle among cells, i.e. a
path already exists from its destination back to its source. The reciprocal
``B -> A`` case is the two-cell ping-pong; longer cycles are caught too.

On a positive detection the engine must REJECT with ``CYCLIC_PINGPONG``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

import networkx as nx

from arbiter.engine.contracts import PINGPONG_WINDOW, CommandProposal

__all__ = ["HandoverEdge", "PingPongCycleDetector"]


@dataclass(frozen=True)
class HandoverEdge:
    """A directed handover recorded at a simulated tick."""

    source_cell: str
    destination_cell: str
    tick: int
    command_id: str = ""


class PingPongCycleDetector:
    """Sliding-window detector for reciprocal or longer handover cycles."""

    def __init__(self, window_ticks: int = PINGPONG_WINDOW) -> None:
        if window_ticks < 1:
            raise ValueError("window_ticks must be >= 1")
        self.window_ticks = window_ticks
        self._edges: List[HandoverEdge] = []

    # -- ingestion ---------------------------------------------------------
    def record(self, cmd: CommandProposal, tick: int) -> HandoverEdge | None:
        """Record ``cmd``'s handover edge; returns it (``None`` if not a handover)."""
        edge = self.edge_for(cmd, tick)
        if edge is None:
            return None
        self._edges.append(edge)
        self.prune(tick)
        return edge

    def edge_for(self, cmd: CommandProposal, tick: int) -> HandoverEdge | None:
        """Derive the directed handover edge described by a proposal."""
        destination = cmd.handover_to
        if destination is None:
            return None
        source = cmd.handover_from or cmd.target_cell
        if source == destination:
            return None
        return HandoverEdge(
            source_cell=source,
            destination_cell=destination,
            tick=int(tick),
            command_id=cmd.command_id,
        )

    # -- window ------------------------------------------------------------
    def prune(self, tick: int) -> None:
        """Drop edges that fell out of the sliding window."""
        self._edges = [
            edge
            for edge in self._edges
            if int(tick) - edge.tick <= self.window_ticks and edge.tick <= int(tick)
        ]

    def active_edges(self, tick: int) -> tuple[HandoverEdge, ...]:
        """Edges still inside the window at ``tick`` (inclusive boundary)."""
        return tuple(
            edge
            for edge in self._edges
            if int(tick) - edge.tick <= self.window_ticks and edge.tick <= int(tick)
        )

    @property
    def edges(self) -> tuple[HandoverEdge, ...]:
        """All edges currently retained by the detector."""
        return tuple(self._edges)

    def reset(self) -> None:
        """Forget all recorded handovers."""
        self._edges.clear()

    # -- detection ---------------------------------------------------------
    def is_cyclic(self, cmd: CommandProposal, tick: int) -> bool:
        """True when ``cmd``'s handover would close a cycle inside the window."""
        prospective = self.edge_for(cmd, tick)
        if prospective is None:
            return False
        self.prune(tick)
        window: nx.DiGraph[Any, Any] = nx.DiGraph()
        for edge in self.active_edges(tick):
            window.add_edge(edge.source_cell, edge.destination_cell)
        window.add_node(prospective.source_cell)
        window.add_node(prospective.destination_cell)
        return bool(nx.has_path(window, prospective.destination_cell, prospective.source_cell))
