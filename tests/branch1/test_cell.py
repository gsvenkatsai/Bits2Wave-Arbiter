"""Unit tests for CellConfiguration, UserEquipment, and CellEntity."""
from arbiter.simulator.cell import CellConfiguration, CellEntity, UserEquipment


def test_cell_configuration_defaults() -> None:
    cfg = CellConfiguration(cell_id="c1", x_coord=10.0, y_coord=20.0)
    assert cfg.carrier_freq_mhz == 2100.0
    assert cfg.max_tx_power_dbm == 46.0
    assert cfg.min_tx_power_dbm == 30.0
    assert cfg.current_tx_power_dbm == 43.0
    assert cfg.antenna_tilt_deg == 6.0
    assert cfg.is_sleeping is False
    assert cfg.neighbor_cell_ids == []


def test_user_equipment_model() -> None:
    ue = UserEquipment(ue_id="ue_test", x_coord=0.0,
                       y_coord=0.0, attached_cell_id="c1")
    assert ue.ue_id == "ue_test"
    assert ue.rx_power_dbm == -100.0
    assert ue.required_rate_mbps == 2.0
    assert ue.is_dropped is False


def test_cell_entity_properties_and_kwarg_init() -> None:
    entity = CellEntity(
        cell_id="c_direct",
        x_coord=100.0,
        y_coord=-50.0,
        carrier_freq_mhz=2100.0,
        current_tx_power_dbm=40.0,
        antenna_tilt_deg=8.0,
        neighbor_cell_ids=["c_other"],
    )
    assert entity.cell_id == "c_direct"
    assert entity.x_coord == 100.0
    assert entity.y_coord == -50.0
    assert entity.carrier_freq_mhz == 2100.0
    assert entity.current_tx_power_dbm == 40.0
    assert entity.antenna_tilt_deg == 8.0
    assert entity.neighbor_cell_ids == ["c_other"]
    assert entity.is_sleeping is False


def test_cell_entity_mutations_and_energy() -> None:
    cfg = CellConfiguration(cell_id="c_mut", x_coord=0.0, y_coord=0.0)
    entity = CellEntity(config=cfg)

    # Power clamping
    entity.set_tx_power(55.0)
    assert entity.current_tx_power_dbm == 46.0
    entity.set_tx_power(20.0)
    assert entity.current_tx_power_dbm == 30.0

    # Tilt clamping
    entity.set_tilt(22.0)
    assert entity.antenna_tilt_deg == 15.0
    entity.set_tilt(-5.0)
    assert entity.antenna_tilt_deg == 0.0

    # Energy calculations
    entity.set_tx_power(43.0)
    assert 19.9 <= entity.effective_tx_power_w() <= 20.0
    assert 419.0 <= entity.energy_consumption_w() <= 420.0

    # Sleep behavior
    entity.set_sleep(True)
    assert entity.is_sleeping is True
    assert entity.effective_tx_power_w() == 0.0
    assert entity.energy_consumption_w() == 0.0


def test_cell_entity_from_fixture_and_to_state() -> None:
    fixture_dict = {
        "cell_id": "cell_A",
        "x": 100.0,
        "y": 200.0,
        "tx_power_dbm": 43.0,
        "tilt_deg": 6.0,
        "is_sleeping": False,
        "neighbors": ["cell_B"],
    }
    entity = CellEntity.from_fixture_dict(fixture_dict)
    assert entity.cell_id == "cell_A"
    assert entity.x_coord == 100.0
    assert entity.y_coord == 200.0
    assert entity.current_tx_power_dbm == 43.0
    assert entity.antenna_tilt_deg == 6.0
    assert entity.neighbor_cell_ids == ["cell_B"]

    state = entity.to_cell_state(active_users=12, load_ratio=0.8)
    assert state.cell_id == "cell_A"
    assert state.active_users == 12
    assert state.load_ratio == 0.8
    assert state.current_tx_power_dbm == 43.0
