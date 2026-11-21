"""Tests for 3D frame ingestion, WebSocket broadcasting, and replay schema validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arbiter.api.server import app
from arbiter.simulator.engine import SimulationEngine


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_post_frame_ingest_and_latest(client: TestClient) -> None:
    """POST /api/v1/frames/ingest stores frame and joins decisions/twin/conflicts."""
    engine = SimulationEngine(seed=42)
    fr = engine.frame(
        world="arbiter",
        proposals=[
            {
                "command_id": "test-cmd-001",
                "controller_type": "ENERGY_SAVING",
                "target_cell_id": "cell_B",
                "parameter": "SLEEP_STATE",
                "requested_value": 1.0,
                "emergency_flag": False,
                "hold_count": 0,
                "timestamp_epoch_ms": engine.simulated_epoch_ms,
            }
        ],
        decisions=[
            {
                "decision_id": "test-dec-001",
                "command_id": "test-cmd-001",
                "action": "DELAY",
                "approved_value": None,
                "reason_code": "INDIRECT_COLLISION",
                "hold_count": 1,
                "delta_utility": -0.05,
            }
        ],
    )

    resp = client.post("/api/v1/frames/ingest", json=fr)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "ok"
    assert res_data["tick"] == fr["tick"]
    assert res_data["decisions_count"] == 1

    # Verify GET /api/v1/frame/latest returns the ingested frame
    resp_latest = client.get("/api/v1/frame/latest")
    assert resp_latest.status_code == 200
    latest = resp_latest.json()
    assert latest["type"] == "frame"
    assert latest["tick"] == fr["tick"]
    assert latest["sim_time_ms"] == fr["sim_time_ms"]
    assert len(latest["cells"]) == 5
    assert len(latest["decisions"]) == 1
    assert latest["decisions"][0]["decision_id"] == "test-dec-001"
    assert "twin" in latest
    assert "intervals" in latest["twin"]


def test_websocket_broadcasts_frame_and_tick_update(client: TestClient) -> None:
    """Websocket clients on /api/v1/ws/live receive both frame and TICK_UPDATE messages."""
    engine = SimulationEngine(seed=42)
    fr = engine.frame()

    with client.websocket_connect("/api/v1/ws/live") as ws:
        # Initial handshake message
        init_msg = ws.receive_json()
        assert init_msg["type"] in ("TICK_UPDATE", "frame")

        # Ingest a new frame
        resp = client.post("/api/v1/frames/ingest", json=fr)
        assert resp.status_code == 200

        # Receive broadcast messages
        msg1 = ws.receive_json()
        msg2 = ws.receive_json()
        received_types = {msg1["type"], msg2["type"]}

        assert "frame" in received_types, "3D frame message was not broadcasted"
        assert "TICK_UPDATE" in received_types, "2D TICK_UPDATE was not broadcasted"


def test_sim_time_advances_by_100_per_tick() -> None:
    """SimulationEngine discrete clock strictly advances by 100 ms per step."""
    start_ms = 1732154400000
    engine = SimulationEngine(start_epoch_ms=start_ms, seed=42)

    for i in range(10):
        assert engine.tick == i
        assert engine.simulated_epoch_ms == start_ms + i * 100
        fr = engine.frame()
        assert fr["tick"] == i
        assert fr["sim_time_ms"] == start_ms + i * 100
        engine.step()

    assert engine.tick == 10
    assert engine.simulated_epoch_ms == start_ms + 1000


def test_recorded_frames_schema_and_metadata() -> None:
    """Verify frames_2am.json and provenance metadata strictly conform to the Frame contract."""
    base_dir = Path(__file__).resolve().parents[2]
    frames_path = base_dir / "arbiter" / "dashboard" / "3d" / "data" / "frames_2am.json"
    meta_path = base_dir / "arbiter" / "dashboard" / "3d" / "data" / "frames_2am.meta.json"
    cf_meta_path = base_dir / "fixtures" / "v3" / "counterfactual_2am.meta.json"
    jsonl_path = base_dir / "fixtures" / "v3" / "frames_2am.jsonl"

    assert frames_path.is_file(), f"frames_2am.json missing at {frames_path}"
    assert meta_path.is_file(), f"frames_2am.meta.json missing at {meta_path}"
    assert cf_meta_path.is_file(), f"counterfactual_2am.meta.json missing at {cf_meta_path}"
    assert jsonl_path.is_file(), f"frames_2am.jsonl missing at {jsonl_path}"

    with open(jsonl_path, "r", encoding="utf-8") as f:
        jsonl_lines = [json.loads(line) for line in f if line.strip()]
    assert len(jsonl_lines) == 50

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["source"] == "real engine run"
    assert meta["seed"] == 42
    assert meta["tick_count"] == 50
    assert "git_commit" in meta

    with open(frames_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "arbiter" in data
    assert "counterfactual" in data
    assert len(data["arbiter"]) == 50
    assert len(data["counterfactual"]) == 50

    required_frame_keys = {
        "type", "tick", "sim_time_ms", "scenario", "world",
        "cells", "users", "proposals", "decisions", "conflicts",
        "plans", "twin", "kpis"
    }
    cell_keys = {"id", "x", "y", "tx_power_dbm", "tilt_deg", "is_sleeping", "active_users", "load_ratio", "energy_w"}
    user_keys = {"id", "x", "y", "cell", "sinr_db", "dropped"}
    kpi_keys = {"dropped_users", "mean_sinr_db", "energy_w", "power_kw"}

    start_sim_time = data["arbiter"][0]["sim_time_ms"]

    for i, frame in enumerate(data["arbiter"]):
        assert frame["type"] == "frame"
        assert frame["world"] == "arbiter"
        assert frame["tick"] == i
        assert frame["sim_time_ms"] == start_sim_time + i * 100
        assert required_frame_keys.issubset(frame.keys())

        # Check cells
        assert len(frame["cells"]) == 5
        for cell in frame["cells"]:
            assert cell_keys.issubset(cell.keys())

        # Check users
        assert len(frame["users"]) > 0
        for user in frame["users"]:
            assert user_keys.issubset(user.keys())

        # Check KPIs
        assert kpi_keys.issubset(frame["kpis"].keys())

    # Check collision decisions at tick 6
    tick6_frame = data["arbiter"][6]
    assert len(tick6_frame["proposals"]) == 3
    assert len(tick6_frame["decisions"]) == 3


def test_2d_dashboard_routes_and_messages_unchanged(client: TestClient) -> None:
    """Verify 2D dashboard remains fully functional and unchanged."""
    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert "2 AM Collision Monitor" in resp_root.text

    resp_dash = client.get("/dashboard/")
    assert resp_dash.status_code == 200
    assert "2 AM Collision Monitor" in resp_dash.text

    resp_app = client.get("/dashboard/app.js")
    assert resp_app.status_code == 200
