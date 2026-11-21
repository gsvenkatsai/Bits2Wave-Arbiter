"""Bounded compromise search for the MODIFY rung (NFR-3: <= 6 twin evals).

The spec's ``bisect_compromise`` calls the twin twice per iteration for six
iterations, i.e. twelve evaluations, which violates the six-evaluation budget,
and it claims the same final resolution as a six-evaluation search. That claim
is wrong; this module implements a golden-section search with one new
evaluation per narrowing step.

Interval arithmetic (documented)
--------------------------------
With ``r = (sqrt(5) - 1) / 2 ~= 0.618`` every narrowing step keeps the bracket
that still contains the maximum and removes ``(1 - r) ~= 38.2%`` of it, so
``width(n) = r**n * width(0)``. The mandated probe of the requested endpoint
plus two seeding evaluations and three narrowing steps uses the full six
evaluations and leaves ``r**3 ~= 0.236`` of the original interval, with the
requested value measured exactly once. Twelve evaluations would have been
necessary for the spec's ~0.016 resolution.

Feasibility and timeouts
------------------------
``evaluate_fn`` returns ``(delta_utility, feasible)``. Infeasible points are
scored as ``-inf`` so they can guide narrowing but are never selected as best.
A point is only a candidate when it is feasible **and** improves on the
baseline. If nothing qualifies the solver returns ``None`` so the ladder falls
through to DELAY/REJECT. A raising ``TimeoutError`` aborts the search and
returns ``None`` with ``timed_out=True``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterator

from arbiter.engine.contracts import MAX_TWIN_EVALS

__all__ = [
    "GOLDEN_RATIO",
    "BisectionResult",
    "BoundedBisectionSolver",
    "BudgetExceeded",
    "EvaluationBudget",
    "EvaluateFn",
]

#: Golden-section ratio ``(sqrt(5) - 1) / 2``.
GOLDEN_RATIO: float = (math.sqrt(5.0) - 1.0) / 2.0

#: ``x -> (delta_utility, feasible)``; may raise ``TimeoutError``.
EvaluateFn = Callable[[float], "tuple[float, bool]"]


class BudgetExceeded(RuntimeError):
    """Raised when a search asks for more twin evaluations than allowed."""


class EvaluationBudget:
    """Hard counter for twin evaluations; raises instead of over-spending."""

    def __init__(self, limit: int = MAX_TWIN_EVALS) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = int(limit)
        self.used = 0

    @property
    def remaining(self) -> int:
        """Evaluations still available."""
        return self.limit - self.used

    def spend(self) -> None:
        """Consume one evaluation, raising before the cap is exceeded."""
        if self.used >= self.limit:
            raise BudgetExceeded(
                f"twin evaluation budget of {self.limit} exhausted"
            )
        self.used += 1


@dataclass(frozen=True)
class BisectionResult:
    """Outcome of one bounded search.

    Unpacks as the documented ``(best_x, best_delta_utility, evaluations_used)``
    tuple while still exposing the timeout flag.
    """

    best_x: float | None
    best_delta_utility: float
    evaluations_used: int
    timed_out: bool = False

    @property
    def found(self) -> bool:
        """True when a feasible improving point was found."""
        return self.best_x is not None

    def __iter__(self) -> Iterator[float | int | None]:
        yield self.best_x
        yield self.best_delta_utility
        yield self.evaluations_used


class BoundedBisectionSolver:
    """Golden-section maximisation of ``delta_utility`` under a hard eval cap."""

    def __init__(
        self,
        max_evaluations: int = MAX_TWIN_EVALS,
        tolerance: float = 1e-3,
    ) -> None:
        if max_evaluations < 1:
            raise ValueError("max_evaluations must be >= 1")
        self.max_evaluations = int(max_evaluations)
        self.tolerance = float(tolerance)

    def solve(
        self,
        lower: float,
        upper: float,
        evaluate_fn: EvaluateFn,
        budget: int | None = None,
    ) -> BisectionResult:
        """Search ``[lower, upper]`` for the best feasible improving point.

        ``upper`` is the requested (target) value and is probed first; ``lower``
        is the current value the search brackets from.
        """
        limit = self.max_evaluations if budget is None else int(budget)
        if limit < 1:
            raise ValueError("budget must be >= 1")

        tracker = EvaluationBudget(limit)
        requested = float(upper)
        a = min(float(lower), requested)
        b = max(float(lower), requested)

        best_x: float | None = None
        best_du = 0.0

        def probe(x: float) -> float:
            nonlocal best_x, best_du
            tracker.spend()
            delta, feasible = evaluate_fn(x)
            value = float(delta)
            if feasible and value > 0.0 and (best_x is None or value > best_du):
                best_x, best_du = x, value
            return value if feasible else float("-inf")

        # 1. Probe the requested value before searching anything.
        try:
            probe(requested)
        except TimeoutError:
            return BisectionResult(None, 0.0, tracker.used, timed_out=True)

        if best_x == requested:
            # Already feasible and improving: nothing to search for.
            return BisectionResult(best_x, best_du, tracker.used)
        if tracker.remaining == 0 or (b - a) <= self.tolerance:
            return BisectionResult(best_x, best_du, tracker.used)

        # 2. Seed the golden-section bracket (two interior probes).
        x1 = b - GOLDEN_RATIO * (b - a)
        x2 = a + GOLDEN_RATIO * (b - a)
        try:
            f1 = probe(x1)
            f2 = probe(x2) if tracker.remaining > 0 else f1
        except TimeoutError:
            return BisectionResult(None, 0.0, tracker.used, timed_out=True)

        # 3. Narrow: one new evaluation per step, removing (1 - r) each time.
        while tracker.remaining > 0 and (b - a) > self.tolerance:
            try:
                if f1 < f2:
                    a = x1
                    x1, f1 = x2, f2
                    x2 = a + GOLDEN_RATIO * (b - a)
                    f2 = probe(x2)
                else:
                    b = x2
                    x2, f2 = x1, f1
                    x1 = b - GOLDEN_RATIO * (b - a)
                    f1 = probe(x1)
            except TimeoutError:
                return BisectionResult(None, 0.0, tracker.used, timed_out=True)

        return BisectionResult(best_x, best_du, tracker.used)
