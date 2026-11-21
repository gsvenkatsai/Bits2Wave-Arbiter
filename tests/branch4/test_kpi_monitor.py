"""
tests/branch4/test_kpi_monitor.py
Tests for KPITelemetryMonitor (FR-8.1, FR-8.2) and ShadowModeGate (FR-8.3, FR-8.4).

All tests use pytest's tmp_path fixture → never touches arbiter_production.db.
The monitor is injectable via its db_path constructor argument.
"""
from __future__ import annotations

import uuid
import pytest

from arbiter.telemetry.monitor import KPITelemetryMonitor, MAPE_WINDOW_SIZE
from arbiter.telemetry.shadow_mode import ShadowModeGate
from arbiter.shared.models import TelemetrySample
from arbiter.shared.constants import TWIN_MAPE_TARGET_PCT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sample(
    sinr_db: float = 10.0,
    dropped_users: int = 2,
    active_users: int = 50,
    throughput_mbps: float = 100.0,
    energy_w: float = 500.0,
    timestamp_epoch_ms: int = 1_700_000_000_000,
    cell_id: str = "cell_A",
) -> TelemetrySample:
    """
    Factory for TelemetrySample.
    Fields sourced from shared/models.py lines 62-70.
    Default timestamp is a fixed epoch so out-of-order tests are deterministic.
    """
    return TelemetrySample(
        sample_id=str(uuid.uuid4()),
        cell_id=cell_id,
        sinr_db=sinr_db,
        dropped_users=dropped_users,
        active_users=active_users,
        throughput_mbps=throughput_mbps,
        energy_consumption_w=energy_w,
        timestamp_epoch_ms=timestamp_epoch_ms,
    )


@pytest.fixture
def monitor(tmp_path):
    """Injectable KPITelemetryMonitor backed by a fresh temp DB (not production)."""
    db = str(tmp_path / "test_arbiter.db")
    return KPITelemetryMonitor(db_path=db)


# ---------------------------------------------------------------------------
# KPITelemetryMonitor — ingest (FR-8.1)
# ---------------------------------------------------------------------------

