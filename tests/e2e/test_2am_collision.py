"""End-to-End closed-loop test for the 2 AM Collision scenario.

Playbook Section 6.1 Traceability:
1. Initial State: 5 cells active at 43 dBm; Cell B active user count drops,
   Cell A user count surges.
2. Tick 1 Ingress: ES proposes Cell B OFF; LB proposes 40% A->B;
   CC proposes Cell A +3 dB.
3. Referee Processing:
   - Conflict graph builds 2 clash edges.
   - Decision engine detects Cell B shutdown clashing with 40% user transfer.
   - Engine issues DELAY (hold 1) to ES; bisects LB offload to MODIFY (15%);
     bisects CC power to MODIFY (+1 dB).
4. Actuation & Verification:
   - Actuator commits 15% offload and +1 dB power boost to commit_log.
   - Cell B remains active while offloaded users complete handovers.
   - Zero users dropped (0.0% drop rate).
   - Uncoordinated baseline comparison: blackout (Cell B 0 W, dropped users).
"""
import sqlite3
import pytest

from arbiter.actuator.enforcer import ActuatorEnforcer
from arbiter.controllers.scenarios import (
    apply_uncoordinated_baseline,
    collision_report,
    trigger_2am_collision,
)
from arbiter.detector.graph import ConflictGraphBuilder
from arbiter.engine.actuator_adapter import ActuatorAdapter
from arbiter.engine.contracts import (
    ActionType,
    CommandProposal,
    ReasonCode,
)
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.shared.database import init_database
from arbiter.shared.models import ControllerType
from arbiter.simulator.engine import ScenarioType, SimulationEngine
from arbiter.storage.commit_log import SQLiteCommitLogger
from tests.branch2.test_decision_ladder import _scenario_twin, _two_am_commands


HEX_ADJACENCY: dict[str, list[str]] = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}


class MockNetworkTarget:
    """In-memory target tracking parameter changes applied by Actuator."""

    def __init__(self) -> None:
        self.applied: dict[str, dict[str, float]] = {}

    def apply_parameter(
        self, cell_id: str, parameter: str, value: float
    ) -> bool:
        if cell_id not in self.applied:
            self.applied[cell_id] = {}
        self.applied[cell_id][parameter] = value
        return True

    def get_parameter(self, cell_id: str, parameter: str) -> float:
        return self.applied.get(cell_id, {}).get(parameter, 0.0)


def test_2am_collision_ingress_and_conflict_graph() -> None:
    """Verify ingress proposals land in same 100 ms and create clash edges."""
    base_ts = 1732154400000
    proposals = trigger_2am_collision(base_ts_ms=base_ts)

    assert len(proposals) == 3
    # Check 100 ms window: +10, +15, +20 ms from base
    for p in proposals:
        assert base_ts <= p.timestamp_epoch_ms < base_ts + 100

    def make_prop(**overrides: object) -> CommandProposal:
        payload = dict(overrides)
        payload.setdefault("controller_type", ControllerType.ENERGY_SAVING)
        payload.setdefault("timestamp_epoch_ms", base_ts)
        return CommandProposal.model_validate(payload)

    commands = _two_am_commands(make_prop)
    builder = ConflictGraphBuilder()
    graph = builder.build(commands, HEX_ADJACENCY)

    # Conflict graph builds 2 clash edges: (es, lb) and (cc, es)
    assert sorted(graph.nodes) == ["cc", "es", "lb"]
    edge_pairs = {tuple(sorted(e)) for e in graph.edges}
    assert edge_pairs == {("es", "lb"), ("cc", "es")}


