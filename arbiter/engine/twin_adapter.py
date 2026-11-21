"""Thin adapter: Branch 3's real digital twin -> Branch 2's ``TwinClient``.

Branch 3 exposes (``arbiter/twin/``):

* ``DigitalTwinEvaluator.predict(candidate_deltas, timeout_budget_ms, evaluation_id)``
  returning a ``TwinEvaluationResponse`` with ``predicted_kpis``
  (``coverage_ratio``, ``normalized_energy``, ``normalized_interference``,
  ``drop_rate_ratio``, plus SINR/affected-cell extras), ``safety_floors_satisfied``,
  ``confidence_margin`` and ``computation_time_ms``;
* ``handle_twin_evaluate(payload) -> dict`` as the plain-Python callable behind
  ``POST /api/v1/twin/evaluate``, taking ``{evaluation_id, candidate_deltas,
  timeout_budget_ms}``.

This module converts between that shape and the Branch 2 protocol without
importing ``arbiter.twin`` at module import time (Branch 3 merges after Branch 2,
so the import must not be a hard dependency). Deltas are passed as
``TwinDelta`` instances, which are duck-type compatible with Branch 3's
``CandidateDelta``; pass ``delta_factory`` to build the real model instead.

Nothing here changes arbitration behaviour: the ladder keeps its own 100 ms
wall-clock guard and treats an empty or malformed prediction as a twin failure.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from arbiter.engine.contracts import TWIN_TIMEOUT_MS

__all__ = [
    "TwinDelta",
    "TwinClientAdapter",
    "KPI_KEYS",
]

#: KPI keys the Branch 2 utility function and floor enforcer consume.
KPI_KEYS: tuple[str, ...] = (
    "coverage_ratio",
    "normalized_energy",
    "normalized_interference",
    "drop_rate_ratio",
)


@dataclass(frozen=True)
class TwinDelta:
    """Candidate delta in Branch 3's schema shape."""

    cell_id: str
    parameter: str
    proposed_value: float


def _as_mapping(response: Any) -> dict[str, Any]:
    """Coerce a pydantic model or mapping into a plain dict."""
    if isinstance(response, Mapping):
        return dict(response)
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        return dict(dump())
    raise TypeError(
        f"twin response must be a mapping or pydantic model, got {type(response)!r}"
    )


def _kpis(raw: Any) -> dict[str, float]:
    """Filter the twin's KPI payload down to the four contract keys."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        dump = getattr(raw, "model_dump", None)
        raw = dump() if callable(dump) else {}
    result: dict[str, float] = {}
    for key in KPI_KEYS:
        value = raw.get(key) if isinstance(raw, Mapping) else None
        if value is None:
            continue
        try:
            result[key] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TwinClientAdapter:
    """``TwinClient`` implementation over ``DigitalTwinEvaluator.predict``."""

    def __init__(
        self,
        evaluator: Any,
        *,
        timeout_budget_ms: float = TWIN_TIMEOUT_MS,
        delta_factory: Callable[[TwinDelta], Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        predict = getattr(evaluator, "predict", None)
        if not callable(predict):
            raise TypeError(
                "evaluator must expose predict(candidate_deltas, timeout_budget_ms=...); "
                "got "
                f"{type(evaluator)!r}"
            )
        self._predict: Callable[..., Any] = predict
        self._timeout_budget_ms = float(timeout_budget_ms)
        self._delta_factory: Callable[[TwinDelta], Any] = delta_factory or (lambda d: d)
        self._clock = clock

    # -- construction helpers ---------------------------------------------
    @classmethod
    def from_handler(
        cls,
        handler: Callable[[dict[str, Any]], Any],
        **kwargs: Any,
    ) -> "TwinClientAdapter":
        """Wrap ``handle_twin_evaluate`` (the ``POST /api/v1/twin/evaluate`` callable)."""
        return cls(_HandlerEvaluator(handler), **kwargs)

    # -- TwinClient protocol ----------------------------------------------
    def evaluate(
        self,
        candidate_deltas: Sequence[Mapping[str, Any]],
        timeout_budget_ms: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate one candidate set and return the Branch 2 response payload."""
        deltas = [self._delta_factory(self._to_delta(raw)) for raw in candidate_deltas]
        budget = (
            self._timeout_budget_ms
            if timeout_budget_ms is None
            else float(timeout_budget_ms)
        )
        started = self._clock()
        response = self._predict(deltas, timeout_budget_ms=budget)
        elapsed_ms = (self._clock() - started) * 1000.0
        return self._normalise(response, elapsed_ms)

    # -- conversions -------------------------------------------------------
    @staticmethod
    def _to_delta(raw: Mapping[str, Any]) -> TwinDelta:
        """Branch 2 candidate dict -> ``TwinDelta`` (``from_value`` is dropped)."""
        return TwinDelta(
            cell_id=str(raw["cell_id"]),
            parameter=str(raw["parameter"]).strip().upper(),
            proposed_value=float(raw["to_value"]),
        )

    @staticmethod
    def _normalise(response: Any, elapsed_ms: float) -> dict[str, Any]:
        """Branch 3 response -> ``TwinClient`` payload."""
        payload = _as_mapping(response)
        kpis = _kpis(payload.get("predicted_kpis"))
        reported = _number(payload.get("computation_time_ms"))
        margin = _number(payload.get("confidence_margin"))
        return {
            "evaluation_id": str(payload.get("evaluation_id", "")),
            "predicted_kpis": kpis,
            "safety_floors_satisfied": payload.get("safety_floors_satisfied"),
            "confidence_margin": 0.0 if margin is None else margin,
            "computation_time_ms": elapsed_ms if reported is None else reported,
        }


class _HandlerEvaluator:
    """Give ``handle_twin_evaluate`` the ``predict``-shaped interface."""

    def __init__(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handler = handler

    def predict(
        self,
        candidate_deltas: Sequence[TwinDelta],
        timeout_budget_ms: float | None = None,
        evaluation_id: str | None = None,
    ) -> Any:
        """Build the ``TwinEvaluationRequest`` payload and call the handler."""
        payload = {
            "evaluation_id": evaluation_id or f"arb-{uuid.uuid4().hex[:8]}",
            "candidate_deltas": [
                {
                    "cell_id": delta.cell_id,
                    "parameter": delta.parameter,
                    "proposed_value": delta.proposed_value,
                }
                for delta in candidate_deltas
            ],
            "timeout_budget_ms": timeout_budget_ms,
        }
        return self._handler(payload)
