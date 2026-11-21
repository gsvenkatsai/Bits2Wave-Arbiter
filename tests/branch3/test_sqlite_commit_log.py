"""Unit tests for SQLite Commit Log, LKG Store, and WAL Concurrency.

SRS FR-7.3, NFR-12.
Verifies Write-Ahead Logging (WAL) configuration, schema indices,
multi-threaded concurrent read/write resilience without database locking,
and LKG cache persistence.
"""
import concurrent.futures
import sqlite3
import pytest

from arbiter.storage.database import DatabaseConnectionFactory
from arbiter.storage.commit_log import SQLiteCommitLogger
from arbiter.storage.lkg_store import LKGStore


def test_wal_mode_and_busy_timeout_enabled(tmp_db_factory):
    """Verify WAL journal mode and busy_timeout PRAGMAs are actively enforced."""
    conn = tmp_db_factory.get_connection()
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode;")
    journal_mode = cursor.fetchone()[0]
    assert str(journal_mode).lower() == "wal"

    cursor.execute("PRAGMA busy_timeout;")
    busy_timeout = cursor.fetchone()[0]
    assert busy_timeout >= 5000


def test_factory_close_and_foreign_keys(tmp_path):
    """Verify DatabaseConnectionFactory close() and configurable foreign_keys."""
    db_file = str(tmp_path / "test_fk.db")
    factory = DatabaseConnectionFactory(db_path=db_file, enable_foreign_keys=True)
    conn = factory.get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys;")
    assert cursor.fetchone()[0] == 1
    factory.close()


def test_commit_log_schema_and_indices(tmp_db_factory, commit_logger):
    """Verify table commit_log and indexes idx_commit_log_cell, idx_commit_log_time exist."""
    conn = tmp_db_factory.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commit_log';")
    assert cursor.fetchone() is not None

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_commit_log_%';")
    indexes = [row[0] for row in cursor.fetchall()]
    assert "idx_commit_log_cell" in indexes
    assert "idx_commit_log_time" in indexes


def test_commit_logger_update_rollback_flag(commit_logger):
    """Verify update_rollback_flag updates the commit row properly."""
    seq = commit_logger.log_commit(
        commit_id="cmt-flag-test",
        decision_id="dec-1",
        command_id="cmd-1",
        target_cell_id="cell_A",
        parameter="TX_POWER_DBM",
        old_value=43.0,
        new_value=44.0,
        applied_at_epoch_ms=1000,
        rollback_flag=0,
    )
    assert seq > 0

    updated = commit_logger.update_rollback_flag("cmt-flag-test", rollback_flag=1)
    assert updated is True

    record = commit_logger.get_commit("cmt-flag-test")
    assert record is not None
    assert record["rollback_flag"] == 1


def test_concurrent_read_write_no_locking(tmp_db_factory, commit_logger):
    """Verify concurrent threads reading and writing simultaneously experience no 'database is locked' errors."""
    n_threads = 8
    writes_per_thread = 25

    def worker_write(thread_id: int):
        for i in range(writes_per_thread):
            cmt_id = f"cmt-concur-{thread_id}-{i}"
            commit_logger.log_commit(
                commit_id=cmt_id,
                decision_id=f"dec-{thread_id}",
                command_id=f"cmd-{thread_id}",
                target_cell_id="cell_B",
                parameter="TX_POWER_DBM",
                old_value=43.0,
                new_value=44.0 + (i * 0.1),
                applied_at_epoch_ms=1732154400000 + i,
            )

    def worker_read(thread_id: int):
        for _ in range(writes_per_thread):
            _ = commit_logger.get_commits_by_cell("cell_B")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads * 2) as pool:
        futures = []
        for tid in range(n_threads):
            futures.append(pool.submit(worker_write, tid))
            futures.append(pool.submit(worker_read, tid))

        for f in concurrent.futures.as_completed(futures):
            # Must complete cleanly without sqlite3.OperationalError: database is locked
            f.result()

    commits = commit_logger.get_commits_by_cell("cell_B")
    assert len(commits) == n_threads * writes_per_thread


def test_lkg_store_persistence_and_cache(tmp_db_factory):
    """Verify LKG store caches values in memory and survives re-instantiation from disk."""
    store1 = LKGStore(tmp_db_factory)
    store1.set_lkg(cell_id="cell_A", parameter="TX_POWER_DBM", value=43.5, updated_at_epoch_ms=1000)
    store1.set_lkg(cell_id="cell_A", parameter="SLEEP_STATE", value=0.0, updated_at_epoch_ms=1001)

    assert store1.get_lkg("cell_A", "TX_POWER_DBM") == 43.5
    assert store1.get_lkg("cell_A", "SLEEP_STATE") == 0.0

    # Test get_cell_lkg and get_all_lkg
    cell_a_params = store1.get_cell_lkg("cell_A")
    assert cell_a_params["TX_POWER_DBM"] == 43.5
    all_lkg = store1.get_all_lkg()
    assert "cell_A" in all_lkg

    # New store instance pointing to same DB
    store2 = LKGStore(tmp_db_factory)
    # Purge in-memory cache to force database query
    store2._cache.clear()
    assert store2.get_lkg("cell_A", "TX_POWER_DBM") == 43.5
    assert store2.get_lkg("cell_A", "SLEEP_STATE") == 0.0
    assert store2.get_lkg("cell_A", "NON_EXISTENT") is None
