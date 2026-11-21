"""Analytical Propagation and Radio Physics Chain for Project ARBITER.

Constraint C-2: Pure closed-form analytical equations in NumPy.
No machine learning, no neural networks, no black-box optimization.
SRS FR-5.1, FR-5.2, FR-5.3.
"""
from __future__ import annotations

import numpy as np
from typing import Dict, List, Optional, Set, Tuple

from arbiter.shared.constants import (
    MIN_COVERAGE_RATIO,
    MAX_DROP_RATE_RATIO,
    MIN_SINR_DB,
)
from arbiter.twin.schemas import (
    CandidateDelta,
    ClusterSnapshot,
    PredictedKPIs,
)

# ============================================================================
# DEFAULT PARAMETERS AND ASSUMPTIONS BLOCK (Rule G8)
# All constants below are not found in arbiter/shared/constants.py and are
# explicitly tagged for verification with the team.
# ============================================================================
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_THERMAL_NOISE_WATTS: float = 1e-13  # Approx -100 dBm thermal noise floor
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_SINR_DROP_THRESHOLD_DB: float = -3.0  # Matches MIN_SINR_DB from shared/constants
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_SINR_DROP_THRESHOLD_LINEAR: float = 0.5011872336272722  # 10 ** (-3.0 / 10.0)
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_MAX_TX_POWER_WATTS: float = 40.0  # 40 Watts (~46 dBm) reference max macro power
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_INTERFERENCE_SCALE_WATTS: float = 1e-9  # Scale for normalizing interference to [0, 1]
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_COLD_START_CONFIDENCE_MARGIN: float = 0.05  # 5% baseline confidence margin
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_TILT_LOSS_FACTOR_DB_PER_DEG: float = 0.1  # Attenuation per degree tilt change
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_SLEEP_POWER_WATTS: float = 0.0  # 0 Watts when cell is in sleep mode
# TBD [Q28-Q30]: not in deck; confirm with team
DEFAULT_SLEEP_OFFLOAD_PENALTY_RATIO: float = 0.15  # Neighbor load penalty ratio when a cell sleeps
# ============================================================================


def dbm_to_watts(dbm: float) -> float:
    """Convert power in dBm to Watts (W = 10^((dBm - 30) / 10))."""
    return float(10.0 ** ((dbm - 30.0) / 10.0))


def watts_to_dbm(watts: float) -> float:
    """Convert power in Watts to dBm (dBm = 10 * log10(W) + 30)."""
    safe_watts = max(watts, 1e-15)
    return float(10.0 * np.log10(safe_watts) + 30.0)


