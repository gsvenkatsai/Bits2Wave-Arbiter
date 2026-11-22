"""Last-Known-Good (LKG) Configuration Store.

Caches and persists Last-Known-Good cell parameter configurations
for fail-safe reversion upon timeouts and actuator rollbacks.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional, Union

from arbiter.storage.database import DatabaseConnectionFactory


LKG_DDL = """
CREATE TABLE IF NOT EXISTS lkg_cell_configurations (
    cell_id TEXT NOT NULL,
    parameter TEXT NOT NULL,
    value REAL NOT NULL,
    updated_at_epoch_ms INTEGER NOT NULL,
    PRIMARY KEY (cell_id, parameter)
);
"""


class LKGStore:
    """Persistent and in-memory cache for Last-Known-Good cell settings."""

    def __init__(
        self, factory_or_path: Union[DatabaseConnectionFactory, str]
    ) -> None:
        if isinstance(factory_or_path, str):
            self.factory: DatabaseConnectionFactory = (
                DatabaseConnectionFactory(db_path=factory_or_path)
            )
        else:
            self.factory = factory_or_path

        self._lock = threading.Lock()
        self._cache: Dict[str, Dict[str, float]] = {}
        self._ensure_schema()
        self._load_cache()

    def _ensure_schema(self) -> None:
        """Create lkg table if not exists."""
        conn = self.factory.get_connection()
        with self._lock:
            conn.executescript(LKG_DDL)
            conn.commit()

    def _load_cache(self) -> None:
        """Warm up in-memory cache from persistent database."""
        conn = self.factory.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT cell_id, parameter, value FROM lkg_cell_configurations"
        )
        rows = cursor.fetchall()
        with self._lock:
            for row in rows:
                cid = row["cell_id"]
                param = row["parameter"]
                val = float(row["value"])
                if cid not in self._cache:
                    self._cache[cid] = {}
                self._cache[cid][param] = val

    def set_lkg(
        self,
        cell_id: str,
        parameter: str,
        value: float,
        updated_at_epoch_ms: int,
    ) -> None:
        """Update LKG configuration for a cell parameter."""
        conn = self.factory.get_connection()
        query = """
        INSERT INTO lkg_cell_configurations
            (cell_id, parameter, value, updated_at_epoch_ms)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(cell_id, parameter) DO UPDATE SET
            value=excluded.value,
            updated_at_epoch_ms=excluded.updated_at_epoch_ms
        """
        with self._lock:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (
                    cell_id,
                    parameter,
                    float(value),
                    int(updated_at_epoch_ms),
                ),
            )
            conn.commit()

            if cell_id not in self._cache:
                self._cache[cell_id] = {}
            self._cache[cell_id][parameter] = float(value)

    def get_lkg(self, cell_id: str, parameter: str) -> Optional[float]:
        """Retrieve LKG parameter value from memory cache or database."""
        with self._lock:
            if cell_id in self._cache and parameter in self._cache[cell_id]:
                return self._cache[cell_id][parameter]

        # Check database
        conn = self.factory.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM lkg_cell_configurations "
            "WHERE cell_id = ? AND parameter = ?",
            (cell_id, parameter),
        )
        row = cursor.fetchone()
        if row is not None:
            val = float(row["value"])
            with self._lock:
                if cell_id not in self._cache:
                    self._cache[cell_id] = {}
                self._cache[cell_id][parameter] = val
            return val
        return None

    def get_cell_lkg(self, cell_id: str) -> Dict[str, float]:
        """Get all known LKG parameters for a cell."""
        with self._lock:
            return dict(self._cache.get(cell_id, {}))

    def get_all_lkg(self) -> Dict[str, Dict[str, float]]:
        """Get snapshot of all cell LKG configurations."""
        with self._lock:
            return {cid: dict(params) for cid, params in self._cache.items()}

    def close(self) -> None:
        """Close all connections managed by the factory."""
        self.factory.close_all()
