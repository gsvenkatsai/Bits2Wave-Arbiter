"""Spec constants for Branch 2.

Values are taken from the frozen ``arbiter.shared.constants`` module when it is
importable, so a team-wide constant change propagates without editing Branch 2.
"""
from __future__ import annotations

from typing import Any, TypeVar, cast

__all__ = [
    "TICK_DURATION_MS",
    "MAX_HOLD_TICKS",
    "BISECTION_MAX_EVALS",
    "TWIN_TIMEOUT_MS",
    "MIN_COVERAGE_RATIO",
    "MAX_DROP_RATE_RATIO",
    "DEFAULT_WEIGHT_COVERAGE",
    "DEFAULT_WEIGHT_ENERGY",
    "DEFAULT_WEIGHT_INTERFERENCE",
    "DEFAULT_WEIGHT_DROP_RATE",
    "PINGPONG_WINDOW_TICKS",
    "BINARY_PARAMETERS",
    "SHARED_CONSTANTS_AVAILABLE",
]

T = TypeVar("T")

_shared: Any = None
try:  # pragma: no cover - depends on the shared module being present
    from arbiter.shared import constants as _shared_constants

    _shared = _shared_constants
except ImportError:  # pragma: no cover - shared module always present today
    _shared = None

SHARED_CONSTANTS_AVAILABLE: bool = _shared is not None


def _spec(name: str, fallback: T) -> T:
    """Return a shared constant when published, else the spec fallback."""
    if _shared is not None and hasattr(_shared, name):
        return cast(T, getattr(_shared, name))
    return fallback


# Tick batch = 100 ms.
TICK_DURATION_MS: int = _spec("TICK_DURATION_MS", 100)
# A command may be held for at most 3 ticks, then it must be decided.
MAX_HOLD_TICKS: int = _spec("MAX_HOLD_TICKS", 3)
# NFR-3 budget: at most 6 twin evaluations per component arbitration.
BISECTION_MAX_EVALS: int = _spec("BISECTION_MAX_EVALS", 6)
# Twin call budget before the referee assumes a timeout.
TWIN_TIMEOUT_MS: float = _spec("TWIN_TIMEOUT_MS", 100.0)

# Safety floors.
MIN_COVERAGE_RATIO: float = _spec("MIN_COVERAGE_RATIO", 0.90)
MAX_DROP_RATE_RATIO: float = _spec("MAX_DROP_RATE_RATIO", 0.02)

# Default multi-objective weights: [0.3, 0.2, 0.2, 0.3].
DEFAULT_WEIGHT_COVERAGE: float = _spec("DEFAULT_WEIGHT_COVERAGE", 0.3)
DEFAULT_WEIGHT_ENERGY: float = _spec("DEFAULT_WEIGHT_ENERGY", 0.2)
DEFAULT_WEIGHT_INTERFERENCE: float = _spec("DEFAULT_WEIGHT_INTERFERENCE", 0.2)
DEFAULT_WEIGHT_DROP_RATE: float = _spec("DEFAULT_WEIGHT_DROP_RATE", 0.3)

# Implicit ping-pong sliding window (ticks) and the binary parameters that skip
# the MODIFY rung (e.g. cell OFF cannot be "partially" applied).
PINGPONG_WINDOW_TICKS: int = 5
BINARY_PARAMETERS: frozenset[str] = frozenset({"SLEEP_STATE"})