class TestIngest:

    def test_ingest_single_sample(self, monitor):
        """Single sample → 1 row written; sample_id and cell_id round-trip."""
        s = _make_sample()
        count = monitor.ingest([s])
        assert count == 1
        rows = monitor.get_recent_events(limit=10)
        assert len(rows) == 1
        assert rows[0]["sample_id"] == s.sample_id
        assert rows[0]["cell_id"] == s.cell_id

    def test_ingest_batch(self, monitor):
        """Batch of 5 samples → 5 rows in event_stream."""
        samples = [_make_sample() for _ in range(5)]
        count = monitor.ingest(samples)
        assert count == 5
        rows = monitor.get_recent_events(limit=10)
        assert len(rows) == 5

    def test_ingest_empty_list(self, monitor):
        """Empty data: ingest([]) → 0 returned, no DB write, no exception."""
        count = monitor.ingest([])
        assert count == 0
        assert monitor.get_recent_events() == []

    def test_ingest_persists_all_fields(self, monitor):
        """
        FR-8.1: every column value round-trips from TelemetrySample to event_stream.
        Column mapping documented in monitor.py::ingest docstring.
        """
        ts = 1_700_000_001_234
        s = _make_sample(
            sinr_db=12.5,
            dropped_users=3,
            active_users=42,
            throughput_mbps=55.5,
            energy_w=300.0,
            timestamp_epoch_ms=ts,
            cell_id="cell_Z",
        )
        monitor.ingest([s])
        row = monitor.get_recent_events(limit=1)[0]
        assert row["cell_id"] == "cell_Z"
        assert row["sinr_db"] == pytest.approx(12.5)
        assert row["dropped_users"] == 3
        assert row["active_users"] == 42
        assert row["throughput_mbps"] == pytest.approx(55.5)
        assert row["energy_w"] == pytest.approx(300.0)
        assert row["recorded_at_epoch_ms"] == ts

    def test_ingest_with_explicit_batch_tick(self, monitor):
        """
        ASSUMPTION column: batch_tick passed explicitly is stored and readable.
        (TelemetrySample has no batch_tick; it's caller-supplied — monitor.py ASSUMPTIONS.)
        """
        s = _make_sample()
        monitor.ingest([s], batch_tick=99)
        row = monitor.get_recent_events(limit=1)[0]
        assert row["batch_tick"] == 99

    def test_ingest_default_batch_tick_is_zero(self, monitor):
        """ASSUMPTION: when batch_tick omitted, event_stream.batch_tick == 0."""
        monitor.ingest([_make_sample()])
        row = monitor.get_recent_events(limit=1)[0]
        assert row["batch_tick"] == 0

    def test_ingest_out_of_order_timestamps(self, monitor):
        """
        Out-of-order timestamps: all rows stored without error.
        get_recent_events returns DESC order by recorded_at_epoch_ms.
        """
        t_base = 1_700_000_000_000
        samples = [
            _make_sample(timestamp_epoch_ms=t_base + 300),
            _make_sample(timestamp_epoch_ms=t_base + 100),
            _make_sample(timestamp_epoch_ms=t_base + 200),
        ]
        count = monitor.ingest(samples)
        assert count == 3
        rows = monitor.get_recent_events(limit=10)
        assert len(rows) == 3
        # Highest timestamp must come first (DESC order)
        assert rows[0]["recorded_at_epoch_ms"] == t_base + 300

    def test_ingest_multiple_batches_accumulate(self, monitor):
        """Two separate ingest calls → rows accumulate in event_stream."""
        monitor.ingest([_make_sample(), _make_sample()])
        monitor.ingest([_make_sample()])
        rows = monitor.get_recent_events(limit=10)
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# KPITelemetryMonitor — MAPE / feed_calibration (FR-8.2)
# ---------------------------------------------------------------------------

