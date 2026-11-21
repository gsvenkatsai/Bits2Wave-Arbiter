"""Database schemas and initial connection helpers for project ARBITER v2."""
from __future__ import annotations

import sqlite3

V2_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS v2_plans (
    plan_id TEXT PRIMARY KEY,
    source_command_id TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    expires_tick INTEGER NOT NULL,
    payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_counterfactuals (
    decision_id TEXT PRIMARY KEY,
    batch_tick INTEGER NOT NULL,
    horizon_ticks INTEGER NOT NULL,
    payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_impact (
    impact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    tariff_label TEXT NOT NULL,
    payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_calibration (
    calibration_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    payload JSON NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v2_plans_source_cmd ON v2_plans(source_command_id);
CREATE INDEX IF NOT EXISTS idx_v2_plans_tick ON v2_plans(created_tick);
CREATE INDEX IF NOT EXISTS idx_v2_counterfactuals_tick ON v2_counterfactuals(batch_tick);
CREATE INDEX IF NOT EXISTS idx_v2_impact_tick ON v2_impact(tick);
CREATE INDEX IF NOT EXISTS idx_v2_calibration_candidate ON v2_calibration(candidate_id);
CREATE INDEX IF NOT EXISTS idx_v2_calibration_tick ON v2_calibration(tick);
"""


def init_v2_tables(conn: sqlite3.Connection) -> None:
    """Idempotently initialize ARBITER v2 database tables and indexes."""
    conn.executescript(V2_SCHEMA_DDL)

