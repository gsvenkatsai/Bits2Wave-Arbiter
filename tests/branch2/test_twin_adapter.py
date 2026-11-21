"""Adapter tests: Branch 3's twin shape <-> Branch 2's TwinClient protocol."""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Mapping, Sequence

import pytest
from pydantic import BaseModel

from arbiter.engine.contracts import ActionType, ReasonCode, UtilityWeights
from arbiter.engine.ladder import DecisionLadderEngine
from arbiter.engine.twin_adapter import TwinClientAdapter, TwinDelta


class FakeKPIs(BaseModel):
    """Mirror of ``arbiter.twin.schemas.PredictedKPIs`` (extra fields included)."""

    coverage_ratio: float
    normalized_energy: float
    normalized_interference: float
    drop_rate_ratio: float
    predicted_sinr_avg_db: float = 12.0
    affected_cell_ids: List[str] = ["A"]


class FakeResponse(BaseModel):
    """Mirror of ``arbiter.twin.schemas.TwinEvaluationResponse``."""

    evaluation_id: str
    computation_time_ms: float
    predicted_kpis: FakeKPIs
    safety_floors_satisfied: bool
    confidence_margin: float


def _field(delta: Any, name: str) -> Any:
    """Read a delta field from an object or a mapping."""
    if isinstance(delta, Mapping):
        return delta[name]
    return getattr(delta, name)


def _curve(deltas: Sequence[Any]) -> Dict[str, float]:
    coverage, energy, interference, drop_rate = 0.95, 0.50, 0.30, 0.01
    for delta in deltas:
        if _field(delta, "parameter") == "OFFLOAD_RATIO":
            value = _field(delta, "proposed_value")
            coverage -= 0.02 * value
            energy -= 0.30 * value
            interference += 0.05 * value
            drop_rate += 0.15 * value * value
    return {
        "coverage_ratio": coverage,
        "normalized_energy": energy,
        "normalized_interference": interference,
        "drop_rate_ratio": drop_rate,
    }


class FakeEvaluator:
    """Duck-typed stand-in for ``DigitalTwinEvaluator``."""

    def __init__(self, margin: float = 0.0) -> None:
        self.margin = margin
        self.calls: List[tuple[int, float | None, str | None]] = []
        self.deltas: List[List[Any]] = []

    def predict(
        self,
        candidate_deltas: Sequence[Any],
        timeout_budget_ms: float | None = None,
        evaluation_id: str | None = None,
    ) -> FakeResponse:
        self.calls.append((len(candidate_deltas), timeout_budget_ms, evaluation_id))
        self.deltas.append(list(candidate_deltas))
        kpis = _curve(candidate_deltas)
        return FakeResponse(
            evaluation_id=evaluation_id or "eval-0001",
            computation_time_ms=0.37,
            predicted_kpis=FakeKPIs(**kpis),
            safety_floors_satisfied=(
                kpis["coverage_ratio"] >= 0.90 and kpis["drop_rate_ratio"] <= 0.02
            ),
            confidence_margin=self.margin,
        )


def test_adapter_maps_candidates_and_response() -> None:
    evaluator = FakeEvaluator()
    client = TwinClientAdapter(evaluator)

    payload = client.evaluate(
        [
            {
                "command_id": "c1",
                "cell_id": "A",
                "parameter": " offload_ratio ",
                "from_value": 0.0,
                "to_value": 0.2,
            }
        ]
    )

    delta = evaluator.deltas[0][0]
    assert isinstance(delta, TwinDelta)
    assert delta == TwinDelta(cell_id="A", parameter="OFFLOAD_RATIO", proposed_value=0.2)
    assert payload["evaluation_id"] == "eval-0001"
    assert payload["computation_time_ms"] == pytest.approx(0.37)
    assert payload["confidence_margin"] == pytest.approx(0.0)
    assert payload["safety_floors_satisfied"] is True
    assert payload["predicted_kpis"] == {
        "coverage_ratio": pytest.approx(0.946),
        "normalized_energy": pytest.approx(0.44),
        "normalized_interference": pytest.approx(0.31),
        "drop_rate_ratio": pytest.approx(0.016),
    }


def test_adapter_passes_the_timeout_budget() -> None:
    evaluator = FakeEvaluator()
    client = TwinClientAdapter(evaluator, timeout_budget_ms=42.0)

    client.evaluate([])
    client.evaluate([], timeout_budget_ms=7.5)

    assert [call[1] for call in evaluator.calls] == [42.0, 7.5]


def test_adapter_measures_elapsed_when_the_twin_omits_it() -> None:
    class NoTiming:
        def predict(self, candidate_deltas: Sequence[Any], **_: Any) -> Dict[str, Any]:
            return {
                "predicted_kpis": {"coverage_ratio": 0.95, "drop_rate_ratio": 0.01},
                "safety_floors_satisfied": True,
            }

    clock = itertools.count(0.0)
    client = TwinClientAdapter(NoTiming(), clock=lambda: next(clock) * 0.001)

    payload = client.evaluate([])

    assert payload["computation_time_ms"] == pytest.approx(1.0)
    assert payload["evaluation_id"] == ""
    assert payload["confidence_margin"] == pytest.approx(0.0)


