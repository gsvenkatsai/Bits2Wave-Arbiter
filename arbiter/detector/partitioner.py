"""FR-4 disconnected-component partitioning and parallel solving.

Connected components of the conflict graph are independent, so each can be
solved on its own thread. A solver exception is contained to its component and
converted into a REJECT/TWIN_TIMEOUT fallback, so one bad evaluation cannot
crash the whole tick batch.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Sequence

import networkx as nx

from arbiter.engine.contracts import (
    ActionType,
    ArbitrationDecision,
    CommandProposal,
    ReasonCode,
)

__all__ = ["SolverFn", "GraphPartitioner", "batch_order"]

#: A solver maps one connected component to that component's decisions.
SolverFn = Callable[[List[CommandProposal]], Sequence[ArbitrationDecision]]


def batch_order(command: CommandProposal) -> tuple[int, str]:
    """Deterministic ordering: creation tick, then command id."""
    return (command.created_tick, command.command_id)


def _default_id() -> str:
    return uuid.uuid4().hex


def _now_ms() -> int:
    return int(time.time() * 1000)


class GraphPartitioner:
    """Splits a conflict graph into components and solves them in parallel."""

    def __init__(
        self,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._id_factory: Callable[[], str] = id_factory or _default_id
        self._clock: Callable[[], int] = clock or _now_ms

    # -- partitioning ------------------------------------------------------
    def components(self, graph: nx.Graph[Any, Any]) -> List[List[CommandProposal]]:
        """Deterministically ordered connected components of commands."""
        components: List[List[CommandProposal]] = []
        for nodes in nx.connected_components(graph):
            commands = [graph.nodes[node]["command"] for node in nodes]
            commands.sort(key=batch_order)
            components.append(commands)
        components.sort(key=lambda component: batch_order(component[0]))
        return components

    # -- solving -----------------------------------------------------------
    def solve_parallel(
        self,
        components: Sequence[Sequence[CommandProposal]],
        solver_fn: SolverFn,
        max_workers: int = 4,
        *,
        tick: int = 0,
        on_error: Callable[[int, BaseException], None] | None = None,
    ) -> List[List[ArbitrationDecision]]:
        """Solve each component, preserving input order.

        A raising solver yields REJECT/TWIN_TIMEOUT decisions for that
        component only; other components and the batch continue normally.
        """
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if not components:
            return []

        def solve(index: int, component: Sequence[CommandProposal]) -> List[ArbitrationDecision]:
            try:
                return list(solver_fn(list(component)))
            except Exception as error:  # noqa: BLE001 - component isolation
                if on_error is not None:
                    on_error(index, error)
                return self._fallback(list(component), tick)

        workers = min(max_workers, len(components))
        if workers == 1:
            return [solve(index, component) for index, component in enumerate(components)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(solve, index, component)
                for index, component in enumerate(components)
            ]
            return [future.result() for future in futures]

    def solve_serial(
        self,
        components: Sequence[Sequence[CommandProposal]],
        solver_fn: SolverFn,
        *,
        tick: int = 0,
        on_error: Callable[[int, BaseException], None] | None = None,
    ) -> List[List[ArbitrationDecision]]:
        """Serial reference implementation (used by tests and debugging)."""
        return self.solve_parallel(
            components, solver_fn, 1, tick=tick, on_error=on_error
        )

    def _fallback(
        self,
        component: Sequence[CommandProposal],
        tick: int,
    ) -> List[ArbitrationDecision]:
        return [
            ArbitrationDecision(
                decision_id=self._id_factory(),
                command_id=command.command_id,
                batch_tick=int(tick),
                action=ActionType.REJECT,
                approved_value=None,
                delta_utility=0.0,
                reason_code=ReasonCode.TWIN_TIMEOUT,
                hold_count=command.hold_count,
                twin_eval_count=0,
                decided_at_epoch_ms=self._clock(),
            )
            for command in component
        ]
