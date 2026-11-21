"""Unit tests for Milan traffic generation and Voronoi assignment."""
import math
from arbiter.simulator.cell import CellConfiguration, UserEquipment
from arbiter.simulator.traffic import (
    MilanTrafficGenerator,
    ScenarioType,
    TrafficDistributionModel,
    VoronoiUserMapper,
)


def test_pool_sizes_5_and_19_cells() -> None:
    model_5 = TrafficDistributionModel(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM)
    assert len(model_5.pool) == 70

    model_19 = TrafficDistributionModel(
        topology="19_CELL", scenario=ScenarioType.PEAK)
    assert len(model_19.pool) == 350


def test_active_count_scales_with_profile() -> None:
    # 2 AM scenario: active_ue_count = 54 (scale = 54/70)
    model = TrafficDistributionModel(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    # At tick 0, get_load_multiplier is 1.0
    active_users = model.get_active_users(tick_index=0)
    expected_count = round(70 * (54 / 70))
    assert len(active_users) == expected_count == 54


def test_reproducibility_with_same_seed() -> None:
    gen1 = MilanTrafficGenerator(scenario=ScenarioType.QUIET_2AM, seed=123)
    gen2 = MilanTrafficGenerator(scenario=ScenarioType.QUIET_2AM, seed=123)
    bounds = (-500.0, 500.0, -500.0, 500.0)

    users1 = gen1.spawn_users(10, bounds)
    users2 = gen2.spawn_users(10, bounds)

    for u1, u2 in zip(users1, users2):
        assert u1.x_coord == u2.x_coord
        assert u1.y_coord == u2.y_coord


def test_mobility_step_and_bounds() -> None:
    gen = MilanTrafficGenerator(scenario=ScenarioType.QUIET_2AM, seed=99)
    bounds = (-100.0, 100.0, -100.0, 100.0)
    users = [UserEquipment(ue_id="ue_0", x_coord=0.0,
                           y_coord=0.0, attached_cell_id="c1")]

    initial_x = users[0].x_coord
    initial_y = users[0].y_coord

    gen.step_mobility(users, bounds, speed_mps=1.2, dt_s=0.1)

    # Distance moved should be ~ 0.12 m
    dx = users[0].x_coord - initial_x
    dy = users[0].y_coord - initial_y
    dist = math.hypot(dx, dy)
    assert 0.10 <= dist <= 0.14, (
        f"Distance moved {dist} m out of expected 0.12 m"
    )

    # User placed right on border should stay clipped within bounds
    border_user = [UserEquipment(
        ue_id="ue_1", x_coord=100.0, y_coord=100.0, attached_cell_id="c1")]
    for _ in range(20):
        gen.step_mobility(border_user, bounds, speed_mps=10.0, dt_s=0.1)
        assert -100.0 <= border_user[0].x_coord <= 100.0
        assert -100.0 <= border_user[0].y_coord <= 100.0


def test_voronoi_nearest_cell_mapping() -> None:
    cells = {
        "cell_left": CellConfiguration(
            cell_id="cell_left", x_coord=-200.0, y_coord=0.0
        ),
        "cell_right": CellConfiguration(
            cell_id="cell_right", x_coord=200.0, y_coord=0.0
        ),
    }
    users = [
        UserEquipment(ue_id="u_l", x_coord=-150.0,
                      y_coord=10.0, attached_cell_id=""),
        UserEquipment(ue_id="u_r", x_coord=180.0,
                      y_coord=-5.0, attached_cell_id=""),
    ]

    VoronoiUserMapper.assign_nearest(users, cells)
    assert users[0].attached_cell_id == "cell_left"
    assert users[1].attached_cell_id == "cell_right"


def test_2am_collision_load_guarantee() -> None:
    # 2 AM scenario: cell_B load < 15% and cell_A load > 75%
    model = TrafficDistributionModel(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    active_users = model.get_active_users(tick_index=0)

    load_A = model.compute_cell_load("cell_A", active_users)
    load_B = model.compute_cell_load("cell_B", active_users)

    assert load_B < 0.15, f"Cell B load {load_B:.2%} must be < 15%"
    assert load_A > 0.75, f"Cell A load {load_A:.2%} must be > 75%"

    counts = {
        cid: sum(1 for u in active_users if u.attached_cell_id == cid)
        for cid in model.cells
    }
    assert counts["cell_A"] == 35, f"Cell A users: {counts['cell_A']}"
    assert counts["cell_B"] == 4, f"Cell B users: {counts['cell_B']}"
    for cid in ("cell_C", "cell_D", "cell_E"):
        assert 4 <= counts[cid] <= 6, f"{cid} users: {counts[cid]}"


def test_2am_baseline_sinr_and_drops() -> None:
    """Regression test asserting criterion (a): healthy baseline metrics."""
    from arbiter.simulator.engine import SimulationEngine
    import numpy as np

    engine = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    samples = engine.step()
    active_users = engine.traffic_model.get_active_users(engine.tick)

    # 0 dropped users
    total_drops = sum(s.dropped_users for s in samples)
    assert total_drops == 0, f"Drops must be 0, got {total_drops}"

    # Cluster mean SINR >= 8.0 dB
    mean_sinr = float(np.mean([u.sinr_db for u in active_users]))
    assert mean_sinr >= 8.0, f"SINR must be >= 8.0 dB, got {mean_sinr:.2f} dB"

    # All 5 cells active at 43.0 dBm
    assert len(engine.cells) == 5
    for cid, cell in engine.cells.items():
        assert not cell.is_sleeping, f"Cell {cid} should not be sleeping"
        assert cell.current_tx_power_dbm == 43.0


def test_scenario_type_parsing() -> None:
    assert ScenarioType.from_str("2am_collision") == ScenarioType.QUIET_2AM
    assert ScenarioType.from_str("PEAK") == ScenarioType.PEAK
    assert ScenarioType.from_str("SURGE") == ScenarioType.SURGE
    assert ScenarioType.from_str("PEAK_HOUR") == ScenarioType.PEAK

    try:
        ScenarioType.from_str("UNKNOWN_XYZ")
        assert False, "Should raise ValueError"
    except ValueError:
        pass


def test_traffic_distribution_model_step_and_empty_edge_cases() -> None:
    model = TrafficDistributionModel(
        topology="5_CELL_CORE", scenario=ScenarioType.SURGE, seed=77
    )
    # Step simulation mobility
    active = model.step(tick_index=1)
    assert len(active) > 0

    # Test edge cases
    gen = MilanTrafficGenerator(scenario=ScenarioType.PEAK)
    gen.step_mobility([], (-10.0, 10.0, -10.0, 10.0))
    VoronoiUserMapper.assign_nearest([], {})
    VoronoiUserMapper.assign_nearest(
        [UserEquipment(ue_id="u", x_coord=0, y_coord=0,
                       attached_cell_id="")], {}
    )
