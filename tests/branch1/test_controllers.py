"""Unit tests for uncoordinated controllers and collision scenarios."""
import pytest
from arbiter.controllers.base import BaseController
from arbiter.controllers.coverage_capacity import CoverageCapacityController
from arbiter.controllers.energy_saving import EnergySavingController
from arbiter.controllers.load_balancing import LoadBalancingController
from arbiter.controllers.public_safety import PublicSafetyController
from arbiter.controllers.scenarios import (
    apply_uncoordinated_baseline,
    collision_report,
    trigger_2am_collision,
)
from arbiter.shared.models import ControllerType, ParameterType
from arbiter.simulator.engine import SimulationEngine
from arbiter.simulator.mocks.mock_proposal_sink import MockProposalSink
from arbiter.simulator.traffic import ScenarioType


def test_energy_saving_controller_triggers_after_5_ticks() -> None:
    ctrl = EnergySavingController(
        consecutive_low_ticks=5, low_load_threshold=0.15)

    # 4 ticks of low load -> no proposal
    for t in range(4):
        proposals = ctrl.evaluate_telemetry({"cell_B": 0.05}, current_tick=t)
        assert len(proposals) == 0

    # 5th tick of low load -> proposes sleep
    proposals = ctrl.evaluate_telemetry({"cell_B": 0.05}, current_tick=4)
    assert len(proposals) == 1
    assert proposals[0].target_cell_id == "cell_B"
    assert proposals[0].parameter == ParameterType.SLEEP_STATE
    assert proposals[0].requested_value == 1.0


def test_load_balancing_controller_triggers_above_75_percent() -> None:
    ctrl = LoadBalancingController(
        high_load_threshold=0.75, default_offload_ratio=0.40)
    adj = {"cell_A": ["cell_B", "cell_C"]}

    # Below 75% -> no offload
    props_low = ctrl.evaluate_telemetry(
        metrics={},
        adjacency_map=adj,
        cell_loads_override={"cell_A": 0.70, "cell_B": 0.10, "cell_C": 0.20},
    )
    assert len(props_low) == 0

    # Above 75% -> offload to least loaded neighbor (cell_B)
    props_high = ctrl.evaluate_telemetry(
        metrics={},
        adjacency_map=adj,
        cell_loads_override={"cell_A": 0.85, "cell_B": 0.10, "cell_C": 0.20},
    )
    assert len(props_high) == 1
    assert props_high[0].target_cell_id == "cell_B"
    assert props_high[0].parameter == ParameterType.OFFLOAD_RATIO
    assert props_high[0].requested_value == 0.40


def test_coverage_capacity_controller_triggers_below_0_sinr() -> None:
    ctrl = CoverageCapacityController(
        edge_sinr_threshold_db=0.0, default_power_boost_db=3.0)

    # SINR >= 0 -> no boost
    props_good = ctrl.evaluate_telemetry(
        metrics={},
        edge_sinr_override={"cell_A": 5.0},
        cell_powers={"cell_A": 43.0},
    )
    assert len(props_good) == 0

    # SINR < 0 -> boost by +3 dB
    props_bad = ctrl.evaluate_telemetry(
        metrics={},
        edge_sinr_override={"cell_A": -1.5},
        cell_powers={"cell_A": 43.0},
    )
    assert len(props_bad) == 1
    assert props_bad[0].target_cell_id == "cell_A"
    assert props_bad[0].parameter == ParameterType.TX_POWER_DBM
    assert props_bad[0].requested_value == 46.0


def test_power_boost_clamped_at_46() -> None:
    ctrl = CoverageCapacityController()
    # At 45 dBm, +3 dB would reach 48, but should be clamped to 46.0
    prop = ctrl.propose_power_boost(
        cell_id="cell_A", delta_db=3.0, current_power_dbm=45.0)
    assert prop.requested_value == 46.0


def test_public_safety_sets_emergency_flag() -> None:
    ctrl = PublicSafetyController()
    prop = ctrl.propose_emergency_channel(cell_id="cell_C", power_dbm=46.0)
    assert prop.emergency_flag is True
    assert prop.controller_type == ControllerType.PUBLIC_SAFETY
    assert prop.target_cell_id == "cell_C"
    assert prop.parameter == ParameterType.TX_POWER_DBM
    assert prop.requested_value == 46.0


