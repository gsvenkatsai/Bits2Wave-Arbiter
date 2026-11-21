"""
tests/branch4/test_fastapi_routes.py
REST endpoint tests for the Branch 4 API.

Sources:
  spec §4.1 pages 4-5  — exact endpoint paths and contracts.
  shared/models.py     — TelemetrySample (lines 62-70), CellState (lines 72-81).
  shared/database.py   — audit_decisions schema (lines 35-45).

Uses FastAPI TestClient (backed by httpx — requirements.txt line 8).
No pytest-asyncio; all tests are synchronous.
All tests use a temp DB (tmp_path fixture) — never touches arbiter_production.db.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from arbiter.api.server import app
from arbiter.api.routes import get_db_path, get_monitor
from arbiter.shared.models import CellState
from arbiter.telemetry.monitor import KPITelemetryMonitor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Absolute path to a fresh temp SQLite file (never production DB)."""
    return str(tmp_path / "test_arbiter.db")


@pytest.fixture
def test_monitor(tmp_db):
    """KPITelemetryMonitor backed by temp DB (injectable data source)."""
    return KPITelemetryMonitor(db_path=tmp_db)


@pytest.fixture
def client(tmp_db, test_monitor):
    """
    TestClient with both monitor and DB-path dependencies overridden.
    Clears all overrides after each test so tests are isolated.
    """
    app.dependency_overrides[get_monitor] = lambda: test_monitor
    app.dependency_overrides[get_db_path] = lambda: tmp_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _sample_dict(**overrides) -> dict:
    """
    Valid TelemetrySample dict.
    Fields sourced from shared/models.py lines 62-70.
    """
    return {
        "sample_id":           str(uuid.uuid4()),
        "cell_id":             "cell_A",
        "sinr_db":             10.0,
        "dropped_users":       2,
        "active_users":        50,
        "throughput_mbps":     100.0,
        "energy_consumption_w": 500.0,
        "timestamp_epoch_ms":  1_700_000_000_000,
        **overrides,
    }


