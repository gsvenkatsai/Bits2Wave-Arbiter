"""Tests for SimulationEngine native frame() and cell_states() (C2)."""
from __future__ import annotations

import pytest
from arbiter.simulator.engine import SimulationEngine
from arbiter.telemetry.frame import extract_engine_frame_components


def test_native_frame_keys_and_contract() -> None:
    """Verify engine.frame() produces all top-level frame keys."""
    engine = SimulationEngine(seed=42)
    fr = engine.frame()

    expected_keys = {
        "type",
        "tick",
        "sim_time_ms",
        "scenario",
        "world",
        "cells",
        "users",
        "proposals",
        "decisions",
        "conflicts",
        "plans",
        "twin",
        "kpis",
        "meta",
    }
    assert expected_keys.issubset(fr.keys())
    assert fr["type"] == "frame"
    assert fr["tick"] == 0
    assert fr["sim_time_ms"] == engine.simulated_epoch_ms
    assert fr["meta"] == {"twin_kind": "real", "sink": "mock"}


def test_native_cell_states_contract() -> None:
    """Verify engine.cell_states() produces exact live per-cell contract."""
    engine = SimulationEngine(seed=42)
    for _ in range(3):
        engine.step()

    states = engine.cell_states()
    assert len(states) == 5

    required_fields = {
        "id",
        "x",
        "y",
        "tx_power_dbm",
        "tilt_deg",
        "is_sleeping",
        "active_users",
        "load_ratio",
        "energy_w",
        "neighbors",
    }
    for cell_state in states:
        assert set(cell_state.keys()) == required_fields
        assert isinstance(cell_state["id"], str)
        assert isinstance(cell_state["x"], float)
        assert isinstance(cell_state["y"], float)
        assert isinstance(cell_state["tx_power_dbm"], float)
        assert isinstance(cell_state["tilt_deg"], float)
        assert isinstance(cell_state["is_sleeping"], bool)
        assert isinstance(cell_state["active_users"], int)
        assert isinstance(cell_state["load_ratio"], float)
        assert isinstance(cell_state["energy_w"], float)
        assert isinstance(cell_state["neighbors"], list)
        assert len(cell_state["neighbors"]) > 0


def test_native_frame_legacy_equivalence() -> None:
    """Compare native engine.frame() with extract_engine_frame_components."""
    engine = SimulationEngine(seed=101)
    for _ in range(5):
        engine.step()

    native = engine.frame()
    legacy = extract_engine_frame_components(engine)

    # Check cells component
    assert len(native["cells"]) == len(legacy["cells"])
    for n_cell, l_cell in zip(native["cells"], legacy["cells"]):
        assert set(n_cell.keys()) == set(l_cell.keys())
        assert n_cell["id"] == l_cell["id"]
        assert pytest.approx(n_cell["x"], abs=0.1) == l_cell["x"]
        assert pytest.approx(n_cell["y"], abs=0.1) == l_cell["y"]
        assert n_cell["tx_power_dbm"] == l_cell["tx_power_dbm"]
        assert n_cell["tilt_deg"] == l_cell["tilt_deg"]
        assert n_cell["is_sleeping"] == l_cell["is_sleeping"]
        assert n_cell["active_users"] == l_cell["active_users"]
        assert pytest.approx(n_cell["load_ratio"], abs=1e-4) == (
            l_cell["load_ratio"]
        )
        assert pytest.approx(n_cell["energy_w"], abs=0.1) == (
            l_cell["energy_w"]
        )

    # Check users component
    assert len(native["users"]) == len(legacy["users"])
    for n_ue, l_ue in zip(native["users"], legacy["users"]):
        assert set(n_ue.keys()) == set(l_ue.keys())
        assert n_ue["id"] == l_ue["id"]
        assert n_ue["cell"] == l_ue["cell"]
        assert pytest.approx(n_ue["x"], abs=0.01) == l_ue["x"]
        assert pytest.approx(n_ue["y"], abs=0.01) == l_ue["y"]
        assert pytest.approx(n_ue["sinr_db"], abs=0.01) == l_ue["sinr_db"]
        assert n_ue["dropped"] == l_ue["dropped"]

    # Check KPIs component
    assert set(native["kpis"].keys()) == set(legacy["kpis"].keys())
    assert native["kpis"]["dropped_users"] == (
        legacy["kpis"]["dropped_users"]
    )
    assert pytest.approx(native["kpis"]["mean_sinr_db"], abs=0.01) == (
        legacy["kpis"]["mean_sinr_db"]
    )
    assert pytest.approx(native["kpis"]["energy_w"], abs=0.1) == (
        legacy["kpis"]["energy_w"]
    )


def test_frame_custom_meta_and_sink() -> None:
    """Verify meta reflects real queue sink and custom overrides."""
    engine_real = SimulationEngine(use_real_queue=True, twin_kind="scripted")
    fr_real = engine_real.frame()
    assert fr_real["meta"]["sink"] == "queue"
    assert fr_real["meta"]["twin_kind"] == "scripted"

    # Custom meta override
    fr_override = engine_real.frame(meta={"custom_key": "val123"})
    assert fr_override["meta"]["sink"] == "queue"
    assert fr_override["meta"]["twin_kind"] == "scripted"
    assert fr_override["meta"]["custom_key"] == "val123"