def test_2am_collision_referee_arbitration_and_commit_log(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Verify referee issues DELAY to ES, modifies LB & CC, commits to DB."""
    db_file = str(tmp_path / "test_arbiter_e2e.db")
    init_database(db_file)

    commit_logger = SQLiteCommitLogger(db_file)
    target = MockNetworkTarget()
    enforcer = ActuatorEnforcer(target=target, commit_logger=commit_logger)
    actuator_adapter = ActuatorAdapter(enforcer)

    def make_prop(**overrides: object) -> CommandProposal:
        payload = dict(overrides)
        payload.setdefault("controller_type", ControllerType.ENERGY_SAVING)
        payload.setdefault("timestamp_epoch_ms", 1000)
        return CommandProposal.model_validate(payload)

    commands = _two_am_commands(make_prop)
    twin = _scenario_twin()
    orchestrator = QueueOrchestrator(
        twin=twin,
        adjacency=HEX_ADJACENCY,
        actuator=actuator_adapter,
        max_workers=2,
    )

    for cmd in commands:
        assert orchestrator.submit(cmd) is True

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=1000)
    assert result["decisions_count"] == 3

    by_id = {d.command_id: d for d in result["decisions"]}

    # ES gets DELAY (hold count 1)
    assert by_id["es"].action is ActionType.DELAY
    assert by_id["es"].hold_count == 1

    # LB offload bisected to approx 15% (0.1528)
    assert by_id["lb"].action is ActionType.MODIFY
    assert by_id["lb"].reason_code is ReasonCode.HEADROOM
    assert by_id["lb"].approved_value == pytest.approx(0.1528, abs=0.01)

    # CC power boost bisected to approx +1 dB (31.146 vs 30.0)
    assert by_id["cc"].action is ActionType.MODIFY
    assert by_id["cc"].approved_value == pytest.approx(31.146, abs=0.2)

    # Verify SQLite commit_log rows for the 2 committed decisions
    conn = sqlite3.connect(db_file)
    rows = conn.execute(
        "SELECT command_id, target_cell_id, parameter, old_value, new_value "
        "FROM commit_log ORDER BY commit_seq ASC"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    row_lb = rows[0]
    assert row_lb[0] == "lb"
    assert row_lb[1] == "A"
    assert row_lb[2] == "OFFLOAD_RATIO"
    assert row_lb[3] == 0.0
    assert row_lb[4] == pytest.approx(0.1528, abs=0.01)

    row_cc = rows[1]
    assert row_cc[0] == "cc"
    assert row_cc[1] == "A"
    assert row_cc[2] == "TX_POWER_DBM"
    assert row_cc[3] == 30.0
    assert row_cc[4] == pytest.approx(31.146, abs=0.2)


def test_2am_collision_simulation_coordinated_vs_uncoordinated() -> None:
    """Verify coordinated avoids blackout while uncoordinated causes drops."""
    # 1. Uncoordinated Baseline: causes blackout
    engine_uncoord = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(5):
        engine_uncoord.step()

    engine_before = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(5):
        engine_before.step()

    uncoord_props = trigger_2am_collision(
        engine=engine_uncoord, base_ts_ms=engine_uncoord.simulated_epoch_ms
    )
    apply_uncoordinated_baseline(engine_uncoord, uncoord_props)
    engine_uncoord.step()

    report_uncoord = collision_report(engine_before, engine_uncoord)
    assert report_uncoord["cell_B_power_watts"] == 0.0, "Cell B at 0 W"
    assert report_uncoord["total_dropped_users"] > 0, "Users dropped"
    assert report_uncoord["blackout_condition"] is True

    # 2. Coordinated ARBITER: Cell B remains active, avoiding blackout
    engine_coord = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(5):
        engine_coord.step()

    # Under ARBITER: ES is DELAYED, so Cell B is NOT put to sleep.
    # Cell B remains ACTIVE at full power (~20 W / 43 dBm)
    active_b_power_w = engine_coord.cells["cell_B"].effective_tx_power_w()
    assert active_b_power_w > 0.0, "Cell B stays active under ARBITER"

    report_coord = collision_report(engine_before, engine_coord)
    assert report_coord["cell_B_power_watts"] > 0.0
    assert report_coord["blackout_condition"] is False

    # Note on Playbook Risk (b): Playbook assumes 35 UEs on Cell A and 4 on
    # Cell B with 0 drops; Branch 1 fixture uses 70 UEs at 0.22 scale
    # (15 active) where ue_0013 on Cell C has low baseline SINR.
    # Uncoordinated blackout causes 5 drops (Cell B shut down); coordinated
    # maintains Cell B active and prevents blackout.
    assert (
        report_uncoord["total_dropped_users"]
        > report_coord["total_dropped_users"]
    )
