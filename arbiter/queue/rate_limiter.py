"""FR-3 flood guard: per-controller admission cap per 100 ms window.

A single misbehaving controller must not be able to swamp arbitration. Every
controller may publish at most ``max_proposals`` proposals per ``window_ms``
window (default 20 per 100 ms, i.e. one arbitration tick); further proposals
are refused at admission. Emergency proposals never pass through here - they
go straight to :class:`arbiter.queue.emergency_lane.EmergencyBypassLane`.

Rejections use an existing reason code: ``ReasonCode.NONE``. A flood-guard
drop is an admission decision, not a safety, twin, ping-pong or uncertainty
verdict, so labelling it as any of those would be false; the specific cause is
carried by the queue/orchestrator counters instead.

The window is keyed on a monotonic clock in milliseconds, which makes the
guard independent of the simulated tick the caller passes to the queue.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict

from arbiter.engine.contracts import ReasonCode

__all__ = [
    "DEFAULT_MAX_PROPOSALS_PER_WINDOW",
    "DEFAULT_RATE_WINDOW_MS",
    "RATE_LIMIT_REASON",
    "ProposalRateLimiter",
]

#: Default per-controller cap per window.
DEFAULT_MAX_PROPOSALS_PER_WINDOW: int = 20

#: Default window length: one arbitration tick (100 ms).
DEFAULT_RATE_WINDOW_MS: int = 100

#: Existing reason code reported for a flood-guard rejection.
RATE_LIMIT_REASON: ReasonCode = ReasonCode.NONE


def _monotonic_ms() -> float:
    return time.monotonic() * 1000.0


class ProposalRateLimiter:
    """Counts accepted proposals per controller inside a rolling window."""

    def __init__(
        self,
        max_proposals: int = DEFAULT_MAX_PROPOSALS_PER_WINDOW,
        window_ms: int = DEFAULT_RATE_WINDOW_MS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_proposals < 1:
            raise ValueError("max_proposals must be >= 1")
        if window_ms < 1:
            raise ValueError("window_ms must be >= 1")
        self.max_proposals = int(max_proposals)
        self.window_ms = int(window_ms)
        self._clock: Callable[[], float] = clock or _monotonic_ms
        self._lock = threading.Lock()
        self._window: Dict[str, int] = {}
        self._counts: Dict[str, int] = {}
        self._rejected: Dict[str, int] = {}

    # -- state -------------------------------------------------------------
    def _window_index(self) -> int:
        return int(self._clock() // self.window_ms)

    def _roll(self, controller_id: str, index: int) -> int:
        stored = self._window.get(controller_id)
        if stored != index:
            self._window[controller_id] = index
            self._counts[controller_id] = 0
            return 0
        return self._counts.get(controller_id, 0)

    # -- policy ------------------------------------------------------------
    def allow(self, controller_id: str) -> bool:
        """True when the controller may publish one more proposal now."""
        with self._lock:
            index = self._window_index()
            used = self._roll(controller_id, index)
            if used >= self.max_proposals:
                self._rejected[controller_id] = (
                    self._rejected.get(controller_id, 0) + 1
                )
                return False
            self._counts[controller_id] = used + 1
            return True

    def count_in_window(self, controller_id: str) -> int:
        """Proposals already accepted for this controller in the window."""
        with self._lock:
            return self._roll(controller_id, self._window_index())

    @property
    def rejected_total(self) -> int:
        """Total proposals refused by the guard since construction/reset."""
        with self._lock:
            return sum(self._rejected.values())

    @property
    def rejected_by_controller(self) -> Dict[str, int]:
        """Rejections per controller since construction/reset."""
        with self._lock:
            return dict(self._rejected)

    def reset(self) -> None:
        """Forget all counters (configuration is kept)."""
        with self._lock:
            self._window.clear()
            self._counts.clear()
            self._rejected.clear()
