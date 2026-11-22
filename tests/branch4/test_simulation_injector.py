"""Unit tests for Scenario Injector & Simulation Controls APIs."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from arbiter.api.server import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_sim_control_state_and_commands(client: TestClient) -> None:
    # 1. Get initial control state
    resp = client.get("/api/v1/sim/control")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "speed" in data
    assert "arbiter_enabled" in data

    # 2. Pause
    resp = client.post("/api/v1/sim/control", json={"command": "pause"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    # 3. Change speed
    resp = client.post("/api/v1/sim/control", json={"command": "speed", "speed": 2.0})
    assert resp.status_code == 200
    assert resp.json()["speed"] == 2.0

    # 4. Resume play
    resp = client.post("/api/v1/sim/control", json={"command": "play"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "playing"


def test_sim_mode_toggle(client: TestClient) -> None:
    # Toggle ARBITER off
    resp = client.post("/api/v1/sim/mode", json={"arbiter_enabled": False})
    assert resp.status_code == 200
    assert resp.json()["arbiter_enabled"] is False
    assert resp.json()["mode"] == "RAW_UNCOORDINATED"

    # Toggle ARBITER on
    resp = client.post("/api/v1/sim/mode", json={"arbiter_enabled": True})
    assert resp.status_code == 200
    assert resp.json()["arbiter_enabled"] is True
    assert resp.json()["mode"] == "ARBITER"


def test_topologies_and_dynamic_tower_management(client: TestClient) -> None:
    # 1. Get available topologies
    resp = client.get("/api/v1/sim/topologies")
    assert resp.status_code == 200
    data = resp.json()
    assert "presets" in data
    assert set(data["presets"]) == {"5_CELL_CORE", "7_CELL", "19_CELL", "BENGALURU_DEMO"}

    # 2. Select 7_CELL topology
    resp = client.post("/api/v1/sim/topology", json={"action": "select", "preset": "7_CELL"})
    assert resp.status_code == 200
    assert resp.json()["active_topology"] == "7_CELL"

    # 3. Add custom tower
    resp = client.post(
        "/api/v1/sim/topology",
        json={"action": "add", "cell_id": "cell_test_tower", "x": 100.0, "y": 200.0, "isd_m": 500.0},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "added"
    assert resp.json()["cell_id"] == "cell_test_tower"

    # 4. Remove custom tower
    resp = client.post(
        "/api/v1/sim/topology",
        json={"action": "remove", "cell_id": "cell_test_tower"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"


def test_controller_multi_instance_registration(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/sim/controller-instance",
        json={
            "instance_id": "ES_north",
            "controller_type": "ENERGY_SAVING",
            "cell_scope": ["cell_C", "cell_D"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "registered"
    assert body["instance"]["instance_id"] == "ES_north"
    assert body["instance"]["cell_scope"] == ["cell_C", "cell_D"]


def test_sim_presets_catalog(client: TestClient) -> None:
    resp = client.get("/api/v1/sim/presets")
    assert resp.status_code == 200
    presets = resp.json()
    preset_ids = {p["id"] for p in presets}
    assert "2am_collision" in preset_ids
    assert "stadium_surge" in preset_ids
    assert "cell_outage" in preset_ids
    assert "emergency_preempt" in preset_ids
    assert "ping_pong" in preset_ids


def test_inject_canonical_2am_collision_with_arbiter_on(client: TestClient) -> None:
    client.post("/api/v1/sim/mode", json={"arbiter_enabled": True})
    resp = client.post("/api/v1/sim/inject", json={"preset": "2am_collision"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "injected"
    assert body["arbiter_enabled"] is True
    decisions = {d["target_cell_id"] + "_" + d["action"]: d for d in body["decisions"]}
    # Cell B sleep delayed
    assert "cell_B_DELAY" in decisions
    # Cell B offload modified
    assert "cell_B_MODIFY" in decisions
    assert decisions["cell_B_MODIFY"]["approved_value"] == pytest.approx(0.1528, abs=0.01)


def test_inject_canonical_2am_collision_with_arbiter_off(client: TestClient) -> None:
    # When ARBITER is turned off (uncoordinated raw mode), all proposals approved directly
    client.post("/api/v1/sim/mode", json={"arbiter_enabled": False})
    resp = client.post("/api/v1/sim/inject", json={"preset": "2am_collision"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "injected"
    assert body["arbiter_enabled"] is False
    for dec in body["decisions"]:
        assert dec["action"] == "APPROVE"
        assert dec["reason_code"] == "UNCOORDINATED_RAW_EXECUTION"

    # Reset back to ARBITER True
    client.post("/api/v1/sim/mode", json={"arbiter_enabled": True})


def test_inject_custom_proposal_and_twin_drift(client: TestClient) -> None:
    # 1. Custom proposal
    resp = client.post(
        "/api/v1/sim/inject",
        json={
            "type": "proposal",
            "controller": "ENERGY_SAVING",
            "target_cell": "cell_A",
            "parameter": "SLEEP_STATE",
            "requested_value": 1.0,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "injected"

    # 2. Digital Twin drift
    resp = client.post(
        "/api/v1/sim/inject",
        json={"type": "drift", "drift_scale": 2.2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "injected"
    assert "twin" in body
    assert body["twin"]["calibrated"] is False
