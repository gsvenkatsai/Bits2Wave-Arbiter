"""Traffic generation, spatial clustering, and Voronoi user mapping."""
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np

from arbiter.simulator.cell import CellConfiguration, UserEquipment
from arbiter.simulator.grid import HexagonalGrid

CELL_CAPACITY_MBPS: float = 80.0


class ScenarioType(str, Enum):
    """Traffic generation scenario profiles."""

    QUIET_2AM = "2_AM_COLLISION_QUIET_NIGHT"
    PEAK = "PEAK_HOUR"
    SURGE = "SURGE_EVENT"

    @classmethod
    def from_str(cls, val: str) -> "ScenarioType":
        """Parse string to ScenarioType with alias tolerance."""
        normalized = val.strip().upper()
        if any(k in normalized for k in ("2AM", "QUIET", "COLLISION")):
            return cls.QUIET_2AM
        if "PEAK" in normalized:
            return cls.PEAK
        if "SURGE" in normalized:
            return cls.SURGE
        for item in cls:
            if item.value == val:
                return item
        raise ValueError(f"Unknown scenario identifier: {val}")


class MilanTrafficGenerator:
    """Generates synthetic UE traffic profiles from Milan traces."""

    def __init__(
        self,
        scenario: Union[ScenarioType, str] = ScenarioType.QUIET_2AM,
        seed: int = 42,
    ) -> None:
        self.scenario = (
            scenario if isinstance(
                scenario, ScenarioType) else ScenarioType.from_str(scenario)
        )
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.profile = self.load_trace_profile(self.scenario)

    @staticmethod
    def load_trace_profile(
        scenario: Union[ScenarioType, str]
    ) -> Dict[str, Any]:
        """Load profile JSON fixture corresponding to the chosen scenario."""
        scen_enum = (
            scenario if isinstance(
                scenario, ScenarioType) else ScenarioType.from_str(scenario)
        )
        fixtures_dir = Path(__file__).resolve(
        ).parents[2] / "fixtures" / "traffic"

        file_map = {
            ScenarioType.QUIET_2AM: fixtures_dir / "milan_2am_quiet.json",
            ScenarioType.PEAK: fixtures_dir / "milan_peak.json",
            ScenarioType.SURGE: fixtures_dir / "milan_surge.json",
        }

        path = file_map[scen_enum]
        with open(path, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
        return data

    def spawn_users(
        self,
        count: int,
        bounds: Tuple[float, float, float, float],
        cells: Optional[Dict[str, CellConfiguration]] = None,
    ) -> List[UserEquipment]:
        """Spawn users via Gaussian clustering and uniform background."""
        xmin, xmax, ymin, ymax = bounds
        hotspots = self.profile.get("hotspots", [])

        # Build weights for hotspots and uniform background
        weights: List[float] = []
        centers: List[Tuple[float, float, float]] = []  # (x, y, sigma)

        if cells is not None and hotspots:
            for h in hotspots:
                cid = str(h["cell_id"])
                w = float(h.get("weight", 0.0))
                sigma = float(h.get("sigma_m", 100.0))
                if cid in cells:
                    weights.append(w)
                    centers.append(
                        (cells[cid].x_coord, cells[cid].y_coord, sigma))

        sum_w = sum(weights)
        if sum_w < 1.0:
            # remaining weight is uniform background
            bg_weight = max(0.0, 1.0 - sum_w)
            weights.append(bg_weight)
        else:
            # normalize weights so sum is 1.0
            weights = [w / sum_w for w in weights]

        weights_arr = np.array(weights, dtype=np.float64)
        weights_arr /= weights_arr.sum()

        users: List[UserEquipment] = []
        for i in range(count):
            choice = int(self.rng.choice(len(weights_arr), p=weights_arr))
            if choice < len(centers):
                cx, cy, sigma = centers[choice]
                x = float(self.rng.normal(cx, sigma))
                y = float(self.rng.normal(cy, sigma))
            else:
                x = float(self.rng.uniform(xmin, xmax))
                y = float(self.rng.uniform(ymin, ymax))

            # Clip to bounds
            x = max(xmin, min(xmax, x))
            y = max(ymin, min(ymax, y))

            ue = UserEquipment(
                ue_id=f"ue_{i:04d}",
                x_coord=round(x, 2),
                y_coord=round(y, 2),
                attached_cell_id="",
                required_rate_mbps=2.0,
            )
            users.append(ue)

        if cells:
            VoronoiUserMapper.assign_nearest(users, cells)

        return users

    def step_mobility(
        self,
        users: List[UserEquipment],
        bounds: Tuple[float, float, float, float],
        speed_mps: float = 1.2,
        dt_s: float = 0.1,
    ) -> None:
        """Advance UE positions via random walk with velocity speed_mps."""
        if not users:
            return

        xmin, xmax, ymin, ymax = bounds
        step_dist = speed_mps * dt_s  # 0.12 m at default parameters

        angles = self.rng.uniform(0.0, 2.0 * math.pi, size=len(users))
        dx = step_dist * np.cos(angles)
        dy = step_dist * np.sin(angles)

        for i, ue in enumerate(users):
            new_x = float(ue.x_coord + dx[i])
            new_y = float(ue.y_coord + dy[i])
            ue.x_coord = round(max(xmin, min(xmax, new_x)), 2)
            ue.y_coord = round(max(ymin, min(ymax, new_y)), 2)

    @staticmethod
    def get_load_multiplier(tick_index: int) -> float:
        """Smooth cyclical temporal load multiplier."""
        # 1000 ticks = 100 s cycle with small +/- 5% diurnal fluctuation
        phase = 2.0 * math.pi * (tick_index % 1000) / 1000.0
        return float(1.0 + 0.05 * math.sin(phase))


class VoronoiUserMapper:
    """Partitions user equipment across cells via nearest-neighbor Voronoi."""

    @staticmethod
    def assign_nearest(
        users: List[UserEquipment],
        cells: Union[Dict[str, CellConfiguration],
                     Sequence[CellConfiguration]],
    ) -> None:
        """Assign each user's attached_cell_id to closest cell."""
        if not users:
            return

        cell_list: List[CellConfiguration] = (
            list(cells.values()) if isinstance(cells, dict) else list(cells)
        )
        if not cell_list:
            return

        ue_coords = np.array([[u.x_coord, u.y_coord]
                             for u in users], dtype=np.float64)
        cell_coords = np.array([[c.x_coord, c.y_coord]
                               for c in cell_list], dtype=np.float64)

        # Pairwise distance (U, C)
        diff = ue_coords[:, np.newaxis, :] - cell_coords[np.newaxis, :, :]
        dists = np.linalg.norm(diff, axis=-1)
        nearest_indices = np.argmin(dists, axis=1)

        for i, ue in enumerate(users):
            ue.attached_cell_id = cell_list[nearest_indices[i]].cell_id


class TrafficDistributionModel:
    """Orchestrates cellular populations, scaling, and load accounting."""

    def __init__(
        self,
        topology: str = "5_CELL_CORE",
        scenario: Union[ScenarioType, str] = ScenarioType.QUIET_2AM,
        seed: int = 42,
    ) -> None:
        self.topology = topology
        self.scenario = (
            scenario if isinstance(
                scenario, ScenarioType) else ScenarioType.from_str(scenario)
        )
        self.seed = seed
        self.generator = MilanTrafficGenerator(
            scenario=self.scenario, seed=seed)

        # Determine cell cluster and population pool size
        grid = HexagonalGrid()
        if "19" in topology:
            self.pool_size = 350
            self.cells = grid.generate_19_cell_cluster(isd=500.0)
        else:
            self.pool_size = 70
            self.cells = grid.generate_5_cell_cluster(isd=500.0)

        self.bounds = grid.bounds(self.cells, isd=500.0)
        prof_scale = self.generator.profile.get("traffic_profile_scale", 1.0)
        self.profile_scale = float(prof_scale)
        self.pool = self.generator.spawn_users(
            self.pool_size, self.bounds, self.cells)

    def get_active_users(self, tick_index: int = 0) -> List[UserEquipment]:
        """Return active user slice based on traffic scale."""
        multiplier = self.generator.get_load_multiplier(tick_index)
        target_count = round(self.pool_size * self.profile_scale * multiplier)
        active_count = max(0, min(self.pool_size, target_count))
        return self.pool[:active_count]

    def step(self, tick_index: int = 0) -> List[UserEquipment]:
        """Step user mobility, update attachments, and return active users."""
        self.generator.step_mobility(self.pool, self.bounds)
        VoronoiUserMapper.assign_nearest(self.pool, self.cells)
        return self.get_active_users(tick_index=tick_index)

    @staticmethod
    def compute_cell_load(
        cell_id: str, active_users: List[UserEquipment]
    ) -> float:
        """Compute cell load ratio [0.0, 1.0] from attached active users."""
        total_rate = sum(
            ue.required_rate_mbps
            for ue in active_users
            if ue.attached_cell_id == cell_id and not ue.is_dropped
        )
        ratio = total_rate / CELL_CAPACITY_MBPS
        return float(min(1.0, max(0.0, ratio)))
