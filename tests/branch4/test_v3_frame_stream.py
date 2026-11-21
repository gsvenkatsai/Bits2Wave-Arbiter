"""
tests/branch4/test_v3_frame_stream.py
Unit and integration tests for ARBITER v3 Frame Broadcast, Replay, and 3D endpoints.

Coverage requirements (SRS traceability & Branch 4 v3 specs):
  1. Frame Shape: Frame validates against FrameMessage Pydantic contract (types, fields, constraints).
  2. Replay: /ws/replay streams all recorded lines in sequential order.
  3. Counterfactual Frames: counterfactual_frames returns two worlds of equal length ('arbiter' and 'counterfactual').
  4. Payload Size: Frame payload size is logged and verified to stay small (< 60 KB for 350 users).
  5. REST Endpoints: GET /api/v1/frame/latest and GET /api/v1/recorder/counterfactual_frames/{decision_id}.
  6. 3D Dashboard Route: GET /3d returns HTML status 200.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Generator
import pytest
from fastapi.testclient import TestClient

from arbiter.api.routes import get_db_path, get_monitor
from arbiter.api.server import app
from arbiter.shared.database_v2 import init_v2_tables
from arbiter.telemetry.frame import FrameMessage
from arbiter.telemetry.monitor import KPITelemetryMonitor
from arbiter.telemetry.recorder import (
    compute_counterfactual_frames,
    record_2am_collision,
)

logger = logging.getLogger("test_v3_frame_stream")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Any) -> str:
    """Fresh temporary SQLite database with v1 and v2 schemas."""
    db_file = str(tmp_path / "test_v3_arbiter.db")
    conn = sqlite3.connect(db_file)
    try:
        from arbiter.shared.database import SCHEMA_DDL
        conn.executescript(SCHEMA_DDL)
        init_v2_tables(conn)
        conn.commit()
    finally:
        conn.close()
    return db_file


@pytest.fixture
def test_monitor(tmp_db: str) -> KPITelemetryMonitor:
    """Isolated KPITelemetryMonitor backed by temp DB."""
    return KPITelemetryMonitor(db_path=tmp_db)


@pytest.fixture
def client(tmp_db: str, test_monitor: KPITelemetryMonitor) -> Generator[TestClient, None, None]:
    """TestClient configured with dependency overrides."""
    app.dependency_overrides[get_monitor] = lambda: test_monitor
    app.dependency_overrides[get_db_path] = lambda: tmp_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Frame Contract Validation Tests
# ---------------------------------------------------------------------------

def test_frame_contract_pydantic_schema() -> None:
    """Verify FrameMessage enforces exact required contract and rejects malformed fields."""
    valid_frame = {
        "type": "frame",
        "tick": 6,
        "sim_time_ms": 1732154400600,
        "scenario": "2am_collision",
        "world": "arbiter",
        "cells": [
            {
                "id": "cell_A",
                "x": 0.0,
                "y": 0.0,
                "tx_power_dbm": 43.0,
                "tilt_deg": 6.0,
                "is_sleeping": False,
                "active_users": 35,
                "load_ratio": 0.875,
                "energy_w": 20450.0,
            }
        ],
        "users": [
            {
                "id": "ue_0001",
                "x": 45.2,
                "y": -12.4,
                "cell": "cell_A",
                "sinr_db": 12.4,
                "dropped": False,
            }
        ],
        "proposals": [
            {
                "command_id": "cmd-es-sleep-01",
                "controller_type": "ENERGY_SAVING",
                "target_cell_id": "cell_B",
                "parameter": "SLEEP_STATE",
                "requested_value": 1.0,
                "emergency_flag": False,
                "hold_count": 0,
                "timestamp_epoch_ms": 1732154400600,
            }
        ],
        "decisions": [
            {
                "decision_id": "dec-2am-collision-06",
                "command_id": "cmd-es-sleep-01",
                "action": "DELAY",
                "approved_value": None,
                "reason_code": "ACTIVE_HO",
                "hold_count": 1,
                "delta_utility": 0.0,
            }
        ],
        "conflicts": [
            {"a": "cmd-es-sleep-01", "b": "cmd-lb-offload-01", "type": "DIRECT"}
        ],
        "plans": [
            {
                "plan_id": "plan-2am-staged-coordination",
                "steps": [
                    {
                        "step_index": 0,
                        "tick_offset": 0,
                        "target_cell_id": "cell_A",
                        "parameter": "OFFLOAD_RATIO",
                        "value": 0.15,
                        "precondition": "load(cell_A)<0.90",
                        "status": "COMPLETED",
                    }
                ],
            }
        ],
        "twin": {
            "calibrated": True,
            "intervals": [
                {
                    "kpi": "coverage_ratio",
                    "mean": 0.985,
                    "lower": 0.970,
                    "upper": 1.000,
                }
            ],
        },
        "kpis": {
            "dropped_users": 0,
            "mean_sinr_db": 8.75,
            "energy_w": 102250.0,
        },
    }

    # Model parses valid structure cleanly
    frame_obj = FrameMessage.model_validate(valid_frame)
    assert frame_obj.type == "frame"
    assert frame_obj.tick == 6
    assert frame_obj.world == "arbiter"
    assert len(frame_obj.cells) == 1
    assert frame_obj.cells[0].id == "cell_A"
    assert len(frame_obj.users) == 1
    assert frame_obj.users[0].dropped is False
    assert len(frame_obj.decisions) == 1
    assert frame_obj.decisions[0].action == "DELAY"
    assert frame_obj.decisions[0].reason_code == "ACTIVE_HO"
    assert frame_obj.twin.calibrated is True
    assert frame_obj.kpis.dropped_users == 0


def test_frame_contract_rejects_extra_fields() -> None:
    """Extra fields must be rejected to guarantee exact contract adherence."""
    with pytest.raises(Exception):
        FrameMessage.model_validate({
            "type": "frame",
            "tick": 0,
            "sim_time_ms": 1000,
            "scenario": "test",
            "world": "arbiter",
            "kpis": {"dropped_users": 0, "mean_sinr_db": 0.0, "energy_w": 0.0},
            "unauthorized_field": "injected",
        })


# ---------------------------------------------------------------------------
# 2. Replay Streams All Lines in Order Tests
# ---------------------------------------------------------------------------

def test_replay_websocket_streams_lines_in_order(client: TestClient) -> None:
    """Verify /ws/replay streams recorded frames in exact ascending tick order."""
    fixture_path = os.path.abspath("fixtures/v3/frames_2am.jsonl")
    if not os.path.exists(fixture_path):
        record_2am_collision(output_path=fixture_path, seed=42, ticks=80)

    with client.websocket_connect("/ws/replay?speed=100.0") as ws:
        received_ticks = []
        for _ in range(10):
            data = ws.receive_json()
            assert data["type"] == "frame"
            received_ticks.append(data["tick"])

        # Validate strictly increasing sequential order
        assert received_ticks == list(range(1, 11)) or received_ticks == list(range(0, 10))
        for i in range(1, len(received_ticks)):
            assert received_ticks[i] == received_ticks[i - 1] + 1


# ---------------------------------------------------------------------------
# 3. Counterfactual Frames: Two Worlds of Equal Length Tests
# ---------------------------------------------------------------------------

def test_counterfactual_frames_equal_worlds_length() -> None:
    """Verify compute_counterfactual_frames returns two worlds of equal horizon length."""
    horizon = 15
    res = compute_counterfactual_frames(decision_id="dec-2am-collision-06", horizon=horizon)

    assert res["decision_id"] == "dec-2am-collision-06"
    assert res["horizon"] == horizon
    assert "arbiter" in res
    assert "counterfactual" in res
    assert len(res["arbiter"]) == horizon
    assert len(res["counterfactual"]) == horizon

    # Validate world tags on each frame
    for f in res["arbiter"]:
        assert f["world"] == "arbiter"
        assert f["type"] == "frame"
        # Validate Pydantic model
        FrameMessage.model_validate(f)

    for f in res["counterfactual"]:
        assert f["world"] == "counterfactual"
        assert f["type"] == "frame"
        FrameMessage.model_validate(f)


def test_counterfactual_frames_rest_endpoint(client: TestClient) -> None:
    """GET /api/v1/recorder/counterfactual_frames/{decision_id} returns 2 worlds."""
    resp = client.get("/api/v1/recorder/counterfactual_frames/dec-2am-collision-06?horizon=12")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["arbiter"]) == 12
    assert len(data["counterfactual"]) == 12
    assert data["arbiter"][0]["world"] == "arbiter"
    assert data["counterfactual"][0]["world"] == "counterfactual"


def test_counterfactual_frames_aged_out_error(client: TestClient) -> None:
    """Non-existent or aged-out snapshot returns clear HTTP 404 error."""
    resp = client.get("/api/v1/recorder/counterfactual_frames/dec-unknown-aged-out")
    assert resp.status_code == 404
    assert "aged out" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Payload Size Logging & Constraint Tests
# ---------------------------------------------------------------------------

def test_payload_size_per_frame_under_60kb() -> None:
    """Frame payload size is logged and must stay small (< 60 KB for 350 users)."""
    fixture_path = os.path.abspath("fixtures/v3/frames_2am.jsonl")
    if not os.path.exists(fixture_path):
        record_2am_collision(output_path=fixture_path, seed=42, ticks=80)

    max_bytes = 0
    total_bytes = 0
    count = 0

    with open(fixture_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            b_len = len(stripped.encode("utf-8"))
            max_bytes = max(max_bytes, b_len)
            total_bytes += b_len
            count += 1

            # Validate each line against Pydantic schema
            data = json.loads(stripped)
            FrameMessage.model_validate(data)

    assert count == 80
    avg_kb = (total_bytes / count) / 1024.0
    max_kb = max_bytes / 1024.0

    print(f"\n[PAYLOAD AUDIT] 80 frames: avg = {avg_kb:.2f} KB, max = {max_kb:.2f} KB")

    # Contract target: under 60 KB
    assert max_kb < 60.0, f"Frame payload {max_kb:.2f} KB exceeds 60 KB limit"
    assert avg_kb < 20.0, f"Average payload {avg_kb:.2f} KB unexpectedly high"


# ---------------------------------------------------------------------------
# 5. REST & 3D Route Tests
# ---------------------------------------------------------------------------

def test_get_latest_frame_endpoint(client: TestClient) -> None:
    """GET /api/v1/frame/latest returns valid frame conforming to contract."""
    resp = client.get("/api/v1/frame/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "frame"
    FrameMessage.model_validate(data)


def test_get_3d_dashboard_route(client: TestClient) -> None:
    """GET /3d returns HTTP 200 serving 3d.html."""
    resp = client.get("/3d")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "ARBITER • 3D RAN Visualizer" in resp.text
    # Verify offline vendoring: NO external unpkg/cdnjs CDN script tags in HTML
    assert "https://cdnjs.cloudflare.com" not in resp.text
    assert "https://unpkg.com" not in resp.text
    assert "vendor/three.min.js" in resp.text