def _insert_decision(conn: sqlite3.Connection, seq: int) -> None:
    """
    Insert one row into audit_decisions.
    Column list: shared/database.py lines 36-44.
    Uses seq to give each row a unique decided_at_epoch_ms for ORDER BY tests.
    """
    conn.execute(
        """
        INSERT INTO audit_decisions
            (decision_id, command_id, batch_tick, action,
             approved_value, delta_utility, reason_code,
             hold_count, decided_at_epoch_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()), str(uuid.uuid4()), seq,
            "APPROVE", None, 0.5, "NONE", 0,
            1_700_000_000_000 + seq,
        ),
    )


# ---------------------------------------------------------------------------
# GET /api/v1/health  — spec §4.1 page 4
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_returns_200(self, client):
        assert client.get("/api/v1/health").status_code == 200

    def test_health_status_is_ok(self, client):
        """spec §4.1 page 4: status must equal 'OK'."""
        assert client.get("/api/v1/health").json()["status"] == "OK"

    def test_health_version_matches_spec(self, client):
        """spec §4.1 page 4: version must equal '1.0.0'."""
        assert client.get("/api/v1/health").json()["version"] == "1.0.0"

    def test_health_has_exactly_two_keys(self, client):
        """Response body has exactly the two keys from spec §4.1 page 4."""
        assert set(client.get("/api/v1/health").json().keys()) == {"status", "version"}


# ---------------------------------------------------------------------------
# POST /api/v1/telemetry/ingest  — spec §4.1 page 4
# ---------------------------------------------------------------------------

class TestIngest:

    def test_ingest_single_sample_200(self, client):
        """Single TelemetrySample as dict → 200, rows_written=1 (ASSUMPTION A1)."""
        resp = client.post("/api/v1/telemetry/ingest", json=_sample_dict())
        assert resp.status_code == 200
        assert resp.json()["rows_written"] == 1

    def test_ingest_batch_200(self, client):
        """List of 3 TelemetrySamples → rows_written=3 (ASSUMPTION A1)."""
        batch = [_sample_dict() for _ in range(3)]
        resp = client.post("/api/v1/telemetry/ingest", json=batch)
        assert resp.status_code == 200
        assert resp.json()["rows_written"] == 3

    def test_ingest_empty_batch_200(self, client):
        """Empty list [] → 200, rows_written=0 (no crash, no DB write)."""
        resp = client.post("/api/v1/telemetry/ingest", json=[])
        assert resp.status_code == 200
        assert resp.json()["rows_written"] == 0

    def test_ingest_invalid_body_missing_fields_422(self, client):
        """Dict with wrong fields → 422 (Pydantic validation failure)."""
        resp = client.post("/api/v1/telemetry/ingest", json={"bad": "field"})
        assert resp.status_code == 422

    def test_ingest_invalid_field_type_422(self, client):
        """sinr_db as string → 422."""
        resp = client.post(
            "/api/v1/telemetry/ingest",
            json=_sample_dict(sinr_db="not_a_number"),
        )
        assert resp.status_code == 422

    def test_ingest_non_object_body_422(self, client):
        """Body is a bare integer (not dict or list) → 422."""
        resp = client.post("/api/v1/telemetry/ingest", json=42)
        assert resp.status_code == 422

    def test_ingest_persists_to_db(self, client, test_monitor):
        """Data is actually in event_stream after a successful ingest."""
        resp = client.post(
            "/api/v1/telemetry/ingest",
            json=_sample_dict(cell_id="cell_PERSIST"),
        )
        assert resp.status_code == 200
        rows = test_monitor.get_recent_events(limit=5)
        assert any(r["cell_id"] == "cell_PERSIST" for r in rows)

    def test_ingest_response_has_rows_written_key(self, client):
        """Response body must contain rows_written key."""
        body = client.post("/api/v1/telemetry/ingest", json=_sample_dict()).json()
        assert "rows_written" in body


# ---------------------------------------------------------------------------
# GET /api/v1/state/cells  — spec §4.1 page 5 — STUB: Branch 3 not merged
# ---------------------------------------------------------------------------

class TestCells:

    def test_cells_returns_200(self, client):
        """GET /api/v1/state/cells → 200. Source: spec §4.1 page 5."""
        assert client.get("/api/v1/state/cells").status_code == 200

    def test_cells_returns_list(self, client):
        """Response is a JSON array (STUB: empty until Branch 3)."""
        assert isinstance(client.get("/api/v1/state/cells").json(), list)

    def test_cells_stub_returns_empty_list(self, client):
        """
        STUB assertion — confirms Branch 3 not yet merged (ASSUMPTION A2).
        Remove this test and wire _get_cell_states() when Branch 3 integrates.
        """
        assert client.get("/api/v1/state/cells").json() == []

    def test_cells_each_element_conforms_to_cell_state_schema(self, client):
        """
        Any returned element must deserialise as CellState (shared/models.py:72).
        Passes vacuously while STUB returns []; will catch schema drift at integration.
        """
        body = client.get("/api/v1/state/cells").json()
        for item in body:
            CellState(**item)   # raises ValueError / ValidationError on mismatch


# ---------------------------------------------------------------------------
# GET /api/v1/decisions/history  — spec §4.1 page 5
# ---------------------------------------------------------------------------

class TestDecisionsHistory:

    def test_decisions_history_200(self, client):
        assert client.get("/api/v1/decisions/history").status_code == 200

    def test_decisions_history_returns_list(self, client):
        assert isinstance(client.get("/api/v1/decisions/history").json(), list)

    def test_decisions_history_empty_db_returns_empty_list(self, client):
        """Empty audit_decisions → []."""
        assert client.get("/api/v1/decisions/history").json() == []

    def test_decisions_history_default_limit_50(self, client, tmp_db):
        """Insert 60 rows; default limit=50 returns at most 50."""
        conn = sqlite3.connect(tmp_db)
        for i in range(60):
            _insert_decision(conn, i)
        conn.commit()
        conn.close()
        body = client.get("/api/v1/decisions/history").json()
        assert len(body) <= 50

    def test_decisions_history_custom_limit(self, client, tmp_db):
        """?limit=3 returns at most 3 rows."""
        conn = sqlite3.connect(tmp_db)
        for i in range(10):
            _insert_decision(conn, i)
        conn.commit()
        conn.close()
        body = client.get("/api/v1/decisions/history?limit=3").json()
        assert len(body) <= 3

    def test_decisions_history_ordered_newest_first(self, client, tmp_db):
        """Rows are returned DESC by decided_at_epoch_ms (spec §4.1 page 5)."""
        conn = sqlite3.connect(tmp_db)
        for i in range(5):
            _insert_decision(conn, i)
        conn.commit()
        conn.close()
        body = client.get("/api/v1/decisions/history").json()
        timestamps = [r["decided_at_epoch_ms"] for r in body]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_decisions_history_invalid_limit_422(self, client):
        """?limit=abc (not an int) → 422 (FastAPI query param validation)."""
        resp = client.get("/api/v1/decisions/history?limit=abc")
        assert resp.status_code == 422

    def test_decisions_history_row_has_expected_columns(self, client, tmp_db):
        """
        Each row contains all columns from audit_decisions DDL.
        Source: shared/database.py lines 36-44.
        """
        conn = sqlite3.connect(tmp_db)
        _insert_decision(conn, 0)
        conn.commit()
        conn.close()
        body = client.get("/api/v1/decisions/history").json()
        expected_keys = {
            "decision_id", "command_id", "batch_tick", "action",
            "approved_value", "delta_utility", "reason_code",
            "hold_count", "decided_at_epoch_ms",
        }
        assert expected_keys.issubset(body[0].keys())

    def test_decisions_history_twin_eval_count_absent(self, client, tmp_db):
        """
        ASSUMPTION A4: twin_eval_count is NOT in the row (column not in DDL).
        Flags the ArbitrationDecision ↔ audit_decisions schema mismatch.
        """
        conn = sqlite3.connect(tmp_db)
        _insert_decision(conn, 0)
        conn.commit()
        conn.close()
        body = client.get("/api/v1/decisions/history").json()
        assert "twin_eval_count" not in body[0]


# ---------------------------------------------------------------------------
# POST /api/v1/benchmarks/run  — spec §4.1 page 5
# ---------------------------------------------------------------------------

class TestBenchmarksRun:

    _VALID_BODY: dict = {
        "scenario":    "2_AM_COLLISION",   # spec §4.1 page 5 example value
        "seeds_count": 10,                  # spec §5.2: 10 seeds
        "modes":       ["UNCOORDINATED", "STATIC_PRIORITY", "ARBITER"],  # spec §4.1 p5
    }

    def test_benchmarks_run_202(self, client):
        """Valid body → 202 Accepted (STUB ASSUMPTION A3)."""
        resp = client.post("/api/v1/benchmarks/run", json=self._VALID_BODY)
        assert resp.status_code == 202

    def test_benchmarks_run_returns_accepted_status(self, client):
        """STUB response contains status='accepted' (ASSUMPTION A3)."""
        body = client.post("/api/v1/benchmarks/run", json=self._VALID_BODY).json()
        assert body["status"] == "accepted"

    def test_benchmarks_run_echoes_scenario(self, client):
        body = client.post("/api/v1/benchmarks/run", json=self._VALID_BODY).json()
        assert body["scenario"] == "2_AM_COLLISION"

    def test_benchmarks_run_echoes_modes(self, client):
        body = client.post("/api/v1/benchmarks/run", json=self._VALID_BODY).json()
        assert body["modes"] == ["UNCOORDINATED", "STATIC_PRIORITY", "ARBITER"]

    def test_benchmarks_run_missing_scenario_422(self, client):
        """scenario field is required; omitting → 422."""
        resp = client.post("/api/v1/benchmarks/run", json={"seeds_count": 5})
        assert resp.status_code == 422

    def test_benchmarks_run_default_seeds_count(self, client):
        """
        seeds_count defaults to 10 (spec §5.2: 10 seeds, 95% CI).
        Source: spec §5.2 page 7, BenchmarkRunRequest default.
        """
        body = client.post(
            "/api/v1/benchmarks/run",
            json={"scenario": "QUIET_NIGHT"},
        ).json()
        assert body["seeds_count"] == 10

    def test_benchmarks_run_default_modes(self, client):
        """
        modes defaults to [UNCOORDINATED, STATIC_PRIORITY, ARBITER].
        Source: spec §4.1 page 5 (exact JSON shown).
        """
        body = client.post(
            "/api/v1/benchmarks/run",
            json={"scenario": "QUIET_NIGHT"},
        ).json()
        assert "ARBITER" in body["modes"]
        assert "UNCOORDINATED" in body["modes"]
        assert "STATIC_PRIORITY" in body["modes"]
