"""Digital-twin stand-ins for Branch 2.

Branch 3 owns the real analytical twin. Branch 2 codes against the
:class:`TwinClient` protocol so the integration swap is a constructor change.
Both mocks are deterministic: identical candidates produce identical responses.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from arbiter.engine.contracts import FLOOR_COVERAGE, FLOOR_DROP_RATE

__all__ = [
    "TwinClient",
    "MockDigitalTwinClient",
    "ScriptedTwin",
    "TwinTimeoutError",
    "DEFAULT_KPIS",
    "floors_satisfied_by",
]

#: KPI dict returned by every twin evaluation.
KPIDict = dict[str, Any]

#: Healthy deterministic baseline used when a test does not override it.
DEFAULT_KPIS: dict[str, float] = {
    "coverage_ratio": 0.95,
    "normalized_energy": 0.50,
    "normalized_interference": 0.30,
    "drop_rate_ratio": 0.01,
}


class TwinTimeoutError(RuntimeError):
    """Raised when a twin evaluation overruns its timeout budget."""


def floors_satisfied_by(kpis: Mapping[str, float]) -> bool:
    """Apply the spec's safety floors to a KPI snapshot."""
    coverage = float(kpis.get("coverage_ratio", 0.0))
    drop_rate = float(kpis.get("drop_rate_ratio", 1.0))
    return coverage >= FLOOR_COVERAGE and drop_rate <= FLOOR_DROP_RATE


@runtime_checkable
class TwinClient(Protocol):
    """Digital-twin evaluation contract (Branch 3 boundary)."""

    def evaluate(
        self,
        candidate_deltas: list[dict[str, Any]],
        timeout_budget_ms: float = 15.0,
    ) -> dict[str, Any]:
        """Forecast joint KPIs for a set of candidate parameter deltas.

        Returns a mapping with ``evaluation_id``, ``predicted_kpis``
        (``coverage_ratio``, ``normalized_energy``,
        ``normalized_interference``, ``drop_rate_ratio``),
        ``safety_floors_satisfied`` and ``confidence_margin``.
        """
        ...


def _response(
    call: int,
    kpis: Mapping[str, float],
    floors_satisfied: bool | None,
    margin: float,
) -> KPIDict:
    predicted = {str(key): float(value) for key, value in kpis.items()}
    floors = floors_satisfied_by(predicted) if floors_satisfied is None else floors_satisfied
    return {
        "evaluation_id": f"mock-eval-{call:04d}",
        "predicted_kpis": predicted,
        "safety_floors_satisfied": floors,
        "confidence_margin": float(margin),
    }


class MockDigitalTwinClient:
    """Deterministic twin returning fixed KPIs with optional fault injection.

    Knobs: ``kpis`` (predicted KPIs), ``confidence_margin``,
    ``floors_satisfied`` (override the derived floor verdict), ``latency_ms``
    (simulated work) and ``raise_on_timeout`` (raise :class:`TwinTimeoutError`
    instead of returning when the simulated latency exceeds the budget).
    """

    def __init__(
        self,
        kpis: Mapping[str, float] | None = None,
        *,
        confidence_margin: float = 0.0,
        floors_satisfied: bool | None = None,
        latency_ms: float = 0.0,
        raise_on_timeout: bool = False,
    ) -> None:
        merged = dict(DEFAULT_KPIS)
        if kpis is not None:
            merged.update(kpis)
        self.kpis = merged
        self.confidence_margin = confidence_margin
        self.floors_satisfied = floors_satisfied
        self.latency_ms = latency_ms
        self.raise_on_timeout = raise_on_timeout
        self.call_count = 0
        self.candidates: list[list[dict[str, Any]]] = []

    @property
    def last_candidate(self) -> list[dict[str, Any]]:
        """Candidate deltas passed to the most recent evaluation."""
        return self.candidates[-1] if self.candidates else []

    def reset(self) -> None:
        """Forget call history (configuration is kept)."""
        self.call_count = 0
        self.candidates.clear()

    def evaluate(
        self,
        candidate_deltas: list[dict[str, Any]],
        timeout_budget_ms: float = 15.0,
    ) -> dict[str, Any]:
        self.call_count += 1
        self.candidates.append([dict(delta) for delta in candidate_deltas])

        if self.latency_ms > timeout_budget_ms:
            if self.raise_on_timeout:
                raise TwinTimeoutError(
                    f"twin overran {timeout_budget_ms:.1f} ms "
                    f"(simulated latency {self.latency_ms:.1f} ms)"
                )
            time.sleep(max(timeout_budget_ms, 0.0) / 1000.0)
        elif self.latency_ms > 0.0:
            time.sleep(self.latency_ms / 1000.0)

        return _response(
            self.call_count, self.kpis, self.floors_satisfied, self.confidence_margin
        )


class ScriptedTwin:
    """Twin whose KPIs are computed from the candidate by a test-supplied curve.

    ``kpi_fn`` maps the candidate delta list to a KPI mapping, which lets tests
    model an arbitrary utility curve (e.g. a peak at a specific value) without
    scripting every response.
    """

    def __init__(
        self,
        kpi_fn: Callable[[list[dict[str, Any]]], Mapping[str, float]],
        *,
        confidence_margin: float = 0.0,
        floors_satisfied: bool | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        self.kpi_fn = kpi_fn
        self.confidence_margin = confidence_margin
        self.floors_satisfied = floors_satisfied
        self.latency_ms = latency_ms
        self.call_count = 0
        self.candidates: list[list[dict[str, Any]]] = []

    @property
    def last_candidate(self) -> list[dict[str, Any]]:
        """Candidate deltas passed to the most recent evaluation."""
        return self.candidates[-1] if self.candidates else []

    def evaluate(
        self,
        candidate_deltas: list[dict[str, Any]],
        timeout_budget_ms: float = 15.0,
    ) -> dict[str, Any]:
        self.call_count += 1
        candidate = [dict(delta) for delta in candidate_deltas]
        self.candidates.append(candidate)
        if self.latency_ms > 0.0:
            time.sleep(self.latency_ms / 1000.0)
        kpis = self.kpi_fn(candidate)
        return _response(
            self.call_count, kpis, self.floors_satisfied, self.confidence_margin
        )
