"""Bounded compromise search tests: NFR-3 budget, feasibility and best point."""
from __future__ import annotations

from typing import Any

import pytest

from arbiter.engine.compromise import (
    CompromiseSearch,
    TwinBudget,
    TwinBudgetExceeded,
)
from arbiter.engine.mocks.mock_twin_client import MockTwinClient

from support import (
    BASE_KPIS,
    BREACH_KPIS,
    GOOD_KPIS,
    GREAT_KPIS,
    WORSE_KPIS,
    twin_response,
)


def test_budget_raises_when_exceeded() -> None:
    twin = MockTwinClient()
    budget = TwinBudget(twin, max_evaluations=6)
    assert budget.remaining == 6
    for _ in range(6):
        assert budget.can_evaluate() is True
        budget.evaluate([])
    assert budget.evaluations == 6
    assert budget.remaining == 0
    assert budget.can_evaluate() is False
    with pytest.raises(TwinBudgetExceeded):
        budget.evaluate([])
    assert twin.call_count == 6


def test_budget_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError):
        TwinBudget(MockTwinClient(), max_evaluations=0)


def test_search_returns_not_found_without_anchor(make_proposal: Any) -> None:
    budget = TwinBudget(MockTwinClient())
    search = CompromiseSearch(budget)
    result = search.search(make_proposal(), [], BASE_KPIS, current_value=None)
    assert result.found is False
    assert result.evaluations == 0
    assert budget.evaluations == 0


def test_search_returns_not_found_when_value_unchanged(make_proposal: Any) -> None:
    budget = TwinBudget(MockTwinClient())
    search = CompromiseSearch(budget)
    result = search.search(make_proposal(requested_value=40.0), [], BASE_KPIS, 40.0)
    assert result.found is False
    assert budget.evaluations == 0


def test_search_finds_first_feasible_bracket(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)])
    budget = TwinBudget(twin)
    search = CompromiseSearch(budget, ladder=(0.5,))

    result = search.search(make_proposal(requested_value=40.0), [], BASE_KPIS, 30.0)

    assert result.found is True
    assert result.value == pytest.approx(35.0)
    assert result.delta_utility == pytest.approx(0.0266, abs=1e-4)
    assert result.evaluations == 1
    assert budget.evaluations == 1


def test_infeasible_points_never_win(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[
            twin_response(WORSE_KPIS),
            twin_response(GOOD_KPIS),
            twin_response(BREACH_KPIS, floors=False),
        ]
    )
    budget = TwinBudget(twin)
    search = CompromiseSearch(budget, ladder=(0.5, 0.25, 0.75))

    result = search.search(make_proposal(requested_value=40.0), [], BASE_KPIS, 30.0)

    assert result.found is True
    assert result.value == pytest.approx(32.5)
    assert budget.evaluations == 3


def test_best_feasible_point_wins(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[twin_response(GOOD_KPIS), twin_response(GREAT_KPIS)]
    )
    budget = TwinBudget(twin)
    search = CompromiseSearch(budget, ladder=(0.5, 0.25))

    result = search.search(make_proposal(requested_value=40.0), [], BASE_KPIS, 30.0)

    assert result.found is True
    assert result.value == pytest.approx(32.5)
    assert result.delta_utility == pytest.approx(0.0425, abs=1e-4)


def test_all_infeasible_returns_not_found(make_proposal: Any) -> None:
    twin = MockTwinClient(
        scripted_responses=[twin_response(WORSE_KPIS), twin_response(BREACH_KPIS, floors=False)]
    )
    budget = TwinBudget(twin)
    search = CompromiseSearch(budget, ladder=(0.5, 0.25))

    result = search.search(make_proposal(requested_value=40.0), [], BASE_KPIS, 30.0)

    assert result.found is False
    assert result.value is None
    assert result.evaluations == 2


def test_search_respects_remaining_budget(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)] * 5)
    budget = TwinBudget(twin, max_evaluations=2)
    search = CompromiseSearch(budget, ladder=(0.5, 0.25, 0.75))

    result = search.search(make_proposal(requested_value=40.0), [], BASE_KPIS, 30.0)

    assert result.found is True
    assert budget.evaluations == 2
    assert twin.call_count == 2


def test_search_honours_confidence_margin(make_proposal: Any) -> None:
    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS, margin=0.5)])
    budget = TwinBudget(twin)
    search = CompromiseSearch(budget, confidence_margin=0.5, ladder=(0.5,))

    result = search.search(make_proposal(requested_value=40.0), [], BASE_KPIS, 30.0)

    assert result.found is False


def test_search_includes_peer_deltas(make_proposal: Any) -> None:
    from arbiter.engine.deltas import build_delta

    twin = MockTwinClient(scripted_responses=[twin_response(GOOD_KPIS)])
    budget = TwinBudget(twin)
    search = CompromiseSearch(budget, ladder=(0.5,))
    peer = make_proposal(command_id="peer", target_cell_id="cell-b", requested_value=38.0)

    search.search(
        make_proposal(command_id="me", requested_value=40.0),
        [build_delta(peer, peer.requested_value, 32.0)],
        BASE_KPIS,
        30.0,
    )

    assert len(twin.last_deltas) == 2
    assert twin.last_deltas[0]["command_id"] == "peer"
    assert twin.last_deltas[1]["command_id"] == "me"
    assert twin.last_deltas[1]["to_value"] == pytest.approx(35.0)
