"""Deterministic digital-twin stand-ins for Branch 2 tests and local runs.

Branch 3 owns the real analytical twin.  Branch 2 codes against the
``TwinClient`` protocol so the integration swap is a one-line constructor
change.  The mock is deliberately deterministic: identical inputs produce
identical KPI forecasts and evaluation ids.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "TwinClient",
    "MockTwinClient",
    "TwinEvaluationError",
    "DEFAULT_KPIS",
    "PARAMETER_SENSITIVITY",
]

KPIDict = dict[str, Any]

#: Deterministic healthy baseline used when a test does not override it.
DEFAULT_KPIS: dict[str, float] = {
    "coverage_ratio": 0.95,
    "normalized_energy": 0.50,
    "normalized_interference": 0.30,
    "drop_rate_ratio": 0.010,
}

#: Per-parameter first-order KPI sensitivity to a unit change of the value:
#: (coverage, energy, interference, drop_rate).
PARAMETER_SENSITIVITY: dict[str, tuple[float, float, float, float]] = {
    "TX_POWER_DBM": (0.020, 0.030, 0.030, -0.0010),
    "TILT_DEG": (-0.010, -0.005, -0.010, 0.0005),
    "SLEEP_STATE": (-0.200, -0.200, -0.100, 0.0200),
    "OFFLOAD_RATIO": (0.005, -0.010, 0.020, 0.0100),
}

KPI_KEYS: tuple[str, str, str, str] = (
    "coverage_ratio",
    "normalized_energy",
    "normalized_interference",
    "drop_rate_ratio",
)


class TwinEvaluationError(RuntimeError):
    """Raised by the mock (and by real twins) when a forecast cannot be made."""


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


def _number(value: Any, default: float = 0.0) -> float:
    """Coerce a delta field to float, treating missing/None as ``default``."""
    if value is None:
        return default
    return float(value)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


class MockTwinClient:
    """Configurable, deterministic :class:`TwinClient` implementation.

    Per-test knobs:

    * ``scripted_responses`` – FIFO of raw responses returned verbatim.
    * ``response_factory`` – callable computing a full response per call.
    * ``raise_at_calls`` – 1-based call numbers that raise.
    * ``timeout_at_calls`` – 1-based call numbers that sleep past the budget.
    * ``floor_breach_at_calls`` – 1-based calls that forecast a floor breach.

    Everything else is a fixed first-order KPI model, so results are stable.
    """

    def __init__(
        self,
        baseline_kpis: Mapping[str, float] | None = None,
        *,
        confidence_margin: float = 0.0,
        scripted_responses: Sequence[Mapping[str, Any]] | None = None,
        response_factory: Callable[[list[dict[str, Any]]], Mapping[str, Any]] | None = None,
        raise_at_calls: Sequence[int] = (),
        timeout_at_calls: Sequence[int] = (),
        floor_breach_at_calls: Sequence[int] = (),
        timeout_overshoot_s: float = 0.05,
        sleep_scale: float = 1.0,
        latency_s: float = 0.0,
    ) -> None:
        baseline = dict(DEFAULT_KPIS)
        if baseline_kpis is not None:
            baseline.update(baseline_kpis)
        self.baseline_kpis = baseline
        self.confidence_margin = confidence_margin
        self._scripted: list[Mapping[str, Any]] = list(scripted_responses or ())
        self._factory = response_factory
        self._raise_at = frozenset(raise_at_calls)
        self._timeout_at = frozenset(timeout_at_calls)
        self._breach_at = frozenset(floor_breach_at_calls)
        self._timeout_overshoot_s = timeout_overshoot_s
        self._sleep_scale = sleep_scale
        self._latency_s = latency_s
        self.call_count = 0
        self.calls: list[list[dict[str, Any]]] = []

    # -- introspection helpers used by tests -------------------------------
    @property
    def last_deltas(self) -> list[dict[str, Any]]:
        """Deltas passed to the most recent evaluation (empty if none)."""
        return self.calls[-1] if self.calls else []

    def reset(self) -> None:
        """Forget call history without discarding scripting."""
        self.call_count = 0
        self.calls.clear()

    # -- TwinClient --------------------------------------------------------
    def evaluate(
        self,
        candidate_deltas: list[dict[str, Any]],
        timeout_budget_ms: float = 15.0,
    ) -> dict[str, Any]:
        self.call_count += 1
        call = self.call_count
        deltas = [dict(delta) for delta in candidate_deltas]
        self.calls.append(deltas)

        if call in self._raise_at:
            raise TwinEvaluationError(f"mock twin failure on call {call}")

        if self._latency_s:
            time.sleep(self._latency_s)

        if call in self._timeout_at:
            time.sleep(max(timeout_budget_ms, 0.0) / 1000.0 + self._timeout_overshoot_s)

        response = self._build_response(call, deltas)
        return response

    # -- internals ---------------------------------------------------------
    def _build_response(self, call: int, deltas: list[dict[str, Any]]) -> KPIDict:
        if self._factory is not None:
            response: dict[str, Any] = dict(self._factory(deltas))
            response.setdefault("evaluation_id", f"mock-eval-{call:04d}")
            response.setdefault("confidence_margin", self.confidence_margin)
            return response

        if self._scripted:
            scripted = dict(self._scripted.pop(0))
            scripted.setdefault("evaluation_id", f"mock-eval-{call:04d}")
            scripted.setdefault("confidence_margin", self.confidence_margin)
            return scripted

        kpis = dict(self.baseline_kpis)
        coverage = kpis["coverage_ratio"]
        energy = kpis["normalized_energy"]
        interference = kpis["normalized_interference"]
        drop_rate = kpis["drop_rate_ratio"]

        for delta in deltas:
            parameter = str(delta.get("parameter", ""))
            sensitivity = PARAMETER_SENSITIVITY.get(parameter)
            if sensitivity is None:
                continue
            change = _number(delta.get("to_value")) - _number(delta.get("from_value"))
            coverage += sensitivity[0] * change
            energy += sensitivity[1] * change
            interference += sensitivity[2] * change
            drop_rate += sensitivity[3] * change

        if call in self._breach_at:
            coverage = 0.85
            drop_rate = 0.05

        predicted = {
            "coverage_ratio": _clamp01(coverage),
            "normalized_energy": _clamp01(energy),
            "normalized_interference": _clamp01(interference),
            "drop_rate_ratio": _clamp01(drop_rate),
        }
        floors_ok = (
            predicted["coverage_ratio"] >= 0.90 and predicted["drop_rate_ratio"] <= 0.02
        )
        return {
            "evaluation_id": f"mock-eval-{call:04d}",
            "predicted_kpis": predicted,
            "safety_floors_satisfied": floors_ok,
            "confidence_margin": self.confidence_margin,
        }
