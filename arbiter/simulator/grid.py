"""Hexagonal cellular grid generation and spatial topology calculations."""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Sequence, Tuple, Union, cast
import networkx as nx
import numpy as np
from numpy.typing import NDArray

from arbiter.simulator.cell import CellConfiguration


class HexagonalGrid:
    """Manages hexagonal cell layout topologies and adjacency graphs."""

    @staticmethod
    def generate_5_cell_cluster(
        isd: float = 500.0,
    ) -> Dict[str, CellConfiguration]:
        """Generate standard 5-cell core cluster scaled by isd / 500.0.

        Source of truth for coordinates and neighbor lists matches
        fixtures/traffic/milan_2am_quiet.json.
        """
        scale = isd / 500.0
        base_cells: List[Dict[str, Any]] = [
            {
                "cell_id": "cell_A",
                "x": 0.0,
                "y": 0.0,
                "neighbors": ["cell_B", "cell_C", "cell_D", "cell_E"],
            },
            {
                "cell_id": "cell_B",
                "x": 500.0,
                "y": 0.0,
                "neighbors": ["cell_A", "cell_C", "cell_D", "cell_E"],
            },
            {
                "cell_id": "cell_C",
                "x": 250.0,
                "y": 433.01,
                "neighbors": ["cell_A", "cell_B", "cell_D", "cell_E"],
            },
            {
                "cell_id": "cell_D",
                "x": -250.0,
                "y": 433.01,
                "neighbors": ["cell_A", "cell_C", "cell_E"],
            },
            {
                "cell_id": "cell_E",
                "x": 0.0,
                "y": -500.0,
                "neighbors": ["cell_A", "cell_B", "cell_D"],
            },
        ]

        result: Dict[str, CellConfiguration] = {}
        for item in base_cells:
            cell_id = str(item["cell_id"])
            x_coord = round(float(item["x"]) * scale, 4)
            y_coord = round(float(item["y"]) * scale, 4)
            neighbors = cast(List[str], item["neighbors"])
            result[cell_id] = CellConfiguration(
                cell_id=cell_id,
                x_coord=x_coord,
                y_coord=y_coord,
                carrier_freq_mhz=2100.0,
                max_tx_power_dbm=46.0,
                min_tx_power_dbm=30.0,
                current_tx_power_dbm=43.0,
                antenna_tilt_deg=6.0,
                is_sleeping=False,
                neighbor_cell_ids=list(neighbors),
            )
        return result

    @classmethod
    def generate_7_cell_cluster(
        cls, isd: float = 500.0
    ) -> Dict[str, CellConfiguration]:
        """Generate 7-cell single-tier hexagonal cluster (cell_A through cell_G).

        Center: cell_A at (0, 0)
        Ring: 6 cells (cell_B..cell_G) at distance isd (angles 0, 60, ..., 300 deg).
        """
        all_ids = [f"cell_{chr(ord('A') + i)}" for i in range(7)]
        coords: List[Tuple[float, float]] = [(0.0, 0.0)]
        angles = [math.radians(deg) for deg in range(0, 360, 60)]
        for angle in angles:
            x = isd * math.cos(angle)
            y = isd * math.sin(angle)
            coords.append((round(x, 4), round(y, 4)))

        temp_cells: List[CellConfiguration] = []
        for cell_id, (x, y) in zip(all_ids, coords):
            temp_cells.append(
                CellConfiguration(
                    cell_id=cell_id,
                    x_coord=x,
                    y_coord=y,
                    carrier_freq_mhz=2100.0,
                    max_tx_power_dbm=46.0,
                    min_tx_power_dbm=30.0,
                    current_tx_power_dbm=43.0,
                    antenna_tilt_deg=6.0,
                    is_sleeping=False,
                    neighbor_cell_ids=[],
                )
            )

        adj_matrix = cls.compute_adjacency_matrix(temp_cells, isd=isd)
        result: Dict[str, CellConfiguration] = {}
        for i, cell in enumerate(temp_cells):
            neighbors = [all_ids[j] for j in range(len(temp_cells)) if adj_matrix[i, j]]
            cell.neighbor_cell_ids = sorted(neighbors)
            result[cell.cell_id] = cell
        return result

    @classmethod
    def generate_bengaluru_cluster(
        cls, isd: float = 800.0
    ) -> Dict[str, CellConfiguration]:
        """Generate Bengaluru metropolitan telecom demo cluster (16 named towers).

        Uses coordinates from topology_bengaluru.json or realistic metro layout.
        """
        topo_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "dashboard",
                "assets",
                "topology_bengaluru.json",
            )
        )
        if os.path.exists(topo_path):
            with open(topo_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            towers = data.get("towers", [])
        else:
            towers = [
                {"id": "cell_A", "name": "Majestic", "x_m": 0.0, "y_m": 0.0, "tx_dbm": 43.0, "tilt_deg": 6.0},
                {"id": "cell_B", "name": "Koramangala", "x_m": 600.0, "y_m": -550.0, "tx_dbm": 43.0, "tilt_deg": 6.0},
                {"id": "cell_C", "name": "Indiranagar", "x_m": 750.0, "y_m": 50.0, "tx_dbm": 43.0, "tilt_deg": 6.0},
                {"id": "cell_D", "name": "MG Road", "x_m": 350.0, "y_m": 0.0, "tx_dbm": 43.0, "tilt_deg": 6.0},
                {"id": "cell_E", "name": "Jayanagar", "x_m": -100.0, "y_m": -750.0, "tx_dbm": 43.0, "tilt_deg": 6.0},
            ]

        temp_cells: List[CellConfiguration] = []
        for t in towers:
            temp_cells.append(
                CellConfiguration(
                    cell_id=t["id"],
                    x_coord=float(t.get("x_m", 0.0)),
                    y_coord=float(t.get("y_m", 0.0)),
                    carrier_freq_mhz=float(t.get("freq_mhz", 2100.0)),
                    max_tx_power_dbm=46.0,
                    min_tx_power_dbm=30.0,
                    current_tx_power_dbm=float(t.get("tx_dbm", 43.0)),
                    antenna_tilt_deg=float(t.get("tilt_deg", 6.0)),
                    is_sleeping=False,
                    neighbor_cell_ids=[],
                )
            )

        adj_matrix = cls.compute_adjacency_matrix(temp_cells, isd=isd)
        result: Dict[str, CellConfiguration] = {}
        for i, cell in enumerate(temp_cells):
            neighbors = [temp_cells[j].cell_id for j in range(len(temp_cells)) if adj_matrix[i, j]]
            cell.neighbor_cell_ids = sorted(neighbors)
            result[cell.cell_id] = cell
        return result

    @classmethod
    def generate_19_cell_cluster(
        cls, isd: float = 500.0
    ) -> Dict[str, CellConfiguration]:
        """Generate 19-cell 2-tier hexagonal cluster (cell_A through cell_S).

        Center: cell_A at (0, 0)
        Tier 1: 6 cells at distance isd (angles 0, 60, ..., 300 deg)
        Tier 2: 12 cells (6 at 2*isd; 6 at sqrt(3)*isd)
        Neighbors computed dynamically via compute_adjacency_matrix.
        """
        # Assign IDs: cell_A .. cell_S
        all_ids = [f"cell_{chr(ord('A') + i)}" for i in range(19)]

        coords: List[Tuple[float, float]] = []
        # Center cell (index 0)
        coords.append((0.0, 0.0))

        # Tier 1 (6 cells, indices 1..6)
        angles_t1 = [math.radians(deg) for deg in range(0, 360, 60)]
        for angle in angles_t1:
            x = isd * math.cos(angle)
            y = isd * math.sin(angle)
            coords.append((round(x, 4), round(y, 4)))

        # Tier 2 outer corners (6 cells, indices 7..12)
        angles_t2_corners = [math.radians(deg) for deg in range(0, 360, 60)]
        for angle in angles_t2_corners:
            x = 2.0 * isd * math.cos(angle)
            y = 2.0 * isd * math.sin(angle)
            coords.append((round(x, 4), round(y, 4)))

        # Tier 2 edge centers (6 cells, indices 13..18)
        dist_t2_edges = math.sqrt(3.0) * isd
        angles_t2_edges = [math.radians(deg) for deg in range(30, 360, 60)]
        for angle in angles_t2_edges:
            x = dist_t2_edges * math.cos(angle)
            y = dist_t2_edges * math.sin(angle)
            coords.append((round(x, 4), round(y, 4)))

        # First construct configurations without neighbor lists
        temp_cells: List[CellConfiguration] = []
        for cell_id, (x, y) in zip(all_ids, coords):
            temp_cells.append(
                CellConfiguration(
                    cell_id=cell_id,
                    x_coord=x,
                    y_coord=y,
                    carrier_freq_mhz=2100.0,
                    max_tx_power_dbm=46.0,
                    min_tx_power_dbm=30.0,
                    current_tx_power_dbm=43.0,
                    antenna_tilt_deg=6.0,
                    is_sleeping=False,
                    neighbor_cell_ids=[],
                )
            )

        # Compute adjacency matrix to populate neighbor lists
        adj_matrix = cls.compute_adjacency_matrix(temp_cells, isd=isd)
        result: Dict[str, CellConfiguration] = {}
        for i, cell in enumerate(temp_cells):
            neighbors = [all_ids[j]
                         for j in range(len(temp_cells)) if adj_matrix[i, j]]
            cell.neighbor_cell_ids = sorted(neighbors)
            result[cell.cell_id] = cell

        return result

    @staticmethod
    def _normalize_cell_list(
        cells: Union[Dict[str, CellConfiguration], Sequence[CellConfiguration]]
    ) -> List[CellConfiguration]:
        if isinstance(cells, dict):
            return list(cells.values())
        return list(cells)

    @classmethod
    def compute_adjacency_matrix(
        cls,
        cells: Union[Dict[str, CellConfiguration],
                     Sequence[CellConfiguration]],
        isd: float = 500.0,
    ) -> NDArray[np.bool_]:
        """Compute NxN boolean adjacency matrix with threshold <= 1.5 * isd.

        Self-loops are strictly False.
        """
        cell_list = cls._normalize_cell_list(cells)
        n = len(cell_list)
        if n == 0:
            return np.zeros((0, 0), dtype=np.bool_)

        coords = np.array([[c.x_coord, c.y_coord]
                          for c in cell_list], dtype=np.float64)
        # Pairwise euclidean distances
        diff = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]
        dist_matrix = np.linalg.norm(diff, axis=-1)

        threshold = 1.5 * isd + 1e-3  # small tolerance for float comparison
        adj_matrix: NDArray[np.bool_] = (
            dist_matrix <= threshold) & (dist_matrix > 1e-3)
        return adj_matrix

    @classmethod
    def adjacency_graph(
        cls,
        cells: Union[Dict[str, CellConfiguration],
                     Sequence[CellConfiguration]],
        isd: float = 500.0,
    ) -> nx.Graph[str]:
        """Create NetworkX Graph with cell coordinates and adjacency edges."""
        cell_list = cls._normalize_cell_list(cells)
        adj_matrix = cls.compute_adjacency_matrix(cell_list, isd=isd)

        graph: nx.Graph[str] = nx.Graph()
        for cell in cell_list:
            graph.add_node(
                cell.cell_id,
                x=cell.x_coord,
                y=cell.y_coord,
                pos=(cell.x_coord, cell.y_coord),
            )

        n = len(cell_list)
        for i in range(n):
            for j in range(i + 1, n):
                if adj_matrix[i, j]:
                    graph.add_edge(cell_list[i].cell_id, cell_list[j].cell_id)

        return graph

    @classmethod
    def bounds(
        cls,
        cells: Union[Dict[str, CellConfiguration],
                     Sequence[CellConfiguration]],
        isd: float = 500.0,
    ) -> Tuple[float, float, float, float]:
        """Calculate bounding box with 0.6 * isd margin."""
        cell_list = cls._normalize_cell_list(cells)
        if not cell_list:
            return (0.0, 0.0, 0.0, 0.0)

        xs = [c.x_coord for c in cell_list]
        ys = [c.y_coord for c in cell_list]
        margin = 0.6 * isd
        return (
            float(min(xs) - margin),
            float(max(xs) + margin),
            float(min(ys) - margin),
            float(max(ys) + margin),
        )
