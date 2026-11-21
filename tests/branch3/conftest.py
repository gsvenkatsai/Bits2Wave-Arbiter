"""Pytest fixtures for Branch 3 testing suite.

Complies with Rule G5 (conftest.py only in tests/branch3/)
and Rule G6 (tmp_path for SQLite databases).
"""
import pytest
import sqlite3
import warnings
from typing import Dict, List
import numpy as np

# Suppress benign thread-local SQLite resource warnings during test teardown
warnings.filterwarnings("ignore", category=ResourceWarning)

from arbiter.storage.database import DatabaseConnectionFactory
from arbiter.storage.commit_log import SQLiteCommitLogger
from arbiter.storage.lkg_store import LKGStore
from arbiter.twin.schemas import (
    ClusterSnapshot,
    NetworkStateProvider,
    NetworkActuationTarget,
)


class InMemoryNetworkTarget:
    """In-memory actuation target simulating physical base station configuration."""

    def __init__(self) -> None:
        self.parameters: Dict[str, Dict[str, float]] = {}
        self.fail_on_cell: Dict[str, bool] = {}

    def apply_parameter(self, cell_id: str, parameter: str, value: float) -> bool:
        if self.fail_on_cell.get(cell_id, False):
            return False
        if cell_id not in self.parameters:
            self.parameters[cell_id] = {}
        self.parameters[cell_id][parameter] = value
        return True

    def get_parameter(self, cell_id: str, parameter: str) -> float:
        return self.parameters.get(cell_id, {}).get(parameter, 0.0)


class MockNetworkStateProvider:
    """Provides a synthetic 5-cell hexagonal cluster snapshot."""

    def __init__(self, version: int = 1) -> None:
        self.version = version
        self.cell_ids = ["cell_A", "cell_B", "cell_C", "cell_D", "cell_E"]
        n = len(self.cell_ids)

        # Distance-decay coupling matrix (symmetric, diagonal=0)
        # Stronger coupling between immediate neighbors
        coupling = np.array([
            [0.0,  0.08, 0.02, 0.01, 0.01],
            [0.08, 0.0,  0.07, 0.02, 0.01],
            [0.02, 0.07, 0.0,  0.06, 0.02],
            [0.01, 0.02, 0.06, 0.0,  0.08],
            [0.01, 0.01, 0.02, 0.08, 0.0],
        ], dtype=np.float64)

        self.coupling_matrix = coupling
        self.tx_power_dbm = {c: 43.0 for c in self.cell_ids}
        self.is_sleeping = {c: False for c in self.cell_ids}
        self.signal_powers_w = np.full(n, 2.5e-8, dtype=np.float64)
        self.current_interference_w = np.full(n, 1.2e-9, dtype=np.float64)
        self.ue_counts = {c: 60 for c in self.cell_ids}
        self.noise_floor_w = 1e-13
        self.neighbor_map = {
            "cell_A": ["cell_B", "cell_C"],
            "cell_B": ["cell_A", "cell_C", "cell_D"],
            "cell_C": ["cell_A", "cell_B", "cell_D", "cell_E"],
            "cell_D": ["cell_B", "cell_C", "cell_E"],
            "cell_E": ["cell_C", "cell_D"],
        }
        self.offload_ratios = {c: 0.0 for c in self.cell_ids}
        self.cell_loads = {c: 0.45 for c in self.cell_ids}
        self.antenna_tilt_deg = {c: 2.0 for c in self.cell_ids}

    def get_snapshot(self) -> ClusterSnapshot:
        return ClusterSnapshot(
            version=self.version,
            cell_ids=list(self.cell_ids),
            coupling_matrix=np.copy(self.coupling_matrix),
            tx_power_dbm=dict(self.tx_power_dbm),
            is_sleeping=dict(self.is_sleeping),
            signal_powers_w=np.copy(self.signal_powers_w),
            current_interference_w=np.copy(self.current_interference_w),
            ue_counts=dict(self.ue_counts),
            noise_floor_w=self.noise_floor_w,
            neighbor_map={k: list(v) for k, v in self.neighbor_map.items()},
            offload_ratios=dict(self.offload_ratios),
            cell_loads=dict(self.cell_loads),
            antenna_tilt_deg=dict(self.antenna_tilt_deg),
        )


@pytest.fixture
def mock_state_provider() -> MockNetworkStateProvider:
    return MockNetworkStateProvider()


@pytest.fixture
def mock_target() -> InMemoryNetworkTarget:
    target = InMemoryNetworkTarget()
    for cid in ["cell_A", "cell_B", "cell_C", "cell_D", "cell_E"]:
        target.apply_parameter(cid, "TX_POWER_DBM", 43.0)
        target.apply_parameter(cid, "SLEEP_STATE", 0.0)
        target.apply_parameter(cid, "TILT_DEG", 2.0)
        target.apply_parameter(cid, "OFFLOAD_RATIO", 0.0)
    return target


@pytest.fixture
def tmp_db_factory(tmp_path) -> DatabaseConnectionFactory:
    """Fixture providing temporary SQLite DB with WAL mode and stub audit_decisions table."""
    db_file = str(tmp_path / "test_arbiter.db")
    factory = DatabaseConnectionFactory(db_path=db_file)
    conn = factory.get_connection()

    # Stub audit_decisions table for Foreign Key validation if needed (Section 3.5)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS audit_decisions (
        decision_id TEXT PRIMARY KEY,
        command_id TEXT NOT NULL,
        batch_tick INTEGER NOT NULL,
        action TEXT NOT NULL,
        approved_value REAL,
        delta_utility REAL NOT NULL,
        reason_code TEXT NOT NULL,
        hold_count INTEGER NOT NULL,
        decided_at_epoch_ms INTEGER NOT NULL
    );
    """)
    conn.commit()
    return factory


@pytest.fixture
def commit_logger(tmp_db_factory) -> SQLiteCommitLogger:
    return SQLiteCommitLogger(tmp_db_factory)


@pytest.fixture
def lkg_store(tmp_db_factory) -> LKGStore:
    return LKGStore(tmp_db_factory)
