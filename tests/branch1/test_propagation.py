"""Unit tests for Cost-231 Hata propagation model and antenna patterns."""
import numpy as np
from arbiter.simulator.cell import CellConfiguration
from arbiter.simulator.propagation import Cost231PropagationModel


def test_cost231_path_loss_nominal() -> None:
    model = Cost231PropagationModel()
    # At 500 meters, 2100 MHz, urban macro
    pl = model.compute_path_loss(
        dist_m=500.0, freq_mhz=2100.0, tx_h=30.0, rx_h=1.5
    )
    assert 125.0 <= pl <= 136.0, f"Path loss {pl} dB out of bounds"


def test_sinr_sleep_cell_zero_power() -> None:
    model = Cost231PropagationModel()
    active_cell = CellConfiguration(
        cell_id="c1", x_coord=0.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=False,
    )
    sleeping_cell = CellConfiguration(
        cell_id="c2", x_coord=500.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=True,
    )

    p_rx_active = model.compute_rx_power_watts(
        ue_x=100.0, ue_y=0.0, cell=active_cell
    )
    p_rx_sleeping = model.compute_rx_power_watts(
        ue_x=400.0, ue_y=0.0, cell=sleeping_cell
    )

    assert p_rx_active > 0.0
    assert p_rx_sleeping == 0.0, "Sleeping cell must radiate 0.0 W"


def test_path_loss_monotonicity() -> None:
    model = Cost231PropagationModel()
    distances = np.array([50.0, 100.0, 200.0, 500.0, 1000.0], dtype=np.float64)
    pl_arr = model.compute_path_loss(dist_m=distances, freq_mhz=2100.0)

    # Check strictly increasing
    assert np.all(np.diff(pl_arr) > 0.0), "Path loss must increase with dist"


def test_raising_interferer_power_lowers_sinr() -> None:
    model = Cost231PropagationModel()
    serving = CellConfiguration(
        cell_id="c1", x_coord=0.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=False,
    )
    interferer_low = CellConfiguration(
        cell_id="c2", x_coord=500.0, y_coord=0.0,
        current_tx_power_dbm=30.0, is_sleeping=False,
    )
    interferer_high = CellConfiguration(
        cell_id="c2", x_coord=500.0, y_coord=0.0,
        current_tx_power_dbm=46.0, is_sleeping=False,
    )

    ue_coords = np.array([[200.0, 0.0]], dtype=np.float64)
    serving_idx = np.array([0], dtype=np.int64)

    sinr_low, _, _ = model.compute_sinr_vector(
        ue_coords, [serving, interferer_low], serving_idx
    )
    sinr_high, _, _ = model.compute_sinr_vector(
        ue_coords, [serving, interferer_high], serving_idx
    )

    assert sinr_low[0] > sinr_high[0], "Higher interferer power lowers SINR"


def test_sleeping_interferer_raises_sinr() -> None:
    model = Cost231PropagationModel()
    serving = CellConfiguration(
        cell_id="c1", x_coord=0.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=False,
    )
    interferer_active = CellConfiguration(
        cell_id="c2", x_coord=500.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=False,
    )
    interferer_sleeping = CellConfiguration(
        cell_id="c2", x_coord=500.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=True,
    )

    ue_coords = np.array([[250.0, 0.0]], dtype=np.float64)
    serving_idx = np.array([0], dtype=np.int64)

    sinr_active, _, _ = model.compute_sinr_vector(
        ue_coords, [serving, interferer_active], serving_idx
    )
    sinr_sleeping, _, _ = model.compute_sinr_vector(
        ue_coords, [serving, interferer_sleeping], serving_idx
    )

    assert sinr_sleeping[0] > sinr_active[0], "Sleeping interferer raises SINR"


def test_antenna_gain_matches_tilt() -> None:
    model = Cost231PropagationModel()
    g_matched = model.compute_antenna_gain(
        theta_deg=6.0, phi_deg=0.0, tilt_deg=6.0
    )
    g_unmatched = model.compute_antenna_gain(
        theta_deg=25.0, phi_deg=0.0, tilt_deg=6.0
    )

    assert g_matched == 15.0
    assert g_unmatched < g_matched


def test_positive_sinr_near_serving_cell() -> None:
    model = Cost231PropagationModel()
    serving = CellConfiguration(
        cell_id="c1", x_coord=0.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=False,
    )
    interferer = CellConfiguration(
        cell_id="c2", x_coord=500.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=False,
    )

    # UE at (100, 0), serving at (0,0) (dist 100m)
    ue_coords = np.array([[100.0, 0.0]], dtype=np.float64)
    best_idx = model.best_server(ue_coords, [serving, interferer])
    assert best_idx[0] == 0, "Serving cell 0 should be best server"

    sinr_db, rx_dbm, interf_dbm = model.compute_sinr_vector(
        ue_coords, [serving, interferer], best_idx
    )
    assert sinr_db[0] > 0.0, f"SINR should be positive, got {sinr_db[0]}"
    assert rx_dbm[0] > interf_dbm[0]


def test_edge_cases_empty_or_sleeping_serving() -> None:
    model = Cost231PropagationModel()
    empty_coords = np.zeros((0, 2), dtype=np.float64)
    rx_mat = model.compute_rx_matrix(empty_coords, [])
    assert rx_mat.shape == (0, 0)

    s, r, i = model.compute_sinr_vector(
        empty_coords, [], np.zeros(0, dtype=np.int64)
    )
    assert len(s) == 0 and len(r) == 0 and len(i) == 0

    best = model.best_server(empty_coords, [])
    assert len(best) == 0

    sleeping = CellConfiguration(
        cell_id="c1", x_coord=0.0, y_coord=0.0,
        current_tx_power_dbm=43.0, is_sleeping=True,
    )
    ue_coords = np.array([[100.0, 0.0]], dtype=np.float64)
    sinr, _, _ = model.compute_sinr_vector(
        ue_coords, [sleeping], np.array([0], dtype=np.int64)
    )
    assert sinr[0] == -100.0
