"""SQLite Commit Logger for Project ARBITER.

SRS FR-7.3: Actuator SHALL record each commit in the SQLite commit log.
Guarantees ACID transactional logging with idempotent deduplication.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Union

from arbiter.storage.database import DatabaseConnectionFactory


COMMIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS commit_log (
    commit_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_id TEXT NOT NULL UNIQUE,
    decision_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    target_cell_id TEXT NOT NULL,
    parameter TEXT NOT NULL,
    old_value REAL NOT NULL,
    new_value REAL NOT NULL,
    applied_at_epoch_ms INTEGER NOT NULL,
    rollback_flag INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_commit_log_cell
    ON commit_log(target_cell_id);
CREATE INDEX IF NOT EXISTS idx_commit_log_time
    ON commit_log(applied_at_epoch_ms);
"""


class SQLiteCommitLogger:
    """ACID-compliant commit logger recording actuation actions to SQLite."""

    def __init__(
        self, factory_or_path: Union[DatabaseConnectionFactory, str]
    ) -> None:
        if isinstance(factory_or_path, str):
            self.factory: DatabaseConnectionFactory = (
                DatabaseConnectionFactory(db_path=factory_or_path)
            )
        else:
            self.factory = factory_or_path

        self._write_lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create commit_log table and indexes if not exists."""
        conn = self.factory.get_connection()
        with self._write_lock:
            conn.executescript(COMMIT_LOG_DDL)
            conn.commit()

    def log_commit(
        self,
        commit_id: str,
        decision_id: str,
        command_id: str,
        target_cell_id: str,
        parameter: str,
        old_value: float,
        new_value: float,
        applied_at_epoch_ms: int,
        rollback_flag: int = 0,
    ) -> int:
        """Record an actuation commit into commit_log. Return commit_seq.

        Uses parameterized SQL only.
        """
        conn = self.factory.get_connection()
        query = """
        INSERT INTO commit_log (
            commit_id, decision_id, command_id, target_cell_id,
            parameter, old_value, new_value, applied_at_epoch_ms, rollback_flag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            str(commit_id),
            str(decision_id),
            str(command_id),
            str(target_cell_id),
            str(parameter),
            float(old_value),
            float(new_value),
            int(applied_at_epoch_ms),
            int(rollback_flag),
        )

        with self._write_lock:
            cursor = conn.cursor()
            cursor.execute(query, params)
            commit_seq = cursor.lastrowid
            conn.commit()
            return int(commit_seq or 0)

    def get_commit(self, commit_id: str) -> Optional[Dict[str, Any]]:
        """Look up commit record by commit_id (idempotency check)."""
        conn = self.factory.get_connection()
        query = "SELECT * FROM commit_log WHERE commit_id = ?"
        cursor = conn.cursor()
        cursor.execute(query, (commit_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_commits_by_cell(
        self, target_cell_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieve historical commits for a specific target cell."""
        conn = self.factory.get_connection()
        query = (
            "SELECT * FROM commit_log WHERE target_cell_id = ? "
            "ORDER BY commit_seq ASC"
        )
        cursor = conn.cursor()
        cursor.execute(query, (target_cell_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def update_rollback_flag(
        self, commit_id: str, rollback_flag: int = 1
    ) -> bool:
        """Update rollback_flag on an existing commit log row."""
        conn = self.factory.get_connection()
        query = "UPDATE commit_log SET rollback_flag = ? WHERE commit_id = ?"
        with self._write_lock:
            cursor = conn.cursor()
            cursor.execute(query, (int(rollback_flag), commit_id))
            conn.commit()
            return bool(cursor.rowcount > 0)

    def close(self) -> None:
        """Close all connections managed by the factory."""
        self.factory.close_all()
