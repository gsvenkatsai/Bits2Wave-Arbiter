"""Tests for the Project ARBITER v3 3D Visualization Dashboard.

Validates:
- /dashboard/3d route returns HTTP 200
- 3D static assets (HTML, CSS, JS, vendor libraries) are properly served
- Three.js stable build and MIT license are vendored locally (zero CDN)
- Offline replay data fixture is available and contains valid frame records
- 2D dashboard remains intact with zero regressions or routing collisions
- DataLayer Node.js test suite runs and passes cleanly
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient

from arbiter.api.server import app

_DASHBOARD_3D_DIR = (
    Path(__file__).resolve().parents[2] / "arbiter" / "dashboard" / "3d"
)


def test_dashboard_3d_route_returns_200() -> None:
    """The /dashboard/3d route returns HTTP 200 and serves HTML."""
    client = TestClient(app)

    response = client.get("/dashboard/3d")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "PROJECT ARBITER" in response.text
    assert "3D Digital Twin" in response.text
    assert "canvas-container" in response.text


def test_dashboard_3d_trailing_slash_and_index() -> None:
    """Both /dashboard/3d/ and /dashboard/3d/index.html return HTTP 200."""
    client = TestClient(app)

    r_slash = client.get("/dashboard/3d/")
    assert r_slash.status_code == 200
    assert "canvas-container" in r_slash.text

    r_index = client.get("/dashboard/3d/index.html")
    assert r_index.status_code == 200
    assert "canvas-container" in r_index.text


def test_threejs_vendor_files_exist_and_licensed() -> None:
    """Three.js r160.0 ESM, OrbitControls, and LICENSE exist locally."""
    vendor_dir = _DASHBOARD_3D_DIR / "vendor"
    assert vendor_dir.is_dir(), f"Vendor directory missing at {vendor_dir}"

    three_js = vendor_dir / "three.module.js"
    assert three_js.is_file(), "three.module.js missing"
    assert three_js.stat().st_size > 500_000, "three.module.js too small"

    license_file = vendor_dir / "LICENSE"
    assert license_file.is_file(), "LICENSE file missing"
    content = license_file.read_text(encoding="utf-8")
    assert "MIT License" in content
    assert "three.js authors" in content

    orbit_controls = vendor_dir / "addons" / "controls" / "OrbitControls.js"
    assert orbit_controls.is_file(), "OrbitControls.js missing"
    assert orbit_controls.stat().st_size > 10_000


def test_offline_recorded_replay_data_valid() -> None:
    """Offline recorded replay fixture exists and conforms to frame format."""
    data_file = _DASHBOARD_3D_DIR / "data" / "frames_2am.json"
    assert data_file.is_file(), f"Replay data missing at {data_file}"

    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "arbiter" in data
    assert "counterfactual" in data
    assert len(data["arbiter"]) > 0
    assert len(data["counterfactual"]) > 0

    frame0 = data["arbiter"][0]
    assert frame0["type"] == "frame"
    assert "cells" in frame0
    assert "users" in frame0
    assert "kpis" in frame0
    assert len(frame0["cells"]) >= 5


def test_no_collision_with_2d_dashboard() -> None:
    """The 2D dashboard remains fully functional at / and /dashboard/."""
    client = TestClient(app)

    r_2d_root = client.get("/")
    assert r_2d_root.status_code == 200
    assert "2 AM Collision Monitor" in r_2d_root.text

    r_2d_prefix = client.get("/dashboard/")
    assert r_2d_prefix.status_code == 200
    assert "2 AM Collision Monitor" in r_2d_prefix.text

    r_2d_app = client.get("/dashboard/app.js")
    assert r_2d_app.status_code == 200


def test_node_data_layer_unit_tests() -> None:
    """Execute node --test tests/branch4/test_data_layer.js."""
    test_file = (
        Path(__file__).resolve().parents[1]
        / "branch4"
        / "test_data_layer.js"
    )
    assert test_file.is_file()

    result = subprocess.run(
        ["node", "--test", str(test_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Node tests failed:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def test_node_arbitration_mapper_unit_tests() -> None:
    """Execute node --test tests/branch4/test_arbitration_mapper.js."""
    test_file = (
        Path(__file__).resolve().parents[1]
        / "branch4"
        / "test_arbitration_mapper.js"
    )
    assert test_file.is_file()

    result = subprocess.run(
        ["node", "--test", str(test_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Node tests failed:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def test_arbitration_layer_hud_elements() -> None:
    """The 3D dashboard includes Event Log and Story Mode elements."""
    client = TestClient(app)
    response = client.get("/dashboard/3d")
    assert response.status_code == 200
    assert "event-log-list" in response.text
    assert "story-mode-banner" in response.text
    assert "btn-story-mode" in response.text
    assert "ARBITRATION VERDICTS" in response.text


def test_node_ghost_world_unit_tests() -> None:
    """Execute node --test tests/branch4/test_ghost_world.js."""
    test_file = (
        Path(__file__).resolve().parents[1]
        / "branch4"
        / "test_ghost_world.js"
    )
    assert test_file.is_file()

    result = subprocess.run(
        ["node", "--test", str(test_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Node tests failed:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def test_ghost_world_and_counterfactual_hud_elements() -> None:
    """The 3D dashboard includes Ghost World, Scrubber Jump, and Risk Dial."""
    client = TestClient(app)
    response = client.get("/dashboard/3d")
    assert response.status_code == 200
    assert "btn-ghost-toggle" in response.text
    assert "btn-view-mode" in response.text
    assert "btn-jump-collision" in response.text
    assert "risk-dial-badge" in response.text
    assert "counterfactual-delta-panel" in response.text
    assert "degradation-banner" in response.text


def test_offline_counterfactual_recorded_data_valid() -> None:
    """Counterfactual 2 AM fixture exists and conforms to contract."""
    cf_file = _DASHBOARD_3D_DIR / "data" / "counterfactual_2am.json"
    assert cf_file.is_file(), f"Counterfactual fixture missing at {cf_file}"

    with open(cf_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "arbiter" in data
    assert "counterfactual" in data
    assert "summary" in data
    assert len(data["arbiter"]) == 20
    assert len(data["counterfactual"]) == 20

    summary = data["summary"]
    assert "with_arbiter" in summary
    assert "without_arbiter" in summary
    assert summary["with_arbiter"]["dropped_users"] == 0
    assert summary["without_arbiter"]["dropped_users"] == 14
    assert "explanation" in summary
    assert "would have dropped 14 more users" in summary["explanation"]

    # Cell B should be sleeping in counterfactual world
    cf_frame = data["counterfactual"][0]
    cell_b = next((c for c in cf_frame["cells"] if c["id"] == "cell_B"), None)
    assert cell_b is not None
    assert cell_b["is_sleeping"] is True


def test_2d_3d_bidirectional_toggle_links() -> None:
    """Both dashboards provide reciprocal switching links."""
    client = TestClient(app)

    # 2D -> 3D link
    r_2d = client.get("/dashboard/")
    assert r_2d.status_code == 200
    assert 'id="btnToggle3D"' in r_2d.text
    assert 'href="/dashboard/3d"' in r_2d.text

    # 3D -> 2D link
    r_3d = client.get("/dashboard/3d")
    assert r_3d.status_code == 200
    assert 'id="btn-toggle-2d"' in r_3d.text
    assert 'href="/dashboard/"' in r_3d.text


def test_clarity_robustness_and_side_panel_elements() -> None:
    """Validate NOC side panel, help modal, camera presets, and banners."""
    client = TestClient(app)
    response = client.get("/dashboard/3d")
    assert response.status_code == 200

    # Provenance badge & banners
    assert "provenance-badge" in response.text
    assert "disconnected-banner" in response.text

    # Camera presets
    assert "btn-cam-1" in response.text
    assert "btn-cam-2" in response.text

    # Help modal & shortcuts
    assert "help-modal" in response.text
    assert "shortcuts-grid" in response.text

    # NOC side panel diagnostics, optimality bars, & impact card
    assert "noc-side-panel" in response.text
    assert "side-decisions-list" in response.text
    assert "Offline Optimality Benchmark" in response.text
    assert "Environmental &amp; ESG Impact" in response.text
    assert "Fleet CO2 Extrapolated" in response.text


def test_offline_check_script_passes() -> None:
    """Execute scripts/check_offline_3d.py to verify 0 external URLs."""
    import sys

    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "check_offline_3d.py"
    )
    assert script_path.is_file(), f"Scanner script missing at {script_path}"

    res = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"Offline scanner failed with code {res.returncode}:\n{res.stdout}"
    )
    assert "100% offline compliant" in res.stdout