def test_exact_2am_collision_trio() -> None:
    base_ts = 1732154400000
    sink = MockProposalSink()
    engine = SimulationEngine()
    setattr(engine, "proposal_sink", sink)

    proposals = trigger_2am_collision(engine=engine, base_ts_ms=base_ts)

    assert len(proposals) == 3
    # 1. ES: target cell_B, SLEEP_STATE, 1.0 (base + 10 ms)
    assert proposals[0].controller_type == ControllerType.ENERGY_SAVING
    assert proposals[0].target_cell_id == "cell_B"
    assert proposals[0].parameter == ParameterType.SLEEP_STATE
    assert proposals[0].requested_value == 1.0
    assert proposals[0].timestamp_epoch_ms == base_ts + 10

    # 2. LB: target cell_B, OFFLOAD_RATIO, 0.40 (base + 15 ms)
    assert proposals[1].controller_type == ControllerType.LOAD_BALANCING
    assert proposals[1].target_cell_id == "cell_B"
    assert proposals[1].parameter == ParameterType.OFFLOAD_RATIO
    assert proposals[1].requested_value == 0.40
    assert proposals[1].timestamp_epoch_ms == base_ts + 15

    # 3. CC: target cell_A, TX_POWER_DBM, 46.0 (base + 20 ms)
    assert proposals[2].controller_type == ControllerType.COVERAGE_CAPACITY
    assert proposals[2].target_cell_id == "cell_A"
    assert proposals[2].parameter == ParameterType.TX_POWER_DBM
    assert proposals[2].requested_value == 46.0
    assert proposals[2].timestamp_epoch_ms == base_ts + 20

    # Verify sink received all 3 proposals
    assert len(sink.received_proposals) == 3


def test_uncoordinated_baseline_yields_blackout() -> None:
    engine = SimulationEngine(topology="5_CELL_CORE",
                              scenario=ScenarioType.QUIET_2AM, seed=42)
    # Warm up 5 ticks
    for _ in range(5):
        engine.step()

    # Create snapshot before collision
    engine_before = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(5):
        engine_before.step()

    proposals = trigger_2am_collision(
        engine=engine, base_ts_ms=engine.simulated_epoch_ms)
    apply_uncoordinated_baseline(engine, proposals)

    # Step after collision applied
    engine.step()

    report = collision_report(engine_before, engine)
    assert report["cell_B_power_watts"] == 0.0, "Cell B must be 0 W"
    assert report["total_dropped_users"] > 0, "Users must be dropped"
    assert report["blackout_condition"] is True


def test_validation_errors_on_invalid_bounds() -> None:
    ctrl = CoverageCapacityController()

    with pytest.raises(ValueError, match="TX_POWER_DBM"):
        ctrl.propose("cell_A", ParameterType.TX_POWER_DBM,
                     requested_value=50.0)

    with pytest.raises(ValueError, match="TX_POWER_DBM"):
        ctrl.propose("cell_A", ParameterType.TX_POWER_DBM,
                     requested_value=20.0)

    with pytest.raises(ValueError, match="TILT_DEG"):
        ctrl.propose("cell_A", ParameterType.TILT_DEG, requested_value=20.0)

    with pytest.raises(ValueError, match="SLEEP_STATE"):
        ctrl.propose("cell_A", ParameterType.SLEEP_STATE, requested_value=2.0)

    with pytest.raises(ValueError, match="OFFLOAD_RATIO"):
        ctrl.propose("cell_A", ParameterType.OFFLOAD_RATIO,
                     requested_value=1.5)


def test_controllers_telemetry_sample_parsing_and_submit() -> None:
    from arbiter.shared.models import TelemetrySample

    sink = MockProposalSink()
    ctrl_es = EnergySavingController(consecutive_low_ticks=1)
    ctrl_lb = LoadBalancingController()
    ctrl_cc = CoverageCapacityController()
    ctrl_ps = PublicSafetyController()

    samples = [
        TelemetrySample(
            sample_id="s1",
            cell_id="cell_A",
            sinr_db=-2.0,
            dropped_users=0,
            active_users=35,
            throughput_mbps=70.0,
            energy_consumption_w=420.0,
            timestamp_epoch_ms=1000,
        ),
        TelemetrySample(
            sample_id="s2",
            cell_id="cell_B",
            sinr_db=15.0,
            dropped_users=0,
            active_users=4,
            throughput_mbps=8.0,
            energy_consumption_w=420.0,
            timestamp_epoch_ms=1000,
        ),
    ]

    # ES with TelemetrySample
    es_props = ctrl_es.evaluate_telemetry(samples, current_tick=1)
    assert len(es_props) > 0

    # LB with TelemetrySample and adjacency
    lb_props = ctrl_lb.evaluate_telemetry(
        samples, adjacency_map={"cell_A": ["cell_B"]})
    assert len(lb_props) > 0

    # CC with TelemetrySample
    cc_props = ctrl_cc.evaluate_telemetry(samples)
    assert len(cc_props) > 0

    # PS evaluate_telemetry returns []
    assert ctrl_ps.evaluate_telemetry(samples) == []

    # BaseController.submit
    res = BaseController.submit(es_props[0], sink)
    assert res["status"] == "QUEUED_MOCK"
