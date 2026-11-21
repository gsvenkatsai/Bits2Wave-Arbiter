"""Mock Telemetry Ingestion sink for isolated simulation testing."""
from typing import Any, Dict, List

from arbiter.shared.models import TelemetrySample


class MockTelemetrySink:
    """In-memory stub collecting telemetry batches from simulation."""

    def __init__(self) -> None:
        self.batches: List[Dict[str, Any]] = []

    def ingest(
        self,
        batch_tick: int,
        timestamp_epoch_ms: int,
        samples: List[TelemetrySample],
    ) -> Dict[str, Any]:
        record = {
            "batch_tick": batch_tick,
            "timestamp_epoch_ms": timestamp_epoch_ms,
            "samples": samples,
        }
        self.batches.append(record)
        return {
            "status": "INGESTED",
            "records_stored": len(samples),
        }


# TODO(INTEGRATION): Replace MockTelemetrySink with HTTP/gRPC client targeting
# Branch 4 Telemetry endpoint. Real implementation in Branch 4:
# POST http://localhost:8000/api/v1/telemetry/ingest
