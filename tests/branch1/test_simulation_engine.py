"""Unit tests for discrete SimulationEngine and mock sinks."""
import time
import pytest
from arbiter.shared.models import (
    CommandProposal,
    ControllerType,
    ParameterType,
)
from arbiter.simulator.engine import SimulationEngine
from arbiter.simulator.mocks.mock_proposal_sink import MockProposalSink
from arbiter.simulator.mocks.mock_telemetry_sink import MockTelemetrySink
from arbiter.simulator.traffic import ScenarioType


def test_step_returns_one_sample_per_cell_with_format() -> None:
    sink = MockTelemetrySink()
    engine = SimulationEngine(
        topology="5_CELL_CORE",
        scenario=ScenarioType.QUIET_2AM,
        seed=42,
        telemetry_sink=sink,
    )
    samples = engine.step()

    assert len(samples) == 5
    for s in samples:
        assert s.sample_id == f"smp-0-{s.cell_id}"
        assert s.cell_id in engine.cells
        assert s.timestamp_epoch_ms == 1732154400000

    assert len(sink.batches) == 1
    assert sink.batches[0]["batch_tick"] == 0


def test_tick_advances_timestamp_by_100ms() -> None:
    engine = SimulationEngine(start_epoch_ms=1000)
    assert engine.simulated_epoch_ms == 1000

    engine.step()
    assert engine.tick == 1
    assert engine.simulated_epoch_ms == 1100

    engine.step()
    assert engine.tick == 2
    assert engine.simulated_epoch_ms == 1200


def test_sleeping_cell_drops_attached_users() -> None:
    engine = SimulationEngine(topology="5_CELL_CORE",
                              scenario=ScenarioType.QUIET_2AM, seed=42)
    # First tick to attach users
    engine.step()

    # Find a cell with attached active users
    active_users = engine.traffic_model.get_active_users(engine.tick)
    target_cid = active_users[0].attached_cell_id

    # Sleep this cell
    delta = CommandProposal(
        command_id="cmd-sleep",
        controller_id="ctrl-es",
        controller_type=ControllerType.ENERGY_SAVING,
        target_cell_id=target_cid,
        parameter=ParameterType.SLEEP_STATE,
        requested_value=1.0,
        timestamp_epoch_ms=engine.simulated_epoch_ms,
    )
    engine.apply_actuation_deltas([delta])

    # Next step
    samples = engine.step()
    target_sample = next(s for s in samples if s.cell_id == target_cid)
    assert target_sample.dropped_users > 0 or target_sample.active_users == 0


def test_low_sinr_drops_user() -> None:
    engine = SimulationEngine(topology="5_CELL_CORE",
                              scenario=ScenarioType.QUIET_2AM, seed=42)
    active_users = engine.traffic_model.get_active_users(0)
    test_ue = active_users[0]
    test_ue.sinr_db = -5.0
    engine.cells[test_ue.attached_cell_id].set_sleep(False)

    engine.evaluate_user_drops([test_ue])
    assert test_ue.is_dropped is True


def test_power_and_tilt_clamp_respected() -> None:
    engine = SimulationEngine(topology="5_CELL_CORE",
                              scenario=ScenarioType.QUIET_2AM, seed=42)

    delta_power_high = CommandProposal(
        command_id="c1",
        controller_id="ctrl-cc",
        controller_type=ControllerType.COVERAGE_CAPACITY,
        target_cell_id="cell_A",
        parameter=ParameterType.TX_POWER_DBM,
        requested_value=60.0,
        timestamp_epoch_ms=engine.simulated_epoch_ms,
    )
    delta_power_low = CommandProposal(
        command_id="c2",
        controller_id="ctrl-cc",
        controller_type=ControllerType.COVERAGE_CAPACITY,
        target_cell_id="cell_B",
        parameter=ParameterType.TX_POWER_DBM,
        requested_value=10.0,
        timestamp_epoch_ms=engine.simulated_epoch_ms,
    )
    delta_tilt_high = CommandProposal(
        command_id="c3",
        controller_id="ctrl-cc",
        controller_type=ControllerType.COVERAGE_CAPACITY,
        target_cell_id="cell_C",
        parameter=ParameterType.TILT_DEG,
        requested_value=25.0,
        timestamp_epoch_ms=engine.simulated_epoch_ms,
    )

    engine.apply_actuation_deltas(
        [delta_power_high, delta_power_low, delta_tilt_high])

    assert engine.cells["cell_A"].current_tx_power_dbm == 46.0  # Max clamp
    assert engine.cells["cell_B"].current_tx_power_dbm == 30.0  # Min clamp
    assert engine.cells["cell_C"].antenna_tilt_deg == 15.0  # Max tilt clamp


