"""Adapter connecting SimulationEngine to Branch 4 KPITelemetryMonitor.

Persists per-tick simulation samples directly into the SQLite event_stream
table and feeds the online MAPE calibration guard.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from arbiter.shared.models import TelemetrySample
from arbiter.telemetry.monitor import KPITelemetryMonitor

__all__ = ["KPITelemetrySink"]


class KPITelemetrySink:
    """Telemetry sink forwarding samples to KPITelemetryMonitor."""

    def __init__(self, monitor: Optional[KPITelemetryMonitor] = None) -> None:
        self.monitor: KPITelemetryMonitor = monitor or KPITelemetryMonitor()

    def ingest(
        self,
        batch_tick: int,
        timestamp_epoch_ms: int,
        samples: List[TelemetrySample],
    ) -> Dict[str, Any]:
        """Ingest samples into SQLite event stream via KPITelemetryMonitor."""
        rows_written = self.monitor.ingest(
            samples=samples, batch_tick=batch_tick
        )
        return {
            "status": "INGESTED",
            "records_stored": rows_written,
            "batch_tick": batch_tick,
            "timestamp_epoch_ms": timestamp_epoch_ms,
        }