class TestMAPE:

    def test_mape_empty_window(self, monitor):
        """Fresh monitor with no predictions → get_mape_pct() is None."""
        assert monitor.get_mape_pct() is None

    def test_feed_calibration_no_prediction(self, monitor):
        """
        Branch-3 isolation: twin_predicted_sinr_db=None → returns None,
        MAPE window stays empty.
        """
        result = monitor.feed_calibration(
            _make_sample(sinr_db=10.0),
            twin_predicted_sinr_db=None,
        )
        assert result is None
        assert monitor.get_mape_pct() is None

    def test_feed_calibration_with_prediction_returns_mape(self, monitor):
        """
        Single prediction → correct MAPE returned.
        |10 - 9| / |10| × 100 = 10.0 %
        """
        result = monitor.feed_calibration(
            _make_sample(sinr_db=10.0),
            twin_predicted_sinr_db=9.0,
        )
        assert result == pytest.approx(10.0)
        assert monitor.get_mape_pct() == pytest.approx(10.0)

    def test_mape_rolling_average_three_pairs(self, monitor):
        """
        3 pairs each with 10% error → mean MAPE = 10%.
          (10.0, 9.0):  |10-9|/10  = 0.10
          (20.0, 22.0): |20-22|/20 = 0.10
          (5.0,  5.5):  |5-5.5|/5  = 0.10
        """
        for actual, pred in [(10.0, 9.0), (20.0, 22.0), (5.0, 5.5)]:
            monitor.feed_calibration(
                _make_sample(sinr_db=actual),
                twin_predicted_sinr_db=pred,
            )
        assert monitor.get_mape_pct() == pytest.approx(10.0)

    def test_mape_mixed_errors(self, monitor):
        """
        Mixed error rates: mean is computed correctly.
          (10.0, 10.0): 0 %
          (10.0,  9.0): 10 %
        mean = 5 %
        """
        monitor.feed_calibration(_make_sample(sinr_db=10.0), twin_predicted_sinr_db=10.0)
        monitor.feed_calibration(_make_sample(sinr_db=10.0), twin_predicted_sinr_db=9.0)
        assert monitor.get_mape_pct() == pytest.approx(5.0)

    def test_mape_zero_actual_skipped_no_crash(self, monitor):
        """
        Divide-by-zero guard: sinr_db == 0.0 → pair skipped, no exception,
        MAPE unchanged from prior value.
        """
        monitor.feed_calibration(_make_sample(sinr_db=10.0), twin_predicted_sinr_db=9.0)
        mape_before = monitor.get_mape_pct()
        # Zero-actual pair must not crash and must not alter the MAPE
        result = monitor.feed_calibration(
            _make_sample(sinr_db=0.0),
            twin_predicted_sinr_db=1.0,
        )
        assert result == pytest.approx(mape_before)
        assert monitor.get_mape_pct() == pytest.approx(mape_before)

    def test_mape_zero_actual_on_empty_window_returns_none(self, monitor):
        """
        Divide-by-zero on empty window: returns None (not 0.0 or an error).
        """
        result = monitor.feed_calibration(
            _make_sample(sinr_db=0.0),
            twin_predicted_sinr_db=1.0,
        )
        assert result is None

    def test_mape_window_overflow_evicts_oldest(self, monitor):
        """
        Window is bounded by MAPE_WINDOW_SIZE.
        Fill window with 0% pairs, then add one 100% pair.
        Oldest 0% entry is evicted; mean = 100% / MAPE_WINDOW_SIZE.
        """
        for _ in range(MAPE_WINDOW_SIZE):
            monitor.feed_calibration(
                _make_sample(sinr_db=10.0), twin_predicted_sinr_db=10.0
            )
        assert monitor.get_mape_pct() == pytest.approx(0.0)

        # One 100% error replaces the oldest 0% entry
        monitor.feed_calibration(
            _make_sample(sinr_db=10.0), twin_predicted_sinr_db=20.0
        )
        # Window: (MAPE_WINDOW_SIZE - 1) × 0% + 1 × 100%
        expected_pct = 100.0 / MAPE_WINDOW_SIZE
        assert monitor.get_mape_pct() == pytest.approx(expected_pct)

    def test_mape_negative_sinr_handled(self, monitor):
        """
        Negative SINR is valid (spec: MIN_SINR_DB = -3.0, shared/constants.py line 17).
        |actual|  is used as denominator so sign is stripped.
          actual = -4.0, predicted = -4.4 → |-4 - (-4.4)| / |-4| = 0.1 → 10%
        """
        result = monitor.feed_calibration(
            _make_sample(sinr_db=-4.0),
            twin_predicted_sinr_db=-4.4,
        )
        assert result == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# KPITelemetryMonitor — get_recent_events
# ---------------------------------------------------------------------------

