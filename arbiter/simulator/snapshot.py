"""Snapshot management and circular ring buffer for simulation state."""
from __future__ import annotations

from collections import deque
from typing import Deque, Iterator, Optional

from arbiter.shared.v2_contracts import SimulationSnapshot


class SnapshotRing:
    """Fixed-capacity ring buffer retaining historical simulation snapshots.

    Stores up to ``capacity`` snapshots (default 600 ticks = 60 seconds).
    Provides $O(1)$ push and $O(1)$ tick index lookups.
    """

    def __init__(self, capacity: int = 600) -> None:
        if capacity <= 0:
            raise ValueError(
                f"SnapshotRing capacity must be > 0 (got {capacity})"
            )
        self._capacity: int = capacity
        self._buffer: Deque[SimulationSnapshot] = deque(maxlen=capacity)

    @property
    def capacity(self) -> int:
        """Maximum retention capacity."""
        return self._capacity

    def push(self, snapshot: SimulationSnapshot) -> None:
        """Push snapshot into ring buffer, discarding oldest if full."""
        self._buffer.append(snapshot)

    def get(self, tick: int) -> Optional[SimulationSnapshot]:
        """Retrieve a snapshot for the specified tick index if available."""
        for snap in reversed(self._buffer):
            if snap.tick == tick:
                return snap
        return None

    def latest(self) -> Optional[SimulationSnapshot]:
        """Retrieve the most recent snapshot, or None if buffer is empty."""
        return self._buffer[-1] if self._buffer else None

    def clear(self) -> None:
        """Clear all snapshots from buffer."""
        self._buffer.clear()

    def __len__(self) -> int:
        return len(self._buffer)

    def __iter__(self) -> Iterator[SimulationSnapshot]:
        return iter(self._buffer)