def test_offload_moves_users_to_target() -> None:
    engine = SimulationEngine(topology="5_CELL_CORE",
                              scenario=ScenarioType.QUIET_2AM, seed=42)
    engine.step()  # step 0 to assign users to cell_A

    # Propose offloading 40% from most loaded neighbor (cell_A) to cell_B
    delta_offload = CommandProposal(
        command_id="c_lb",
        controller_id="ctrl-lb",
        controller_type=ControllerType.LOAD_BALANCING,
        target_cell_id="cell_B",
        parameter=ParameterType.OFFLOAD_RATIO,
        requested_value=0.40,
        timestamp_epoch_ms=engine.simulated_epoch_ms,
    )
    engine.apply_actuation_deltas([delta_offload])

    # Next step will honor offload overrides
    engine.step()
    pinned_count = sum(
        1 for cid in engine.offload_overrides.values() if cid == "cell_B")
    assert pinned_count > 0, "Offload must pin users to target cell_B"


def test_determinism_with_same_seed() -> None:
    eng1 = SimulationEngine(topology="5_CELL_CORE",
                            scenario=ScenarioType.QUIET_2AM, seed=777)
    eng2 = SimulationEngine(topology="5_CELL_CORE",
                            scenario=ScenarioType.QUIET_2AM, seed=777)

    for _ in range(5):
        s1 = eng1.step()
        s2 = eng2.step()
        for sample1, sample2 in zip(s1, s2):
            assert sample1.sinr_db == sample2.sinr_db
            assert sample1.active_users == sample2.active_users
            assert sample1.energy_consumption_w == sample2.energy_consumption_w


def test_50_ticks_finish_under_2_seconds() -> None:
    engine = SimulationEngine(topology="5_CELL_CORE",
                              scenario=ScenarioType.QUIET_2AM, seed=42)
    t0 = time.perf_counter()
    for _ in range(50):
        engine.step()
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, f"50 ticks took {elapsed:.3f} s (must be < 2.0 s)"


def test_19_cell_cluster_runs_with_350_users() -> None:
    engine = SimulationEngine(
        topology="19_CELL", scenario=ScenarioType.PEAK, seed=42)
    assert len(engine.users) == 350
    assert len(engine.cells) == 19

    samples = engine.step()
    assert len(samples) == 19


def test_mock_proposal_sink() -> None:
    sink = MockProposalSink()
    proposal = CommandProposal(
        command_id="cmd-1",
        controller_id="ctrl-test",
        controller_type=ControllerType.ENERGY_SAVING,
        target_cell_id="cell_A",
        parameter=ParameterType.SLEEP_STATE,
        requested_value=1.0,
        timestamp_epoch_ms=1000,
    )
    res = sink.submit_proposal(proposal)
    assert res["status"] == "QUEUED_MOCK"
    assert res["command_id"] == "cmd-1"
    assert len(sink.received_proposals) == 1


def test_edge_sinr_and_dict_deltas() -> None:
    engine = SimulationEngine(topology="5_CELL_CORE",
                              scenario=ScenarioType.QUIET_2AM, seed=42)
    engine.step()

    edge_sinr = engine.get_edge_sinr()
    assert len(edge_sinr) == 5

    # Dict delta
    dict_delta = {
        "target_cell_id": "cell_A",
        "parameter": "TX_POWER_DBM",
        "approved_value": 44.0,
    }
    engine.apply_actuation_deltas(
        [
            dict_delta,
            {"target_cell_id": "unknown_cell", "parameter": "TILT_DEG"},
        ]
    )
    assert engine.cells["cell_A"].current_tx_power_dbm == 44.0


def test_cli_main_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    from arbiter.simulator.engine import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "engine.py",
            "--scenario",
            "2am_collision",
            "--ticks",
            "8",
            "--cells",
            "5",
        ],
    )
    main()