class TestGetRecentEvents:

    def test_get_recent_events_empty_db(self, monitor):
        """Empty DB → returns []."""
        assert monitor.get_recent_events() == []

    def test_get_recent_events_limit(self, monitor):
        """Insert 10 rows, query limit=5 → returns exactly 5."""
        for i in range(10):
            monitor.ingest([_make_sample(timestamp_epoch_ms=1_700_000_000_000 + i)])
        rows = monitor.get_recent_events(limit=5)
        assert len(rows) == 5

    def test_get_recent_events_order(self, monitor):
        """Rows are returned newest-first (DESC recorded_at_epoch_ms)."""
        t_base = 1_700_000_000_000
        for i in range(5):
            monitor.ingest([_make_sample(timestamp_epoch_ms=t_base + i * 1000)])
        rows = monitor.get_recent_events(limit=5)
        timestamps = [r["recorded_at_epoch_ms"] for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_get_recent_events_default_limit_fifty(self, monitor):
        """Default limit is 50: insert 60, get 50."""
        for i in range(60):
            monitor.ingest([_make_sample(timestamp_epoch_ms=1_700_000_000_000 + i)])
        rows = monitor.get_recent_events()
        assert len(rows) == 50

    def test_get_recent_events_dict_has_expected_keys(self, monitor):
        """Each returned dict contains all event_stream columns."""
        monitor.ingest([_make_sample()])
        row = monitor.get_recent_events(limit=1)[0]
        expected_keys = {
            "event_seq", "event_id", "batch_tick", "sample_id",
            "cell_id", "sinr_db", "dropped_users", "active_users",
            "throughput_mbps", "energy_w", "recorded_at_epoch_ms",
        }
        assert expected_keys.issubset(row.keys())


# ---------------------------------------------------------------------------
# ShadowModeGate (FR-8.3, FR-8.4)
# ---------------------------------------------------------------------------

class TestShadowModeGate:

    def test_gate_default_state_disabled(self):
        """FR-8.4: fresh gate → no MAPE yet → closed-loop disabled."""
        gate = ShadowModeGate()
        assert gate.is_closed_loop_enabled() is False

    def test_gate_enabled_below_threshold(self):
        """MAPE < TWIN_MAPE_TARGET_PCT → enabled."""
        gate = ShadowModeGate()
        gate.update(4.5)
        assert gate.is_closed_loop_enabled() is True

    def test_gate_disabled_above_threshold(self):
        """MAPE > TWIN_MAPE_TARGET_PCT → disabled."""
        gate = ShadowModeGate()
        gate.update(12.0)
        assert gate.is_closed_loop_enabled() is False

    def test_gate_exactly_at_threshold_disabled(self):
        """
        MAPE == TWIN_MAPE_TARGET_PCT (10.0) → NOT enabled.
        FR-8.4: closed-loop enabled when MAPE *strictly below* threshold.
        """
        gate = ShadowModeGate()
        gate.update(TWIN_MAPE_TARGET_PCT)   # == 10.0 from shared/constants.py
        assert gate.is_closed_loop_enabled() is False

    def test_gate_transitions_enabled_disabled(self):
        """Gate transitions correctly through multiple updates."""
        gate = ShadowModeGate()
        gate.update(5.0)
        assert gate.is_closed_loop_enabled() is True
        gate.update(15.0)
        assert gate.is_closed_loop_enabled() is False
        gate.update(1.0)
        assert gate.is_closed_loop_enabled() is True

    def test_gate_status_dict_keys_and_values(self):
        """get_status() returns dict with correct keys and matching values."""
        gate = ShadowModeGate()
        gate.update(5.5)
        status = gate.get_status()
        assert set(status.keys()) == {"enabled", "mape_pct", "threshold"}
        assert status["enabled"] is True
        assert status["mape_pct"] == pytest.approx(5.5)
        assert status["threshold"] == pytest.approx(TWIN_MAPE_TARGET_PCT)

    def test_gate_status_no_mape_received(self):
        """get_status() before any update: enabled=False, mape_pct=None."""
        gate = ShadowModeGate()
        status = gate.get_status()
        assert status["enabled"] is False
        assert status["mape_pct"] is None
        assert status["threshold"] == pytest.approx(TWIN_MAPE_TARGET_PCT)

    def test_gate_custom_threshold_injectable(self):
        """Custom threshold overrides the default (injectable for tests)."""
        gate = ShadowModeGate(mape_threshold_pct=5.0)
        gate.update(4.9)
        assert gate.is_closed_loop_enabled() is True
        gate.update(5.0)   # exactly at custom threshold → still disabled
        assert gate.is_closed_loop_enabled() is False

    def test_gate_threshold_sourced_from_shared_constant(self):
        """
        Default threshold must equal TWIN_MAPE_TARGET_PCT (10.0) from
        shared/constants.py — not a hardcoded literal in shadow_mode.py.
        """
        gate = ShadowModeGate()
        status = gate.get_status()
        assert status["threshold"] == pytest.approx(TWIN_MAPE_TARGET_PCT)
