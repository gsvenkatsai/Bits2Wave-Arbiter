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
# GET /api/v1/state/cells  — Contract C4 (Live state only, no invented numbers)
# ---------------------------------------------------------------------------

class TestCells:

    def test_cells_without_frame_returns_no_data(self, client):
        """GET /api/v1/state/cells with no frame yet responds 200 with no_data (Contract C4)."""
        import arbiter.api.routes as routes
        old_frame = routes._latest_frame
        try:
            routes._latest_frame = None
            resp = client.get("/api/v1/state/cells")
            assert resp.status_code == 200
            data = resp.json()
            assert data == {"status": "no_data", "cells": []}
        finally:
            routes._latest_frame = old_frame

    def test_cells_with_frame_returns_cell_states(self, client):
        """GET /api/v1/state/cells with ingested frame derives response honestly from frame."""
        import arbiter.api.routes as routes
        old_frame = routes._latest_frame
        try:
            sample_cells = [
                {
                    "id": "cell_A",
                    "x": 0.0,
                    "y": 0.0,
                    "tx_power_dbm": 43.0,
                    "tilt_deg": 6.0,
                    "is_sleeping": False,
                    "active_users": 15,
                    "load_ratio": 0.45,
                    "neighbor_cell_ids": ["cell_B"]
                },
                {
                    "id": "cell_B",
                    "x": 200.0,
                    "y": 0.0,
                    "tx_power_dbm": 40.0,
                    "tilt_deg": 4.0,
                    "is_sleeping": True,
                    "active_users": 0,
                    "load_ratio": 0.0,
                    "neighbor_cell_ids": ["cell_A"]
                }
            ]
            routes._latest_frame = {"cells": sample_cells}
            resp = client.get("/api/v1/state/cells")
            assert resp.status_code == 200
            body = resp.json()
            assert isinstance(body, list)
            assert len(body) == 2
            for item in body:
                cell_state = CellState(**item)
                assert isinstance(cell_state, CellState)
            assert body[0]["cell_id"] == "cell_A"
            assert body[0]["active_users"] == 15
            assert body[1]["cell_id"] == "cell_B"
            assert body[1]["is_sleeping"] is True
        finally:
            routes._latest_frame = old_frame


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


# ---------------------------------------------------------------------------
# Dashboard Static Files & Frontend Assets (Spec §5.1, §4.1)
# ---------------------------------------------------------------------------

