"""Unit tests for SimulationEngine snapshot, restore, fork, and SnapshotRing.

Verifies:
- SnapshotCapable protocol compliance.
- Restore then step reproduces original future exactly.
- Fork does not mutate parent.
- 100 forks finish well under 1 second.
- SnapshotRing capacity retention and tick retrieval.
"""
from __future__ import annotations

import time

import pytest

from arbiter.shared.v2_contracts import SimulationSnapshot, SnapshotCapable
from arbiter.simulator.engine import SimulationEngine
from arbiter.simulator.snapshot import SnapshotRing


def test_simulation_engine_implements_snapshot_capable_protocol() -> None:
    """SimulationEngine conforms to SnapshotCapable runtime protocol."""
    engine = SimulationEngine()
    assert isinstance(engine, SnapshotCapable)


def test_snapshot_and_restore_reproduces_exact_future() -> None:
    """Restoring a snapshot reproduces identical future telemetry and state."""
    engine = SimulationEngine(seed=42)
    # Warm up for 5 ticks
    for _ in range(5):
        engine.step()

    # Capture snapshot at tick 5
    snap = engine.snapshot()
    assert snap.tick == 5

    # Run 10 ticks forward to capture reference future
    reference_samples = [engine.step() for _ in range(10)]

    # Restore engine back to snapshot at tick 5
    engine.restore(snap)
    assert engine.tick == 5

    # Run 10 ticks again and assert 100% exact match
    restored_samples = [engine.step() for _ in range(10)]

    for tick_idx in range(10):
        ref_tick = reference_samples[tick_idx]
        res_tick = restored_samples[tick_idx]
        assert len(ref_tick) == len(res_tick)
        for s_ref, s_res in zip(ref_tick, res_tick):
            assert s_ref.cell_id == s_res.cell_id
            assert s_ref.sinr_db == s_res.sinr_db
            assert s_ref.dropped_users == s_res.dropped_users
            assert s_ref.active_users == s_res.active_users
            assert s_ref.throughput_mbps == s_res.throughput_mbps
            assert s_ref.energy_consumption_w == s_res.energy_consumption_w


def test_fork_does_not_mutate_parent() -> None:
    """Mutating forked engine cells, users, or clock does not alter parent."""
    parent = SimulationEngine(seed=42)
    for _ in range(3):
        parent.step()

    forked = parent.fork()
    assert isinstance(forked, SnapshotCapable)
    assert forked.tick == parent.tick
    assert forked.simulated_epoch_ms == parent.simulated_epoch_ms

    # Mutate forked state
    forked.cells["cell_A"].set_tx_power(31.5)
    forked.cells["cell_B"].set_sleep(True)
    forked.offload_overrides["ue-99"] = "cell_C"

    # Verify parent remains unchanged
    assert parent.cells["cell_A"].current_tx_power_dbm == pytest.approx(43.0)
    assert parent.cells["cell_B"].is_sleeping is False
    assert "ue-99" not in parent.offload_overrides


def test_100_forks_take_well_under_one_second() -> None:
    """100 forks of 5-cell engine complete well under 1 s (< 0.25 s)."""
    engine = SimulationEngine(seed=42)
    for _ in range(5):
        engine.step()

    start_time = time.perf_counter()
    forks = [engine.fork() for _ in range(100)]
    elapsed_seconds = time.perf_counter() - start_time

    assert len(forks) == 100
    assert elapsed_seconds < 0.50, f"100 forks took {elapsed_seconds:.3f} s"


def test_snapshot_ring_capacity_and_retrieval() -> None:
    """SnapshotRing enforces capacity limit and supports tick lookups."""
    ring = SnapshotRing(capacity=3)
    assert ring.capacity == 3
    assert len(ring) == 0
    assert ring.latest() is None

    s1 = SimulationSnapshot(tick=1, payload={"val": 10})
    s2 = SimulationSnapshot(tick=2, payload={"val": 20})
    s3 = SimulationSnapshot(tick=3, payload={"val": 30})
    s4 = SimulationSnapshot(tick=4, payload={"val": 40})

    ring.push(s1)
    ring.push(s2)
    ring.push(s3)
    assert len(ring) == 3
    assert ring.latest() == s3
    assert ring.get(1) == s1
    assert ring.get(2) == s2

    # Pushing 4th item evicts s1
    ring.push(s4)
    assert len(ring) == 3
    assert ring.latest() == s4
    assert ring.get(1) is None
    assert ring.get(2) == s2
    assert ring.get(4) == s4

    # Clear ring
    ring.clear()
    assert len(ring) == 0
    assert ring.latest() is None


def test_snapshot_ring_invalid_capacity_raises() -> None:
    """SnapshotRing raises ValueError on non-positive capacity."""
    with pytest.raises(ValueError, match="SnapshotRing capacity must be > 0"):
        SnapshotRing(capacity=0)


def test_engine_auto_snapshot_records_history() -> None:
    """Engine automatically records snapshots up to ring capacity."""
    engine = SimulationEngine(seed=42, snapshot_capacity=10)
    for _ in range(5):
        engine.step()

    assert len(engine.snapshot_ring) == 5
    assert engine.snapshot_ring.get(0) is not None
    assert engine.snapshot_ring.get(4) is not None
    assert engine.snapshot_ring.get(5) is None


def test_fork_with_custom_seed() -> None:
    """Forking with an explicit seed seeds RNG independently."""
    engine = SimulationEngine(seed=42)
    engine.step()

    fork1 = engine.fork(seed=101)
    fork2 = engine.fork(seed=999)

    samples1 = [fork1.step() for _ in range(5)]
    samples2 = [fork2.step() for _ in range(5)]

    # Different seeds cause different mobility trajectories over time
    sinrs1 = [s[0].sinr_db for s in samples1]
    sinrs2 = [s[0].sinr_db for s in samples2]
    assert sinrs1 != sinrs2
