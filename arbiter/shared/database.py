"""Database schemas and initial connection helpers for SQLite."""
import sqlite3
import os
from arbiter.shared.database_v2 import init_v2_tables

SCHEMA_DDL = """
PRAGMA journal_mode=WAL;

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

CREATE TABLE IF NOT EXISTS event_stream (
    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    batch_tick INTEGER NOT NULL,
    sample_id TEXT NOT NULL,
    cell_id TEXT NOT NULL,
    sinr_db REAL NOT NULL,
    dropped_users INTEGER NOT NULL,
    active_users INTEGER NOT NULL,
    throughput_mbps REAL NOT NULL,
    energy_w REAL NOT NULL,
    recorded_at_epoch_ms INTEGER NOT NULL
);

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

CREATE INDEX IF NOT EXISTS idx_commit_log_cell ON commit_log(target_cell_id);
CREATE INDEX IF NOT EXISTS idx_event_stream_tick ON event_stream(batch_tick);
"""

def init_database(db_path: str = "./arbiter_production.db") -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_DDL)
        init_v2_tables(conn)
        conn.commit()
    finally:
        conn.close()
