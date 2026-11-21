"""FR-4 pairwise conflict classifier.

Rules live in an explicit table (:data:`INDIRECT_RULES`) with one small matcher
per rule kind, so adding a harmful combination is a data change rather than a
new branch deep inside a comparison function.

DIRECT
    Same target cell, same parameter, and *opposing* directions relative to
    each command's ``current_value`` (one raises, the other lowers). Identical
    or same-direction requests are not conflicts.

INDIRECT
    Different cells within the hop radius (default 2, SRS Q17) whose actions
    form a harmful combination from the rule table.

IMPLICIT_PINGPONG is intentionally **not** produced here: it needs history and
comes from :class:`arbiter.detector.cycle_detector.PingPongCycleDetector`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, FrozenSet, Iterable, Mapping, Sequence

import networkx as nx

from arbiter.engine.contracts import CommandProposal, ConflictType

__all__ = [
    "AdjacencyLike",
    "ConflictClassifier",
    "IndirectRule",
    "RuleKind",
    "INDIRECT_RULES",
    "DEFAULT_HOP_RADIUS",
    "POWER_PARAMETERS",
    "TILT_PARAMETERS",
    "SLEEP_PARAMETERS",
    "OFFLOAD_PARAMETERS",
    "direction",
    "normalize_parameter",
]

#: Default neighbourhood radius for INDIRECT clashes (SRS Q17).
DEFAULT_HOP_RADIUS = 2

#: Values closer than this are treated as "no change".
VALUE_EPSILON = 1e-9

#: Cell adjacency is either a plain mapping or a networkx graph. The runtime
#: alias stays unsubscripted because networkx 3.x Graph is not subscriptable.
if TYPE_CHECKING:
    AdjacencyLike = Mapping[str, Sequence[str]] | nx.Graph[Any, Any]
else:  # pragma: no cover - typing-only difference
    AdjacencyLike = Mapping[str, Sequence[str]] | nx.Graph

# -- parameter vocabulary ---------------------------------------------------
POWER_PARAMETERS: FrozenSet[str] = frozenset({"TX_POWER_DBM", "TX_POWER", "TX_POWER_DB"})
TILT_PARAMETERS: FrozenSet[str] = frozenset({"TILT_DEG", "TILT", "ELECTRICAL_TILT_DEG"})
SLEEP_PARAMETERS: FrozenSet[str] = frozenset({"SLEEP_STATE", "CELL_STATE", "CELL_ON_OFF"})
OFFLOAD_PARAMETERS: FrozenSet[str] = frozenset(
    {"OFFLOAD_RATIO", "HANDOVER_SHARE", "LOAD_SHARE"}
)


def normalize_parameter(parameter: object) -> str:
    """Canonical upper-case parameter name used by the rule table."""
    value = getattr(parameter, "value", parameter)
    return str(value).strip().upper()


def direction(cmd: CommandProposal, epsilon: float = VALUE_EPSILON) -> int:
    """Return +1 for an increase, -1 for a decrease and 0 for no change."""
    delta = cmd.requested_value - cmd.current_value
    if delta > epsilon:
        return 1
    if delta < -epsilon:
        return -1
    return 0


def _is_sleep_on(cmd: CommandProposal) -> bool:
    return normalize_parameter(cmd.parameter) in SLEEP_PARAMETERS and direction(cmd) > 0


def _is_offload_increase(cmd: CommandProposal) -> bool:
    return (
        normalize_parameter(cmd.parameter) in OFFLOAD_PARAMETERS and direction(cmd) > 0
    )


def _is_power_up(cmd: CommandProposal) -> bool:
    parameter = normalize_parameter(cmd.parameter)
    return parameter in POWER_PARAMETERS or parameter in TILT_PARAMETERS


class RuleKind(str, Enum):
    """Which matcher implements an INDIRECT rule."""

    SLEEP_VS_INBOUND_OFFLOAD = "SLEEP_VS_INBOUND_OFFLOAD"
    POWER_UP_VS_SENSITIVE_NEIGHBOUR = "POWER_UP_VS_SENSITIVE_NEIGHBOUR"


@dataclass(frozen=True)
class IndirectRule:
    """One documented harmful combination between neighbouring cells."""

    name: str
    kind: RuleKind
    description: str


#: The complete INDIRECT rule table (add rules here, not in if-chains).
INDIRECT_RULES: tuple[IndirectRule, ...] = (
    IndirectRule(
        name="sleep_vs_inbound_offload",
        kind=RuleKind.SLEEP_VS_INBOUND_OFFLOAD,
        description=(
            "A cell is put to sleep while an offload/handover share rises toward "
            "it: the sleep saves energy but the rising share would push users "
            "into a cell that is being switched off."
        ),
    ),
    IndirectRule(
        name="power_up_vs_sensitive_neighbour",
        kind=RuleKind.POWER_UP_VS_SENSITIVE_NEIGHBOUR,
        description=(
            "Tx-power or tilt increase on one cell while a neighbour within the "
            "hop radius is interference-sensitive: the stronger signal raises "
            "interference in the neighbour."
        ),
    ),
)


# -- adjacency index --------------------------------------------------------
class _AdjacencyIndex:
    """Undirected cell graph with cached shortest-path lookups."""

    def __init__(self, graph: nx.Graph[Any, Any]) -> None:
        self._graph = graph

    @classmethod
    def from_adjacency(cls, adjacency: AdjacencyLike) -> "_AdjacencyIndex":
        if isinstance(adjacency, (nx.Graph, nx.DiGraph)):
            return cls(nx.Graph(adjacency))
        graph: nx.Graph[Any, Any] = nx.Graph()
        for cell, neighbours in adjacency.items():
            graph.add_node(cell)
            for neighbour in neighbours:
                graph.add_edge(cell, neighbour)
        return cls(graph)

    @property
    def graph(self) -> nx.Graph[Any, Any]:
        """The undirected cell graph."""
        return self._graph

    def distance(self, cell_a: str, cell_b: str) -> int | None:
        if cell_a == cell_b:
            return 0
        if cell_a not in self._graph or cell_b not in self._graph:
            return None
        try:
            return int(nx.shortest_path_length(self._graph, cell_a, cell_b))
        except nx.NetworkXNoPath:
            return None

    def within(self, cell_a: str, cell_b: str, radius: int) -> bool:
        distance = self.distance(cell_a, cell_b)
        return distance is not None and 1 <= distance <= radius

    def cells_within(self, cell: str, radius: int) -> frozenset[str]:
        if cell not in self._graph:
            return frozenset({cell}) if radius >= 0 else frozenset()
        lengths = nx.single_source_shortest_path_length(self._graph, cell, cutoff=radius)
        return frozenset(str(node) for node in lengths)


def _fingerprint(adjacency: AdjacencyLike) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
    nodes: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    if isinstance(adjacency, (nx.Graph, nx.DiGraph)):
        edges = [(str(u), str(v)) for u, v in adjacency.edges()]
        nodes.update(str(node) for node in adjacency.nodes())
    else:
        edges = []
        for cell, neighbours in adjacency.items():
            nodes.add(str(cell))
            edges.extend((str(cell), str(neighbour)) for neighbour in neighbours)
    for left, right in edges:
        pairs.add((left, right) if left <= right else (right, left))
    return frozenset(pairs), frozenset(nodes)


# -- rule matchers ----------------------------------------------------------
Matcher = Callable[["ConflictClassifier", CommandProposal, CommandProposal, _AdjacencyIndex], bool]


def _match_sleep_vs_inbound_offload(
    classifier: "ConflictClassifier",
    sleeper: CommandProposal,
    offloader: CommandProposal,
    index: _AdjacencyIndex,
) -> bool:
    if not _is_sleep_on(sleeper) or not _is_offload_increase(offloader):
        return False
    if offloader.handover_to is not None:
        return offloader.handover_to == sleeper.target_cell
    # No explicit destination: rising offload on a neighbouring cell is assumed
    # to spill toward the sleeping cell.
    return index.within(sleeper.target_cell, offloader.target_cell, classifier.hop_radius)


def _match_power_up_vs_sensitive_neighbour(
    classifier: "ConflictClassifier",
    aggressor: CommandProposal,
    victim: CommandProposal,
    index: _AdjacencyIndex,
) -> bool:
    if not _is_power_up(aggressor):
        return False
    if aggressor.target_cell == victim.target_cell:
        return False
    if not index.within(aggressor.target_cell, victim.target_cell, classifier.hop_radius):
        return False
    if not classifier.is_interference_sensitive(victim.target_cell):
        return False
    return direction(aggressor) > 0


_MATCHERS: dict[RuleKind, Matcher] = {
    RuleKind.SLEEP_VS_INBOUND_OFFLOAD: _match_sleep_vs_inbound_offload,
    RuleKind.POWER_UP_VS_SENSITIVE_NEIGHBOUR: _match_power_up_vs_sensitive_neighbour,
}


class ConflictClassifier:
    """Classifies a command pair as DIRECT, INDIRECT or not conflicting."""

    def __init__(
        self,
        hop_radius: int = DEFAULT_HOP_RADIUS,
        interference_sensitive_cells: Iterable[str] | None = None,
        cache_size: int = 8,
    ) -> None:
        if hop_radius < 1:
            raise ValueError("hop_radius must be >= 1")
        self.hop_radius = hop_radius
        #: ``None`` means every cell is treated as interference-sensitive, the
        #: conservative default; pass an explicit set to narrow the rule.
        self.interference_sensitive_cells: FrozenSet[str] | None = (
            None
            if interference_sensitive_cells is None
            else frozenset(interference_sensitive_cells)
        )
        self._cache_size = max(cache_size, 1)
        self._index_cache: dict[
            tuple[frozenset[tuple[str, str]], frozenset[str]], _AdjacencyIndex
        ] = {}

    # -- adjacency helpers -------------------------------------------------
    def index_for(self, adjacency: AdjacencyLike) -> _AdjacencyIndex:
        """Return a cached index for this adjacency structure."""
        key = _fingerprint(adjacency)
        cached = self._index_cache.get(key)
        if cached is None:
            if len(self._index_cache) >= self._cache_size:
                self._index_cache.clear()
            cached = _AdjacencyIndex.from_adjacency(adjacency)
            self._index_cache[key] = cached
        return cached

    def hop_distance(self, adjacency: AdjacencyLike, cell_a: str, cell_b: str) -> int | None:
        """Shortest path length between two cells (``None`` if disconnected)."""
        return self.index_for(adjacency).distance(cell_a, cell_b)

    def cells_within(
        self,
        cell: str,
        adjacency: AdjacencyLike,
        radius: int | None = None,
    ) -> frozenset[str]:
        """All cells inside ``radius`` hops of ``cell``, including itself."""
        effective = self.hop_radius if radius is None else radius
        return self.index_for(adjacency).cells_within(cell, effective)

    def is_interference_sensitive(self, cell: str) -> bool:
        """Whether the power-up rule treats ``cell`` as interference-sensitive."""
        if self.interference_sensitive_cells is None:
            return True
        return cell in self.interference_sensitive_cells

    # -- classification ----------------------------------------------------
    def classify(
        self,
        a: CommandProposal,
        b: CommandProposal,
        adjacency: AdjacencyLike,
    ) -> ConflictType | None:
        """Classify a command pair; ``None`` when they do not conflict."""
        return self.explain(a, b, adjacency)[0]

    def explain(
        self,
        a: CommandProposal,
        b: CommandProposal,
        adjacency: AdjacencyLike,
    ) -> tuple[ConflictType | None, str | None]:
        """Classify a pair and return the matched rule name (if any)."""
        if a.command_id == b.command_id:
            return None, None

        if a.target_cell == b.target_cell:
            if normalize_parameter(a.parameter) != normalize_parameter(b.parameter):
                return None, None
            direction_a, direction_b = direction(a), direction(b)
            if direction_a * direction_b < 0:
                return ConflictType.DIRECT, "opposing_values"
            return None, None

        index = self.index_for(adjacency)
        if not index.within(a.target_cell, b.target_cell, self.hop_radius):
            return None, None
        for rule in INDIRECT_RULES:
            matcher = _MATCHERS[rule.kind]
            if matcher(self, a, b, index) or matcher(self, b, a, index):
                return ConflictType.INDIRECT, rule.name
        return None, None
