"""Unit tests for HexagonalGrid topology and spatial calculations."""
import json
from pathlib import Path
import numpy as np
from arbiter.simulator.grid import HexagonalGrid


def test_5_cell_cluster_matches_fixture() -> None:
    grid = HexagonalGrid()
    cells = grid.generate_5_cell_cluster(isd=500.0)

    assert len(cells) == 5
    assert set(cells.keys()) == {
        "cell_A", "cell_B", "cell_C", "cell_D", "cell_E"}

    # Load quiet night fixture
    fixture_path = (
        Path(__file__).resolve().parents[2] /
        "fixtures" / "traffic" / "milan_2am_quiet.json"
    )
    with open(fixture_path, "r", encoding="utf-8") as f:
        fixture_data = json.load(f)

    for item in fixture_data["cells"]:
        cid = item["cell_id"]
        assert cid in cells
        assert cells[cid].x_coord == pytest_approx(item["x"])
        assert cells[cid].y_coord == pytest_approx(item["y"])
        assert sorted(cells[cid].neighbor_cell_ids) == sorted(
            item["neighbors"])


def test_19_cell_cluster_structure() -> None:
    grid = HexagonalGrid()
    cells = grid.generate_19_cell_cluster(isd=500.0)

    assert len(cells) == 19
    expected_ids = {f"cell_{chr(ord('A') + i)}" for i in range(19)}
    assert set(cells.keys()) == expected_ids

    # Center cell is cell_A at (0, 0)
    assert cells["cell_A"].x_coord == 0.0
    assert cells["cell_A"].y_coord == 0.0

    # cell_A must have exactly 6 tier-1 neighbors (cell_B..cell_G)
    tier1_ids = [f"cell_{chr(ord('B') + i)}" for i in range(6)]
    assert sorted(cells["cell_A"].neighbor_cell_ids) == sorted(tier1_ids)


def test_adjacency_matrix_symmetry_and_diagonal() -> None:
    grid = HexagonalGrid()
    cells = grid.generate_19_cell_cluster(isd=500.0)
    adj = grid.compute_adjacency_matrix(cells, isd=500.0)

    assert adj.shape == (19, 19)
    assert adj.dtype == bool

    # Zero diagonal (no self-loops)
    assert not np.any(np.diag(adj))

    # Symmetric: adj == adj.T
    assert np.array_equal(adj, adj.T)


def test_isd_scaling() -> None:
    grid = HexagonalGrid()
    cells_500 = grid.generate_5_cell_cluster(isd=500.0)
    cells_1000 = grid.generate_5_cell_cluster(isd=1000.0)

    for cid in cells_500:
        assert cells_1000[cid].x_coord == pytest_approx(
            cells_500[cid].x_coord * 2.0)
        assert cells_1000[cid].y_coord == pytest_approx(
            cells_500[cid].y_coord * 2.0)


def test_adjacency_graph() -> None:
    grid = HexagonalGrid()
    cells = grid.generate_5_cell_cluster(isd=500.0)
    graph = grid.adjacency_graph(cells, isd=500.0)

    assert len(graph.nodes) == 5
    for cid in cells:
        assert cid in graph.nodes
        assert "x" in graph.nodes[cid]
        assert "y" in graph.nodes[cid]

    # Check edges exist
    assert graph.number_of_edges() > 0


def test_bounds_calculation() -> None:
    grid = HexagonalGrid()
    cells = grid.generate_5_cell_cluster(isd=500.0)
    xmin, xmax, ymin, ymax = grid.bounds(cells, isd=500.0)

    xs = [c.x_coord for c in cells.values()]
    ys = [c.y_coord for c in cells.values()]

    margin = 0.6 * 500.0
    assert xmin == min(xs) - margin
    assert xmax == max(xs) + margin
    assert ymin == min(ys) - margin
    assert ymax == max(ys) + margin


def test_empty_cells_edge_cases() -> None:
    grid = HexagonalGrid()
    adj = grid.compute_adjacency_matrix([])
    assert adj.shape == (0, 0)

    empty_bounds = grid.bounds([])
    assert empty_bounds == (0.0, 0.0, 0.0, 0.0)


def pytest_approx(val: float, tolerance: float = 1e-3) -> float:
    # simple helper for floating approx comparison
    return round(val, 3)