def test_adapter_requires_a_predict_callable() -> None:
    with pytest.raises(TypeError, match="must expose predict"):
        TwinClientAdapter(object())


def test_adapter_rejects_unusable_response_objects() -> None:
    class Broken:
        def predict(self, candidate_deltas: Sequence[Any], **_: Any) -> object:
            return object()

    with pytest.raises(TypeError, match="mapping or pydantic model"):
        TwinClientAdapter(Broken()).evaluate([])


def test_adapter_missing_kpis_are_reported_as_empty() -> None:
    class NoKpis:
        def predict(self, candidate_deltas: Sequence[Any], **_: Any) -> Dict[str, Any]:
            return {"evaluation_id": "e1", "computation_time_ms": 0.1}

    payload = TwinClientAdapter(NoKpis()).evaluate([])

    assert payload["predicted_kpis"] == {}


def test_adapter_uses_the_delta_factory() -> None:
    evaluator = FakeEvaluator()
    seen: List[TwinDelta] = []

    def factory(delta: TwinDelta) -> Dict[str, Any]:
        seen.append(delta)
        return {
            "cell_id": delta.cell_id,
            "parameter": delta.parameter,
            "proposed_value": delta.proposed_value,
        }

    TwinClientAdapter(evaluator, delta_factory=factory).evaluate(
        [{"cell_id": "B", "parameter": "SLEEP_STATE", "to_value": 1.0}]
    )

    assert seen == [TwinDelta(cell_id="B", parameter="SLEEP_STATE", proposed_value=1.0)]
    assert evaluator.deltas[0][0] == {
        "cell_id": "B",
        "parameter": "SLEEP_STATE",
        "proposed_value": 1.0,
    }


def test_adapter_propagates_twin_timeouts() -> None:
    class TimingOut:
        def predict(self, candidate_deltas: Sequence[Any], **_: Any) -> Any:
            raise TimeoutError("over budget")

    with pytest.raises(TimeoutError):
        TwinClientAdapter(TimingOut()).evaluate([])


def test_from_handler_builds_the_api_payload() -> None:
    payloads: List[Dict[str, Any]] = []

    def handler(payload: Dict[str, Any]) -> Dict[str, Any]:
        payloads.append(payload)
        return {
            "evaluation_id": payload["evaluation_id"],
            "computation_time_ms": 1.5,
            "predicted_kpis": {
                "coverage_ratio": 0.93,
                "normalized_energy": 0.4,
                "normalized_interference": 0.3,
                "drop_rate_ratio": 0.015,
            },
            "safety_floors_satisfied": True,
            "confidence_margin": 0.01,
        }

    client = TwinClientAdapter.from_handler(handler, timeout_budget_ms=25.0)
    payload = client.evaluate(
        [{"cell_id": "C", "parameter": "TX_POWER_DBM", "to_value": 32.0}]
    )

    sent = payloads[0]
    assert set(sent) == {"evaluation_id", "candidate_deltas", "timeout_budget_ms"}
    assert sent["evaluation_id"].startswith("arb-")
    assert sent["candidate_deltas"] == [
        {"cell_id": "C", "parameter": "TX_POWER_DBM", "proposed_value": 32.0}
    ]
    assert sent["timeout_budget_ms"] == pytest.approx(25.0)
    assert payload["confidence_margin"] == pytest.approx(0.01)
    assert payload["safety_floors_satisfied"] is True


def test_from_handler_rejects_non_callables() -> None:
    with pytest.raises(TypeError, match="handler must be callable"):
        TwinClientAdapter.from_handler(object())  # type: ignore[arg-type]


def test_adapter_drives_the_ladder(make_proposal: Any) -> None:
    evaluator = FakeEvaluator()
    client = TwinClientAdapter(evaluator)
    command = make_proposal(
        command_id="lb",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from="A",
        handover_to="B",
    )

    decision = DecisionLadderEngine().evaluate(
        [command], None, client, UtilityWeights(), 1, 0
    )[0]

    assert decision.action is ActionType.MODIFY
    assert decision.reason_code is ReasonCode.HEADROOM
    assert decision.approved_value is not None
    assert 0.05 <= decision.approved_value <= 0.35
    assert sum(call[0] for call in evaluator.calls) <= 6


def test_adapter_surfaces_twin_failures_as_timeouts(make_proposal: Any) -> None:
    class TimingOut:
        def predict(self, candidate_deltas: Sequence[Any], **_: Any) -> Any:
            raise TimeoutError("over budget")

    decision = DecisionLadderEngine().evaluate(
        [make_proposal()], None, TwinClientAdapter(TimingOut()), UtilityWeights(), 1, 0
    )[0]

    assert decision.action is ActionType.REJECT
    assert decision.reason_code is ReasonCode.TWIN_TIMEOUT
