"""
arbiter/telemetry/shadow_mode.py
Shadow Mode Gate — Branch 4.

SRS traceability:
  FR-8.3  Advisory Shadow Mode: log recommendations.
  FR-8.4  Enable closed-loop actuation when twin MAPE < TWIN_MAPE_TARGET_PCT.

Threshold source: arbiter/shared/constants.py::TWIN_MAPE_TARGET_PCT (= 10.0).
"""
from __future__ import annotations

from typing import Optional

from arbiter.shared.constants import TWIN_MAPE_TARGET_PCT  # = 10.0, line 6

# Module-level default: sourced from shared constant, never hardcoded.
_DEFAULT_THRESHOLD: float = TWIN_MAPE_TARGET_PCT


class ShadowModeGate:
    """
    Advisory gate that enables closed-loop actuation only when the
    digital twin's rolling MAPE stays *strictly below* the threshold
    (FR-8.3, FR-8.4).

    Closed-loop is disabled until at least one MAPE reading arrives.
    The gate is stateless beyond the last MAPE reading; call ``update``
    after every ``KPITelemetryMonitor.feed_calibration`` result.

    ``mape_threshold_pct`` is injectable so tests can probe boundary
    conditions without altering the shared constant.
    """

    def __init__(self, mape_threshold_pct: float = _DEFAULT_THRESHOLD) -> None:
        self._threshold: float = mape_threshold_pct
        self._last_mape: Optional[float] = None

    def update(self, mape_pct: float) -> None:
        """
        Record the latest MAPE reading from KPITelemetryMonitor.

        Parameters
        ----------
        mape_pct: Rolling MAPE in % returned by get_mape_pct().
        """
        self._last_mape = mape_pct

    def is_closed_loop_enabled(self) -> bool:
        """
        Return True iff closed-loop actuation is permitted.

        Conditions (FR-8.4):
          1. At least one MAPE reading has been received (not None).
          2. The last reading is *strictly below* the threshold.
             Equality (mape == threshold) keeps the gate closed.
        """
        if self._last_mape is None:
            return False
        return self._last_mape < self._threshold

    def get_status(self) -> dict:
        """
        Return current gate state as a plain dict.

        Keys
        ----
        enabled   : bool  — result of is_closed_loop_enabled().
        mape_pct  : float | None — last MAPE reading (None if no data yet).
        threshold : float — configured threshold (default = TWIN_MAPE_TARGET_PCT).
        """
        return {
            "enabled": self.is_closed_loop_enabled(),
            "mape_pct": self._last_mape,
            "threshold": self._threshold,
        }
