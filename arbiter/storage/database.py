"""SQLite Database Connection Factory with WAL mode and concurrency resilience.

SRS FR-7.3, NFR-12.
Ensures Write-Ahead Logging (WAL) is active, busy_timeout is configured,
and connections are safely managed across concurrent threads.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional

from arbiter.shared.database import init_database

__all__ = ["DatabaseConnectionFactory", "init_database"]


class DatabaseConnectionFactory:
    """Thread-safe SQLite connection factory enabling WAL and busy handling."""

    def __init__(
        self,
        db_path: str = "./arbiter_production.db",
        busy_timeout_ms: int = 5000,
        enable_foreign_keys: bool = False,
    ) -> None:
        self.db_path: str = db_path
        self.busy_timeout_ms: int = busy_timeout_ms
        self.enable_foreign_keys: bool = enable_foreign_keys
        self._local = threading.local()
        self._lock = threading.Lock()

        # Initialize directory and ensure base WAL pragmas
        os.makedirs(
            os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True
        )
        self._init_pragmas()

    def _init_pragmas(self) -> None:
        """Initialize database-level pragmas."""
        with self._lock:
            timeout_sec = self.busy_timeout_ms / 1000.0
            conn = sqlite3.connect(self.db_path, timeout=timeout_sec)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
                if self.enable_foreign_keys:
                    conn.execute("PRAGMA foreign_keys=ON;")
                else:
                    conn.execute("PRAGMA foreign_keys=OFF;")
                conn.commit()
            finally:
                conn.close()

    def get_connection(self) -> sqlite3.Connection:
        """Provide a thread-local SQLite connection for concurrent access."""
        conn: Optional[sqlite3.Connection] = getattr(
            self._local, "connection", None
        )
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=self.busy_timeout_ms / 1000.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
            if self.enable_foreign_keys:
                conn.execute("PRAGMA foreign_keys=ON;")
            else:
                conn.execute("PRAGMA foreign_keys=OFF;")
            self._local.connection = conn
        return conn

    def close(self) -> None:
        """Close connection for current thread."""
        conn: Optional[sqlite3.Connection] = getattr(
            self._local, "connection", None
        )
        if conn is not None:
            conn.close()
            self._local.connection = None
