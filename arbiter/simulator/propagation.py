"""Cost-231 Hata Urban propagation model and 3GPP 3D antenna pattern."""
import math
from typing import Sequence, Tuple, Union, overload
import numpy as np
from numpy.typing import NDArray

from arbiter.simulator.cell import CellConfiguration


class Cost231PropagationModel:
    """Analytical Cost-231 Hata urban radio propagation and SINR calculator."""

    CARRIER_FREQ_MHZ: float = 2100.0
    DEFAULT_TX_H: float = 30.0
    DEFAULT_RX_H: float = 1.5
    CORRECTION_FACTOR_CM: float = 3.0  # Metropolitan urban center
    NOISE_FLOOR_DBM: float = -104.0
    N0_WATTS: float = float(10.0 ** (-104.0 / 10.0) * 1e-3)
    ANTENNA_MAX_GAIN_DBI: float = 15.0
    MIN_DISTANCE_M: float = 10.0

    def __init__(
        self,
        carrier_freq_mhz: float = 2100.0,
        tx_h: float = 30.0,
        rx_h: float = 1.5,
        cm: float = 3.0,
    ) -> None:
        self.freq_mhz = carrier_freq_mhz
        self.tx_h = tx_h
        self.rx_h = rx_h
        self.cm = cm

    @overload
    def compute_path_loss(
        self,
        dist_m: float,
        freq_mhz: float = ...,
        tx_h: float = ...,
        rx_h: float = ...,
    ) -> float: ...

    @overload
    def compute_path_loss(
        self,
        dist_m: NDArray[np.float64],
        freq_mhz: float = ...,
        tx_h: float = ...,
        rx_h: float = ...,
    ) -> NDArray[np.float64]: ...

    def compute_path_loss(
        self,
        dist_m: Union[float, NDArray[np.float64]],
        freq_mhz: float = 2100.0,
        tx_h: float = 30.0,
        rx_h: float = 1.5,
    ) -> Union[float, NDArray[np.float64]]:
        """Compute Cost-231 Urban Path Loss in dB over distance d (meters)."""
        fc = float(freq_mhz)
        h_tx = float(tx_h)
        h_rx = float(rx_h)

        log_fc = math.log10(fc)
        log_htx = math.log10(h_tx)

        # a(h_rx) mobile antenna height correction factor
        a_hrx = (1.1 * log_fc - 0.7) * h_rx - (1.56 * log_fc - 0.8)

        # Base intercept
        base_term = 46.3 + 33.9 * log_fc - 13.82 * log_htx - a_hrx + self.cm
        dist_slope = 44.9 - 6.55 * log_htx

        if isinstance(dist_m, (int, float)):
            d_eff = max(self.MIN_DISTANCE_M, float(dist_m))
            log_d_km = math.log10(d_eff / 1000.0)
            return float(base_term + dist_slope * log_d_km)
        else:
            d_eff_arr = np.maximum(dist_m, self.MIN_DISTANCE_M)
            log_d_km_arr = np.log10(d_eff_arr / 1000.0)
            res = base_term + dist_slope * log_d_km_arr
            return np.asarray(res, dtype=np.float64)

    @overload
    def compute_antenna_gain(
        self,
        theta_deg: float,
        phi_deg: float = ...,
        tilt_deg: float = ...,
    ) -> float: ...

    @overload
    def compute_antenna_gain(
        self,
        theta_deg: NDArray[np.float64],
        phi_deg: Union[float, NDArray[np.float64]] = ...,
        tilt_deg: Union[float, NDArray[np.float64]] = ...,
    ) -> NDArray[np.float64]: ...

    def compute_antenna_gain(
        self,
        theta_deg: Union[float, NDArray[np.float64]],
        phi_deg: Union[float, NDArray[np.float64]] = 0.0,
        tilt_deg: Union[float, NDArray[np.float64]] = 6.0,
    ) -> Union[float, NDArray[np.float64]]:
        """Calculate 3GPP-style 3D directional antenna gain (dBi).

        Vertical: A_v = -min(12 * ((theta - tilt) / 10)^2, 30)
        Horizontal: A_h = -min(12 * (phi / 65)^2, 25)
        G = 15 + (-min(-(A_v + A_h), 30))
        """
        is_scalar = (
            isinstance(theta_deg, (int, float))
            and isinstance(phi_deg, (int, float))
            and isinstance(tilt_deg, (int, float))
        )
        if is_scalar:
            t = float(theta_deg)
            p = float(phi_deg)
            tilt = float(tilt_deg)

            a_v = -min(12.0 * ((t - tilt) / 10.0) ** 2, 30.0)
            a_h = -min(12.0 * (p / 65.0) ** 2, 25.0)
            combined_loss = -min(-(a_v + a_h), 30.0)
            return float(self.ANTENNA_MAX_GAIN_DBI + combined_loss)
        else:
            theta_arr = np.asarray(theta_deg, dtype=np.float64)
            phi_arr = np.asarray(phi_deg, dtype=np.float64)
            tilt_arr = np.asarray(tilt_deg, dtype=np.float64)

            a_v_arr = -np.minimum(
                12.0 * ((theta_arr - tilt_arr) / 10.0) ** 2, 30.0
            )
            a_h_arr = -np.minimum(12.0 * (phi_arr / 65.0) ** 2, 25.0)
            combined_loss_arr = -np.minimum(-(a_v_arr + a_h_arr), 30.0)
            res = self.ANTENNA_MAX_GAIN_DBI + combined_loss_arr
            return np.asarray(res, dtype=np.float64)

    def compute_rx_power_watts(
        self,
        ue_x: float,
        ue_y: float,
        cell: CellConfiguration,
    ) -> float:
        """Received power in Watts. Strictly 0.0 W if sleeping."""
        if cell.is_sleeping:
            return 0.0

        dx = float(ue_x - cell.x_coord)
        dy = float(ue_y - cell.y_coord)
        d_horizontal = math.sqrt(dx * dx + dy * dy)
        delta_h = float(self.tx_h - self.rx_h)
        d_3d = math.sqrt(d_horizontal * d_horizontal + delta_h * delta_h)

        theta_deg = math.degrees(math.atan2(delta_h, max(d_horizontal, 1.0)))
        gain_dbi = self.compute_antenna_gain(
            theta_deg=theta_deg,
            phi_deg=0.0,
            tilt_deg=cell.antenna_tilt_deg,
        )
        pl_db = self.compute_path_loss(
            dist_m=d_3d,
            freq_mhz=cell.carrier_freq_mhz,
            tx_h=self.tx_h,
            rx_h=self.rx_h,
        )

        rx_power_dbm = cell.current_tx_power_dbm + gain_dbi - pl_db
        rx_power_watts = float(10.0 ** (rx_power_dbm / 10.0) * 1e-3)
        return rx_power_watts

    def compute_rx_matrix(
        self,
        ue_coords: NDArray[np.float64],
        cells: Sequence[CellConfiguration],
    ) -> NDArray[np.float64]:
        """Compute received power matrix of shape (U, C) in Watts.

        Zeroes for sleeping cells. Fully vectorized across all users.
        """
        u_count = len(ue_coords)
        c_count = len(cells)
        if u_count == 0 or c_count == 0:
            return np.zeros((u_count, c_count), dtype=np.float64)

        # ue_coords: (U, 2)
        ue_x = ue_coords[:, 0:1]  # (U, 1)
        ue_y = ue_coords[:, 1:2]  # (U, 1)

        # Extract cell arrays of shape (1, C)
        cell_x = np.array([[c.x_coord for c in cells]], dtype=np.float64)
        cell_y = np.array([[c.y_coord for c in cells]], dtype=np.float64)
        cell_tx_dbm = np.array(
            [[c.current_tx_power_dbm for c in cells]], dtype=np.float64)
        cell_tilt = np.array(
            [[c.antenna_tilt_deg for c in cells]], dtype=np.float64)
        cell_freq = np.array(
            [[c.carrier_freq_mhz for c in cells]], dtype=np.float64)
        cell_sleeping = np.array([c.is_sleeping for c in cells], dtype=bool)

        dx = ue_x - cell_x  # (U, C)
        dy = ue_y - cell_y  # (U, C)
        d_horizontal = np.sqrt(dx * dx + dy * dy)
        delta_h = self.tx_h - self.rx_h
        d_3d = np.sqrt(d_horizontal * d_horizontal + delta_h * delta_h)

        # Elevation angle below horizon (degrees)
        theta_deg = np.degrees(np.arctan2(
            delta_h, np.maximum(d_horizontal, 1.0)))

        # Antenna gain (U, C)
        g_dbi = self.compute_antenna_gain(
            theta_deg=theta_deg, phi_deg=0.0, tilt_deg=cell_tilt)

        # Path loss (U, C)
        # Vectorized formula
        log_fc = np.log10(cell_freq)
        log_htx = math.log10(self.tx_h)
        a_hrx = (1.1 * log_fc - 0.7) * self.rx_h - (1.56 * log_fc - 0.8)
        base_term = 46.3 + 33.9 * log_fc - 13.82 * log_htx - a_hrx + self.cm
        dist_slope = 44.9 - 6.55 * log_htx

        d_eff = np.maximum(d_3d, self.MIN_DISTANCE_M)
        pl_db = base_term + dist_slope * np.log10(d_eff / 1000.0)

        rx_power_dbm = cell_tx_dbm + g_dbi - pl_db
        rx_power_watts = (10.0 ** (rx_power_dbm / 10.0)) * 1e-3

        # Zero out sleeping cells
        rx_power_watts[:, cell_sleeping] = 0.0

        return np.asarray(rx_power_watts, dtype=np.float64)

    def best_server(
        self,
        ue_coords: NDArray[np.float64],
        cells: Sequence[CellConfiguration],
    ) -> NDArray[np.int64]:
        """Return index of best server cell for each UE."""
        u_count = len(ue_coords)
        if u_count == 0:
            return np.zeros(0, dtype=np.int64)

        rx_matrix = self.compute_rx_matrix(ue_coords, cells)
        best_indices = np.argmax(rx_matrix, axis=1)
        return np.asarray(best_indices, dtype=np.int64)

    def compute_sinr_vector(
        self,
        ue_coords: NDArray[np.float64],
        active_cells: Sequence[CellConfiguration],
        serving_idx: NDArray[np.int64],
    ) -> Tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
        """Calculate SINR (dB), received power (dBm), and interference (dBm).

        If serving cell is asleep, returns safe defaults (-100 dB).
        """
        u_count = len(ue_coords)
        if u_count == 0:
            return (
                np.zeros(0, dtype=np.float64),
                np.zeros(0, dtype=np.float64),
                np.zeros(0, dtype=np.float64),
            )

        rx_matrix = self.compute_rx_matrix(
            ue_coords, active_cells)  # (U, C) in Watts
        row_indices = np.arange(u_count)
        p_rx_serving = rx_matrix[row_indices, serving_idx]  # (U,)

        total_rx = np.sum(rx_matrix, axis=1)  # (U,)
        p_interference = np.maximum(0.0, total_rx - p_rx_serving)  # (U,)

        # Denominator: thermal noise + interference
        denominator = self.N0_WATTS + p_interference

        # Safe SINR calculation
        valid_mask = p_rx_serving > 0.0
        sinr_db = np.full(u_count, -100.0, dtype=np.float64)
        rx_dbm = np.full(u_count, -140.0, dtype=np.float64)
        interf_dbm = np.full(u_count, -140.0, dtype=np.float64)

        if np.any(valid_mask):
            sinr_linear = p_rx_serving[valid_mask] / denominator[valid_mask]
            sinr_db[valid_mask] = 10.0 * np.log10(sinr_linear)
            rx_dbm[valid_mask] = 10.0 * \
                np.log10(p_rx_serving[valid_mask] / 1e-3)

        interf_valid = p_interference > 0.0
        if np.any(interf_valid):
            interf_dbm[interf_valid] = 10.0 * \
                np.log10(p_interference[interf_valid] / 1e-3)

        return (
            np.asarray(sinr_db, dtype=np.float64),
            np.asarray(rx_dbm, dtype=np.float64),
            np.asarray(interf_dbm, dtype=np.float64),
        )
