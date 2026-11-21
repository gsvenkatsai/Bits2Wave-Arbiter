"""Bounded golden-section solver tests: optimum, feasibility, budget, timeout."""
from __future__ import annotations

import random
from typing import Callable, List, Tuple

import pytest

from arbiter.engine.bisection import (
    BisectionResult,
    BoundedBisectionSolver,
    BudgetExceeded,
    EvaluationBudget,
)

EvaluateFn = Callable[[float], Tuple[float, bool]]


def _unimodal(peak: float = 4.0, height: float = 10.0) -> EvaluateFn:
    def evaluate(x: float) -> Tuple[float, bool]:
        return (height - (x - peak) ** 2, True)

    return evaluate


def test_unimodal_curve_finds_the_optimum() -> None:
    solver = BoundedBisectionSolver()

    result = solver.solve(0.0, 10.0, _unimodal())

    assert result.found is True
    assert result.best_x is not None
    assert abs(result.best_x - 4.0) <= 0.5
    assert result.best_delta_utility >= 9.9
    assert result.evaluations_used <= 6
    assert result.timed_out is False


def test_requested_endpoint_is_probed_first() -> None:
    # The requested value is the optimum, so one evaluation suffices.
    calls: List[float] = []

    def evaluate(x: float) -> Tuple[float, bool]:
        calls.append(x)
        return (10.0 - (x - 10.0) ** 2, True)

    result = BoundedBisectionSolver().solve(0.0, 10.0, evaluate)

    assert calls == [10.0]
    assert result.best_x == pytest.approx(10.0)
    assert result.evaluations_used == 1


def test_infeasible_region_is_excluded() -> None:
    # Peak at 8 is inside a floor-infeasible region; only x <= 7 is allowed.
    def evaluate(x: float) -> Tuple[float, bool]:
        return (10.0 - (x - 8.0) ** 2, x <= 7.0)

    result = BoundedBisectionSolver().solve(0.0, 10.0, evaluate)

    assert result.found is True
    assert result.best_x is not None
    assert result.best_x <= 7.0
    assert result.best_delta_utility > 0.0


def test_all_infeasible_returns_none() -> None:
    calls = {"count": 0}

    def evaluate(x: float) -> Tuple[float, bool]:
        calls["count"] += 1
        return (10.0, False)

    result = BoundedBisectionSolver().solve(0.0, 10.0, evaluate)

    assert result.best_x is None
    assert result.found is False
    assert result.best_delta_utility == pytest.approx(0.0)
    assert calls["count"] <= 6
    assert result.evaluations_used == calls["count"]


def test_non_improving_points_are_not_selected() -> None:
    def evaluate(x: float) -> Tuple[float, bool]:
        return (-abs(x - 5.0) - 1.0, True)

    result = BoundedBisectionSolver().solve(0.0, 10.0, evaluate)

    assert result.best_x is None
    assert result.best_delta_utility == pytest.approx(0.0)


def test_budget_is_never_exceeded_across_random_curves() -> None:
    rng = random.Random(20261121)
    solver = BoundedBisectionSolver()

    for _ in range(200):
        peak = rng.uniform(0.0, 10.0)
        scale = rng.uniform(0.05, 3.0)
        feasible_limit = rng.uniform(0.0, 10.0)
        counter = {"n": 0}

        def evaluate(
            x: float,
            peak: float = peak,
            scale: float = scale,
            feasible_limit: float = feasible_limit,
        ) -> Tuple[float, bool]:
            counter["n"] += 1
            assert counter["n"] <= 6, "budget exceeded"
            return (10.0 - scale * (x - peak) ** 2, x <= feasible_limit)

        result = solver.solve(0.0, 10.0, evaluate, budget=6)

        assert counter["n"] <= 6
        assert result.evaluations_used == counter["n"]
        assert result.found is False or (
            result.best_x is not None and result.best_x <= feasible_limit
        )


def test_timeout_aborts_and_flags() -> None:
    calls = {"count": 0}

    def evaluate(x: float) -> Tuple[float, bool]:
        calls["count"] += 1
        if calls["count"] == 2:
            raise TimeoutError("twin overran")
        # The requested endpoint is not improving, so the search continues.
        return (0.0, True)

    result = BoundedBisectionSolver().solve(0.0, 10.0, evaluate)

    assert result.timed_out is True
    assert result.best_x is None
    assert result.best_delta_utility == pytest.approx(0.0)
    assert result.evaluations_used == 2


def test_timeout_during_narrowing() -> None:
    calls = {"count": 0}

    def evaluate(x: float) -> Tuple[float, bool]:
        calls["count"] += 1
        if calls["count"] == 4:
            raise TimeoutError("twin died mid-search")
        return (0.0, True)

    result = BoundedBisectionSolver().solve(0.0, 10.0, evaluate)

    assert result.timed_out is True
    assert result.best_x is None
    assert result.evaluations_used == 4


def test_timeout_on_the_endpoint_probe() -> None:
    def evaluate(x: float) -> Tuple[float, bool]:
        raise TimeoutError("immediate")

    result = BoundedBisectionSolver().solve(0.0, 10.0, evaluate)

    assert result.timed_out is True
    assert result.evaluations_used == 1


def test_small_budgets_are_respected() -> None:
    solver = BoundedBisectionSolver()

    single = solver.solve(0.0, 10.0, _unimodal(), budget=1)
    assert single.evaluations_used == 1
    assert single.best_x is None  # requested endpoint was not improving

    three = solver.solve(0.0, 10.0, _unimodal(), budget=3)
    assert three.evaluations_used <= 3


def test_reversed_bounds_still_search() -> None:
    result = BoundedBisectionSolver().solve(10.0, 0.0, _unimodal())

    assert result.found is True
    assert result.best_x is not None
    assert abs(result.best_x - 4.0) <= 0.5


def test_equal_bounds_probe_once() -> None:
    calls: List[float] = []

    def evaluate(x: float) -> Tuple[float, bool]:
        calls.append(x)
        return (5.0, True)

    result = BoundedBisectionSolver().solve(3.0, 3.0, evaluate)

    assert calls == [3.0]
    assert result.best_x == pytest.approx(3.0)
    assert result.evaluations_used == 1


def test_result_unpacks_as_a_triple() -> None:
    result = BoundedBisectionSolver().solve(0.0, 10.0, _unimodal())
    best_x, best_delta, evaluations = result
    assert isinstance(result, BisectionResult)
    assert best_x == result.best_x
    assert best_delta == pytest.approx(result.best_delta_utility)
    assert evaluations == result.evaluations_used


def test_evaluation_budget_raises_when_exceeded() -> None:
    budget = EvaluationBudget(2)
    budget.spend()
    budget.spend()
    assert budget.remaining == 0
    with pytest.raises(BudgetExceeded):
        budget.spend()


def test_invalid_budgets() -> None:
    with pytest.raises(ValueError):
        EvaluationBudget(0)
    with pytest.raises(ValueError):
        BoundedBisectionSolver(max_evaluations=0)
    with pytest.raises(ValueError):
        BoundedBisectionSolver().solve(0.0, 1.0, _unimodal(), budget=0)
