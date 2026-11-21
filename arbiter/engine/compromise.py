"""Bounded compromise search (MODIFY rung) with a hard twin-evaluation budget.

The spec's ``bisect_compromise`` calls the twin twice per iteration for six
iterations (12 evaluations) which violates NFR-3.  This module implements a
single-evaluation-per-step bracket over at most six evaluations in total; the
counter raises if the limit is ever exceeded and only feasible points can win.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from arbiter.engine.constants import BISECTION_MAX_EVALS
from arbiter.engine.contracts import CommandProposal, UtilityWeights
from arbiter.engine.deltas import CandidateDelta, build_delta
from arbiter.engine.mocks.mock_twin_client import TwinClient
from arbiter.engine.utility import floors_satisfied, utility_delta

__all__ = [
    "TwinBudgetExceeded",
    "TwinBudget",
    "CompromiseResult",
    "CompromiseSearch",
    "SEARCH_LADDER",
]

#: Deterministic bracket positions (fraction of the way from the current value
#: toward the requested value).  At most ``BISECTION_MAX_EVALS`` are ever used.
SEARCH_LADDER: tuple[float, ...] = (0.5, 0.25, 0.75, 0.125, 0.875, 0.625)


class TwinBudgetExceeded(RuntimeError):
    """Raised when a component asks the twin for more than its allowed calls."""


class TwinBudget:
    """Hard per-component cap on twin evaluations (NFR-3)."""

    def __init__(self, twin: TwinClient, max_evaluations: int = BISECTION_MAX_EVALS) -> None:
        if max_evaluations < 1:
            raise ValueError("max_evaluations must be >= 1")
        self._twin = twin
        self.max_evaluations = max_evaluations
        self.evaluations = 0

    @property
    def remaining(self) -> int:
        """Evaluations still available in this component's budget."""
        return self.max_evaluations - self.evaluations

    def can_evaluate(self, count: int = 1) -> bool:
        """True when ``count`` more evaluations fit inside the budget."""
        return count <= self.remaining

    def evaluate(
        self,
        candidate_deltas: list[CandidateDelta],
        timeout_budget_ms: float = 15.0,
    ) -> dict[str, Any]:
        """Spend one evaluation, raising before exceeding the cap."""
        if not self.can_evaluate():
            raise TwinBudgetExceeded(
                f"twin evaluation budget of {self.max_evaluations} exhausted"
            )
        self.evaluations += 1
        return self._twin.evaluate(candidate_deltas, timeout_budget_ms)


@dataclass(frozen=True)
class CompromiseResult:
    """Outcome of one bounded compromise search."""

    found: bool
    value: float | None
    delta_utility: float
    evaluations: int
    kpis: Mapping[str, float] | None = None


class CompromiseSearch:
    """Finds the best feasible partial move for one command.

    A candidate is feasible only when the twin reports both safety floors, the
    joint utility strictly improves, and the improvement clears the confidence
    margin.  Infeasible candidates can never be selected.
    """

    def __init__(
        self,
        budget: TwinBudget,
        weights: UtilityWeights | None = None,
        confidence_margin: float = 0.0,
        timeout_budget_ms: float = 15.0,
        ladder: Sequence[float] = SEARCH_LADDER,
    ) -> None:
        self._budget = budget
        self._weights = weights
        self._margin = confidence_margin
        self._timeout_budget_ms = timeout_budget_ms
        self._ladder = tuple(ladder)

    def search(
        self,
        command: CommandProposal,
        peer_deltas: Sequence[CandidateDelta],
        baseline_kpis: Mapping[str, float],
        current_value: float | None,
    ) -> CompromiseResult:
        """Bracket between the current and requested values within the budget."""
        start_evaluations = self._budget.evaluations
        if current_value is None or current_value == command.requested_value:
            return CompromiseResult(
                found=False,
                value=None,
                delta_utility=0.0,
                evaluations=0,
            )

        best: CompromiseResult | None = None
        for fraction in self._ladder:
            if not self._budget.can_evaluate():
                break
            value = current_value + fraction * (command.requested_value - current_value)
            deltas = list(peer_deltas)
            deltas.append(build_delta(command, value, current_value))
            response = self._budget.evaluate(deltas, self._timeout_budget_ms)
            kpis = _as_kpis(response.get("predicted_kpis"))
            delta = utility_delta(kpis, baseline_kpis, self._weights)
            feasible = (
                bool(response.get("safety_floors_satisfied", False))
                and floors_satisfied(kpis)
                and delta > 0.0
                and abs(delta) >= self._margin
            )
            if not feasible:
                continue
            if best is None or delta > best.delta_utility:
                best = CompromiseResult(
                    found=True,
                    value=value,
                    delta_utility=delta,
                    evaluations=self._budget.evaluations - start_evaluations,
                    kpis=kpis,
                )

        if best is None:
            return CompromiseResult(
                found=False,
                value=None,
                delta_utility=0.0,
                evaluations=self._budget.evaluations - start_evaluations,
            )
        return best


def _as_kpis(value: object) -> Mapping[str, float]:
    """Coerce a twin KPI mapping, tolerating ``None``/malformed payloads."""
    if isinstance(value, Mapping):
        return {str(key): float(item) for key, item in value.items()}
    return {}