class TestDashboardStaticFiles:

    def test_static_index_html_returns_200_and_html_type(self, client):
        """GET / serves index.html with text/html content-type."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "ARBITER" in resp.text
        assert "2 AM COLLISION MONITOR" in resp.text

    def test_static_app_js_returns_200_and_js_type(self, client):
        """GET /app.js serves app.js with javascript content-type."""
        resp = client.get("/app.js")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "javascript" in content_type

    def test_static_style_css_returns_200_and_css_type(self, client):
        """GET /style.css serves style.css with text/css content-type."""
        resp = client.get("/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")

    def test_action_badge_css_classes_exist_in_stylesheet(self):
        """All 4 action badge classes from Spec §5.1 exist in style.css."""
        import pathlib
        css_path = pathlib.Path("arbiter/dashboard/style.css")
        assert css_path.exists(), "style.css missing"
        css_content = css_path.read_text(encoding="utf-8")
        assert ".badge-approve" in css_content
        assert ".badge-modify" in css_content
        assert ".badge-delay" in css_content
        assert ".badge-reject" in css_content

    def test_synthetic_and_mock_strings_exist_in_app_js(self):
        """'SYNTHETIC' and 'MOCK' strings must be explicitly present in app.js."""
        import pathlib
        js_path = pathlib.Path("arbiter/dashboard/app.js")
        assert js_path.exists(), "app.js missing"
        js_content = js_path.read_text(encoding="utf-8")
        assert "SYNTHETIC" in js_content
        assert "MOCK" in js_content

    def test_no_innerhtml_assignment_with_server_data(self):
        """Grep test: No innerHTML = assignments in app.js (safe DOM manipulation only)."""
        import pathlib
        import re
        js_path = pathlib.Path("arbiter/dashboard/app.js")
        js_content = js_path.read_text(encoding="utf-8")
        # Assert no innerHTML assignment exists
        matches = re.findall(r"\.innerHTML\s*=", js_content)
        assert len(matches) == 0, f"Found unsafe innerHTML assignment in app.js: {matches}"


# ---------------------------------------------------------------------------
# GET /api/v1/twin/intervals (Contract C3) & build_frame (Contract C2)
# ---------------------------------------------------------------------------

class TestTwinIntervals:

    def test_intervals_without_evaluator(self, client):
        """GET /api/v1/twin/intervals returns no_evaluator when none is registered."""
        from arbiter.api.routes import get_twin_evaluator
        app.dependency_overrides[get_twin_evaluator] = lambda: None
        try:
            resp = client.get("/api/v1/twin/intervals")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "no_evaluator"
            assert data["calibrated"] is False
            assert data["intervals"] == []
        finally:
            app.dependency_overrides.pop(get_twin_evaluator, None)

    def test_intervals_with_evaluator_uncalibrated(self, client):
        """GET /api/v1/twin/intervals with uncalibrated evaluator (Contract C3)."""
        from arbiter.api.routes import get_twin_evaluator

        class MockEvaluator:
            def calibration_status(self):
                return {
                    "calibrated": False,
                    "n_calibration": 12,
                    "interval_halfwidth_db": None,
                }

            def predict_with_intervals(self, features=None):
                return [{"cell_id": "cell_A", "lower": 1.0, "upper": 3.0}]

        app.dependency_overrides[get_twin_evaluator] = lambda: MockEvaluator()
        try:
            resp = client.get("/api/v1/twin/intervals")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["calibrated"] is False
            assert data["interval_halfwidth_db"] is None
            assert len(data["intervals"]) == 1
        finally:
            app.dependency_overrides.pop(get_twin_evaluator, None)

    def test_intervals_with_evaluator_calibrated(self, client):
        """GET /api/v1/twin/intervals with calibrated evaluator (Contract C3)."""
        from arbiter.api.routes import get_twin_evaluator

        class MockEvaluator:
            def calibration_status(self):
                return {
                    "calibrated": True,
                    "n_calibration": 200,
                    "interval_halfwidth_db": 0.28,
                }

            def predict_with_intervals(self, features=None):
                return [{"cell_id": "cell_A", "lower": 2.0, "upper": 2.56}]

        app.dependency_overrides[get_twin_evaluator] = lambda: MockEvaluator()
        try:
            resp = client.get("/api/v1/twin/intervals")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["calibrated"] is True
            assert data["interval_halfwidth_db"] == 0.28
            assert data["n_calibration"] == 200
        finally:
            app.dependency_overrides.pop(get_twin_evaluator, None)


class TestBuildFrame:

    def test_build_frame_calls_native_method_when_present(self):
        """When engine implements frame natively (Aki Contract C2), build_frame delegates."""
        from arbiter.telemetry.frame import build_frame

        class NativeEngine:
            def frame(self):
                return {"tick": 42, "native": True}

        engine = NativeEngine()
        res = build_frame(engine)
        assert res == {"tick": 42, "native": True}

    def test_build_frame_without_native_method_does_not_mutate_class(self):
        """build_frame builds from public accessors without monkey-patching SimulationEngine."""
        from arbiter.telemetry.frame import build_frame

        class CustomEngine:
            def __init__(self):
                self.sim_time_s = 1.5
                self.current_tick = 15
                self.cluster = None
                self.users = []

            def get_kpis(self):
                return {"energy_w": 5000.0}

        engine = CustomEngine()
        assert not hasattr(CustomEngine, "frame")
        res = build_frame(engine)
        assert res["tick"] == 15
        assert res["sim_time_ms"] == 1500
        assert not hasattr(CustomEngine, "frame"), "build_frame must NEVER monkey-patch the class"


