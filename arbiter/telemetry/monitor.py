"""
arbiter/telemetry/monitor.py
KPI Telemetry Monitor — Branch 4.

SRS traceability:
  FR-8.1  Ingest live SINR / drop telemetry; persist to SQLite event_stream.
  FR-8.2  Feed live telemetry to online calibration and MAPE guard.

Schema source: arbiter/shared/database.py::SCHEMA_DDL (event_stream table).
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from collections import deque
from typing import Optional

from arbiter.shared.constants import TWIN_MAPE_TARGET_PCT  # = 10.0, line 6
from arbiter.shared.database import init_database
from arbiter.shared.models import TelemetrySample

# ---------------------------------------------------------------------------
# ASSUMPTIONS — not defined in Branch_4 spec or shared/constants.py.
# Change here only; nothing else in this file uses a magic number.
# ---------------------------------------------------------------------------

# ASSUMPTION: rolling MAPE window size.
#   Spec §5.2 names TWIN_MAPE_TARGET_PCT as the guard threshold but specifies
#   no window length. Using 20 samples; revisit when Branch 3 integrates.
MAPE_WINDOW_SIZE: int = 20

# ASSUMPTION: batch_tick default.
#   event_stream.batch_tick is NOT NULL (shared/database.py).
#   TelemetrySample has no batch_tick field (shared/models.py).
#   Caller (Branch 1) must pass the actual tick; 0 is the stand-in.
#   Flagged to Akshay — update once POST body is confirmed.
_DEFAULT_BATCH_TICK: int = 0

# ---------------------------------------------------------------------------
# Module-level defaults (overridable via constructor)
# ---------------------------------------------------------------------------

# DB_PATH: read from env (see .env.example::SQLITE_DB_PATH), fall back to spec default.
DB_PATH: str = os.environ.get("SQLITE_DB_PATH", "./arbiter_production.db")

# MAPE_THRESHOLD: sourced from shared/constants.py::TWIN_MAPE_TARGET_PCT (= 10.0).
# Never hardcoded here; the constant owns the value.
MAPE_THRESHOLD: float = TWIN_MAPE_TARGET_PCT


# ---------------------------------------------------------------------------
# KPITelemetryMonitor
# ---------------------------------------------------------------------------

class KPITelemetryMonitor:
    """
    Ingests live TelemetrySample objects, persists them to the SQLite
    event_stream table (FR-8.1), and maintains a rolling MAPE estimate
    for the digital-twin calibration guard (FR-8.2).

    ``db_path`` is injectable; tests pass a tmp_path, production reads DB_PATH.
    """

    def __init__(self, db_path: str = DB_PATH) -> None:
        self._db_path = db_path
        # Idempotent: creates tables if they don't exist (shared DDL).
        init_database(db_path)
        # Rolling deque of (actual_sinr_db, twin_predicted_sinr_db) pairs.
        # deque(maxlen=N) evicts the oldest entry automatically on overflow.
        self._mape_window: deque[tuple[float, float]] = deque(
            maxlen=MAPE_WINDOW_SIZE
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(
        self,
        samples: list[TelemetrySample],
        batch_tick: int = _DEFAULT_BATCH_TICK,
    ) -> int:
        """
        Persist a batch of TelemetrySamples to the event_stream table.

        Parameters
        ----------
        samples:
            One or more TelemetrySample objects from the Branch 1 simulator
            (FR-8.1). Accepts an empty list; returns 0 with no DB write.
        batch_tick:
            Simulation tick index for the whole batch.
            ASSUMPTION: defaults to _DEFAULT_BATCH_TICK (= 0) until Branch 1
            wires the actual tick counter. Confirm with Akshay.

        Returns
        -------
        int
            Number of rows written (== len(samples)).

        Column mapping (shared/database.py::SCHEMA_DDL):
            event_id            ← new UUID per row (uniqueness guarantee)
            batch_tick          ← caller-supplied (ASSUMPTION: 0 by default)
            sample_id           ← TelemetrySample.sample_id
            cell_id             ← TelemetrySample.cell_id
            sinr_db             ← TelemetrySample.sinr_db
            dropped_users       ← TelemetrySample.dropped_users
            active_users        ← TelemetrySample.active_users
            throughput_mbps     ← TelemetrySample.throughput_mbps
            energy_w            ← TelemetrySample.energy_consumption_w
            recorded_at_epoch_ms← TelemetrySample.timestamp_epoch_ms
        """
        if not samples:
            return 0

        rows = [
            (
                str(uuid.uuid4()),       # event_id
                batch_tick,              # batch_tick (ASSUMPTION: shared for whole batch)
                s.sample_id,
                s.cell_id,
                s.sinr_db,
                s.dropped_users,
                s.active_users,
                s.throughput_mbps,
                s.energy_consumption_w,  # → energy_w column
                s.timestamp_epoch_ms,    # → recorded_at_epoch_ms
            )
            for s in samples
        ]

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO event_stream
                    (event_id, batch_tick, sample_id, cell_id,
                     sinr_db, dropped_users, active_users,
                     throughput_mbps, energy_w, recorded_at_epoch_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(samples)

    def feed_calibration(
        self,
        sample: TelemetrySample,
        twin_predicted_sinr_db: Optional[float] = None,
    ) -> Optional[float]:
        """
        Feed one TelemetrySample to the online MAPE guard (FR-8.2).

        MAPE formula
        ------------
        MAPE = mean( |actual - predicted| / |actual| ) × 100

        Source: standard absolute MAPE. The spec (§5.2) references
        TWIN_MAPE_TARGET_PCT and the MAPE guard concept but does not
        prescribe a specific variant. ASSUMPTION: denominator = |actual_sinr_db|.

        Divide-by-zero guard
        --------------------
        If actual_sinr_db == 0.0 the pair is skipped (denominator undefined).
        The method returns the current MAPE (or None if window is empty).

        Branch-3 isolation
        ------------------
        If ``twin_predicted_sinr_db`` is None (Branch 3 not yet wired),
        the sample is skipped entirely; returns None.

        Parameters
        ----------
        sample:                 Live TelemetrySample (actual SINR from Branch 1).
        twin_predicted_sinr_db: Digital-twin SINR prediction from Branch 3.
                                Pass None to skip.

        Returns
        -------
        float | None  Current rolling MAPE in %, or None.
        """
        if twin_predicted_sinr_db is None:
            return None

        actual = sample.sinr_db
        if actual == 0.0:
            # Skip: cannot compute MAPE without a non-zero denominator.
            return self.get_mape_pct()

        self._mape_window.append((actual, twin_predicted_sinr_db))
        return self.get_mape_pct()

    def get_mape_pct(self) -> Optional[float]:
        """
        Return the current rolling-window MAPE in percent.

        Returns None if no twin predictions have been received yet
        (window is empty → cannot guard the shadow-mode gate).
        """
        if not self._mape_window:
            return None
        total = sum(
            abs(actual - predicted) / abs(actual)
            for actual, predicted in self._mape_window
        )
        return (total / len(self._mape_window)) * 100.0

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """
        Return the ``limit`` most-recent rows from event_stream,
        ordered by recorded_at_epoch_ms DESC (newest first).
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT *
                FROM event_stream
                ORDER BY recorded_at_epoch_ms DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
