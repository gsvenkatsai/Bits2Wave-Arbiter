"""Mock Telemetry Feeder for Project ARBITER.

Simulates the telemetry event stream from Branch 4 before merge.
SRS FR-5.4, FR-5.6.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional


class MockTelemetryFeed:
    """Simulates realistic cell telemetry emissions per simulation tick.

    Emits per-cell SINR (dB), drops, throughput, and active user counts.
    """

    def __init__(self, cell_ids: Optional[List[str]] = None, seed: int = 42) -> None:
        self.cell_ids: List[str] = cell_ids or ["cell_A", "cell_B", "cell_C", "cell_D", "cell_E"]
        self.random = random.Random(seed)
        self.current_tick: int = 0

    def generate_tick_telemetry(self) -> List[Dict[str, Any]]:
        """Generate a single tick's telemetry samples across all cells."""
        # TODO(INTEGRATION): Connect calibration loop to real SQLite event stream from Branch 4
        self.current_tick += 1
        samples: List[Dict[str, Any]] = []

        for cid in self.cell_ids:
            # Baseline nominal SINR ~ 12-16 dB with slight noise
            base_sinr = 14.0 if cid != "cell_B" else 11.5
            noise = self.random.gauss(0.0, 0.8)
            sinr_val = float(base_sinr + noise)

            dropped_users = int(max(0, self.random.randint(0, 1)))
            active_users = int(max(10, 45 + int(self.random.gauss(0, 5))))

            sample: Dict[str, Any] = {
                "cell_id": cid,
                "sinr_db": sinr_val,
                "dropped_users": dropped_users,
                "active_users": active_users,
                "batch_tick": self.current_tick,
            }
            samples.append(sample)

        return samples