class AnalyticalPropagationChain:
    """Vectorized analytical radio propagation model (FR-5.1, FR-5.2).

    Executes closed-form matrix propagation calculations over a 1-2 hop neighborhood:
      delta_p_tx = candidate_power_watts - current_power_watts
      delta_interference = coupling_matrix @ delta_p_tx
      new_sinr = signal_powers / (noise_floor + current_interference + delta_interference)
      estimated_drops = sum(new_sinr < sinr_drop_threshold) / total_ue_count
    """

    def __init__(self) -> None:
        self._cached_version: Optional[int] = None
        self._cached_coupling: Optional[np.ndarray] = None
        self._cached_cell_to_idx: Optional[Dict[str, int]] = None

    def _ensure_cache(self, snapshot: ClusterSnapshot) -> Tuple[np.ndarray, Dict[str, int]]:
        """Cache coupling matrix and cell index lookup table by snapshot version."""
        if self._cached_version != snapshot.version or self._cached_coupling is None or self._cached_cell_to_idx is None:
            self._cached_coupling = np.asarray(snapshot.coupling_matrix, dtype=np.float64)
            self._cached_cell_to_idx = {cell_id: idx for idx, cell_id in enumerate(snapshot.cell_ids)}
            self._cached_version = snapshot.version
        return self._cached_coupling, self._cached_cell_to_idx

    def get_affected_neighborhood(
        self,
        commanded_cell_ids: List[str],
        neighbor_map: Dict[str, List[str]],
        max_hops: int = 2,
    ) -> List[str]:
        """Compute the set of cells within 1-2 hops of commanded cells (O(k))."""
        affected: Set[str] = set(commanded_cell_ids)
        current_layer: Set[str] = set(commanded_cell_ids)
        for _ in range(max_hops):
            next_layer: Set[str] = set()
            for cid in current_layer:
                neighbors = neighbor_map.get(cid, [])
                for n in neighbors:
                    if n not in affected:
                        affected.add(n)
                        next_layer.add(n)
            current_layer = next_layer
            if not current_layer:
                break
        return sorted(list(affected))

    def evaluate_deltas(
        self,
        snapshot: ClusterSnapshot,
        candidate_deltas: List[CandidateDelta],
    ) -> PredictedKPIs:
        """Run vectorized propagation physics and return predicted multi-cell KPIs.

        Guaranteed pure NumPy O(k) evaluation over the affected sub-cluster.
        """
        coupling_matrix, cell_to_idx = self._ensure_cache(snapshot)
        n_cells = len(snapshot.cell_ids)

        commanded_cell_ids = [d.cell_id for d in candidate_deltas if d.cell_id in cell_to_idx]
        if not commanded_cell_ids:
            # Fallback to all cells in snapshot if unrecognized
            commanded_cell_ids = list(snapshot.cell_ids)

        affected_cells = self.get_affected_neighborhood(commanded_cell_ids, snapshot.neighbor_map, max_hops=2)
        affected_indices = np.array([cell_to_idx[c] for c in affected_cells if c in cell_to_idx], dtype=np.int64)

        if len(affected_indices) == 0:
            affected_indices = np.arange(n_cells, dtype=np.int64)
            affected_cells = list(snapshot.cell_ids)

        # Baseline powers in Watts
        current_power_watts = np.zeros(n_cells, dtype=np.float64)
        for idx, cid in enumerate(snapshot.cell_ids):
            if snapshot.is_sleeping.get(cid, False):
                current_power_watts[idx] = DEFAULT_SLEEP_POWER_WATTS
            else:
                dbm = snapshot.tx_power_dbm.get(cid, 43.0)
                current_power_watts[idx] = dbm_to_watts(dbm)

        candidate_power_watts = np.copy(current_power_watts)
        simulated_sleeping = dict(snapshot.is_sleeping)
        simulated_loads = dict(snapshot.cell_loads)
        tilt_adjustments_db = np.zeros(n_cells, dtype=np.float64)

        # Apply candidate deltas
        for delta in candidate_deltas:
            cid = delta.cell_id
            if cid not in cell_to_idx:
                continue
            idx = cell_to_idx[cid]
            param = delta.parameter.upper()
            val = float(delta.proposed_value)

            if param in ("TX_POWER_DBM", "POWER"):
                candidate_power_watts[idx] = dbm_to_watts(val)
                simulated_sleeping[cid] = False
            elif param in ("SLEEP_STATE", "SLEEP"):
                # Handle SLEEP_STATE (sleep => 0 W; users re-associated to best neighbor)
                # Convention: 0.0 in "Cell B OFF (0 W)" or 1.0 (is_sleeping=True) triggers sleep
                is_sleep_requested = (val <= 0.0 or val >= 1.0)
                if val == 0.0:
                    # Explicit 0 W / OFF proposal
                    is_sleep_requested = True
                simulated_sleeping[cid] = is_sleep_requested
                if is_sleep_requested:
                    candidate_power_watts[idx] = DEFAULT_SLEEP_POWER_WATTS
                    # Offload users to best neighbor (raising neighbor load)
                    neighbors = snapshot.neighbor_map.get(cid, [])
                    if neighbors:
                        best_neighbor = neighbors[0]
                        source_load = simulated_loads.get(cid, 0.5)
                        simulated_loads[best_neighbor] = simulated_loads.get(best_neighbor, 0.4) + (source_load * 0.8)
                        simulated_loads[cid] = 0.0
                else:
                    # Wake up: restore default tx power
                    candidate_power_watts[idx] = dbm_to_watts(snapshot.tx_power_dbm.get(cid, 43.0))
            elif param in ("OFFLOAD_RATIO", "OFFLOAD"):
                # Shifts load fraction between cells
                # e.g., 0.40 moves 40% of cell's users to primary neighbor
                neighbors = snapshot.neighbor_map.get(cid, [])
                if neighbors:
                    target_neighbor = neighbors[0]
                    curr_load = simulated_loads.get(cid, 0.5)
                    shift = curr_load * np.clip(val, 0.0, 1.0)
                    simulated_loads[cid] = max(0.0, curr_load - shift)
                    simulated_loads[target_neighbor] = simulated_loads.get(target_neighbor, 0.4) + shift
            elif param in ("TILT_DEG", "TILT"):
                # Antenna tilt adjusts gain / interference
                curr_tilt = snapshot.antenna_tilt_deg.get(cid, 0.0)
                delta_tilt = val - curr_tilt
                tilt_adjustments_db[idx] = delta_tilt * DEFAULT_TILT_LOSS_FACTOR_DB_PER_DEG

        # Sub-cluster slice (O(k))
        delta_p_tx = candidate_power_watts[affected_indices] - current_power_watts[affected_indices]
        sub_coupling = coupling_matrix[np.ix_(affected_indices, affected_indices)]
        delta_interference = np.dot(sub_coupling, delta_p_tx)

        # Tilt interference attenuation
        tilt_factor = 10.0 ** (-tilt_adjustments_db[affected_indices] / 10.0)
        delta_interference = delta_interference * tilt_factor

        signal_powers = np.asarray(snapshot.signal_powers_w[affected_indices], dtype=np.float64)
        current_interference = np.asarray(snapshot.current_interference_w[affected_indices], dtype=np.float64)
        noise_floor = float(snapshot.noise_floor_w if snapshot.noise_floor_w > 0 else DEFAULT_THERMAL_NOISE_WATTS)

        # Scale received desired signal powers proportionally to serving cell transmit power changes
        power_scale_factors = np.ones(len(affected_indices), dtype=np.float64)
        for i, idx in enumerate(affected_indices):
            curr_w = current_power_watts[idx]
            cand_w = candidate_power_watts[idx]
            if curr_w > 1e-12:
                power_scale_factors[i] = cand_w / curr_w
            else:
                power_scale_factors[i] = 1.0 if cand_w <= 1e-12 else cand_w / dbm_to_watts(43.0)

        scaled_signal_powers = signal_powers * power_scale_factors
        total_interference = np.maximum(current_interference + delta_interference, 0.0)
        denominator = noise_floor + total_interference
        new_sinr = scaled_signal_powers / np.maximum(denominator, 1e-15)

        # Average SINR in dB
        sinr_db = 10.0 * np.log10(np.maximum(new_sinr, 1e-12))
        predicted_sinr_avg_db = float(np.mean(sinr_db))

        # Drops: estimated_drops = sum(new_sinr < sinr_drop_threshold) / total_ue_count
        total_ues = sum(snapshot.ue_counts.get(snapshot.cell_ids[idx], 50) for idx in affected_indices)
        if total_ues <= 0:
            total_ues = max(len(affected_indices) * 50, 1)

        # Per-cell UE drop count estimation
        dropped_ues = 0.0
        for i, idx in enumerate(affected_indices):
            cell_ues = snapshot.ue_counts.get(snapshot.cell_ids[idx], 50)
            if new_sinr[i] < DEFAULT_SINR_DROP_THRESHOLD_LINEAR:
                # Poor SINR drops users
                dropped_ues += cell_ues * 0.4
            elif new_sinr[i] < 1.0:  # < 0 dB
                dropped_ues += cell_ues * 0.05

        drop_rate_ratio = float(np.clip(dropped_ues / float(total_ues), 0.0, 1.0))

        # Coverage ratio: fraction of affected cells/UEs with acceptable SINR (>= MIN_SINR_DB)
        min_sinr_linear = 10.0 ** (MIN_SINR_DB / 10.0)
        coverage_ratio = float(np.clip(np.mean(new_sinr >= min_sinr_linear), 0.0, 1.0))

        # Normalized energy: sum of active power / (N * max_power)
        active_power_sum = float(np.sum(candidate_power_watts[affected_indices]))
        max_possible_power = float(len(affected_indices) * DEFAULT_MAX_TX_POWER_WATTS)
        normalized_energy = float(np.clip(active_power_sum / max(max_possible_power, 1e-6), 0.0, 1.0))

        # Normalized interference
        mean_interference = float(np.mean(total_interference))
        normalized_interference = float(np.clip(mean_interference / DEFAULT_INTERFERENCE_SCALE_WATTS, 0.0, 1.0))

        return PredictedKPIs(
            coverage_ratio=coverage_ratio,
            normalized_energy=normalized_energy,
            normalized_interference=normalized_interference,
            drop_rate_ratio=drop_rate_ratio,
            predicted_sinr_avg_db=predicted_sinr_avg_db,
            affected_cell_ids=affected_cells,
        )
